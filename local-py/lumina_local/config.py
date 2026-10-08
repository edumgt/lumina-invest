"""로컬 설정 — 환경변수(.env) 또는 CLI 인자로 덮어쓴다.

app/config.py 의 QUANT_* / RISK_* 기본값을 그대로 가져왔다. .env 는 선택이며,
없으면 아래 기본값으로 동작한다(모의 장부 + Yahoo 시세).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict, replace
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """의존성 없이 .env 를 환경변수로 올린다(이미 있는 값은 덮어쓰지 않는다)."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _str(key: str, default: str) -> str:
    return (os.getenv(key) or default).strip()


def _int(key: str, default: int) -> int:
    try:
        return int(_str(key, str(default)))
    except ValueError:
        return default


def _float(key: str, default: float) -> float:
    try:
        return float(_str(key, str(default)))
    except ValueError:
        return default


def _bool(key: str, default: bool) -> bool:
    return _str(key, "true" if default else "false").lower() in ("1", "true", "yes", "on")


def _list(key: str, default: list[str]) -> list[str]:
    raw = _str(key, "")
    return [s.strip() for s in raw.split(",") if s.strip()] or list(default)


@dataclass
class Settings:
    # ── 실행 ────────────────────────────────────────────────────────────
    data_dir: Path = BASE_DIR / "data"
    cycle_sec: int = 180                 # 루프 주기(초). app 의 QUANT_CYCLE_SEC 과 같은 의미
    mode: str = "paper"                  # paper = 로컬 가상장부만 / live = KIS 실주문까지
    market_data_source: str = "yahoo"    # yahoo | kis | mock(오프라인 테스트)
    user_id: str = "local"               # 상태 파일 안에서 계정을 구분하는 키

    # ── 종목 선정 ───────────────────────────────────────────────────────
    symbol_source: str = "ai"            # ai = 점수 상위 N / manual = selected_symbols
    selected_symbols: list[str] = field(default_factory=list)
    ai_top_n: int = 3
    strategy_id: str = ""                # strategies/<id>.json 을 쓰면 스펙 규칙으로 판정
    candle_period: str = "2y"
    candle_cache_hours: float = 6.0

    # ── 주문 크기 ───────────────────────────────────────────────────────
    initial_capital: float = 10_000_000.0
    per_trade_budget: float = 1_000_000.0
    buy_ratio: float = 1.0
    sell_ratio: float = 0.5

    # ── 위험관리 ────────────────────────────────────────────────────────
    risk_daily_loss_limit_pct: float = 3.0
    risk_max_position_pct: float = 30.0
    risk_max_orders_per_day: int = 20
    risk_cooldown_min: int = 30

    # ── 공격 모드(분봉 단기) ────────────────────────────────────────────
    aggressive: bool = False
    aggressive_candle_interval: str = "5m"
    aggressive_candle_range: str = "5d"
    aggressive_cache_min: int = 2
    aggressive_cooldown_min: int = 3
    aggressive_max_orders_per_day: int = 300
    aggressive_max_buys_per_cycle: int = 2
    aggressive_max_sells_per_cycle: int = 3
    aggressive_force_buy: bool = True
    aggressive_take_profit_pct: float = 1.5
    aggressive_stop_loss_pct: float = 1.0

    # ── KIS 실주문(live 모드에서만 사용) ────────────────────────────────
    kis_app_key: str = ""
    kis_app_secret: str = ""
    kis_account_no: str = ""             # 8자리 CANO + 2자리 상품코드 (예: 5012345601)
    kis_paper: bool = True               # True = 모의투자 서버(openapivts)
    kis_enforce_market_hours: bool = True
    http_timeout: float = 15.0

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> "Settings":
        _load_dotenv(env_file or (BASE_DIR / ".env"))
        return cls(
            data_dir=Path(_str("LOCAL_DATA_DIR", str(BASE_DIR / "data"))),
            cycle_sec=_int("QUANT_CYCLE_SEC", 180),
            mode=_str("QUANT_MODE", "paper").lower(),
            market_data_source=_str("MARKET_DATA_SOURCE", "yahoo").lower(),
            user_id=_str("LOCAL_USER_ID", "local"),
            symbol_source=_str("QUANT_SYMBOL_SOURCE", "ai").lower(),
            selected_symbols=_list("QUANT_SELECTED_SYMBOLS", []),
            ai_top_n=_int("QUANT_AI_TOP_N", 3),
            strategy_id=_str("QUANT_STRATEGY_ID", ""),
            candle_period=_str("QUANT_CANDLE_PERIOD", "2y"),
            candle_cache_hours=_float("QUANT_CANDLE_CACHE_HOURS", 6.0),
            initial_capital=_float("QUANT_INITIAL_CAPITAL", 10_000_000.0),
            per_trade_budget=_float("QUANT_PER_TRADE_BUDGET", 1_000_000.0),
            buy_ratio=_float("QUANT_BUY_RATIO", 1.0),
            sell_ratio=_float("QUANT_SELL_RATIO", 0.5),
            risk_daily_loss_limit_pct=_float("RISK_DAILY_LOSS_LIMIT_PCT", 3.0),
            risk_max_position_pct=_float("RISK_MAX_POSITION_PCT", 30.0),
            risk_max_orders_per_day=_int("RISK_MAX_ORDERS_PER_DAY", 20),
            risk_cooldown_min=_int("RISK_COOLDOWN_MIN", 30),
            aggressive=_bool("QUANT_AGGRESSIVE_MODE", False),
            aggressive_candle_interval=_str("QUANT_AGGRESSIVE_CANDLE_INTERVAL", "5m"),
            aggressive_candle_range=_str("QUANT_AGGRESSIVE_CANDLE_RANGE", "5d"),
            aggressive_cache_min=_int("QUANT_AGGRESSIVE_CACHE_MIN", 2),
            aggressive_cooldown_min=_int("QUANT_AGGRESSIVE_COOLDOWN_MIN", 3),
            aggressive_max_orders_per_day=_int("QUANT_AGGRESSIVE_MAX_ORDERS_PER_DAY", 300),
            aggressive_max_buys_per_cycle=_int("QUANT_AGGRESSIVE_MAX_BUYS_PER_CYCLE", 2),
            aggressive_max_sells_per_cycle=_int("QUANT_AGGRESSIVE_MAX_SELLS_PER_CYCLE", 3),
            aggressive_force_buy=_bool("QUANT_AGGRESSIVE_FORCE_BUY", True),
            aggressive_take_profit_pct=_float("QUANT_AGGRESSIVE_TAKE_PROFIT_PCT", 1.5),
            aggressive_stop_loss_pct=_float("QUANT_AGGRESSIVE_STOP_LOSS_PCT", 1.0),
            kis_app_key=_str("KIS_APP_KEY", ""),
            kis_app_secret=_str("KIS_APP_SECRET", ""),
            kis_account_no=_str("KIS_ACCOUNT_NO", ""),
            kis_paper=_bool("KIS_PAPER", True),
            kis_enforce_market_hours=_bool("KIS_ENFORCE_MARKET_HOURS", True),
            http_timeout=_float("HTTP_TIMEOUT", 15.0),
        )

    # ── 파생 값 ─────────────────────────────────────────────────────────
    @property
    def state_path(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    def kis_configured(self) -> bool:
        return bool(self.kis_app_key and self.kis_app_secret and self.kis_account_no)

    def clamped(self) -> "Settings":
        """app 쪽 사이클이 적용하던 상·하한을 그대로 적용한 복사본."""
        return replace(
            self,
            mode=self.mode if self.mode in ("paper", "live") else "paper",
            symbol_source=self.symbol_source if self.symbol_source in ("ai", "manual") else "ai",
            per_trade_budget=max(10_000.0, min(self.per_trade_budget, 10_000_000.0)),
            buy_ratio=max(0.1, min(self.buy_ratio, 1.0)),
            sell_ratio=max(0.1, min(self.sell_ratio, 1.0)),
            ai_top_n=max(1, self.ai_top_n),
            cycle_sec=max(10, self.cycle_sec),
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["data_dir"] = str(self.data_dir)
        for secret in ("kis_app_key", "kis_app_secret", "kis_account_no"):
            d[secret] = "***" if d[secret] else ""
        return d
