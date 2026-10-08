"""섹터별 투자 인디케이터 집계 — 실제 데이터 없이 집계 규칙·결측 처리·점수만 검증한다."""
import asyncio
from unittest.mock import AsyncMock, patch

from app.services import sector_indicators as si


def _candles(start: float, n: int, step: float, t0: int = 1_700_000_000) -> dict:
    return {"source": "yahoo", "candles": [
        {"time": t0 + i * 86400, "close": start + i * step} for i in range(n)]}


def test_weighted_aggregations_skip_missing_and_nonpositive():
    rows = [
        {"cap": 900.0, "per": 10.0, "roe": 20.0},
        {"cap": 100.0, "per": 50.0, "roe": None},      # roe 결측 → 평균에서 제외
        {"cap": 100.0, "per": -5.0, "roe": 5.0},       # 적자 PER → 배수 집계에서 제외
        {"cap": None,  "per": 1.0,  "roe": 999.0},     # 시총 없음 → 가중 집계 불가
    ]
    # 조화평균: (900+100)/(900/10 + 100/50) = 1000/92
    assert si._w_harmonic(rows, "per") == round(1000 / 92, 2)
    # 산술 가중평균(roe): (900*20 + 100*5)/1000
    assert si._w_mean(rows, "roe") == 18.5
    assert si._median(rows, "per") == 5.5          # 중위값은 결측만 제외
    assert si._coverage(rows, "roe") == 3


def test_percentile_rank_direction():
    pool = [10.0, 20.0, 30.0]
    assert si._percentile_rank(30.0, pool) == 83.3                       # 높을수록 좋음
    assert si._percentile_rank(10.0, pool, lower_is_better=True) == 83.3  # 낮을수록 좋음(저PER)
    assert si._percentile_rank(None, pool) is None
    assert si._percentile_rank(5.0, [5.0]) == 50.0                       # 비교 대상이 없으면 중립


def test_cap_weighted_index_uses_common_dates_and_weights():
    members = [
        {"cap": 300.0, "_closes": si._closes_by_date(_candles(100, 5, 10)["candles"])},   # +40%
        {"cap": 100.0, "_closes": si._closes_by_date(_candles(50, 5, 0)["candles"])},     # 0%
    ]
    dates, index = si._cap_weighted_index(members)
    assert len(dates) == 5 and index[0] == 100.0
    assert round(index[-1], 2) == round(0.75 * 140 + 0.25 * 100, 2)   # 시총 비중 3:1


def test_sector_overview_marks_missing_price_and_scores(monkeypatch):
    """일봉 없는 종목은 인덱스에서 빼되 구성종목 수에는 남기고, 사유를 basis.errors 에 적는다."""
    universe = [
        {"symbol": "A.KS", "name": "에이", "sector": "반도체"},
        {"symbol": "B.KS", "name": "비", "sector": "반도체"},
        {"symbol": "C.KS", "name": "씨", "sector": "IT"},
    ]
    funds = {
        "A.KS": {"cap": 1000.0, "per": 10.0, "perBasis": "trailing", "roe": 20.0, "revenueGrowth": 30.0, "debt": 10.0, "currentRatio": 2.0, "opMargin": 15.0},
        "B.KS": {"cap": 100.0, "per": 20.0, "perBasis": "forward", "roe": 5.0, "revenueGrowth": 1.0, "debt": 80.0, "currentRatio": 1.0, "opMargin": 3.0},
        "C.KS": {"cap": 500.0, "per": 40.0, "perBasis": "forward", "roe": 2.0, "revenueGrowth": -5.0, "debt": 90.0, "currentRatio": 0.9, "opMargin": 1.0},
    }
    candles = {"A.KS": _candles(100, 300, 1), "B.KS": {"candles": []}, "C.KS": _candles(100, 300, -0.2),
               si.BENCHMARK["symbol"]: _candles(100, 300, 0.1)}
    monkeypatch.setattr(si, "QUANT_STOCKS", universe)
    monkeypatch.setattr(si, "QUANT_SECTORS", ("반도체", "IT"))
    with patch.object(si, "get_fundamentals", AsyncMock(side_effect=lambda s: funds[s])), \
         patch.object(si, "get_candles", AsyncMock(side_effect=lambda s, period="2y": candles[s])), \
         patch.object(si, "cache_get", AsyncMock(return_value=None)), \
         patch.object(si, "cache_set", AsyncMock(return_value=None)):
        out = asyncio.run(si.sector_overview(force=True))

    by = {s["sector"]: s for s in out["sectors"]}
    semi = by["반도체"]
    assert semi["count"] == 2 and semi["basis"]["priced"] == 1
    assert semi["basis"]["errors"] == ["비: 일봉 없음"]
    assert semi["valuation"]["per_basis"] == {"trailing": 1, "forward": 1}
    # 가격이 오르는 반도체가 하락하는 IT 보다 점수가 높고, 판정 규칙이 적용된다
    assert semi["score"]["total"] > by["IT"]["score"]["total"]
    assert semi["verdict"] in ("비중확대", "중립") and by["IT"]["verdict"] in ("중립", "비중축소")
    # 근거는 계산된 수치를 인용한다 (KOSPI 대비 초과수익 문장 포함)
    assert any("vs KOSPI" in r for r in semi["rationale"])
    assert out["benchmark"]["ret_12m"] is not None and out["score_weights"] == si.SCORE_WEIGHTS
    # 구성종목 표는 비중 내림차순, 결측 종목도 남는다
    assert [h["name"] for h in semi["holdings"]] == ["에이", "비"]
    assert semi["holdings"][0]["weight_pct"] == 90.91
