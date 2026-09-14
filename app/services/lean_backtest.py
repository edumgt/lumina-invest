"""QuantConnect LEAN 백테스트 — domain-rag-lab의 lean_backtest_service 이식.

흐름: Yahoo Finance 일봉(httpx) → prices.csv + LEAN 알고리즘(main.py) 생성 → LEAN 실행 →
pandas로 전략 곡선/리스크 지표 계산 → 결과 dict.

LEAN 실행 모드 (settings.LEAN_MODE):
  - ssh    : domain-rag-lab 방식. 원격 서버(LEAN_SSH_HOST)로 scp 후 `docker run quantconnect/lean` (SSH).
  - docker : stock-coin-trade 방식. 같은 호스트의 Docker 데몬에서 직접 `docker run quantconnect/lean`.
             (컨테이너 안에서 실행 시 /var/run/docker.sock 마운트 + LEAN_HOST_WORKDIR 지정 필요)
  - local  : LEAN을 실행하지 않고 pandas 계산만 수행 (LEAN 미설치 환경의 폴백).
  - auto   : LEAN_SSH_HOST가 있으면 ssh, docker CLI가 있으면 docker, 둘 다 없으면 local.

수익률·MDD·샤프 등 화면 지표는 원본과 같이 pandas로 계산하고, LEAN은 동일 전략을 실제 엔진에서
돌려 로그와 통계(summary.json)를 함께 돌려준다.
"""
from __future__ import annotations

import asyncio
import glob
import json
import os
import shutil
import subprocess
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pandas as pd

from app.config import settings
from app.services.stock import HEADERS

STRATEGY_LABELS = {
    "buy_hold": "매수 후 보유",
    "ma_cross": "이동평균 교차 (골든/데드크로스)",
    "dca": "정액 분할 매수 (DCA)",
    "momentum": "모멘텀 돌파",
}
STRATEGY_HINTS = {
    "buy_hold": "첫 거래일에 전액 매수 후 종료일까지 보유합니다. 다른 전략의 기준선이 됩니다.",
    "ma_cross": "단기 이동평균이 장기 이동평균 위로 올라가면 매수, 아래로 내려가면 전량 매도합니다.",
    "dca": "일정 거래일 간격으로 같은 금액을 나누어 매수합니다. 진입 시점 분산 효과를 확인합니다.",
    "momentum": "직전 N거래일 최고가를 돌파하면 매수, 최저가를 이탈하면 매도합니다.",
}

# 지표 계산용 선행 구간 (달력일)
_LOOKBACK_BUFFER_DAYS = {
    "buy_hold": 0,
    "dca": 0,
    "ma_cross": lambda long_window: long_window * 2 + 15,
    "momentum": lambda breakout_window: breakout_window * 2 + 15,
}

# LEAN 엔진이 Initialize() 전에 반드시 읽는 정적 참조 데이터 (QuantConnect/Lean 저장소에서 벤더링).
_REFERENCE_DATA_DIR = Path(__file__).parent / "lean_reference_data"
_REFERENCE_DATA_FILES = [
    "market-hours/market-hours-database.json",
    "symbol-properties/symbol-properties-database.csv",
    "symbol-properties/security-database.csv",
]


class LeanBacktestError(RuntimeError):
    pass


def resolve_lean_mode() -> str:
    mode = (settings.LEAN_MODE or "auto").lower()
    if mode != "auto":
        return mode
    if settings.LEAN_SSH_HOST and settings.LEAN_SSH_KEY_PATH:
        return "ssh"
    if shutil.which("docker") or os.path.exists(settings.DOCKER_SOCK):
        return "docker"
    return "local"


def lean_status() -> dict:
    mode = resolve_lean_mode()
    return {
        "mode": mode,
        "image": settings.LEAN_DOCKER_IMAGE,
        "ssh_host": settings.LEAN_SSH_HOST if mode == "ssh" else "",
        "workdir": settings.LEAN_WORKDIR if mode == "docker" else settings.LEAN_REMOTE_WORKDIR,
        "docker_via": ("cli" if shutil.which("docker") else "socket") if mode == "docker" else "",
        "engine_runs": mode in ("ssh", "docker"),
        "reference_data_ok": all((_REFERENCE_DATA_DIR / f).exists() for f in _REFERENCE_DATA_FILES),
        "strategies": [{"value": k, "label": v, "hint": STRATEGY_HINTS[k]} for k, v in STRATEGY_LABELS.items()],
    }


# ── 가격 데이터 ───────────────────────────────────────────────────────────

async def fetch_close_series(ticker: str, start: date, end: date) -> pd.Series:
    """Yahoo chart API(period1/period2)로 일봉 종가를 받아 DatetimeIndex Series로 돌려준다."""
    p1 = int(datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).timestamp())
    p2 = int(datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc).timestamp())
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}"
    params = {"interval": "1d", "period1": p1, "period2": p2, "events": "div,splits"}
    async with httpx.AsyncClient(timeout=20.0, headers=HEADERS) as client:
        resp = await client.get(url, params=params)
        if resp.status_code == 404:
            raise LeanBacktestError(f"Yahoo Finance에서 티커를 찾지 못했습니다: {ticker}")
        resp.raise_for_status()
        result = resp.json().get("chart", {}).get("result") or []
    if not result:
        raise LeanBacktestError("Yahoo Finance에서 해당 기간의 종가를 받지 못했습니다.")
    data = result[0]
    ts = data.get("timestamp") or []
    ind = data.get("indicators", {})
    adj = (ind.get("adjclose") or [{}])[0].get("adjclose")
    closes = adj if adj else (ind.get("quote") or [{}])[0].get("close") or []
    rows = [(datetime.fromtimestamp(t, tz=timezone.utc).date(), c) for t, c in zip(ts, closes) if c is not None]
    if len(rows) < 2:
        raise LeanBacktestError("Yahoo Finance에서 해당 기간의 종가를 받지 못했습니다.")
    idx = pd.to_datetime([r[0] for r in rows])
    return pd.Series([float(r[1]) for r in rows], index=idx, name="close")


# ── 서비스 ─────────────────────────────────────────────────────────────────

class LeanBacktestService:
    async def run(
        self,
        *,
        ticker: str,
        start_date: date,
        end_date: date,
        compare_start_date: date,
        compare_end_date: date,
        initial_cash: float,
        strategy: str = "buy_hold",
        short_window: int = 20,
        long_window: int = 60,
        dca_interval_days: int = 21,
        breakout_window: int = 20,
    ) -> dict:
        if start_date >= end_date or compare_start_date >= compare_end_date:
            raise LeanBacktestError("시작일은 종료일보다 앞서야 합니다.")
        if strategy not in STRATEGY_LABELS:
            raise LeanBacktestError(f"지원하지 않는 전략입니다: {strategy}")

        buffer_days = self._lookback_buffer(strategy, long_window, breakout_window)
        fetch_start = min(start_date, compare_start_date) - timedelta(days=buffer_days)
        fetch_end = max(end_date, compare_end_date)
        close = await fetch_close_series(ticker, fetch_start, fetch_end)

        series = close.loc[(close.index.date >= start_date) & (close.index.date <= end_date)]
        comparison = close.loc[(close.index.date >= compare_start_date) & (close.index.date <= compare_end_date)]
        if len(series) < 2 or len(comparison) < 2:
            raise LeanBacktestError("선택한 기간에 거래일 데이터가 충분하지 않습니다.")

        strategy_curve = self._strategy_curve(strategy, close, start_date, end_date, short_window, long_window, dca_interval_days, breakout_window)
        if len(strategy_curve) < 2:
            raise LeanBacktestError("선택한 기간에 전략을 계산할 데이터가 충분하지 않습니다.")

        # LEAN 실행 (블로킹 subprocess → 스레드)
        mode = resolve_lean_mode()
        algorithm_source = self._algorithm_source(strategy, start_date, end_date, initial_cash, short_window, long_window, dca_interval_days, breakout_window)
        lean_log, lean_stats, lean_ok = "", {}, False
        if mode in ("ssh", "docker"):
            try:
                lean_log, lean_stats = await asyncio.to_thread(self._run_lean, mode, strategy, close, algorithm_source)
                lean_ok = bool(lean_stats)
            except LeanBacktestError as exc:
                lean_log = f"[LEAN 실행 실패] {exc}"
            except Exception as exc:  # 네트워크·SSH·Docker 오류는 화면 지표까지 막지 않는다.
                lean_log = f"[LEAN 실행 오류] {exc}"
        else:
            lean_log = "[LEAN 미실행] LEAN_MODE=local — LEAN_SSH_HOST(원격) 또는 Docker(로컬)를 설정하면 동일 전략을 QuantConnect LEAN 엔진에서 실행합니다."

        strategy_return = (float(strategy_curve.iloc[-1]) / float(strategy_curve.iloc[0]) - 1) * 100
        benchmark_return = self._return(series)
        comparison_return = self._return(comparison)
        drawdown = (strategy_curve / strategy_curve.cummax() - 1).min() * 100
        analytics = self._analytics(strategy, close, strategy_curve, start_date, end_date, short_window, long_window, dca_interval_days, breakout_window)
        step = max(1, len(strategy_curve) // 120)
        points = [{"date": idx.strftime("%Y-%m-%d"), "value": round(float(value * initial_cash), 2)}
                  for idx, value in strategy_curve.iloc[::step].items()]
        bench_curve = series / float(series.iloc[0])
        bench_points = [{"date": idx.strftime("%Y-%m-%d"), "value": round(float(value * initial_cash), 2)}
                        for idx, value in bench_curve.iloc[::max(1, len(bench_curve) // 120)].items()]

        engine = {"ssh": "QuantConnect LEAN (remote docker) + Yahoo Finance",
                  "docker": "QuantConnect LEAN (local docker) + Yahoo Finance",
                  "local": "pandas (LEAN 미실행) + Yahoo Finance"}[mode]
        return {
            "ticker": ticker,
            "engine": engine,
            "lean_mode": mode,
            "lean_ok": lean_ok,
            "strategy": strategy,
            "strategy_label": STRATEGY_LABELS.get(strategy, strategy),
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "compare_start_date": compare_start_date.isoformat(),
            "compare_end_date": compare_end_date.isoformat(),
            "initial_cash": initial_cash,
            "strategy_return_pct": round(strategy_return, 2),
            "benchmark_return_pct": round(benchmark_return, 2),
            "comparison_return_pct": round(comparison_return, 2),
            "outperformance_pct": round(strategy_return - comparison_return, 2),
            "max_drawdown_pct": round(float(drawdown), 2),
            **analytics,
            "points": points,
            "benchmark_points": bench_points,
            "lean_statistics": lean_stats,
            "lean_log": lean_log[-6000:],
            "algorithm_source": algorithm_source,
            "disclaimer": "Yahoo Finance 일봉 기반 교육용 예시입니다. 배당·세금·수수료·슬리피지·데이터 품질과 실제 체결은 반영하지 않으며 투자 권유가 아닙니다.",
        }

    # ── LEAN 실행 ───────────────────────────────────────────────────────

    def _run_lean(self, mode: str, strategy: str, close: pd.Series, algorithm_source: str) -> tuple[str, dict]:
        work_id = f"workflow-{uuid.uuid4().hex[:12]}"
        class_name = self._algorithm_class_name(strategy)
        lean_args = (
            "--environment backtesting --algorithm-language Python "
            f"--algorithm-type-name {class_name} "
            "--algorithm-location /workspace/main.py --data-folder /workspace/data "
            "--results-destination-folder /workspace/results --backtest-name workflow"
        )
        if mode == "ssh":
            return self._run_lean_ssh(work_id, close, algorithm_source, lean_args)
        return self._run_lean_docker(work_id, close, algorithm_source, lean_args)

    def _run_lean_docker(self, work_id: str, close: pd.Series, algorithm_source: str, lean_args: str) -> tuple[str, dict]:
        """같은 호스트의 Docker 데몬에서 LEAN 컨테이너를 실행한다.

        - 작업 폴더 루트(LEAN_WORKDIR)를 통째로 /workspace 에 마운트하고 하위 폴더(work_id)를 인자로 넘긴다.
          → 호스트 경로 대신 named volume(LEAN_DOCKER_VOLUME)을 쓸 수 있어 컨테이너 안에서도 이식성이 좋다.
        - Docker CLI가 있으면 CLI, 없으면 /var/run/docker.sock Engine API(httpx UDS)로 실행한다.
        """
        base = Path(settings.LEAN_WORKDIR).resolve()
        local = base / work_id
        (local / "data").mkdir(parents=True, exist_ok=True)
        (local / "results").mkdir(parents=True, exist_ok=True)
        self._write_prices(local / "data" / "prices.csv", close)
        (local / "main.py").write_text(algorithm_source, encoding="utf-8")
        for relative in _REFERENCE_DATA_FILES:
            src = _REFERENCE_DATA_DIR / relative
            if not src.exists():
                raise LeanBacktestError(f"LEAN 참조 데이터 파일이 없습니다: {relative}")
            dst = local / "data" / relative
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)

        # 컨테이너 안에서 실행 중이면(Docker-outside-of-Docker) named volume 또는 호스트 경로로 바꿔 마운트한다.
        mount_src = settings.LEAN_DOCKER_VOLUME or settings.LEAN_HOST_WORKDIR or str(base)
        args = lean_args.replace("/workspace/", f"/workspace/{work_id}/").split()
        try:
            if shutil.which("docker"):
                cmd = ["docker", "run", "--rm", "-v", f"{mount_src}:/workspace", settings.LEAN_DOCKER_IMAGE, *args]
                try:
                    result = subprocess.run(cmd, text=True, capture_output=True, timeout=settings.LEAN_TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired:
                    raise LeanBacktestError(f"LEAN 백테스트가 {settings.LEAN_TIMEOUT_SECONDS}초 안에 끝나지 않았습니다.")
                log = (result.stdout + "\n" + result.stderr).strip()
            elif os.path.exists(settings.DOCKER_SOCK):
                log = self._docker_api_run(args, f"{mount_src}:/workspace")
            else:
                raise LeanBacktestError("docker CLI도 Docker 소켓(/var/run/docker.sock)도 없어 LEAN을 실행할 수 없습니다.")
            stats = self._read_summary(local / "results")
        finally:
            if not settings.LEAN_KEEP_WORKDIR:
                shutil.rmtree(local, ignore_errors=True)
        return log, stats

    def _docker_api_run(self, args: list[str], bind: str) -> str:
        """Docker Engine API(unix socket)로 `docker run --rm` 을 흉내낸다 (CLI 미설치 컨테이너용)."""
        transport = httpx.HTTPTransport(uds=settings.DOCKER_SOCK)
        timeout = settings.LEAN_TIMEOUT_SECONDS
        with httpx.Client(transport=transport, base_url="http://docker", timeout=30.0) as client:
            img = client.get(f"/images/{settings.LEAN_DOCKER_IMAGE}/json")
            if img.status_code == 404:
                raise LeanBacktestError(f"Docker 이미지가 없습니다: {settings.LEAN_DOCKER_IMAGE} — 호스트에서 `docker pull {settings.LEAN_DOCKER_IMAGE}` 후 다시 시도하세요.")
            created = client.post("/containers/create", json={
                "Image": settings.LEAN_DOCKER_IMAGE,
                "Cmd": args,
                "HostConfig": {"Binds": [bind]},
            })
            if created.status_code >= 300:
                raise LeanBacktestError(f"LEAN 컨테이너 생성 실패: {created.text[:300]}")
            cid = created.json()["Id"]
            try:
                started = client.post(f"/containers/{cid}/start")
                if started.status_code >= 300:
                    raise LeanBacktestError(f"LEAN 컨테이너 시작 실패: {started.text[:300]}")
                try:
                    client.post(f"/containers/{cid}/wait", timeout=timeout + 5)
                except httpx.TimeoutException:
                    client.post(f"/containers/{cid}/kill")
                    raise LeanBacktestError(f"LEAN 백테스트가 {timeout}초 안에 끝나지 않았습니다.")
                logs = client.get(f"/containers/{cid}/logs", params={"stdout": 1, "stderr": 1})
                return self._demux_docker_logs(logs.content)
            finally:
                client.delete(f"/containers/{cid}", params={"force": 1, "v": 1})

    @staticmethod
    def _demux_docker_logs(raw: bytes) -> str:
        """Docker 로그 스트림(8바이트 헤더 프레임)을 평문으로 변환한다. TTY 로그면 그대로 디코드."""
        out, i, n = [], 0, len(raw)
        while i + 8 <= n:
            stream_type = raw[i]
            if stream_type not in (0, 1, 2) or raw[i + 1:i + 4] != b"\x00\x00\x00":
                return raw.decode("utf-8", errors="replace")
            size = int.from_bytes(raw[i + 4:i + 8], "big")
            out.append(raw[i + 8:i + 8 + size].decode("utf-8", errors="replace"))
            i += 8 + size
        return "".join(out).strip()

    def _run_lean_ssh(self, work_id: str, close: pd.Series, algorithm_source: str, lean_args: str) -> tuple[str, dict]:
        if not settings.LEAN_SSH_HOST or not settings.LEAN_SSH_KEY_PATH:
            raise LeanBacktestError("원격 LEAN 실행 설정(LEAN_SSH_HOST, LEAN_SSH_KEY_PATH)이 없습니다.")
        import tempfile
        with tempfile.TemporaryDirectory(prefix="lean-backtest-") as tmp:
            local = Path(tmp)
            (local / "data").mkdir()
            self._write_prices(local / "data" / "prices.csv", close)
            (local / "main.py").write_text(algorithm_source, encoding="utf-8")
            remote = f"{settings.LEAN_REMOTE_WORKDIR}/{work_id}"
            self._ssh(f"mkdir -p {remote}/data {remote}/results")
            shared = self._ensure_shared_reference_data()
            self._ssh(f"cp -r {shared}/. {remote}/data/")
            self._scp(local / "main.py", f"{remote}/main.py")
            self._scp(local / "data" / "prices.csv", f"{remote}/data/prices.csv")
            command = f"docker run --rm -v {remote}:/workspace {settings.LEAN_DOCKER_IMAGE} {lean_args}"
            log = self._ssh(command, timeout=settings.LEAN_TIMEOUT_SECONDS, check=False)
            summary = self._ssh(f"cat {remote}/results/*-summary.json 2>/dev/null || true", check=False)
            stats = {}
            try:
                if summary.strip().startswith("{"):
                    stats = self._pick_statistics(json.loads(summary))
            except Exception:
                stats = {}
            if not settings.LEAN_KEEP_WORKDIR:
                self._ssh(f"rm -rf {remote}", check=False)
        return log, stats

    @staticmethod
    def _read_summary(results_dir: Path) -> dict:
        for path in glob.glob(str(results_dir / "*-summary.json")):
            try:
                return LeanBacktestService._pick_statistics(json.loads(Path(path).read_text(encoding="utf-8")))
            except Exception:
                continue
        return {}

    @staticmethod
    def _pick_statistics(summary: dict) -> dict:
        statistics = (summary or {}).get("statistics") or {}
        keys = ["Start Equity", "End Equity", "Net Profit", "Compounding Annual Return", "Sharpe Ratio",
                "Drawdown", "Total Orders", "Win Rate", "Annual Standard Deviation"]
        picked = {k: statistics[k] for k in keys if k in statistics}
        if not picked and statistics:
            picked = dict(list(statistics.items())[:10])
        return picked

    def _ensure_shared_reference_data(self) -> str:
        shared = f"{settings.LEAN_REMOTE_WORKDIR}/_shared-lean-data"
        marker = f"{shared}/symbol-properties/symbol-properties-database.csv"
        check = self._ssh(f"test -f {marker} && echo present || echo missing", check=False)
        if check.strip().endswith("present"):
            return shared
        self._ssh(f"mkdir -p {shared}/market-hours {shared}/symbol-properties")
        for relative in _REFERENCE_DATA_FILES:
            local_path = _REFERENCE_DATA_DIR / relative
            if not local_path.exists():
                raise LeanBacktestError(f"LEAN 참조 데이터 파일이 없습니다: {relative}")
            self._scp(local_path, f"{shared}/{relative}")
        return shared

    def _ssh(self, command: str, timeout: int = 30, check: bool = True) -> str:
        args = ["ssh", "-i", settings.LEAN_SSH_KEY_PATH, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
                f"{settings.LEAN_SSH_USER}@{settings.LEAN_SSH_HOST}", command]
        try:
            result = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise LeanBacktestError(f"원격 LEAN 작업이 {timeout}초 안에 끝나지 않았습니다.")
        if check and result.returncode:
            raise LeanBacktestError(result.stderr.strip() or "원격 LEAN 작업을 시작하지 못했습니다.")
        return (result.stdout + "\n" + result.stderr).strip()

    def _scp(self, source: Path, destination: str) -> None:
        args = ["scp", "-i", settings.LEAN_SSH_KEY_PATH, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
                str(source), f"{settings.LEAN_SSH_USER}@{settings.LEAN_SSH_HOST}:{destination}"]
        result = subprocess.run(args, text=True, capture_output=True, timeout=60)
        if result.returncode:
            raise LeanBacktestError(result.stderr.strip() or "원격 작업 폴더로 데이터를 전송하지 못했습니다.")

    # ── 분석 ────────────────────────────────────────────────────────────

    def _analytics(self, strategy, close, strategy_curve, start_date, end_date, short_window, long_window, dca_interval_days, breakout_window) -> dict:
        daily = strategy_curve.pct_change().dropna()
        periods = max(1, len(strategy_curve) - 1)
        years = periods / 252
        annualized_return = ((float(strategy_curve.iloc[-1]) / float(strategy_curve.iloc[0])) ** (1 / years) - 1) * 100 if years > 0 else 0.0
        annualized_volatility = float(daily.std(ddof=0) * (252 ** 0.5) * 100) if len(daily) else 0.0
        sharpe = ((annualized_return / 100) - 0.02) / (annualized_volatility / 100) if annualized_volatility else 0.0

        if strategy == "ma_cross":
            position = self._position_ma_cross(close, short_window, long_window)
        elif strategy == "momentum":
            position = self._position_momentum(close, breakout_window)
        elif strategy == "dca":
            position = close * 0
            window = position.loc[(position.index.date >= start_date) & (position.index.date <= end_date)]
            for i, idx in enumerate(window.index):
                position.loc[idx] = min(1.0, (i // max(1, dca_interval_days) + 1) / max(1, (len(window) - 1) // max(1, dca_interval_days) + 1))
        else:
            position = close * 0 + 1.0
        position_window = position.loc[(position.index.date >= start_date) & (position.index.date <= end_date)]
        invested_days = float(position_window.mean() * 100) if len(position_window) else 0.0
        changes = position_window.diff().fillna(position_window.iloc[0] if len(position_window) else 0)
        trade_count = int((changes.abs() > 0.001).sum())

        price_window = close.loc[(close.index.date >= start_date) & (close.index.date <= end_date)]
        last = float(price_window.iloc[-1])
        ma20 = float(price_window.rolling(20).mean().iloc[-1]) if len(price_window) >= 20 else None
        ma60 = float(price_window.rolling(60).mean().iloc[-1]) if len(price_window) >= 60 else None
        ret20 = self._return(price_window.iloc[-21:]) if len(price_window) >= 21 else None
        high_252 = float(price_window.iloc[-252:].max())
        low_252 = float(price_window.iloc[-252:].min())
        range_position = ((last - low_252) / (high_252 - low_252) * 100) if high_252 > low_252 else 50.0
        if ma20 is not None and ma60 is not None:
            trend = "상승 추세 관찰" if last > ma20 > ma60 else "하락·횡보 구간 관찰" if last < ma20 < ma60 else "추세 혼조 구간"
        else:
            trend = "추세 판단에 필요한 거래일이 부족함"
        return {
            "annualized_return_pct": round(annualized_return, 2),
            "annualized_volatility_pct": round(annualized_volatility, 2),
            "sharpe_ratio": round(sharpe, 2),
            "invested_days_pct": round(invested_days, 1),
            "trade_count": trade_count,
            "market_snapshot": {
                "as_of": price_window.index[-1].strftime("%Y-%m-%d"),
                "last_price": round(last, 2),
                "return_20d_pct": round(ret20, 2) if ret20 is not None else None,
                "ma20": round(ma20, 2) if ma20 is not None else None,
                "ma60": round(ma60, 2) if ma60 is not None else None,
                "range_252d_position_pct": round(range_position, 1),
                "trend": trend,
            },
        }

    @staticmethod
    def _lookback_buffer(strategy: str, long_window: int, breakout_window: int) -> int:
        entry = _LOOKBACK_BUFFER_DAYS.get(strategy, 0)
        if callable(entry):
            return entry(long_window if strategy == "ma_cross" else breakout_window)
        return entry

    def _strategy_curve(self, strategy, close, start_date, end_date, short_window, long_window, dca_interval_days, breakout_window):
        if strategy == "dca":
            window = close.loc[(close.index.date >= start_date) & (close.index.date <= end_date)]
            return self._equity_dca(window, dca_interval_days)
        if strategy == "ma_cross":
            position_full = self._position_ma_cross(close, short_window, long_window)
        elif strategy == "momentum":
            position_full = self._position_momentum(close, breakout_window)
        else:
            position_full = close * 0 + 1.0
        equity_full = self._equity_from_position(close, position_full)
        window_equity = equity_full.loc[(equity_full.index.date >= start_date) & (equity_full.index.date <= end_date)]
        if len(window_equity) < 2:
            return window_equity
        return window_equity / float(window_equity.iloc[0])

    @staticmethod
    def _equity_from_position(close, position):
        daily_return = close.pct_change().fillna(0.0)
        return (1.0 + daily_return * position).cumprod()

    @staticmethod
    def _position_ma_cross(close, short_window: int, long_window: int):
        short_ma = close.rolling(short_window).mean()
        long_ma = close.rolling(long_window).mean()
        signal = (short_ma > long_ma).astype(float)
        return signal.shift(1).fillna(0.0)  # 교차 관찰 다음 날 행동 (look-ahead 방지)

    @staticmethod
    def _position_momentum(close, window: int):
        prior_high = close.shift(1).rolling(window).max()
        prior_low = close.shift(1).rolling(window).min()
        holding = False
        flags = []
        for price, hi, lo in zip(close, prior_high, prior_low):
            if not holding and hi == hi and price > hi:
                holding = True
            elif holding and lo == lo and price < lo:
                holding = False
            flags.append(1.0 if holding else 0.0)
        position = close * 0
        position[:] = flags
        return position.shift(1).fillna(0.0)

    @staticmethod
    def _equity_dca(close, interval_days: int):
        interval_days = max(1, interval_days)
        n = len(close)
        buy_points = set(range(0, n, interval_days))
        portion = 1.0 / len(buy_points)
        shares, cash, values = 0.0, 1.0, []
        for i, price in enumerate(close):
            price = float(price)
            if i in buy_points and price > 0:
                cash -= portion
                shares += portion / price
            values.append(shares * price + cash)
        equity = close * 0
        equity[:] = values
        return equity

    @staticmethod
    def _return(series) -> float:
        return (float(series.iloc[-1]) / float(series.iloc[0]) - 1) * 100

    @staticmethod
    def _write_prices(path: Path, close) -> None:
        lines = ["date,close"] + [f"{idx.strftime('%Y-%m-%d')},{float(value):.8f}" for idx, value in close.items()]
        path.write_text("\n".join(lines), encoding="utf-8")

    # ── LEAN 알고리즘 소스 생성 ───────────────────────────────────────

    @staticmethod
    def _algorithm_class_name(strategy: str) -> str:
        return {
            "buy_hold": "YFinanceBuyHoldAlgorithm",
            "ma_cross": "YFinanceMovingAverageCrossAlgorithm",
            "dca": "YFinanceDollarCostAveragingAlgorithm",
            "momentum": "YFinanceMomentumBreakoutAlgorithm",
        }.get(strategy, "YFinanceBuyHoldAlgorithm")

    @staticmethod
    def _reader_boilerplate() -> str:
        return (
            'from AlgorithmImports import *\n'
            'from QuantConnect.Python import PythonData\n'
            'from QuantConnect import Globals, SubscriptionTransportMedium\n'
            'from QuantConnect.Data import SubscriptionDataSource\n'
            'import os\n'
            'from datetime import datetime, timedelta\n\n'
            'class YFPrice(PythonData):\n'
            '    def get_source(self, config, date, is_live):\n'
            '        return SubscriptionDataSource(os.path.join(Globals.DataFolder, "prices.csv"), SubscriptionTransportMedium.LocalFile)\n'
            '    def reader(self, config, line, date, is_live):\n'
            '        if line.startswith("date"):\n'
            '            return None\n'
            '        d, close = line.split(",")\n'
            '        item = YFPrice()\n'
            '        item.symbol = config.symbol\n'
            '        item.time = datetime.strptime(d, "%Y-%m-%d")\n'
            '        item.end_time = item.time + timedelta(days=1)\n'
            '        item.value = float(close)\n'
            '        return item\n\n'
        )

    def _algorithm_source(self, strategy, start, end, cash, short_window, long_window, dca_interval_days, breakout_window) -> str:
        header = f'''    def initialize(self):
        self.set_start_date({start.year}, {start.month}, {start.day})
        self.set_end_date({end.year}, {end.month}, {end.day})
        self.set_cash({cash})
        self.asset = self.add_data(YFPrice, "YF", Resolution.DAILY).symbol
        # 커스텀 데이터의 기본 lot size(1주)는 cash/price < 1 인 경우 SetHoldings()를 0으로 만든다.
        # 아주 작은 lot size를 지정해 소수 단위 포지션을 허용한다.
        self.securities[self.asset].symbol_properties = SymbolProperties("", "USD", 1, 0.01, 0.0000001, "")
'''
        if strategy == "ma_cross":
            body = f'''{header}        self.short_window = {short_window}
        self.long_window = {long_window}
        self.prices = []

    def on_data(self, data):
        if not data.contains_key(self.asset):
            return
        self.prices.append(float(data[self.asset].value))
        self.prices = self.prices[-self.long_window:]
        if len(self.prices) < self.long_window:
            return
        short_ma = sum(self.prices[-self.short_window:]) / self.short_window
        long_ma = sum(self.prices) / self.long_window
        if short_ma > long_ma and not self.portfolio.invested:
            self.set_holdings(self.asset, 1)
        elif short_ma <= long_ma and self.portfolio.invested:
            self.liquidate(self.asset)
'''
            class_name = "YFinanceMovingAverageCrossAlgorithm"
        elif strategy == "dca":
            total_days = max(1, (end - start).days)
            body = f'''{header}        self.interval_days = {dca_interval_days}
        self.day_count = 0
        self.buys_done = 0
        self.total_buys = max(1, {total_days} // self.interval_days)
        self.portion = 1.0 / self.total_buys

    def on_data(self, data):
        if not data.contains_key(self.asset):
            return
        if self.day_count % self.interval_days == 0 and self.buys_done < self.total_buys:
            self.buys_done += 1
            target = min(1.0, self.buys_done * self.portion)
            self.set_holdings(self.asset, target)
        self.day_count += 1
'''
            class_name = "YFinanceDollarCostAveragingAlgorithm"
        elif strategy == "momentum":
            body = f'''{header}        self.window = {breakout_window}
        self.prices = []

    def on_data(self, data):
        if not data.contains_key(self.asset):
            return
        price = float(data[self.asset].value)
        if len(self.prices) >= self.window:
            prior_high = max(self.prices[-self.window:])
            prior_low = min(self.prices[-self.window:])
            if not self.portfolio.invested and price > prior_high:
                self.set_holdings(self.asset, 1)
            elif self.portfolio.invested and price < prior_low:
                self.liquidate(self.asset)
        self.prices.append(price)
'''
            class_name = "YFinanceMomentumBreakoutAlgorithm"
        else:
            body = f'''{header}
    def on_data(self, data):
        if not self.portfolio.invested and data.contains_key(self.asset):
            self.set_holdings(self.asset, 1)
'''
            class_name = "YFinanceBuyHoldAlgorithm"
        return self._reader_boilerplate() + f"class {class_name}(QCAlgorithm):\n" + body


service = LeanBacktestService()
