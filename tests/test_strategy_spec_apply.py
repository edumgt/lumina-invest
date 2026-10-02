"""전략 스펙 적용(순수 함수): 유니버스 제한·최대 종목 수·임계값 재판정."""
from app.services.auto_trade import apply_strategy_spec_to_signal, apply_strategy_spec_to_symbols

SPEC = {"strategy_id": "ma_cross_kr", "version": 2, "universe": ["005930", "000660"],
        "position_sizing": {"max_symbols": 1}, "signal_weights": {"buy_threshold": 0.25, "sell_threshold": -0.5}}


def test_symbols_restricted_to_universe_and_capped():
    assert apply_strategy_spec_to_symbols(["035420.KS", "000660.KS", "005930.KS"], SPEC) == ["000660.KS"]
    # 교집합 없으면 원본 유지 (전략이 종목 선정 화면 선택을 완전히 무효화하지 않음)
    assert apply_strategy_spec_to_symbols(["035420.KS"], SPEC) == ["035420.KS"]
    assert apply_strategy_spec_to_symbols(["005930.KS"], {"universe": []}) == ["005930.KS"]


def test_signal_rejudged_by_thresholds():
    buy = apply_strategy_spec_to_signal({"action": "관망", "score": 2, "reasons": ["RSI 중립"]}, SPEC)
    assert buy["action"] == "매수" and buy["normalized_score"] == 0.25 and buy["reasons"][0] == "RSI 중립"
    strong = apply_strategy_spec_to_signal({"action": "매수", "score": 5}, SPEC)
    assert strong["action"] == "강력 매수"
    hold = apply_strategy_spec_to_signal({"action": "매도", "score": -3}, SPEC)   # -0.375 > -0.5 → 관망
    assert hold["action"] == "관망"
    sell = apply_strategy_spec_to_signal({"action": "관망", "score": -4}, SPEC)
    assert sell["action"] == "매도"
    assert "ma_cross_kr v2" in sell["reasons"][-1]


def test_ml_score_is_weighted_when_spec_asks():
    spec = {**SPEC, "signal_weights": {"technical": 0.5, "lightgbm": 0.5, "buy_threshold": 0.3, "sell_threshold": -0.3}}
    # 지표 +2 → +0.25, ML +0.75 → 평균 +0.5 ≥ 0.3 → 매수
    out = apply_strategy_spec_to_signal({"action": "관망", "score": 2, "reasons": []}, spec, ml_score=0.75)
    assert out["action"] == "매수" and out["normalized_score"] == 0.5 and "ML +0.75" in out["reasons"][-1]
    # ML 없음 → 지표만(0.25 < 0.3) → 관망, 사유에 표시
    none = apply_strategy_spec_to_signal({"action": "관망", "score": 2}, spec, ml_score=None)
    assert none["action"] == "관망" and "ML 점수 없음" in none["reasons"][-1]
    # lightgbm 가중 0이면 ML 점수를 무시
    ignored = apply_strategy_spec_to_signal({"action": "관망", "score": 2}, SPEC, ml_score=1.0)
    assert ignored["normalized_score"] == 0.25


def test_ml_scores_by_symbol_normalizes_and_aliases(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock, patch

    from app.services import auto_trade

    data = {"scores": {"005930.KS": {"pred_ann_return_pct": 15.0}, "000660.KS": {"pred_ann_return_pct": -60.0}, "bad": {"pred_ann_return_pct": None}}}
    with patch.object(auto_trade, "get_batch_training_scores", AsyncMock(return_value=data)):
        scores = asyncio.run(auto_trade.ml_scores_by_symbol())
    assert scores["005930.KS"] == 0.5 and scores["005930"] == 0.5
    assert scores["000660.KS"] == -1.0 and "bad" not in scores
    with patch.object(auto_trade, "get_batch_training_scores", AsyncMock(return_value=None)):
        assert asyncio.run(auto_trade.ml_scores_by_symbol()) == {}
