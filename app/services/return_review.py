"""수익율검증 — KIS 일별 평가액(kis_equity_snapshots)을 매매 이력·전략설정 프로필과 맞춰 보고, 수익률을 올리기 위한 가이드를 만든다.

구성
  1. parse_pasted(): KIS 화면 표(날짜 · 누적수익률 · 누적손익 · 원금 · 평가액 · 건수) 붙여넣기 → 행.
  2. enrich()/summary(): 일별 변화율·낙폭·최악/최고일·변동성.
  3. trading_by_day()/trade_stats(): live_orders(FILLED) 를 평균단가법으로 왕복 손익·승률·손익비·기대값, 날짜별 체결 수.
  4. mode_by_day(): 전략설정 프로필(strategy_settings 의 _periods/_bucket_for)로 그 날 장 마감 시점의 운영 모드를 귀속.
  5. build_guide(): 위 수치로 근거 있는 진단 → 권고(전략설정 어느 항목을 어떻게) → 기대 효과 → 검증 방법.
  6. record_gateway_snapshot(): 게이트웨이 잔고로 오늘 행 기록 (비트 태스크 quant.equity_snapshot · 화면 버튼 공용).

모든 판단 함수는 순수 함수라 DB 없이 테스트한다. 숫자는 '권유'가 아니라 설정 변경의 근거로만 쓴다.
"""
from __future__ import annotations

import logging
import math
import re
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
CLOSE_TIME = time(15, 30)
DEFAULT_PRINCIPAL = 100_000_000.0

# 2026-10-13 사용자가 KIS Testbed 화면에서 옮겨 적은 실측값 (alembic 0011 시드와 동일)
SEED_ROWS: list[dict[str, Any]] = [
    {"snap_date": "2026-09-28", "cum_return_pct": -0.07, "cum_pnl": -72_700, "principal": 100_000_000, "equity": 99_927_300, "raw_count": 2284},
    {"snap_date": "2026-09-29", "cum_return_pct": -0.06, "cum_pnl": -59_730, "principal": 100_000_000, "equity": 99_940_270, "raw_count": 2246},
    {"snap_date": "2026-09-30", "cum_return_pct": -0.15, "cum_pnl": -153_141, "principal": 100_000_000, "equity": 99_846_859, "raw_count": 2360},
    {"snap_date": "2026-10-01", "cum_return_pct": 0.19, "cum_pnl": 187_460, "principal": 100_000_000, "equity": 100_187_460, "raw_count": 1598},
    {"snap_date": "2026-10-02", "cum_return_pct": 0.19, "cum_pnl": 185_889, "principal": 100_000_000, "equity": 100_185_889, "raw_count": 1649},
    {"snap_date": "2026-10-06", "cum_return_pct": 0.07, "cum_pnl": 74_154, "principal": 100_000_000, "equity": 100_074_154, "raw_count": 1902},
    {"snap_date": "2026-10-07", "cum_return_pct": -0.50, "cum_pnl": -500_146, "principal": 100_000_000, "equity": 99_499_854, "raw_count": 2927},
    {"snap_date": "2026-10-08", "cum_return_pct": -2.04, "cum_pnl": -2_044_366, "principal": 100_000_000, "equity": 97_955_634, "raw_count": 3443},
    {"snap_date": "2026-10-12", "cum_return_pct": -6.66, "cum_pnl": -6_661_763, "principal": 100_000_000, "equity": 93_338_237, "raw_count": 4021},
]


# ── 1. 붙여넣기 파싱 ───────────────────────────────────────────────────────────

_DATE_RE = re.compile(r"^(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})\.?$")


def _num(token: str) -> float:
    return float(token.replace(",", "").replace("%", "").replace("원", "").strip())


def parse_pasted(text: str) -> list[dict[str, Any]]:
    """KIS 화면 표를 탭/공백 구분으로 붙여넣은 텍스트 → 행 목록. 형식이 맞지 않는 줄은 ValueError(줄 번호 포함)."""
    rows: list[dict[str, Any]] = []
    for lineno, raw in enumerate((text or "").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p for p in re.split(r"[\t ]+", line) if p]
        if len(parts) < 5:
            raise ValueError(f"{lineno}행: 날짜·수익률·손익·원금·평가액 5개 이상이 필요합니다 ({line[:40]})")
        m = _DATE_RE.match(parts[0])
        if not m:
            raise ValueError(f"{lineno}행: 날짜 형식(YYYY.MM.DD) 이 아닙니다: {parts[0]}")
        snap = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        try:
            pct, pnl, principal, equity = _num(parts[1]), _num(parts[2]), _num(parts[3]), _num(parts[4])
            cnt = int(_num(parts[5])) if len(parts) > 5 and parts[5].replace(",", "").lstrip("-").isdigit() else None
        except ValueError as exc:
            raise ValueError(f"{lineno}행: 숫자를 읽지 못했습니다 ({exc})") from exc
        if principal <= 0 or equity <= 0:
            raise ValueError(f"{lineno}행: 원금·평가액은 양수여야 합니다")
        if abs((principal + pnl) - equity) > max(1.0, principal * 0.0005):
            raise ValueError(f"{lineno}행: 원금+손익({principal + pnl:,.0f}) 과 평가액({equity:,.0f}) 이 맞지 않습니다")
        rows.append({"snap_date": snap, "cum_return_pct": pct, "cum_pnl": pnl, "principal": principal, "equity": equity, "raw_count": cnt})
    if not rows:
        raise ValueError("읽을 수 있는 행이 없습니다.")
    rows.sort(key=lambda r: r["snap_date"])
    return rows


# ── 2. 일별 변화·요약 ──────────────────────────────────────────────────────────

def _d(v: Any) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])


def enrich(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """날짜순 정렬 + daily_change_pct(앞 행 대비 평가액 변화) · daily_pnl · drawdown_pct(최고 평가액 대비) 를 붙인다."""
    out: list[dict[str, Any]] = []
    prev = None
    peak = 0.0
    for r in sorted(rows, key=lambda x: _d(x["snap_date"])):
        eq = float(r["equity"])
        peak = max(peak, eq)
        item = {**r, "snap_date": _d(r["snap_date"]).isoformat(), "equity": eq, "principal": float(r["principal"]),
                "cum_return_pct": float(r["cum_return_pct"]), "cum_pnl": float(r["cum_pnl"]),
                "daily_change_pct": round((eq / prev["equity"] - 1) * 100, 3) if prev else None,
                "daily_pnl": round(eq - prev["equity"], 0) if prev else None,
                "drawdown_pct": round((eq / peak - 1) * 100, 3) if peak else 0.0,
                "gap_days": (_d(r["snap_date"]) - _d(prev["snap_date"])).days if prev else None}
        out.append(item)
        prev = item
    return out


def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    rows = rows if rows and "daily_change_pct" in rows[0] else enrich(rows)
    if not rows:
        return {"days": 0}
    last, first = rows[-1], rows[0]
    changes = [r["daily_change_pct"] for r in rows if r["daily_change_pct"] is not None]
    worst = min(rows[1:], key=lambda r: r["daily_change_pct"]) if changes else None
    best = max(rows[1:], key=lambda r: r["daily_change_pct"]) if changes else None
    mean = sum(changes) / len(changes) if changes else 0.0
    vol = math.sqrt(sum((c - mean) ** 2 for c in changes) / len(changes)) if changes else 0.0
    recent = rows[-5:]
    recent_chg = (recent[-1]["equity"] / recent[0]["equity"] - 1) * 100 if len(recent) > 1 else 0.0
    return {
        "days": len(rows), "first_date": first["snap_date"], "last_date": last["snap_date"],
        "principal": last["principal"], "equity": last["equity"],
        "cum_return_pct": last["cum_return_pct"], "cum_pnl": last["cum_pnl"],
        "max_drawdown_pct": round(min(r["drawdown_pct"] for r in rows), 3),
        "peak_equity": max(r["equity"] for r in rows),
        "up_days": sum(1 for c in changes if c > 0), "down_days": sum(1 for c in changes if c < 0), "flat_days": sum(1 for c in changes if c == 0),
        "avg_daily_change_pct": round(mean, 3), "daily_volatility_pct": round(vol, 3),
        "worst_day": {"date": worst["snap_date"], "change_pct": worst["daily_change_pct"], "pnl": worst["daily_pnl"]} if worst else None,
        "best_day": {"date": best["snap_date"], "change_pct": best["daily_change_pct"], "pnl": best["daily_pnl"]} if best else None,
        "recent_change_pct": round(recent_chg, 3), "recent_span": f"{recent[0]['snap_date']}~{recent[-1]['snap_date']}" if len(recent) > 1 else "",
        "loss_share_recent_pct": _loss_share(rows),
    }


def _loss_share(rows: list[dict[str, Any]]) -> float | None:
    """최근 3개 관측일 손실이 전체 누적 손실에서 차지하는 비율(%). 손실이 아니면 None."""
    losses = [r["daily_pnl"] for r in rows if r["daily_pnl"] is not None and r["daily_pnl"] < 0]
    if not losses:
        return None
    recent = [r["daily_pnl"] for r in rows[-3:] if r["daily_pnl"] is not None and r["daily_pnl"] < 0]
    return round(sum(recent) / sum(losses) * 100, 1) if sum(losses) else None


# ── 3. 매매 이력 ───────────────────────────────────────────────────────────────

def _norm(symbol: str) -> str:
    return str(symbol or "").strip().upper().removesuffix(".KS").removesuffix(".KQ")[:6]


def _to_kst_date(ts: Any) -> date | None:
    if ts is None:
        return None
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return None
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(KST).date()
    return ts if isinstance(ts, date) else None


def trading_by_day(live_rows: list[dict[str, Any]], all_live: list[dict[str, Any]] | None = None,
                   virtual_rows: list[dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    """live_rows: FILLED 실주문 [{time, symbol, side(BUY|SELL), qty, price}]. all_live: 상태 무관 전체(체결률용). virtual_rows: 가상 체결.

    반환 {date: {fills, buys, sells, buy_amount, sell_amount, realized, wins, losses, orders, open_or_cancelled, virtual_buys, virtual_sells, round_trips: [...]}}
    평균단가법: 매수마다 평균단가 갱신, 매도마다 (매도가-평균단가)×수량 을 그 날 실현손익으로.
    """
    by: dict[str, dict[str, Any]] = defaultdict(lambda: {"fills": 0, "buys": 0, "sells": 0, "buy_amount": 0.0, "sell_amount": 0.0, "realized": 0.0,
                                                          "wins": 0, "losses": 0, "orders": 0, "open_or_cancelled": 0, "virtual_buys": 0, "virtual_sells": 0, "round_trips": []})
    pos: dict[str, tuple[int, float]] = {}
    for r in sorted(live_rows, key=lambda x: str(x.get("time"))):
        d = _to_kst_date(r.get("time"))
        if d is None or not r.get("qty") or not r.get("price"):
            continue
        key = d.isoformat()
        sym, side, qty, price = _norm(r.get("symbol")), str(r.get("side") or "").upper(), int(r["qty"]), float(r["price"])
        day = by[key]
        day["fills"] += 1
        if side in ("BUY", "buy"):
            q, avg = pos.get(sym, (0, 0.0))
            pos[sym] = (q + qty, (q * avg + qty * price) / (q + qty) if q + qty else price)
            day["buys"] += 1
            day["buy_amount"] += qty * price
        else:
            q, avg = pos.get(sym, (0, 0.0))
            sell_qty = min(qty, q) if q > 0 else 0
            pnl = (price - avg) * sell_qty if sell_qty else 0.0
            pos[sym] = (max(0, q - qty), avg)
            day["sells"] += 1
            day["sell_amount"] += qty * price
            day["realized"] += pnl
            if sell_qty:
                day["wins" if pnl > 0 else "losses"] += 1
                day["round_trips"].append({"symbol": sym, "qty": sell_qty, "entry": round(avg, 2), "exit": price, "pnl": round(pnl, 0), "pnl_pct": round((price / avg - 1) * 100, 3) if avg else None})
    for r in all_live or []:
        d = _to_kst_date(r.get("time"))
        if d is None:
            continue
        by[d.isoformat()]["orders"] += 1
        if str(r.get("status") or "").upper() != "FILLED":
            by[d.isoformat()]["open_or_cancelled"] += 1
    for r in virtual_rows or []:
        d = _to_kst_date(r.get("time"))
        if d is None:
            continue
        by[d.isoformat()]["virtual_buys" if str(r.get("side") or "").lower() == "buy" else "virtual_sells"] += 1
    for v in by.values():
        v["realized"] = round(v["realized"], 0); v["buy_amount"] = round(v["buy_amount"], 0); v["sell_amount"] = round(v["sell_amount"], 0)
    return dict(by)


def trade_stats(by_day: dict[str, dict[str, Any]]) -> dict[str, Any]:
    trips = [t for d in by_day.values() for t in d.get("round_trips", [])]
    wins = [t["pnl"] for t in trips if t["pnl"] > 0]
    losses = [t["pnl"] for t in trips if t["pnl"] <= 0]
    orders = sum(d["orders"] for d in by_day.values())
    fills = sum(d["fills"] for d in by_day.values())
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    win_rate = len(wins) / len(trips) * 100 if trips else None
    expectancy = ((win_rate / 100) * avg_win + (1 - win_rate / 100) * avg_loss) if trips else None
    pnl_pcts = [t["pnl_pct"] for t in trips if t.get("pnl_pct") is not None]
    return {
        "fills": fills, "orders": orders, "fill_rate_pct": round(fills / orders * 100, 1) if orders else None,
        "round_trips": len(trips), "wins": len(wins), "losses": len(losses), "win_rate_pct": round(win_rate, 1) if win_rate is not None else None,
        "avg_win": round(avg_win, 0), "avg_loss": round(avg_loss, 0),
        "payoff_ratio": round(avg_win / abs(avg_loss), 2) if avg_loss else None,
        "expectancy": round(expectancy, 0) if expectancy is not None else None,
        "realized_total": round(sum(t["pnl"] for t in trips), 0),
        "avg_trip_pct": round(sum(pnl_pcts) / len(pnl_pcts), 3) if pnl_pcts else None,
        "days_traded": sum(1 for d in by_day.values() if d["fills"]),
    }


# ── 4. 날짜별 운영 모드 귀속 ─────────────────────────────────────────────────

def mode_by_day(profiles: list[dict], dates: list[str], now: datetime | None = None) -> dict[str, dict[str, Any]]:
    """각 날짜의 장 마감(15:30 KST) 시점에 적용 중이던 전략설정 프로필과 운영 모드."""
    from app.services import strategy_settings as ss
    from app.services import signal_mode as sm
    periods = ss._periods(profiles or [], now or datetime.now(timezone.utc))
    out: dict[str, dict[str, Any]] = {}
    for d in dates:
        ts = datetime.combine(_d(d), CLOSE_TIME, tzinfo=KST)
        per = ss._bucket_for(periods, ts) or {}
        values, row = per.get("values") or {}, per.get("row") or {}
        if per.get("id") == "before" or not per:
            mode, label = "", "프로필 이전(서버 기본값)"
        else:
            mode = values.get("QUANT_SIGNAL_MODE") or ("aggressive" if values.get("QUANT_AGGRESSIVE_MODE") else ("backtest" if row.get("strategy_id") else "custom"))
            label = sm.LABELS.get(mode, mode)
        out[d] = {"profile": per.get("id") or "", "profile_label": per.get("label") or "", "mode": mode, "mode_label": label,
                  "aggressive": bool(values.get("QUANT_AGGRESSIVE_MODE")) if values else None,
                  "take_profit": values.get("QUANT_AGGRESSIVE_TAKE_PROFIT_PCT"), "stop_loss": values.get("QUANT_AGGRESSIVE_STOP_LOSS_PCT"),
                  "max_buys": values.get("QUANT_AGGRESSIVE_MAX_BUYS_PER_CYCLE"), "strategy_id": row.get("strategy_id") or ""}
    return out


# ── 5. 가이드 ──────────────────────────────────────────────────────────────────

def pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 4 or n != len(ys):
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs); syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    return round(sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(sxx * syy), 3)


def _f(id_: str, level: str, title: str, evidence: list[str], actions: list[dict[str, Any]], expected: str, verify: str) -> dict[str, Any]:
    return {"id": id_, "level": level, "title": title, "evidence": evidence, "actions": actions, "expected": expected, "verify": verify}


def _act(text: str, field: str | None = None, view: str = "strategy-settings") -> dict[str, Any]:
    return {"text": text, "field": field, "view": view}


def build_guide(rows: list[dict[str, Any]], by_day: dict[str, dict[str, Any]], tstats: dict[str, Any],
                modes: dict[str, dict[str, Any]], limits: dict[str, Any] | None = None, eff: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """근거 기반 진단·권고 목록. rows 는 enrich() 결과. 비어 있으면 데이터 적재 안내만."""
    eff = eff or {}
    limits = limits or {}
    if not rows:
        return [_f("no-data", "info", "일별 평가액 데이터가 없습니다", ["KIS 화면의 일별 손익 표를 아래 「붙여넣기 적재」 로 넣거나, 15:40 자동 스냅샷을 기다리세요."], [], "", "")]
    s = summary(rows)
    out: list[dict[str, Any]] = []
    cum, mdd = s["cum_return_pct"], s["max_drawdown_pct"]

    # (1) 누적 성과
    lvl = "bad" if cum <= -3 else "warn" if cum < 0 else "ok"
    ev = [f"관측 {s['days']}거래일 ({s['first_date']} ~ {s['last_date']}) · 원금 {s['principal']:,.0f}원 → 평가액 {s['equity']:,.0f}원",
          f"상승 {s['up_days']}일 / 하락 {s['down_days']}일 · 평균 일변화 {s['avg_daily_change_pct']:+.2f}%p · 일 변동성 {s['daily_volatility_pct']:.2f}%p"]
    if s["worst_day"]:
        ev.append(f"최악일 {s['worst_day']['date']} {s['worst_day']['change_pct']:+.2f}%p ({s['worst_day']['pnl']:+,.0f}원)")
    if s.get("loss_share_recent_pct") is not None:
        ev.append(f"최근 3개 관측일 손실이 전체 손실의 {s['loss_share_recent_pct']:.0f}%")
    out.append(_f("overview", lvl, f"누적 수익률 {cum:+.2f}% ({s['cum_pnl']:+,.0f}원) · 최대 낙폭 {mdd:.2f}%", ev, [],
                  "", "이 화면의 일별 표가 매 영업일 15:40 자동 스냅샷으로 이어지는지 확인"))

    # (2) 손실 가속
    changes = [r for r in rows if r["daily_change_pct"] is not None]
    if len(changes) >= 3:
        tail = changes[-3:]
        if all(r["daily_change_pct"] < 0 for r in tail) and abs(tail[-1]["daily_change_pct"]) > abs(tail[0]["daily_change_pct"]):
            out.append(_f("acceleration", "bad", "손실이 최근 관측일에 가속되고 있습니다 — 먼저 매수를 멈추고 원인을 분리하세요",
                          [f"최근 3개 관측일 변화: " + " → ".join(f"{r['snap_date'][5:]} {r['daily_change_pct']:+.2f}%p" for r in tail),
                           "하락 폭이 커지는 패턴은 규칙이 시장 국면과 어긋났거나 매매 빈도가 손실을 증폭하는 신호입니다."],
                          [_act("① 공격 모드라면 「사이클당 최대 매수 종목」 0 → 보유분 청산만 하며 관찰", "QUANT_AGGRESSIVE_MAX_BUYS_PER_CYCLE"),
                           _act("「강제 로테이션 매수」 OFF — 시그널 없는 매수 차단", "QUANT_AGGRESSIVE_FORCE_BUY"),
                           _act("설정별 성과에서 손실이 커진 프로필과 직전 프로필의 차이를 비교", None, "strategy-performance")],
                          "신규 손실 유입 차단. 보유분 청산 뒤 평가액 변동이 멈추면 원인이 '매매 규칙'임이 확인됩니다.",
                          "다음 2~3거래일 일변화가 ±0.5%p 안에 들어오는지"))

    # (3) 운영 모드별 성과
    per_mode: dict[str, list[float]] = defaultdict(list)
    for r in changes:
        m = modes.get(r["snap_date"], {})
        per_mode[m.get("mode_label") or "미상"].append(r["daily_change_pct"])
    labeled = {k: v for k, v in per_mode.items() if k not in ("프로필 이전(서버 기본값)", "미상") and v}
    if labeled:
        avg = {k: sum(v) / len(v) for k, v in labeled.items()}
        worst_mode = min(avg, key=avg.get)
        ev = [f"{k}: {len(v)}일 평균 {avg[k]:+.2f}%p (합계 {sum(v):+.2f}%p)" for k, v in labeled.items()]
        if avg[worst_mode] < -0.3:
            out.append(_f("mode", "bad", f"「{worst_mode}」 구간의 평균 일수익이 가장 나쁩니다 — 모드 전환을 검토하세요", ev,
                          [_act("「운영 모드」 를 ② 백테스트 기반 규칙으로 — pr 에서 합격한 이동평균·돌파 전략(검증된 파라미터)로 매매", "QUANT_SIGNAL_MODE"),
                           _act("합격 전략이 아직 없으면 ③ 사용자 설정 + 보수적 임계값(매수 ≥ 3, 매도 ≤ -1, 추세 필터 ON) 으로 거래 빈도를 줄여 관찰", "QUANT_CUSTOM_BUY_SCORE"),
                           _act("pr LEAN 백테스트에서 ma_cross 20/60 · momentum 20 을 KOSPI200 ETF·대형주로 돌려 「전략 스펙으로 저장」", None, "external:pr")],
                          "분봉 잡음 대신 일봉 규칙으로 바뀌어 거래 수가 1/10 이하로 줄고, 백테스트 MDD 20% 이하가 검증된 규칙만 집행됩니다.",
                          "전환 후 1주: 일 체결 수, 일변화 분포, 설정별 성과 표의 새 프로필 실현손익"))
        else:
            out.append(_f("mode", "info", "운영 모드별 일수익 비교", ev, [], "", ""))
    else:
        out.append(_f("mode", "info", "운영 모드별 비교는 전략설정 프로필이 쌓인 뒤 가능합니다",
                      ["모든 관측일이 전략설정 프로필 저장 이전 구간입니다. 전략설정에서 한 번 저장하면 이후 날짜는 모드·프로필별로 귀속됩니다."],
                      [_act("전략설정에서 현재 값 그대로 「저장·적용」(프로필 메모 권장)", None)], "", ""))

    # (4) 과매매: 체결 수/건수 vs 일변화
    xs, ys = [], []
    for r in changes:
        t = by_day.get(r["snap_date"])
        if t and t["fills"]:
            xs.append(float(t["fills"])); ys.append(r["daily_change_pct"])
    corr = pearson(xs, ys)
    cnt_rows = [r for r in rows if r.get("raw_count") is not None]
    cxs = [float(r["raw_count"]) for r in cnt_rows if r["daily_change_pct"] is not None]
    cys = [r["daily_change_pct"] for r in cnt_rows if r["daily_change_pct"] is not None]
    ccorr = pearson(cxs, cys)
    ev = []
    if corr is not None:
        ev.append(f"일 체결 수 vs 일변화 상관 r = {corr:+.2f} ({len(xs)}일)")
    if ccorr is not None:
        ev.append(f"KIS 화면 건수 열 vs 일변화 상관 r = {ccorr:+.2f} · 건수 {cnt_rows[0]['raw_count']:,} → {cnt_rows[-1]['raw_count']:,}")
    if tstats.get("days_traded"):
        ev.append(f"체결 {tstats['fills']}건 / 주문 {tstats['orders']}건 · 거래일 {tstats['days_traded']}일 · 일평균 {tstats['fills'] / max(1, tstats['days_traded']):.1f}건")
    overtrading = (corr is not None and corr <= -0.3) or (ccorr is not None and ccorr <= -0.3)
    if overtrading:
        out.append(_f("overtrading", "warn", "거래가 많은 날일수록 손실이 큽니다 — 매매 빈도를 줄이는 것이 첫 번째 지렛대입니다", ev,
                      [_act("「분봉 간격」 5m → 15m: MA 교차 잡음 감소로 신호 수 자체가 줄어듭니다", "QUANT_AGGRESSIVE_CANDLE_INTERVAL"),
                       _act("「사이클당 최대 매수 종목」 2 → 1, 「쿨다운」 3 → 15분, 「일 주문 수 한도」 300 → 60", "QUANT_AGGRESSIVE_MAX_BUYS_PER_CYCLE"),
                       _act("「약세 매도 임계 점수」 -1 → -2: 단일 지표로 흔들려 파는 것을 막음", "QUANT_AGGRESSIVE_WEAK_SELL_SCORE")],
                      "체결 수가 줄면 슬리피지·반복 손절 비용이 함께 줄어 같은 규칙으로도 일변화 분산이 작아집니다.",
                      "일 체결 수와 일변화 상관이 0 근처로 돌아오는지, 일 변동성(%p) 감소"))
    elif ev:
        out.append(_f("overtrading", "info", "매매 빈도와 손익", ev, [], "", ""))

    # (5) 승률·손익비·기대값 vs 익절/손절 설정
    tp = float(eff.get("QUANT_AGGRESSIVE_TAKE_PROFIT_PCT") or 0) if eff.get("QUANT_AGGRESSIVE_MODE", True) else float(eff.get("QUANT_BASE_TAKE_PROFIT_PCT") or 0)
    sl = float(eff.get("QUANT_AGGRESSIVE_STOP_LOSS_PCT") or 0) if eff.get("QUANT_AGGRESSIVE_MODE", True) else float(eff.get("QUANT_BASE_STOP_LOSS_PCT") or 0)
    breakeven = (sl / (tp + sl) * 100) if (tp + sl) > 0 else None
    if tstats.get("round_trips"):
        ev = [f"왕복 {tstats['round_trips']}건 · 승률 {tstats['win_rate_pct']}% · 평균 이익 {tstats['avg_win']:+,.0f}원 / 평균 손실 {tstats['avg_loss']:+,.0f}원 · 손익비 {tstats['payoff_ratio']}",
              f"거래당 기대값 {tstats['expectancy']:+,.0f}원 · 실현손익 합계 {tstats['realized_total']:+,.0f}원 · 평균 왕복 수익률 {tstats['avg_trip_pct']:+.2f}%" if tstats.get("avg_trip_pct") is not None else f"거래당 기대값 {tstats['expectancy']:+,.0f}원"]
        if breakeven is not None:
            ev.append(f"현재 익절 +{tp:g}% / 손절 -{sl:g}% 의 손익분기 승률 = {breakeven:.0f}% (손절÷(익절+손절))")
        neg = (tstats["expectancy"] or 0) < 0
        below = breakeven is not None and tstats["win_rate_pct"] is not None and tstats["win_rate_pct"] < breakeven
        if neg or below:
            out.append(_f("expectancy", "bad" if neg else "warn", "거래당 기대값이 음수입니다 — 익절·손절 비대칭과 승률이 맞지 않습니다", ev,
                          [_act(f"익절 폭을 손절보다 크게: 손절 -{max(sl, 1.0):g}% 유지 시 익절 +{max(tp, sl * 2):g}% 이상(손익비 ≥ 2) 또는 손절을 더 좁게", "QUANT_AGGRESSIVE_TAKE_PROFIT_PCT"),
                           _act("승률이 손익분기보다 낮으면 신호 자체를 거르세요: 매수 조건 강화(15m 봉, 약세 임계 -2) 또는 ② 백테스트 규칙", "QUANT_AGGRESSIVE_WEAK_SELL_SCORE"),
                           _act("수수료·슬리피지 전 수치입니다. 시장가 주문이라면 체결가 괴리를 KIS 모의투자결과 화면에서 확인", None, "kis-monitor")],
                          "기대값이 0 이상이 되면 거래 수가 늘어도 손실이 쌓이지 않습니다. 손익비 2 이면 승률 34% 로 손익분기.",
                          "다음 20왕복의 승률·손익비·기대값 (이 화면 자동 갱신)"))
        else:
            out.append(_f("expectancy", "ok", "거래당 기대값이 양수입니다", ev, [], "", "기대값 유지 여부를 주 1회 확인"))
    else:
        out.append(_f("expectancy", "info", "완결된 왕복 거래(매수→매도)가 아직 없어 승률·손익비를 계산할 수 없습니다",
                      ["live_orders 의 FILLED 매도가 생기면 평균단가법으로 자동 계산됩니다. KIS 모의투자는 체결 목록을 비워 주므로 confirm_fills 의 보유수량 추정이 FILLED 로 바꿔야 집계됩니다."],
                      [_act("KIS 모의투자결과에서 열린 주문 백로그·체결 확인 상태 점검", None, "kis-monitor")], "", ""))

    # (6) 일손실 한도
    limit = float(limits.get("daily_loss_limit_pct") or 0)
    if limit > 0:
        breaches = [r for r in changes if r["daily_change_pct"] <= -limit]
        if breaches:
            out.append(_f("daily-limit", "warn", f"일손실 한도 {limit:g}% 를 넘긴 날이 {len(breaches)}일 있습니다 — 비상 정지가 작동했는지 확인하세요",
                          [", ".join(f"{r['snap_date']} {r['daily_change_pct']:+.2f}%p" for r in breaches),
                           "실계좌 일손실 검사는 그날 첫 사이클이 본 게이트웨이 평가액을 기준점으로 잡습니다. 첫 사이클 전에 이미 손실이 났거나 조회가 실패하면 한도가 늦게 걸립니다.",
                           f"관측일 사이 공백(휴장·주말)이 있으면 표의 일변화는 여러 날의 합입니다(예: gap {max(r['gap_days'] or 0 for r in breaches)}일)."],
                          [_act("「일손실 한도」 3% → 2%: 더 일찍 멈추고 사람이 재가동(비상 정지 수동 유지)", "risk_daily_loss_limit_pct"),
                           _act("KIS 모의투자결과의 사이클 이력에서 해당 날짜 risk.halted / live.breached 기록 확인", None, "kis-monitor")],
                          "한도 초과일의 추가 손실을 차단합니다. 한 번의 큰 손실일이 누적 성과를 좌우하는 구조를 끊습니다.",
                          "한도 초과일 0일, 비상 정지 발생 시 그날 이후 주문 0건"))

    # (7) 체결률
    if tstats.get("orders") and tstats.get("fill_rate_pct") is not None and tstats["fill_rate_pct"] < 70:
        out.append(_f("fill-rate", "warn", f"체결률 {tstats['fill_rate_pct']}% — 미체결·취소가 많아 시그널과 실제 포지션이 어긋납니다",
                      [f"주문 {tstats['orders']}건 중 체결 {tstats['fills']}건"],
                      [_act("일봉 모드면 「일봉 모드 실주문 유형」 MARKET 또는 지정가 +1틱(7절 L9)", "STOCK_COIN_TRADE_ORDER_TYPE"),
                       _act("미체결 자동 취소 분(STOCK_COIN_TRADE_CANCEL_OPEN_AFTER_MIN) 20분 — 백로그 차단(7절 L2·L23)", None)],
                      "가상 장부와 실계좌 포지션이 일치해 위험관리(비중·일손실)가 실제 계좌 기준으로 작동합니다.", "체결률 ≥ 85%"))

    # (8) 데이터 공백
    gaps = [r for r in rows if (r.get("gap_days") or 0) > 1]
    weekday_gaps = [r for r in gaps if (r["gap_days"] or 0) - (2 if _d(r["snap_date"]).weekday() == 0 else 0) > 1]
    if weekday_gaps:
        out.append(_f("coverage", "info", "관측일 공백이 있습니다 — 자동 스냅샷으로 매 영업일을 채우세요",
                      [", ".join(f"{r['snap_date']} (앞 행과 {r['gap_days']}일)" for r in weekday_gaps), "공휴일(예: 10/9 한글날)·조회 누락이 섞이면 일변화 해석이 흐려집니다."],
                      [_act("매 영업일 15:40 KST 비트 태스크 quant.equity_snapshot 이 게이트웨이 총평가액을 기록합니다. 오늘 분은 「오늘 스냅샷 기록」 버튼", None, "return-review")], "", ""))

    # (9) 로드맵
    out.append(_f("roadmap", "info", "수익률을 올리기 위한 순서 (가이드)", [
        "1단계 (오늘): 손실 유입 차단 — 매수 한도 0 또는 강제 로테이션 OFF, 일손실 한도 2%. 보유분은 익절·손절로만 정리.",
        "2단계 (이번 주): pr LEAN 백테스트에서 ma_cross 20/60 · momentum 20 을 KOSPI200 ETF(069500)·삼성전자·SK하이닉스로 돌려 합격(MDD ≤ 20%, 거래 ≥ 30, 연수익 > 0) 전략을 「전략 스펙으로 저장」.",
        "3단계: 전략설정 「운영 모드」 ② 백테스트 기반 규칙 + 그 전략 선택, 유니버스 엄격 적용 ON, 일봉 모드 익절 +5% / 손절 -3%, 지정가 +1틱. 프로필 메모에 '②전환'.",
        "4단계 (1주 관찰): 이 화면에서 ②전환 이후 일변화 분포·일 체결 수·기대값을 전환 전과 비교. 설정별 성과 표의 프로필 실현손익이 양수인지.",
        "5단계: 합격 전략을 2개 이상 만들어 번갈아 재검증(최근 기간)하고, 재검증 불합격이면 운영에서 내린다. 실전 전환은 Testbed 2주 연속 기대값 > 0 일 때(7절 S1).",
    ], [_act("전략설정 열기", None, "strategy-settings"), _act("pr LEAN 백테스트 열기", None, "external:pr")], "", ""))
    return out


# ── 6. 게이트웨이 스냅샷 기록 ─────────────────────────────────────────────────

async def upsert_rows(db, rows: list[dict[str, Any]], environment: str = "paper", source: str = "paste", note: str = "") -> dict[str, int]:
    """(environment, snap_date) 기준 upsert. 반환 {inserted, updated}."""
    from sqlalchemy import select
    from app.models.review import KisEquitySnapshot
    inserted = updated = 0
    for r in rows:
        d = _d(r["snap_date"])
        existing = (await db.execute(select(KisEquitySnapshot).where(KisEquitySnapshot.environment == environment, KisEquitySnapshot.snap_date == d))).scalar_one_or_none()
        if existing is None:
            existing = KisEquitySnapshot(environment=environment, snap_date=d)
            db.add(existing)
            inserted += 1
        else:
            updated += 1
        existing.principal = float(r["principal"]); existing.equity = float(r["equity"])
        existing.cum_return_pct = float(r["cum_return_pct"]); existing.cum_pnl = float(r["cum_pnl"])
        existing.raw_count = r.get("raw_count"); existing.source = source; existing.note = (note or r.get("note") or "")[:200]
    await db.commit()
    return {"inserted": inserted, "updated": updated}


async def record_gateway_snapshot(db, principal: float | None = None, environment: str | None = None, today: date | None = None) -> dict[str, Any]:
    """게이트웨이 잔고(총평가액)로 오늘 행을 기록한다. 원금은 인자 → 최근 행 원금 → 기본 1억 순."""
    from sqlalchemy import select
    from app.models.review import KisEquitySnapshot
    from app.services.brokers import stock_coin_trade_gateway as gateway
    env = environment or gateway.environment()
    if not gateway.is_configured():
        return {"ok": False, "error": "게이트웨이(STOCK_COIN_TRADE_*) 가 설정되지 않았습니다."}
    try:
        balance = await gateway.get_balance()
    except gateway.GatewayError as exc:
        return {"ok": False, "error": f"[{exc.code}] {exc}"}
    equity = float(balance.get("totalEvalAmount") or 0) or float(balance.get("cashBalance") or 0)
    if equity <= 0:
        return {"ok": False, "error": "게이트웨이 평가액이 0 입니다."}
    if principal is None:
        last = (await db.execute(select(KisEquitySnapshot).where(KisEquitySnapshot.environment == env).order_by(KisEquitySnapshot.snap_date.desc()))).scalars().first()
        principal = float(last.principal) if last else DEFAULT_PRINCIPAL
    d = today or datetime.now(KST).date()
    row = {"snap_date": d, "principal": principal, "equity": equity, "cum_pnl": equity - principal, "cum_return_pct": round((equity / principal - 1) * 100, 4), "raw_count": None}
    result = await upsert_rows(db, [row], environment=env, source="gateway", note=f"게이트웨이 잔고 {datetime.now(KST).strftime('%H:%M')} KST")
    return {"ok": True, "environment": env, "row": {**row, "snap_date": d.isoformat()}, **result}
