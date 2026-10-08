"""External finance failures must degrade explicitly, without leaking tokens or breaking comparison."""
import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from app.services import stock


@pytest.fixture
def dependencies(monkeypatch):
    monkeypatch.setattr(stock, 'cache_get', AsyncMock(return_value=None))
    monkeypatch.setattr(stock, 'cache_set', AsyncMock())
    monkeypatch.setattr(stock, 'cache_info', AsyncMock(return_value={'updated_at': '2026-10-07T00:00:00Z'}))
    monkeypatch.setattr(stock, '_yahoo_chart', AsyncMock(return_value=None))
    monkeypatch.setattr(stock, '_get_yahoo_crumb', AsyncMock(return_value=({'cookie': 'test'}, 'test-crumb')))


def client_responses(monkeypatch, statuses):
    responses = iter(statuses)
    class Client:
        def __init__(self, **kwargs): self.cookies = {'cookie': 'test'}
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, *args, **kwargs):
            status, body = next(responses)
            return httpx.Response(status, json=body, request=httpx.Request('GET', 'https://provider.invalid/?crumb=SECRET'))
    monkeypatch.setattr(stock.httpx, 'AsyncClient', Client)


def test_401_refreshes_credentials_and_recovers(monkeypatch, dependencies):
    client_responses(monkeypatch, [(401, {}), (200, {'quoteSummary': {'result': [{'price': {'regularMarketPrice': {'raw': 100}}}]}})])
    result = asyncio.run(stock.get_fundamentals('005930.KS'))
    assert result['data_status'] == 'complete' and result['price'] == 100
    assert stock._get_yahoo_crumb.await_args_list[1].kwargs == {'rejected_crumb': 'test-crumb'}


def test_429_uses_labelled_stale_data(monkeypatch, dependencies):
    stock.cache_get.side_effect = [None, {'symbol': '005930.KS', 'roe': 12}]
    client_responses(monkeypatch, [(429, {}), (429, {})])
    result = asyncio.run(stock.get_fundamentals('005930.KS'))
    assert result['roe'] == 12 and result['data_status'] == 'stale'
    assert result['cached_at'] == '2026-10-07T00:00:00Z'
    assert 'SECRET' not in result['warning'] and '429' in result['warning']
    assert stock.cache_get.await_args_list[-1].kwargs['max_age_hours'] == 168
    stock.cache_set.assert_not_awaited()


def test_no_financials_provides_price_only(monkeypatch, dependencies):
    client_responses(monkeypatch, [(404, {})])
    stock._yahoo_chart.return_value = {'meta': {'regularMarketPrice': 100}}
    result = asyncio.run(stock.get_fundamentals('005930.KS'))
    assert result['data_status'] == 'partial' and result['price'] == 100
    assert result.get('roe') is None and 'error' not in result
    stock.cache_set.assert_not_awaited()


def test_missing_auth_and_quote_is_explicitly_unavailable(dependencies):
    stock._get_yahoo_crumb.return_value = None
    result = asyncio.run(stock.get_fundamentals('005930.KS'))
    assert result['data_status'] == 'unavailable' and result['price'] is None
    assert 'error' not in result and result['warning']


def test_upstream_failure_does_not_raise_502(monkeypatch, dependencies):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routes import stocks
    client_responses(monkeypatch, [(503, {}), (503, {})])
    app = FastAPI(); app.include_router(stocks.router)
    response = TestClient(app).get('/api/stocks/fundamentals?symbol=005930.KS')
    assert response.status_code == 200
    assert response.json()['data_status'] == 'unavailable'


def test_bad_crumb_response_is_not_cached(monkeypatch):
    monkeypatch.setattr(stock, '_yahoo_session', {'cookies': None, 'crumb': None, 'ts': 0})
    monkeypatch.setattr(stock, '_yahoo_session_lock', asyncio.Lock())
    client_responses(monkeypatch, [(404, {}), (429, {'error': 'Too Many Requests'})])
    assert asyncio.run(stock._get_yahoo_crumb()) is None
    assert stock._yahoo_session['crumb'] is None


def test_shared_authentication_is_requested_once(monkeypatch):
    monkeypatch.setattr(stock, '_yahoo_session', {'cookies': None, 'crumb': None, 'ts': 0})
    monkeypatch.setattr(stock, '_yahoo_session_lock', asyncio.Lock())
    calls = []
    class Client:
        def __init__(self, **kwargs): self.cookies = {'cookie': 'test'}
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, **kwargs):
            calls.append(url)
            await asyncio.sleep(0)
            return httpx.Response(200, text='valid-crumb', request=httpx.Request('GET', url))
    monkeypatch.setattr(stock.httpx, 'AsyncClient', Client)
    async def run(): return await asyncio.gather(*(stock._get_yahoo_crumb() for _ in range(4)))
    results = asyncio.run(run())
    assert len(calls) == 2 and len(results) == 4 and all(r[1] == 'valid-crumb' for r in results)
