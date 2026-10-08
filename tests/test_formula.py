"""자유 산식 DSL: 안전성 · causal(룩어헤드 차단) · 계산 · 버전 체크섬 · 코드 생성."""
import pandas as pd
import pytest

from app.services import formula as fx
from tests.conftest import make_candles


def test_blocked_constructs():
    for bad in ["__import__('os')", "close.mean()", "[1,2]", "lambda x: x", "open(1)", "shift(close, 0)", "shift(close, -1)",
                "'abc'", "close ** 50", "unknown_fn(close)", "foo + 1"]:
        with pytest.raises(fx.FormulaError):
            fx.compute(make_candles(120), bad)


def test_validate_reports_names_and_checksum():
    info = fx.validate_definition("zscore(close, n)", "zscore(close, n) < -2", "zscore(close, n) > 0", {"n": 20})
    assert info["ok"] and "zscore" in info["functions"] and "n" in info["variables"] and len(info["checksum"]) == 16
    with pytest.raises(fx.FormulaError):
        fx.validate_definition("zscore(close, n)", params={})            # n 미정의
    with pytest.raises(fx.FormulaError):
        fx.validate_definition("sma(close, 20)", params={"close": 1})    # 예약어 충돌


def test_checksum_changes_only_with_definition():
    a = fx.definition_checksum("sma(close, 20)", "", "", {"n": 5})
    b = fx.definition_checksum("sma(close, 20) ", None, None, {"n": 5.0})  # 공백/None/float 동일 취급
    c = fx.definition_checksum("sma(close, 20)", "", "", {"n": 6})
    assert a == b != c


def test_compute_is_causal():
    """마지막 봉을 바꿔도 이전 봉의 지표·신호는 변하지 않는다."""
    candles = make_candles(300)
    r1 = fx.compute(candles, "zscore(close, 20)", "crossover(ema(close,5), ema(close,20))", "crossunder(ema(close,5), ema(close,20))")
    tampered = [dict(c) for c in candles]; tampered[-1]["close"] *= 1.5
    r2 = fx.compute(tampered, "zscore(close, 20)", "crossover(ema(close,5), ema(close,20))", "crossunder(ema(close,5), ema(close,20))")
    assert r1["series"]["indicator"][:-1] == r2["series"]["indicator"][:-1]
    assert r1["series"]["position"][:-1] == r2["series"]["position"][:-1]


def test_compute_backtest_and_signals():
    r = fx.compute(make_candles(400), "rsi(close, 14)", "rsi(close, 14) < 35 and close > sma(close, 50)", "rsi(close, 14) > 70",
                   commission_bps=10, slippage_bps=5)
    assert r["backtest"] and "total_return_pct" in r["backtest"] and r["backtest"]["commission_bps"] == 10
    assert r["latest_signal"] in ("BUY", "SELL", "HOLD", "HOLD_LONG")
    assert len(r["series"]["times"]) == len(r["series"]["indicator"]) <= 250
    r2 = fx.compute(make_candles(200), "close / sma(close, n) - 1", params={"n": 10})
    assert r2["backtest"] is None and r2["latest_value"] is not None


def test_where_and_params_and_templates_run():
    for t in fx.TEMPLATES:
        r = fx.compute(make_candles(400), t["indicator_expr"], t["buy_expr"], t["sell_expr"], t["params"])
        assert r["rows"] > 0
    r = fx.compute(make_candles(150), "where(rsi(close) < 30, 1, 0) + nz(shift(close, 1) / close - 1)")
    assert r["latest_value"] is not None


def test_code_generation():
    pine = fx.to_pine("Test", "zscore(close, n)", "crossover(ema(close,5), ema(close,20))", "rsi(close,14) > 70", {"n": 20})
    # 기간 파라미터는 input.int 로 내야 한다 (Pine 의 length 는 series int, input.float 를 넘기면 컴파일 오류)
    assert "//@version=6" in pine and "ta.crossover(ta.ema(close, 5), ta.ema(close, 20))" in pine and 'input.int(20, "n")' in pine
    py = fx.to_python("Test", "sma(close, 20)", None, None, {})
    assert "compute(candles" in py


def test_negative_shift_rejected_at_validation():
    with pytest.raises(fx.FormulaError):
        fx.validate_definition("shift(close, -1)")
    with pytest.raises(fx.FormulaError):
        fx.validate_definition("shift(close, 0)")
    assert fx.validate_definition("shift(close, 1)")["ok"]


def test_pine_no_double_prefix():
    pine = fx.to_pine("Z", "zscore(close, n)", None, None, {"n": 20})
    assert "ta.ta." not in pine and "ta.sma(close, n)" in pine


def test_pine_v6_syntax_pitfalls():
    """Pine 에 없는 문법으로 새지 않는지 — 거듭제곱·obv·상수·중첩 인자·연쇄 비교."""
    pine = fx.to_pine("Edge", "sqrt(std(close, 20) ** 2) + obv() / 1000 + pi",
                      "30 < rsi(close, 14) < 70", "where(True, close, shift(sma(close, 5), 3)) > close", {})
    assert "**" not in pine and "^" not in pine and "math.pow(ta.stdev(close, 20), 2)" in pine
    assert "ta.obv /" in pine and "ta.obv(" not in pine   # ta.obv 는 변수, 함수 호출이 아니다
    assert "math.pi" in pine and "True" not in pine and "true ?" in pine
    assert "(ta.sma(close, 5))[3]" in pine                 # 중첩 인자도 shift 변환됨 (구 정규식은 실패)
    assert "((30 < ta.rsi(close, 14)) and (ta.rsi(close, 14) < 70))" in pine  # 연쇄 비교 분해


def test_pine_casts_numeric_signal_to_bool():
    """v6 는 숫자→bool 암묵 변환을 없앴다 — plotshape 에 넘기는 신호는 bool() 로 감싼다."""
    pine = fx.to_pine("B", "close", "barssince(close > open)", "crossunder(close, open)", {})
    assert "buy_signal  = bool(ta.barssince((close > open)))" in pine
    assert "sell_signal = ta.crossunder(close, open)" in pine   # 이미 bool 이면 덧씌우지 않는다


def test_pine_param_input_types():
    """기간 파라미터만 input.int — Pine length 는 정수여야 하고, 배수(k)는 2.5 를 넣을 수 있어야 한다."""
    pine = fx.to_pine("BB", "zscore(close, n)", "close < bb_lower(close, n, k)", None, {"n": 20, "k": 2.0})
    assert 'n = input.int(20, "n")' in pine and 'k = input.float(2.0, "k")' in pine
    # 기간이 식이면 int() 로 감싼다 (v6 는 정수끼리 나눠도 실수라 length 자리에 못 쓴다)
    assert "ta.sma(close, int((n * 2)))" in fx.to_pine("E", "sma(close, n * 2)", None, None, {"n": 10})
    assert "ta.sma(close, n)" in fx.to_pine("E", "sma(close, n)", None, None, {"n": 10})


def test_pine_rejects_nested_history_operator():
    """Pine 은 같은 값에 [] 를 한 번만 허용한다 — close[1][2] 가 되는 중첩은 거부."""
    import pytest as _pt
    assert "(close)[3]" in fx.to_pine("S", "shift(close, 3)", None, None, {})
    with _pt.raises(fx.FormulaError, match="중첩"):
        fx.to_pine("S", "shift(shift(close, 1), 2)", None, None, {})
    with _pt.raises(fx.FormulaError, match="중첩"):
        fx.to_pine("S", "pct_change(shift(close, 1))", None, None, {})


def test_pine_rejects_unconvertible_function():
    import pytest as _pt
    with _pt.raises(fx.FormulaError):
        fx._pine_expr(fx.parse_expr("nope(close)"))
