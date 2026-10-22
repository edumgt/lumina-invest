"""Daily rule boundaries and auditable score contributions."""
import pytest
from app.services.stock import calculate_quant_indicators, _calc_rsi, _generate_signal
from app.services.aggressive_mode import score_intraday
from app.config import settings


def test_flat_prices_are_neutral_in_daily_and_intraday_rules():
    values = [100.0] * 80
    r = calculate_quant_indicators('TEST', {'candles': [{'time': i, 'close': v} for i, v in enumerate(values)]})
    assert r['current_rsi'] == 50 and r['signal']['score'] == 0
    assert r['signal']['action'] == '관망'
    assert any('= 중기' in x for x in r['signal']['reasons'])
    assert score_intraday(values, [100] * 80)['score'] == 0


@pytest.mark.parametrize('values, expected', [([float(i) for i in range(80)], 100),
    ([100.0-i for i in range(80)], 0), ([100.0]*80, 50)])
def test_rsi_zero_gain_or_loss(values, expected):
    assert _calc_rsi(values)[-1] == expected


def test_strong_buy_has_exactly_reproducible_score_and_cross_reason():
    signal = _generate_signal([100., 101.], [50., 55.], [99., 101.], [100., 100.],
                              [100., 100.], [110., 110.], [90., 90.])
    assert signal['action'] == '강력 매수' and signal['score'] == 3
    assert sum(c['points'] for c in signal['components']) == signal['score']
    assert any('상향 돌파' in c['reason'] and c['points'] == 3 for c in signal['components'])
    assert signal['strong_buy_threshold'] == 3


def test_cross_is_not_itself_final_strong_buy_when_other_rules_offset_it():
    signal = _generate_signal([100., 120.], [60., 80.], [99., 101.], [100., 100.],
                              [100., 100.], [110., 110.], [90., 90.])
    assert signal['score'] == 0 and signal['action'] == '관망'
    assert sum(c['points'] for c in signal['components']) == 0


def test_intraday_labels_match_actual_fifteen_minute_bars(monkeypatch):
    monkeypatch.setattr(settings, 'QUANT_AGGRESSIVE_CANDLE_INTERVAL', '15m')
    r = score_intraday([100.+i for i in range(40)], [100.] * 40)
    assert r['momentum_window_min'] == 90
    assert any('15분 MA5' in reason for reason in r['reasons'])
    assert any('최근 6봉(90분)' in reason for reason in r['reasons'])
    assert not any('5분 MA5' == reason[:6] or '30분 모멘텀' in reason for reason in r['reasons'])


@pytest.mark.parametrize("expanded", [False, True])
def test_candidate_api_reports_applied_scope_and_interval(monkeypatch, expanded):
    import asyncio
    from unittest.mock import AsyncMock
    from app.routes.stocks import korea_candidates
    from app.services import korea_market_scan, data_cache, strategy_settings
    monkeypatch.setattr(korea_market_scan, 'get_scan', AsyncMock(return_value={'coverage': {}, 'updated_at': 'now'}))
    monkeypatch.setattr(korea_market_scan, 'candidate_stocks', AsyncMock(return_value=[
        {'symbol': '263750.KQ', 'name': '펄어비스'}, {'symbol': '207940.KS', 'name': '삼성바이오로직스'}]))
    monkeypatch.setattr(data_cache, 'cache_get_many', AsyncMock(return_value={}))
    async def apply_saved_profile():
        monkeypatch.setattr(settings, 'QUANT_AGGRESSIVE_CANDLE_INTERVAL', '15m')
        monkeypatch.setattr(settings, 'QUANT_AGGRESSIVE_MAX_BUYS_PER_CYCLE', 1)
        monkeypatch.setattr(settings, 'QUANT_KOREA_ALLOW_PAPER_EXPANSION', expanded)
        monkeypatch.setattr(settings, 'QUANT_SIGNAL_MODE', 'aggressive')
        return {}
    monkeypatch.setattr(strategy_settings, 'load_and_apply', apply_saved_profile)
    r = asyncio.run(korea_candidates(limit=40))
    assert r['stocks'][0]['kis_order_allowed'] is True
    assert r['stocks'][1]['kis_order_allowed'] is expanded
    assert r['signal_policy']['order_universe_restricted'] is not expanded
    assert r['signal_policy']['execution_interval'] == '15m'
    assert r['signal_policy']['max_buys_per_cycle'] == 1
