"""공격 모드 — 5분봉 단기 시그널로 매 사이클 매수·매도 (QUANT_AGGRESSIVE_MODE).

왜 필요한가: 기본 사이클은 일봉 지표(6시간 캐시)를 쓰므로 하루 종일 같은 시그널이 나오고, 보유가 없는 종목의
매도 시그널은 생략되며 쿨다운 30분이 걸려 5분 주기여도 거래가 거의 없다. 공격 모드는

  1. 지표를 5분봉으로 계산한다. 점수 = 추세(MA5 vs MA20 ±1, 교차 ±2) + 30분 모멘텀(±1, 1% 이상 ±2) + RSI 보조(±1) + 거래량 급증(+1).
     score ≥ 1 매수(≥3 강력), ≤ -1 매도(≤ -3 강력).
  2. 매 사이클 모멘텀 상위 종목을 최대 N개 매수한다. 매수 시그널이 하나도 없으면 1위를 매수한다(로테이션).
  3. 가상 QUANT 장부 보유분은 익절(+TP%)·손절(-SL%) 이면 전량, 약세 시그널이면 sell_ratio 만큼 매도한다.
  4. 쿨다운·일 주문 수 한도를 env 값으로 덮어쓴다. 종목 비중·일손실 한도·비상 정지는 그대로 둔다.
  5. 게이트웨이 실주문은 시장가(MARKET)로 보내 체결을 우선한다.

auto_trade._run_quant_cycle 이 is_enabled() 일 때만 이 모듈을 호출하므로, false 면 기존 동작과 완전히 같다.
"""
from __future__ import annotations

import logging
from dataclasses import replace

from app.config import settings
from app.services.risk_guard import RiskLimits
from app.services.stock import get_candles, _calc_rsi, _calc_sma

logger = logging.getLogger(__name__)

MOMENTUM_BARS = 6          # 5분봉 6개 = 30분 모멘텀
VOLUME_WINDOW = 20


def is_enabled() -> bool:
    return bool(settings.QUANT_AGGRESSIVE_MODE)


def order_type() -> str | None:
    ot = (settings.QUANT_AGGRESSIVE_ORDER_TYPE or "").strip().upper()
    return ot if ot in ("MARKET", "LIMIT") else None


def apply_limits(limits: RiskLimits) -> RiskLimits:
    """쿨다운·일 주문 수만 공격 모드 값으로. 비중·일손실·kill switch 는 유지."""
    return replace(limits,
                   cooldown_min=max(0, int(settings.QUANT_AGGRESSIVE_COOLDOWN_MIN)),
                   max_orders_per_day=max(0, int(settings.QUANT_AGGRESSIVE_MAX_ORDERS_PER_DAY)))


def score_intraday(closes: list[float], volumes: list[float | None]) -> dict:
    """5분봉 시계열 → {action, score, reasons, momentum_pct, rsi}. get_quant_indicators 의 signal 과 같은 모양."""
    n = len(closes)
    reasons: list[str] = []
    score = 0
    rsi = _calc_rsi(closes) if n >= 15 else [None] * n
    ma5 = _calc_sma(closes, 5) if n >= 5 else [None] * n
    ma20 = _calc_sma(closes, 20) if n >= 20 else [None] * n
    last_rsi = rsi[-1] if rsi else None
    # RSI 는 보조 신호(±1): 공격 모드는 추세·모멘텀을 우선한다. 추세 구간에서 RSI 과열이 매수를 막지 않게.
    if last_rsi is not None:
        if last_rsi < 35:
            reasons.append(f"5분 RSI 과매도 {last_rsi:.0f}"); score += 1
        elif last_rsi > 65:
            reasons.append(f"5분 RSI 과매수 {last_rsi:.0f}"); score -= 1
    if ma5[-1] is not None and ma20[-1] is not None:
        if ma5[-1] > ma20[-1]:
            reasons.append("5분 MA5 > MA20"); score += 1
        else:
            reasons.append("5분 MA5 < MA20"); score -= 1
        if n >= 2 and ma5[-2] is not None and ma20[-2] is not None:
            if ma5[-2] <= ma20[-2] and ma5[-1] > ma20[-1]:
                reasons.append("5분 골든크로스"); score += 2
            elif ma5[-2] >= ma20[-2] and ma5[-1] < ma20[-1]:
                reasons.append("5분 데드크로스"); score -= 2
    momentum_pct = 0.0
    if n > MOMENTUM_BARS and closes[-1 - MOMENTUM_BARS]:
        momentum_pct = (closes[-1] / closes[-1 - MOMENTUM_BARS] - 1) * 100
        if momentum_pct >= 0.3:
            reasons.append(f"30분 모멘텀 {momentum_pct:+.2f}%"); score += 2 if momentum_pct >= 1.0 else 1
        elif momentum_pct <= -0.3:
            reasons.append(f"30분 모멘텀 {momentum_pct:+.2f}%"); score -= 2 if momentum_pct <= -1.0 else 1
    vols = [float(v) for v in volumes[-VOLUME_WINDOW - 1:-1] if v]
    if vols and volumes and volumes[-1]:
        avg = sum(vols) / len(vols)
        if avg > 0 and float(volumes[-1]) / avg >= 1.5 and momentum_pct > 0:
            reasons.append("거래량 급증(상승)"); score += 1
    if score >= 3:
        action = "강력 매수"
    elif score >= 1:
        action = "매수"
    elif score <= -3:
        action = "강력 매도"
    elif score <= -1:
        action = "매도"
    else:
        action = "관망"
    return {"action": action, "score": score, "reasons": reasons or ["중립"],
            "momentum_pct": round(momentum_pct, 3), "rsi": last_rsi}


def _interval_minutes() -> int:
    raw = str(settings.QUANT_AGGRESSIVE_CANDLE_INTERVAL or "5m").lower().rstrip("m")
    try:
        return max(1, int(raw))
    except ValueError:
        return 5


async def _intraday_candles(symbol: str) -> tuple[list[dict], float | None, str]:
    """(캔들, 현재가, 소스). MARKET_DATA_SOURCE=kis 면 KIS 실시간 분봉, 실패 시 Yahoo 폴백."""
    from app.services import kis_market_data as kmd
    if kmd.is_enabled() and kmd.is_krx(symbol):
        try:
            data = await kmd.get_intraday_candles(symbol, _interval_minutes())
            candles = [c for c in (data.get("candles") or []) if c.get("close")]
            if candles:
                return candles, data.get("price"), "kis"
            logger.warning("KIS 분봉 빈 응답 %s", symbol)
        except Exception as exc:
            logger.warning("KIS 분봉 조회 실패 %s: %s%s", symbol, exc, " — Yahoo 폴백" if settings.MARKET_DATA_FALLBACK_YAHOO else "")
        if not settings.MARKET_DATA_FALLBACK_YAHOO:
            return [], None, "kis"
    data = await get_candles(symbol, period=settings.QUANT_AGGRESSIVE_CANDLE_RANGE,
                             interval=settings.QUANT_AGGRESSIVE_CANDLE_INTERVAL,
                             max_age_hours=max(1, int(settings.QUANT_AGGRESSIVE_CACHE_MIN)) / 60)
    candles = [c for c in (data.get("candles") or []) if c.get("close")]
    return candles, None, "yahoo"


async def get_intraday_indicators(symbol: str) -> dict:
    """get_quant_indicators 와 호환되는 축약 지표. current_price 는 KIS 현재가(있으면) 또는 마지막 봉 종가."""
    candles, live_price, source = await _intraday_candles(symbol)
    if len(candles) < 2:
        return {"symbol": symbol, "signal": {"action": "관망", "score": 0, "reasons": ["분봉 없음"], "momentum_pct": 0.0},
                "current_price": None, "intraday": True, "source": source}
    closes = [float(c["close"]) for c in candles]
    volumes = [c.get("volume") for c in candles]
    sig = score_intraday(closes, volumes)
    return {"symbol": symbol, "signal": sig, "current_price": float(live_price) if live_price else closes[-1], "intraday": True,
            "interval": settings.QUANT_AGGRESSIVE_CANDLE_INTERVAL, "bars": len(closes), "source": source}


def plan(indicator_map: dict[str, dict], target_symbols: list[str], holdings: dict[str, tuple[int, float]],
         price_map: dict[str, float]) -> dict:
    """이번 사이클의 매수/매도 결정.

    반환 {"buy": [symbol...], "sell": {symbol: {"ratio": float|None, "reason": str}}, "ranked": [...], "notes": [...]}
    sell.ratio None 은 행의 sell_ratio 를 쓰라는 뜻(약세 시그널), 1.0 은 전량(익절·손절).
    """
    tp = float(settings.QUANT_AGGRESSIVE_TAKE_PROFIT_PCT)
    sl = float(settings.QUANT_AGGRESSIVE_STOP_LOSS_PCT)
    max_buys = max(0, int(settings.QUANT_AGGRESSIVE_MAX_BUYS_PER_CYCLE))
    max_sells = max(0, int(settings.QUANT_AGGRESSIVE_MAX_SELLS_PER_CYCLE))
    notes: list[str] = []

    def sig(sym: str) -> dict:
        return (indicator_map.get(sym) or {}).get("signal") or {}

    # ── 매도: 보유분 전부 점검 (대상 종목 밖이어도) ──
    sells: dict[str, dict] = {}
    exits: list[tuple[float, str, dict]] = []   # (우선순위, symbol, info)
    for sym, (qty, avg) in holdings.items():
        if qty <= 0:
            continue
        price = price_map.get(sym)
        if not price or not avg:
            continue
        pnl = (price / avg - 1) * 100
        s = sig(sym)
        if pnl >= tp:
            exits.append((1000 + pnl, sym, {"ratio": 1.0, "reason": f"익절 {pnl:+.2f}% ≥ +{tp}%"}))
        elif pnl <= -sl:
            exits.append((900 - pnl, sym, {"ratio": 1.0, "reason": f"손절 {pnl:+.2f}% ≤ -{sl}%"}))
        elif int(s.get("score", 0)) <= -1:
            exits.append((float(-s.get("score", 0)), sym, {"ratio": None, "reason": f"5분 약세 시그널 (score {s.get('score')}): " + ", ".join(s.get("reasons", []))}))
    exits.sort(key=lambda t: t[0], reverse=True)
    for _, sym, info in exits[:max_sells]:
        sells[sym] = info
    if len(exits) > max_sells:
        notes.append(f"매도 후보 {len(exits)}개 중 {max_sells}개만 처리(사이클 한도)")

    # ── 매수: 대상 종목을 (score, momentum) 으로 정렬 ──
    ranked = sorted(
        [s for s in target_symbols if price_map.get(s)],
        key=lambda s: (int(sig(s).get("score", 0)), float(sig(s).get("momentum_pct", 0.0))), reverse=True,
    )
    buys = [s for s in ranked if int(sig(s).get("score", 0)) >= 1 and s not in sells][:max_buys]
    if not buys and max_buys > 0 and settings.QUANT_AGGRESSIVE_FORCE_BUY:
        cand = next((s for s in ranked if s not in sells), None)
        if cand:
            buys = [cand]
            notes.append(f"매수 시그널 없음 → 모멘텀 1위 {cand} 로테이션 매수")
    return {"buy": buys, "sell": sells, "ranked": ranked, "notes": notes}
