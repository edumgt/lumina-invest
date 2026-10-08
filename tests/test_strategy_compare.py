"""Multi-strategy comparisons must share a snapshot, costs, and warm-up window."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services import investment_research as research
from app.routes import stocks
from app.lib.session import get_current_user
from tests.conftest import make_candles


def test_comparison_uses_one_indicator_window(monkeypatch):
    calls = []
    original = research.indicators
    def compute(candles):
        calls.append(candles)
        return original(candles)
    monkeypatch.setattr(research, 'indicators', compute)
    result = research.compare_strategies(make_candles(), ['rsi','ma','bollinger','composite'])
    assert len(calls) == 1
    rows = result['results']
    assert [r['strategy'] for r in rows] == ['rsi','ma','bollinger','composite','buy_hold']
    for r in rows:
        assert (r['data_start'],r['data_end'],r['data_points']) == (result['data_start'],result['data_end'],result['data_points'])
        assert r['times'] == rows[0]['times']
        assert r['cost_bps'] == 10 and r['slippage_bps'] == 5
        assert r['excess_return_pct'] == pytest.approx(round(r['total_return_pct']-rows[-1]['total_return_pct'],2))
        assert r['cum_returns'][-1] == r['total_return_pct']
        assert r['explanation']['strategy'] == r['strategy']
    assert rows[-1]['excess_return_pct'] == 0


def test_hold_benchmark_pays_entry_cost_but_remains_hold():
    candles = make_candles()
    paid = research.compare_strategies(candles,['ma'],10,5,5,15)
    free = research.compare_strategies(candles,['ma'],0,0)
    strategy, hold = paid['results']
    assert strategy['stop_loss_pct'] == 5 and strategy['take_profit_pct'] == 15
    assert hold['stop_loss_pct'] is None and hold['take_profit_pct'] is None
    assert hold['stop_loss_exits'] == hold['take_profit_exits'] == 0
    assert hold['trade_count'] == 1
    assert hold['holding_days'] == hold['data_points'] - 1
    assert hold['cost_pct'] == pytest.approx(.15)
    assert hold['gross_return_pct'] == free['results'][-1]['total_return_pct']
    assert hold['total_return_pct'] < hold['gross_return_pct']
    assert hold['explanation']['kind'] == 'buy_hold'


@pytest.mark.parametrize('selected', [[], ['unknown'], ['rsi','unknown']])
def test_invalid_selection(selected):
    assert 'error' in research.compare_strategies(make_candles(),selected)


def test_subset_deduplication_and_insufficient_data():
    result = research.compare_strategies(make_candles(),['ma','ma'])
    assert [r['strategy'] for r in result['results']] == ['ma','buy_hold']
    assert 'error' in research.compare_strategies(make_candles(90),['ma'])


@pytest.fixture
def compare_client(monkeypatch):
    app = FastAPI()
    app.include_router(stocks.router)
    app.dependency_overrides[get_current_user] = lambda: {'id':'test-user'}
    calls = []
    async def get_candles(symbol,period,interval):
        calls.append((symbol,period,interval))
        return {'candles':make_candles(), 'source':'fixture'}
    monkeypatch.setattr(stocks,'get_candles',get_candles)
    with TestClient(app) as client:
        yield client,calls,app


def test_compare_endpoint_fetches_once_and_returns_selected(compare_client):
    client,calls,_ = compare_client
    response = client.get('/api/quant/compare?symbol=005930.KS&period=3y&strategies=ma,rsi,ma&cost_bps=20&slippage_bps=7&stop_loss_pct=5')
    assert response.status_code == 200
    assert calls == [('005930.KS','3y','1d')]
    result = response.json()
    assert result['symbol'] == '005930.KS' and result['period'] == '3y'
    assert result['source'] == 'fixture'
    assert [r['strategy'] for r in result['results']] == ['ma','rsi','buy_hold']
    assert result['settings'] == {'cost_bps':20.0,'slippage_bps':7.0,'stop_loss_pct':5.0,'take_profit_pct':None}


@pytest.mark.parametrize('query', ['strategies=', 'strategies=bad', 'period=2y', 'cost_bps=-1', 'stop_loss_pct=0'])
def test_endpoint_validation_happens_before_fetch(compare_client,query):
    client,calls,_ = compare_client
    assert client.get('/api/quant/compare?'+query).status_code == 422
    assert not calls


def test_endpoint_requires_login(compare_client):
    client,calls,app = compare_client
    app.dependency_overrides.clear()
    assert client.get('/api/quant/compare').status_code == 401
    assert not calls


def test_endpoint_reports_missing_prices(compare_client,monkeypatch):
    client,_,_ = compare_client
    async def empty(*args,**kwargs): return {'candles':[]}
    monkeypatch.setattr(stocks,'get_candles',empty)
    assert client.get('/api/quant/compare').status_code == 404
