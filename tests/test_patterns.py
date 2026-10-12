"""캔들 패턴·지지/저항·돌파·멀티타임프레임 종합 로직."""
import pandas as pd
import pytest

from app.services import patterns as pt
from tests.conftest import make_candles


def _df(rows):
    idx = pd.date_range("2026-01-01", periods=len(rows), freq="D")
    return pd.DataFrame(rows, index=idx, columns=["open", "high", "low", "close", "volume"])


def _flat(n=24, price=100.0):
    return [[price, price + 1, price - 1, price, 1000] for _ in range(n)]


def test_hammer_and_bullish_engulfing_detected():
    rows = _flat() + [[100, 100.5, 90, 99.5, 1000]]                     # 긴 아래꼬리 해머
    keys = {p["key"] for p in pt.detect_candle_patterns(_df(rows), lookback=1)}
    assert "hammer" in keys
    rows = _flat() + [[100, 101, 97, 98, 1000], [97, 104, 96.5, 103, 1500]]  # 음봉 뒤 장악 양봉
    keys = {p["key"] for p in pt.detect_candle_patterns(_df(rows), lookback=1)}
    assert "bullish_engulfing" in keys


def test_doji_and_three_black_crows():
    rows = _flat() + [[100, 103, 97, 100.1, 1000]]
    assert "doji" in {p["key"] for p in pt.detect_candle_patterns(_df(rows), lookback=1)}
    rows = _flat() + [[100, 100.5, 96, 96.5, 1000], [96.5, 97, 92, 92.5, 1000], [92.5, 93, 88, 88.5, 1000]]
    assert "three_black_crows" in {p["key"] for p in pt.detect_candle_patterns(_df(rows), lookback=1)}


def test_support_resistance_levels_found():
    rows = []
    for i in range(120):   # 90~110 사이 진동 → 110 저항, 90 지지 반복 터치
        c = 100 + 10 * (1 if (i // 10) % 2 == 0 else -1) * ((i % 10) / 9)
        rows.append([c, c + 0.5, c - 0.5, c, 1000])
    sr = pt.support_resistance(_df(rows))
    assert sr["levels"]
    prices = [l["price"] for l in sr["levels"]]
    assert any(abs(p - 110) < 2 for p in prices) or any(abs(p - 90) < 2 for p in prices)
    assert all(l["type"] in ("support", "resistance") for l in sr["levels"])


def test_breakout_52w_high_and_20d():
    rows = _flat(300)
    rows.append([100, 130, 100, 128, 5000])  # 급등 신고가 + 거래량 5배
    events = {e["key"]: e for e in pt.detect_breakouts(_df(rows))}
    assert "breakout_20d" in events and events["breakout_20d"]["confirmed"]
    assert "high_52w" in events


def test_pattern_summary_on_synthetic_candles():
    r = pt.pattern_summary(make_candles(300))
    assert {"patterns", "support_resistance", "breakouts", "pattern_score", "pattern_bias"} <= set(r)


def test_combine_timeframes_confidence_and_action():
    r = pt.combine_timeframes([{"label": "m", "weight": .2, "score": 3}, {"label": "d", "weight": .5, "score": 4}, {"label": "w", "weight": .3, "score": 2}])
    assert r["action"] == "강력 매수" and r["agreement"] == 100 and r["confidence"] >= 80
    mixed = pt.combine_timeframes([{"weight": .5, "score": 2}, {"weight": .5, "score": -2}])
    assert mixed["action"] == "관망" and mixed["confidence"] < 30
    assert pt.combine_timeframes([{"weight": 1, "error": "x"}])["action"] == "관망"


def test_timeframe_score_reasons():
    r = pt.timeframe_score(make_candles(120))
    assert -6 <= r["score"] <= 6 and len(r["reasons"]) >= 4


# ── 일목균형표 · 매물대 · 갭 · 거래량 급증 (2026-10-12 추가) ──────────────

def _trend(n=120, start=100.0, step=1.0):
    """단조 상승(step>0)·하락(step<0) 봉. 일목 구름 위/아래 판정이 확실하도록 변동 폭을 작게 둔다."""
    rows, p = [], start
    for _ in range(n):
        rows.append([p, p + abs(step) * 0.6, p - abs(step) * 0.6, p + step * 0.5, 1000])
        p += step
    return rows


def test_ichimoku_uptrend_above_cloud_three_fold_bullish():
    r = pt.ichimoku_state(_df(_trend(step=1.0)))
    assert r["position"] == "above_cloud" and r["three_fold"] == "삼역 호전"
    assert r["score"] >= 4 and r["tenkan"] > r["kijun"] and r["cloud_top"] >= r["cloud_bottom"]


def test_ichimoku_downtrend_below_cloud_and_short_data_error():
    r = pt.ichimoku_state(_df(_trend(step=-1.0)))
    assert r["position"] == "below_cloud" and r["three_fold"] == "삼역 역전" and r["score"] <= -4
    assert "error" in pt.ichimoku_state(_df(_trend(n=50)))


def test_ichimoku_cloud_break_event_on_last_bar():
    rows = _trend(n=110, step=-0.5)                      # 하락으로 구름 아래 형성
    last = rows[-1][3]
    rows.append([last, last + 40, last - 0.5, last + 38, 5000])  # 장대 양봉으로 구름 위로 점프
    r = pt.ichimoku_state(_df(rows))
    keys = {e["key"] for e in r["events"]}
    assert "cloud_break_up" in keys and r["position"] == "above_cloud"


def test_volume_profile_poc_and_value_area():
    rows = _flat(60, 100.0)                              # 100 부근에 거래 집중
    rows += [[110, 111, 109, 110, 100] for _ in range(20)]  # 110 부근은 소량
    rows[-1] = [110, 111, 109, 110.5, 100]
    vp = pt.volume_profile(_df(rows), lookback=80, bins=10)
    assert abs(sum(r["pct"] for r in vp["rows"]) - 100) < 0.01
    assert vp["poc"]["low"] <= 100 <= vp["poc"]["high"]          # 최대 매물대는 100 구간
    assert vp["value_area"]["low"] <= 100 <= vp["value_area"]["high"]
    assert vp["position"] == "above_poc" and vp["nearest_heavy_below"] is not None
    assert vp["nearest_heavy_below"]["high"] < 110.5 and vp["nearest_heavy_above"] is None


def test_gap_up_with_volume_then_fade_is_exhaustion_suspect():
    rows = _flat(30, 100.0)
    rows.append([104, 105, 102.5, 102.8, 5000])          # 전 봉 고가 101 위 갭 시가, 거래량 5배, 되밀림(종가<시가), 갭 미메움
    gaps = pt.detect_gaps(_df(rows))
    assert len(gaps) == 1
    g = gaps[0]
    assert g["key"] == "gap_up" and g["kind"] == "소진 갭 의심" and g["direction"] == "bearish"
    assert g["faded"] and not g["filled"] and g["vol_ratio"] >= 1.5 and g["bars_ago"] == 0


def test_gap_down_filled_and_breakaway_gap():
    rows = _flat(30, 100.0)
    rows.append([96, 99.5, 95.5, 96.2, 1000])            # 갭 하락 후 고가 99.5 → 전 봉 저가 99 이상이면 메움
    g = pt.detect_gaps(_df(rows))[0]
    assert g["key"] == "gap_down" and g["kind"] == "메워진 갭" and g["filled"]
    rows = _flat(30, 100.0) + [[104, 106, 103.5, 105.5, 4000]]  # 거래량 동반·유지 → 돌파 갭
    g = pt.detect_gaps(_df(rows))[0]
    assert g["kind"] == "돌파 갭" and g["direction"] == "bullish"
    assert pt.detect_gaps(_df(_flat(30) + [[100.5, 101.5, 99.5, 100.6, 1000]])) == []   # 1% 미만은 갭 아님


def test_volume_spike_event_in_breakouts():
    rows = _flat(70, 100.0)
    rows.append([100, 100.3, 97, 97.5, 3000])            # 거래량 3배 음봉
    events = {e["key"]: e for e in pt.detect_breakouts(_df(rows))}
    assert events["volume_spike"]["direction"] == "bearish" and events["volume_spike"]["confirmed"]


def test_pattern_summary_includes_new_sections_and_intraday_stamp():
    r = pt.pattern_summary(make_candles(300))
    assert {"ichimoku", "volume_profile", "gaps", "intraday"} <= set(r)
    assert r["intraday"] is False and len(r["as_of"]) == 10
    hourly = make_candles(200)
    for i, c in enumerate(hourly):
        c["time"] = 1_700_000_000 + i * 3600
    r = pt.pattern_summary(hourly)
    assert r["intraday"] is True and len(r["as_of"]) == 16          # "YYYY-MM-DD HH:MM"
    assert all("time" in p for p in r["patterns"])


def test_timeframe_score_adds_ichimoku_reason_when_enough_bars():
    r = pt.timeframe_score(make_candles(120))
    assert any("일목" in x for x in r["reasons"])
    r = pt.timeframe_score(make_candles(50))
    assert not any("일목" in x for x in r["reasons"])
