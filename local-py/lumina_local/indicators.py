"""기술적 지표 + 규칙 기반 매매 시그널 — 네트워크·DB 없는 순수 계산.

app/services/stock.py 의 _calc_rsi / _calc_sma / _calc_bollinger / _generate_signal /
get_quant_indicators 를 그대로 옮겼다. 점수 체계(대략 -8~+8)와 action 문구를 바꾸지 않았으므로
전략 스펙의 임계값·백테스트 결과가 app 쪽과 동일하게 재현된다.
"""
from __future__ import annotations


def calc_rsi(closes: list[float], period: int = 14) -> list[float | None]:
    rsi: list[float | None] = [None] * len(closes)
    if len(closes) < period + 1:
        return rsi
    gains, losses = [], []
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    for i in range(period, len(closes)):
        if i > period:
            diff = closes[i] - closes[i - 1]
            avg_gain = (avg_gain * (period - 1) + max(diff, 0)) / period
            avg_loss = (avg_loss * (period - 1) + max(-diff, 0)) / period
        rs = avg_gain / avg_loss if avg_loss > 0 else 100
        rsi[i] = round(100 - (100 / (1 + rs)), 2)
    return rsi


def calc_sma(closes: list[float], period: int) -> list[float | None]:
    result: list[float | None] = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        result[i] = round(sum(closes[i - period + 1:i + 1]) / period, 2)
    return result


def calc_bollinger(closes: list[float], period: int = 20, std_mult: float = 2.0):
    upper: list[float | None] = [None] * len(closes)
    mid: list[float | None] = [None] * len(closes)
    lower: list[float | None] = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1:i + 1]
        m = sum(window) / period
        std = (sum((x - m) ** 2 for x in window) / period) ** 0.5
        mid[i] = round(m, 2)
        upper[i] = round(m + std_mult * std, 2)
        lower[i] = round(m - std_mult * std, 2)
    return upper, mid, lower


def generate_signal(closes, rsi, ma5, ma20, ma60, bb_upper, bb_lower) -> dict:
    """규칙 기반 매매 시그널. score + = 매수, - = 매도."""
    n = len(closes) - 1
    signals: list[str] = []
    score = 0

    if rsi[n] is not None:
        if rsi[n] < 30:
            signals.append("RSI 과매도 (매수 신호)")
            score += 2
        elif rsi[n] > 70:
            signals.append("RSI 과매수 (매도 신호)")
            score -= 2
        else:
            signals.append(f"RSI 중립 ({rsi[n]:.1f})")

    if ma5[n] and ma20[n] and ma5[n - 1] and ma20[n - 1]:
        if ma5[n] > ma20[n] and ma5[n - 1] <= ma20[n - 1]:
            signals.append("골든크로스 (강력 매수)")
            score += 3
        elif ma5[n] < ma20[n] and ma5[n - 1] >= ma20[n - 1]:
            signals.append("데드크로스 (강력 매도)")
            score -= 3
        elif ma5[n] > ma20[n]:
            signals.append("단기 이평 > 중기 이평 (매수 우위)")
            score += 1
        else:
            signals.append("단기 이평 < 중기 이평 (매도 우위)")
            score -= 1

    if bb_upper[n] and bb_lower[n]:
        if closes[n] < bb_lower[n]:
            signals.append("볼린저 하단 이탈 (반등 가능)")
            score += 1
        elif closes[n] > bb_upper[n]:
            signals.append("볼린저 상단 돌파 (조정 가능)")
            score -= 1

    if score >= 3:
        action, color = "강력 매수", "green"
    elif score >= 1:
        action, color = "매수", "lightgreen"
    elif score <= -3:
        action, color = "강력 매도", "red"
    elif score <= -1:
        action, color = "매도", "salmon"
    else:
        action, color = "관망", "gray"

    return {"action": action, "color": color, "score": score, "reasons": signals}


def quant_indicators(symbol: str, candles: list[dict], source: str = "") -> dict:
    """캔들(OHLCV) → 지표 + 시그널. 캔들이 20개 미만이면 error 를 담아 돌려준다."""
    if len(candles) < 20:
        return {"symbol": symbol, "error": "데이터 부족", "signal": {},
                "current_price": None, "bars": len(candles), "source": source}

    closes = [float(c["close"]) for c in candles if c.get("close") is not None]
    times = [c.get("time") for c in candles if c.get("close") is not None]

    rsi = calc_rsi(closes)
    ma5 = calc_sma(closes, 5)
    ma20 = calc_sma(closes, 20)
    ma60 = calc_sma(closes, 60)
    bb_upper, bb_mid, bb_lower = calc_bollinger(closes)
    signal = generate_signal(closes, rsi, ma5, ma20, ma60, bb_upper, bb_lower)

    return {
        "symbol": symbol,
        "times": times[-100:],
        "closes": closes[-100:],
        "rsi": rsi[-100:],
        "ma5": ma5[-100:],
        "ma20": ma20[-100:],
        "ma60": ma60[-100:],
        "bb_upper": bb_upper[-100:],
        "bb_mid": bb_mid[-100:],
        "bb_lower": bb_lower[-100:],
        "signal": signal,
        "current_price": closes[-1],
        "current_rsi": rsi[-1],
        "interval": "1d",
        "bars": len(closes),
        "as_of": times[-1] if times else None,
        "source": source,
    }


def signal_basis(indicators: dict) -> dict:
    """판단 근거가 어느 시장 데이터에서 나왔는지(화면/로그 표시용)."""
    return {
        "source": indicators.get("source"),
        "interval": indicators.get("interval"),
        "bars": indicators.get("bars"),
        "as_of": indicators.get("as_of"),
        "price_source": indicators.get("price_source"),
    }
