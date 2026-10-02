"""스펙 entry/exit 규칙 직접 평가, 종목별 Ridge ML 점수."""
from app.services import ml_symbol_score as ms
from app.services.auto_trade import apply_strategy_spec_to_signal, evaluate_spec_rules
from tests.conftest import make_candles


def _spec(ind="ma_cross", entry="short_above_long", exit_="short_below_long", **params):
    return {"strategy_id": "s", "version": 1, "entry": {"indicator": ind, "params": params, "condition": entry},
            "exit": {"indicator": ind, "params": params, "condition": exit_}, "signal_weights": {"buy_threshold": 0.9, "sell_threshold": -0.9}}


def test_ma_cross_rule_drives_action_over_threshold():
    up = {"closes": [100.0] * 30 + [101, 103, 106, 110, 115]}       # 단기 MA > 장기 MA
    res = evaluate_spec_rules(_spec(short_window=5, long_window=20), up)
    assert res == {"entry": True, "exit": False, "detail": res["detail"]}
    sig = apply_strategy_spec_to_signal({"action": "관망", "score": 0}, _spec(short_window=5, long_window=20), indicators=up)
    assert sig["action"] == "매수" and sig["rule_based"] is True and "진입=True" in sig["reasons"][-1]
    down = {"closes": [100.0] * 30 + [99, 97, 94, 90, 85]}
    assert apply_strategy_spec_to_signal({"action": "매수", "score": 5}, _spec(short_window=5, long_window=20), indicators=down)["action"] == "매도"


def test_momentum_and_always_rules_and_fallbacks():
    closes = list(range(100, 130)) + [140]
    assert evaluate_spec_rules(_spec("momentum", "breakout_high", "breakdown_low", breakout_window=20), {"closes": closes})["entry"] is True
    assert evaluate_spec_rules(_spec("buy_hold", "always", "never"), {"closes": [1.0, 2.0]}) == {"entry": True, "exit": False, "detail": "규칙 buy_hold: 진입=True 청산=False"}
    assert evaluate_spec_rules(_spec(short_window=5, long_window=200), {"closes": [1.0] * 50}) is None     # 데이터 부족 → None
    assert evaluate_spec_rules(_spec("unknown_ind", "x", "y"), {"closes": [1.0] * 50}) is None
    sig = apply_strategy_spec_to_signal({"action": "매수", "score": 8}, _spec(short_window=5, long_window=200), indicators={"closes": [1.0] * 50})
    assert sig["rule_based"] is False and sig["action"] == "강력 매수"   # 규칙 불가 → 임계값 방식 유지


def test_symbol_ml_score_from_candles():
    candles = make_candles(400, seed=7)
    pct = ms.predict_forward_return_pct(candles)
    assert pct is not None and -50 < pct < 50
    score = ms.symbol_score(candles)
    assert score is not None and -1.0 <= score <= 1.0
    assert ms.symbol_score(candles[:50]) is None                      # 데이터 부족
    assert ms.score_from_return_pct(12.0) == 1.0 and ms.score_from_return_pct(-2.5) == -0.5
