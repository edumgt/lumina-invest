import pandas as pd
import pytest
from app.services.investment_research import explain_strategy, _position, backtest_strategy
from tests.conftest import make_candles

@pytest.mark.parametrize('strategy', ['rsi', 'ma', 'bollinger', 'composite'])
def test_xai_matches_backtest_and_period(strategy):
    result = backtest_strategy(make_candles(400), strategy=strategy)
    xai = result['explanation']
    assert xai['strategy'] == strategy
    assert xai['action'] == result['latest_signal']
    assert xai['as_of'] == result['data_end']
    assert result['data_start'] < result['data_end']
    assert result['data_points'] >= 60
    assert all(type(r['matched']) is bool and r['observed'] for r in xai['buy_conditions'] + xai['sell_conditions'])

@pytest.mark.parametrize('strategy,changes,expected', [
    ('rsi', {'rsi': 29}, 'BUY'), ('rsi', {'rsi': 30}, 'HOLD'),
    ('ma', {'ma5': 101}, 'BUY'), ('ma', {'ma5': 100}, 'HOLD'),
    ('bollinger', {'close': 89}, 'BUY'), ('bollinger', {'close': 90}, 'HOLD'),
    ('composite', {'ma5': 101, 'rsi': 51, 'macd': 2}, 'BUY'),
    ('composite', {'ma5': 101, 'rsi': 50, 'macd': 2}, 'HOLD'),
])
def test_buy_boundaries(strategy, changes, expected):
    row = dict(close=100, ma5=100, ma20=100, rsi=50, macd=1, macd_signal=1, bb_lower=90, bb_upper=110)
    df = pd.DataFrame([row, row | changes], index=pd.date_range('2026-01-01', periods=2))
    xai = explain_strategy(df, strategy, _position(df, strategy))
    assert xai['action'] == expected
    assert all(r['matched'] for r in xai['buy_conditions']) == (expected == 'BUY')

@pytest.mark.parametrize('strategy,changes', [
    ('rsi', {'rsi':71}), ('ma', {'ma5':99}), ('bollinger', {'close':111}),
    ('composite', {'ma5':99}), ('composite', {'rsi':76}),
])
def test_sell_conditions_and_prior_position(strategy, changes):
    row = dict(close=100, ma5=100, ma20=100, rsi=50, macd=1, macd_signal=1, bb_lower=90, bb_upper=110)
    df = pd.DataFrame([row, row | changes], index=pd.date_range('2026-01-01', periods=2))
    xai = explain_strategy(df, strategy, pd.Series([1,0], index=df.index))
    assert xai['action'] == 'SELL'
    assert any(r['matched'] for r in xai['sell_conditions'])
