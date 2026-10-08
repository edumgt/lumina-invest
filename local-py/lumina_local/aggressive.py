"""공격 모드 — 5분봉 단기 시그널로 매 사이클 매수·매도.

app/services/aggressive_mode.py 를 그대로 옮겼다(점수·우선순위·한도 규칙 동일).

  1. 지표를 분봉으로 계산한다. 점수 = 추세(MA5 vs MA20 ±1, 교차 ±2) + 30분 모멘텀(±1, 1% 이상 ±2)
     + RSI 보조(±1) + 거래량 급증(+1). score ≥ 1 매수(≥3 강력), ≤ -1 매도(≤ -3 강력).
  2. 매 사이클 모멘텀 상위 종목을 최대 N개 매수한다. 매수 시그널이 없으면 1위를 매수(로테이션).
  3. 보유분은 익절(+TP%)·손절(-SL%) 이면 전량, 약세 시그널이면 sell_ratio 만큼 매도한다.
  4. 쿨다운·일 주문 수 한도를 공격 모드 값으로 덮어쓴다. 비중·일손실·비상 정지는 그대로 둔다.
"""
from __future__ import annotations

import logging

from .config import Settings
from .indicators import calc_rsi, calc_sma

logger = logging.getLogger(__name__)

MOMENTUM_BARS = 6          # 5분봉 6개 = 30분 모멘텀
VOLUME_WINDOW = 20


def score_intraday(closes: list[float], volumes: list[float | None]) -> dict:
    """분봉 시계열 → {action, score, reasons, momentum_pct, rsi}."""
    n = len(closes)
    reasons: list[str] = []
    score = 0
    rsi = calc_rsi(closes) if n >= 15 else [None] * n
    ma5 = calc_sma(closes, 5) if n >= 5 else [None] * n
    ma20 = calc_sma(closes, 20) if n >= 20 else [None] * n
    last_rsi = rsi[-1] if rsi else None
    # RSI 는 보조 신호(±1): 추세 구간에서 RSI 과열이 매수를 막지 않게 한다.
    if last_rsi is not None:
        if last_rsi < 35:
            reasons.append(f"분봉 RSI 과매도 {last_rsi:.0f}"); score += 1
        elif last_rsi > 65:
            reasons.append(f"분봉 RSI 과매수 {last_rsi:.0f}"); score -= 1
    if ma5[-1] is not None and ma20[-1] is not None:
        if ma5[-1] > ma20[-1]:
            reasons.append("분봉 MA5 > MA20"); score += 1
        else:
            reasons.append("분봉 MA5 < MA20"); score -= 1
        if n >= 2 and ma5[-2] is not None and ma20[-2] is not None:
            if ma5[-2] <= ma20[-2] and ma5[-1] > ma20[-1]:
                reasons.append("분봉 골든크로스"); score += 2
            elif ma5[-2] >= ma20[-2] and ma5[-1] < ma20[-1]:
                reasons.append("분봉 데드크로스"); score -= 2
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


def intraday_indicators(symbol: str, market, settings: Settings) -> dict:
    """quant_indicators 와 호환되는 축약 지표. 데이터가 없으면 '판단 불가'를 담는다."""
    data = market.intraday_candles(symbol)
    candles = [c for c in (data.get("candles") or []) if c.get("close")]
    source = data.get("source") or ""
    if len(candles) < 2:
        return {"symbol": symbol,
                "signal": {"action": "판단 불가", "score": 0, "error": "분봉 없음",
                           "reasons": ["시장 데이터(분봉)를 받지 못해 판단하지 않았습니다"], "momentum_pct": 0.0},
                "current_price": None, "intraday": True, "source": source,
                "bars": len(candles), "as_of": None}
    closes = [float(c["close"]) for c in candles]
    volumes = [c.get("volume") for c in candles]
    sig = score_intraday(closes, volumes)
    return {"symbol": symbol, "signal": sig, "current_price": closes[-1], "intraday": True,
            "interval": settings.aggressive_candle_interval, "bars": len(closes), "source": source,
            "as_of": candles[-1].get("time"), "price_source": "last_close"}


def plan(settings: Settings, indicator_map: dict[str, dict], target_symbols: list[str],
         holdings: dict[str, tuple[int, float]], price_map: dict[str, float]) -> dict:
    """이번 사이클의 매수/매도 결정.

    반환 {"buy": [symbol...], "sell": {symbol: {"ratio": float|None, "reason": str}}, "ranked": [...], "notes": [...]}
    sell.ratio None 은 설정의 sell_ratio 를 쓰라는 뜻(약세 시그널), 1.0 은 전량(익절·손절).
    """
    tp = float(settings.aggressive_take_profit_pct)
    sl = float(settings.aggressive_stop_loss_pct)
    max_buys = max(0, int(settings.aggressive_max_buys_per_cycle))
    max_sells = max(0, int(settings.aggressive_max_sells_per_cycle))
    notes: list[str] = []

    def sig(sym: str) -> dict:
        return (indicator_map.get(sym) or {}).get("signal") or {}

    # ── 매도: 보유분 전부 점검(대상 종목 밖이어도) ──
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
            exits.append((float(-s.get("score", 0)), sym,
                          {"ratio": None, "reason": f"분봉 약세 시그널 (score {s.get('score')}): "
                                                    + ", ".join(s.get("reasons", []))}))
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
    if not buys and max_buys > 0 and settings.aggressive_force_buy:
        cand = next((s for s in ranked if s not in sells), None)
        if cand:
            buys = [cand]
            notes.append(f"매수 시그널 없음 → 모멘텀 1위 {cand} 로테이션 매수")
    return {"buy": buys, "sell": sells, "ranked": ranked, "notes": notes}
