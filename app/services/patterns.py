"""차트 패턴 탐지 + 멀티타임프레임 종합 신호.

1) 캔들 패턴   : 도지·해머·역해머·유성형·장악형(상승/하락)·샛별/석별형·적삼병/흑삼병·마루보즈
2) 지지·저항선 : 피벗 고점/저점을 군집화해 가격대별 터치 횟수로 강도를 매기고, 현재가 기준 가장 가까운 지지/저항 산출
3) 돌파 신호   : 저항 상향 돌파·지지 하향 이탈(거래량 확인 포함)·52주 신고가/신저가·골든/데드크로스·볼린저 밴드 돌파
4) 멀티타임프레임: 분봉(60m)·일봉·주봉 각각의 지표 점수를 가중 합산해 매수/매도/관망 + 신뢰도(0~100) 산출

모든 계산은 과거 봉만 사용한다(룩어헤드 없음). 시그널은 교육용이며 투자 권유가 아니다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.services import ta_utils as ta
from app.services.quant_pipeline import preprocess

# ── 1. 캔들 패턴 ──────────────────────────────────────────────────────────

PATTERN_META = {
    "doji":               ("도지", "neutral", "시가≈종가. 매수·매도 힘의 균형, 추세 전환 가능성"),
    "hammer":             ("해머", "bullish", "긴 아래꼬리 + 작은 몸통. 하락 후 반등 신호"),
    "inverted_hammer":    ("역해머", "bullish", "긴 위꼬리 + 작은 몸통(하락 추세 중). 반등 시도"),
    "shooting_star":      ("유성형", "bearish", "긴 위꼬리 + 작은 몸통(상승 추세 중). 상승 피로"),
    "hanging_man":        ("교수형", "bearish", "상승 추세 끝의 해머 모양. 하락 전환 경고"),
    "bullish_engulfing":  ("상승 장악형", "bullish", "음봉을 완전히 덮는 양봉. 강한 매수 전환"),
    "bearish_engulfing":  ("하락 장악형", "bearish", "양봉을 완전히 덮는 음봉. 강한 매도 전환"),
    "morning_star":       ("샛별형", "bullish", "긴 음봉 → 작은 봉 → 긴 양봉. 바닥 반전"),
    "evening_star":       ("석별형", "bearish", "긴 양봉 → 작은 봉 → 긴 음봉. 천장 반전"),
    "three_white_soldiers": ("적삼병", "bullish", "연속 3개 양봉 상승. 강한 상승 추세"),
    "three_black_crows":  ("흑삼병", "bearish", "연속 3개 음봉 하락. 강한 하락 추세"),
    "bullish_marubozu":   ("상승 마루보즈", "bullish", "꼬리 없는 장대 양봉. 매수 우위 지속"),
    "bearish_marubozu":   ("하락 마루보즈", "bearish", "꼬리 없는 장대 음봉. 매도 우위 지속"),
}


def _bar(df: pd.DataFrame, i: int) -> dict:
    r = df.iloc[i]
    o, h, l, c = float(r.open), float(r.high), float(r.low), float(r.close)
    rng = max(h - l, 1e-9)
    body = abs(c - o)
    return {"o": o, "h": h, "l": l, "c": c, "range": rng, "body": body, "body_pct": body / rng,
            "upper": h - max(o, c), "lower": min(o, c) - l, "bull": c > o, "bear": c < o}


def detect_candle_patterns(df: pd.DataFrame, lookback: int = 5) -> list[dict]:
    """최근 lookback개 봉에서 발견된 캔들 패턴 목록 (최신순)."""
    if len(df) < 25:
        return []
    close = df["close"].astype(float)
    avg_body = (df["close"].astype(float) - df["open"].astype(float)).abs().rolling(20).mean()
    trend = close.pct_change(5)  # 직전 5일 추세로 해머/교수형 구분
    found: list[dict] = []
    for i in range(len(df) - 1, max(len(df) - 1 - lookback, 2), -1):
        b, p1, p2 = _bar(df, i), _bar(df, i - 1), _bar(df, i - 2)
        ab = float(avg_body.iloc[i]) if pd.notna(avg_body.iloc[i]) else b["body"]
        up = float(trend.iloc[i - 1]) if pd.notna(trend.iloc[i - 1]) else 0.0
        hits: list[str] = []
        if b["body_pct"] <= 0.1:
            hits.append("doji")
        elif b["body_pct"] >= 0.9 and b["body"] >= ab:
            hits.append("bullish_marubozu" if b["bull"] else "bearish_marubozu")
        small_body = b["body_pct"] <= 0.35
        if small_body and b["lower"] >= 2 * b["body"] and b["upper"] <= b["body"]:
            hits.append("hanging_man" if up > 0.03 else "hammer")
        if small_body and b["upper"] >= 2 * b["body"] and b["lower"] <= b["body"]:
            hits.append("shooting_star" if up > 0.03 else "inverted_hammer")
        if b["bull"] and p1["bear"] and b["c"] >= p1["o"] and b["o"] <= p1["c"] and b["body"] > p1["body"]:
            hits.append("bullish_engulfing")
        if b["bear"] and p1["bull"] and b["o"] >= p1["c"] and b["c"] <= p1["o"] and b["body"] > p1["body"]:
            hits.append("bearish_engulfing")
        if p2["bear"] and p2["body"] > ab and p1["body"] < ab * 0.5 and b["bull"] and b["body"] > ab and b["c"] > (p2["o"] + p2["c"]) / 2:
            hits.append("morning_star")
        if p2["bull"] and p2["body"] > ab and p1["body"] < ab * 0.5 and b["bear"] and b["body"] > ab and b["c"] < (p2["o"] + p2["c"]) / 2:
            hits.append("evening_star")
        if b["bull"] and p1["bull"] and p2["bull"] and b["c"] > p1["c"] > p2["c"] and min(b["body_pct"], p1["body_pct"], p2["body_pct"]) > 0.5:
            hits.append("three_white_soldiers")
        if b["bear"] and p1["bear"] and p2["bear"] and b["c"] < p1["c"] < p2["c"] and min(b["body_pct"], p1["body_pct"], p2["body_pct"]) > 0.5:
            hits.append("three_black_crows")
        for key in hits:
            name, direction, desc = PATTERN_META[key]
            found.append({"date": df.index[i].strftime("%Y-%m-%d"), "bars_ago": len(df) - 1 - i, "key": key,
                          "name": name, "direction": direction, "description": desc})
    return found


# ── 2. 지지·저항 ──────────────────────────────────────────────────────────

def support_resistance(df: pd.DataFrame, window: int = 5, lookback: int = 120, tolerance_pct: float = 1.0,
                       max_levels: int = 6) -> dict:
    """피벗 고점/저점 군집화로 지지·저항 레벨을 찾는다."""
    d = df.tail(lookback)
    if len(d) < window * 2 + 5:
        return {"levels": [], "nearest_support": None, "nearest_resistance": None}
    high, low, close = d["high"].astype(float), d["low"].astype(float), d["close"].astype(float)
    last = float(close.iloc[-1])
    pivots: list[tuple[float, str]] = []
    for i in range(window, len(d) - window):
        if high.iloc[i] == high.iloc[i - window:i + window + 1].max():
            pivots.append((float(high.iloc[i]), "high"))
        if low.iloc[i] == low.iloc[i - window:i + window + 1].min():
            pivots.append((float(low.iloc[i]), "low"))
    # 가격 근접(tolerance) 군집화
    clusters: list[dict] = []
    for price, kind in sorted(pivots):
        for cl in clusters:
            if abs(price - cl["price"]) / cl["price"] * 100 <= tolerance_pct:
                cl["prices"].append(price); cl["price"] = float(np.mean(cl["prices"])); cl["touches"] += 1
                cl["kinds"].add(kind)
                break
        else:
            clusters.append({"price": price, "prices": [price], "touches": 1, "kinds": {kind}})
    levels = []
    for cl in clusters:
        typ = "support" if cl["price"] < last else "resistance"
        levels.append({"price": round(cl["price"], 2), "type": typ, "touches": cl["touches"],
                       "distance_pct": round((cl["price"] / last - 1) * 100, 2),
                       "strength": "강" if cl["touches"] >= 3 else "중" if cl["touches"] == 2 else "약"})
    levels.sort(key=lambda x: (-x["touches"], abs(x["distance_pct"])))
    levels = levels[:max_levels]
    sup = [l for l in levels if l["type"] == "support"]
    res = [l for l in levels if l["type"] == "resistance"]
    nearest_s = max(sup, key=lambda x: x["price"]) if sup else None
    nearest_r = min(res, key=lambda x: x["price"]) if res else None
    return {"levels": sorted(levels, key=lambda x: -x["price"]), "nearest_support": nearest_s,
            "nearest_resistance": nearest_r, "last_price": round(last, 2), "lookback_bars": len(d)}


# ── 3. 돌파 신호 ──────────────────────────────────────────────────────────

def detect_breakouts(df: pd.DataFrame, sr: dict | None = None) -> list[dict]:
    """최근 봉 기준 돌파/이탈·크로스 이벤트."""
    if len(df) < 65:
        return []
    close = df["close"].astype(float); vol = df["volume"].astype(float)
    last, prev = float(close.iloc[-1]), float(close.iloc[-2])
    vol_ratio = float(vol.iloc[-1] / max(vol.tail(20).mean(), 1e-9))
    events: list[dict] = []
    sr = sr or support_resistance(df)
    # 직전 20일 고점/저점(오늘 제외) 돌파
    hi20, lo20 = float(close.iloc[-21:-1].max()), float(close.iloc[-21:-1].min())
    if last > hi20:
        events.append({"key": "breakout_20d", "name": "20일 고점 상향 돌파", "direction": "bullish",
                       "detail": f"종가 {last:,.0f} > 직전 20일 최고 {hi20:,.0f}" + (" · 거래량 확인" if vol_ratio >= 1.5 else ""), "confirmed": vol_ratio >= 1.5})
    if last < lo20:
        events.append({"key": "breakdown_20d", "name": "20일 저점 하향 이탈", "direction": "bearish",
                       "detail": f"종가 {last:,.0f} < 직전 20일 최저 {lo20:,.0f}" + (" · 거래량 확인" if vol_ratio >= 1.5 else ""), "confirmed": vol_ratio >= 1.5})
    # 지지/저항 레벨 돌파
    for lvl in sr.get("levels", []):
        p = lvl["price"]
        if prev <= p < last:
            events.append({"key": "resistance_break", "name": f"저항선 {p:,.0f} 상향 돌파", "direction": "bullish", "detail": f"터치 {lvl['touches']}회 저항 통과", "confirmed": vol_ratio >= 1.5})
        if prev >= p > last:
            events.append({"key": "support_break", "name": f"지지선 {p:,.0f} 하향 이탈", "direction": "bearish", "detail": f"터치 {lvl['touches']}회 지지 붕괴", "confirmed": vol_ratio >= 1.5})
    # 52주 신고가/신저가
    yr = close.tail(252)
    if last >= float(yr.max()):
        events.append({"key": "high_52w", "name": "52주 신고가", "direction": "bullish", "detail": f"{last:,.0f}", "confirmed": True})
    if last <= float(yr.min()):
        events.append({"key": "low_52w", "name": "52주 신저가", "direction": "bearish", "detail": f"{last:,.0f}", "confirmed": True})
    # 골든/데드크로스 (최근 5봉 내)
    ma20, ma60 = ta.sma(close, 20), ta.sma(close, 60)
    cross_up = (ma20 > ma60) & (ma20.shift(1) <= ma60.shift(1))
    cross_dn = (ma20 < ma60) & (ma20.shift(1) >= ma60.shift(1))
    if cross_up.tail(5).any():
        events.append({"key": "golden_cross", "name": "골든크로스 (MA20 > MA60)", "direction": "bullish", "detail": f"{int(cross_up.tail(5).values[::-1].argmax())}봉 전 발생", "confirmed": True})
    if cross_dn.tail(5).any():
        events.append({"key": "dead_cross", "name": "데드크로스 (MA20 < MA60)", "direction": "bearish", "detail": f"{int(cross_dn.tail(5).values[::-1].argmax())}봉 전 발생", "confirmed": True})
    # 볼린저
    bb_u, _m, bb_l = ta.bollinger(close, 20, 2.0)
    if last > float(bb_u.iloc[-1]):
        events.append({"key": "bb_upper_break", "name": "볼린저 상단 돌파", "direction": "bearish", "detail": "과매수·조정 가능(추세장에선 강세 지속)", "confirmed": False})
    if last < float(bb_l.iloc[-1]):
        events.append({"key": "bb_lower_break", "name": "볼린저 하단 이탈", "direction": "bullish", "detail": "과매도·반등 가능", "confirmed": False})
    return events


def pattern_summary(candles: list[dict]) -> dict:
    df = preprocess(candles)
    if len(df) < 65:
        return {"error": f"데이터 부족: {len(df)}봉 (최소 65 필요)"}
    patterns = detect_candle_patterns(df)
    sr = support_resistance(df)
    breakouts = detect_breakouts(df, sr)
    score = 0.0
    for p in patterns:
        w = 1.0 if p["bars_ago"] == 0 else 0.5
        score += w * (1 if p["direction"] == "bullish" else -1 if p["direction"] == "bearish" else 0)
    for e in breakouts:
        w = 1.5 if e.get("confirmed") else 1.0
        score += w * (1 if e["direction"] == "bullish" else -1)
    return {"patterns": patterns, "support_resistance": sr, "breakouts": breakouts,
            "pattern_score": round(score, 2),
            "pattern_bias": "bullish" if score >= 1 else "bearish" if score <= -1 else "neutral",
            "last_price": sr.get("last_price"), "as_of": df.index[-1].strftime("%Y-%m-%d")}


# ── 4. 멀티타임프레임 ──────────────────────────────────────────────────────

TIMEFRAMES = [  # (라벨, yahoo interval, range, 가중치)
    ("분봉(60분)", "60m", "1mo", 0.2),
    ("일봉", "1d", "1y", 0.5),
    ("주봉", "1wk", "5y", 0.3),
]


def timeframe_score(candles: list[dict]) -> dict:
    """한 타임프레임의 기술적 지표 점수(-6~+6)와 근거."""
    df = preprocess(candles)
    if len(df) < 35:
        return {"error": f"데이터 부족({len(df)}봉)"}
    c = df["close"].astype(float)
    rsi = float(ta.rsi(c, 14).iloc[-1])
    ma5, ma20 = float(ta.sma(c, 5).iloc[-1]), float(ta.sma(c, 20).iloc[-1])
    macd_line, macd_sig, _h = ta.macd(c, 12, 26, 9)
    bb_u, bb_m, bb_l = ta.bollinger(c, 20, 2.0)
    last = float(c.iloc[-1])
    score, reasons = 0, []
    if rsi < 30: score += 2; reasons.append(f"RSI {rsi:.0f} 과매도")
    elif rsi > 70: score -= 2; reasons.append(f"RSI {rsi:.0f} 과매수")
    else: reasons.append(f"RSI {rsi:.0f} 중립")
    if ma5 > ma20: score += 1; reasons.append("MA5 > MA20 상승 배열")
    else: score -= 1; reasons.append("MA5 < MA20 하락 배열")
    if float(macd_line.iloc[-1]) > float(macd_sig.iloc[-1]): score += 1; reasons.append("MACD > 시그널")
    else: score -= 1; reasons.append("MACD < 시그널")
    if last > float(bb_u.iloc[-1]): score -= 1; reasons.append("볼린저 상단 돌파")
    elif last < float(bb_l.iloc[-1]): score += 1; reasons.append("볼린저 하단 이탈")
    if last > float(bb_m.iloc[-1]): score += 1; reasons.append("20MA 위 (추세 우호)")
    else: score -= 1; reasons.append("20MA 아래 (추세 비우호)")
    return {"score": score, "rsi": round(rsi, 1), "ma5": round(ma5, 2), "ma20": round(ma20, 2), "last": round(last, 2),
            "reasons": reasons, "bars": len(df), "as_of": df.index[-1].strftime("%Y-%m-%d %H:%M")}


def combine_timeframes(results: list[dict]) -> dict:
    """[{label, weight, score}] → 종합 신호·신뢰도. 신뢰도 = 방향 일치도 × 점수 크기."""
    valid = [r for r in results if "score" in r]
    if not valid:
        return {"action": "관망", "composite": 0.0, "confidence": 0, "agreement": 0}
    wsum = sum(r["weight"] for r in valid) or 1.0
    composite = sum(r["score"] * r["weight"] for r in valid) / wsum          # -6 ~ +6
    direction = 1 if composite > 0 else -1 if composite < 0 else 0
    agree = sum(r["weight"] for r in valid if np.sign(r["score"]) == direction and direction != 0) / wsum
    magnitude = min(1.0, abs(composite) / 4.0)
    confidence = int(round(100 * (0.5 * agree + 0.5 * magnitude)))
    if composite >= 2.5: action = "강력 매수"
    elif composite >= 1: action = "매수"
    elif composite <= -2.5: action = "강력 매도"
    elif composite <= -1: action = "매도"
    else: action = "관망"
    return {"action": action, "composite": round(composite, 2), "confidence": confidence, "agreement": round(agree * 100)}


async def multi_timeframe_signal(symbol: str) -> dict:
    from app.services.stock import get_candles
    rows = []
    for label, interval, rng, weight in TIMEFRAMES:
        try:
            candles = (await get_candles(symbol, period=rng, interval=interval, max_age_hours=0)).get("candles", [])
            r = timeframe_score(candles) if candles else {"error": "시세 없음"}
        except Exception as exc:
            r = {"error": str(exc)[:80]}
        rows.append({"label": label, "interval": interval, "range": rng, "weight": weight, **r})
    combined = combine_timeframes(rows)
    # 일봉 패턴 점수를 보조 근거로 첨부
    pattern = None
    try:
        daily = next((r for r in rows if r["interval"] == "1d"), None)
        candles = (await get_candles(symbol, period="1y", interval="1d", max_age_hours=0)).get("candles", [])
        pattern = pattern_summary(candles) if candles else None
    except Exception:
        pattern = None
    return {"symbol": symbol, "timeframes": rows, **combined, "pattern": pattern,
            "disclaimer": "교육용 기술적 신호입니다. 분봉은 최근 1개월 60분봉, 주봉은 5년 데이터를 사용합니다."}
