import asyncio
from datetime import date,timedelta
from unittest.mock import AsyncMock
import pytest
from app.services import korea_universe as master,korea_market_scan as scan
from app.config import settings


def test_master_equity_filter_and_market_suffixes():
    assert len(master.STOCKS)>2000
    index={s['symbol']:s for s in master.STOCKS}
    assert '005930.KS' in index and '000250.KQ' in index
    assert '069500.KS' not in index  # ETF must not enter the equity scope.
    assert index['005930.KS']['sector']=='전기·전자'
    assert index['035420.KS']['sector']=='IT 서비스'
    assert '005935.KS' in index  # Preferred shares are still listed equity.


def test_invalid_master_does_not_replace_existing():
    before=list(master.STOCKS)
    with pytest.raises(ValueError):master.apply_master({'stocks':[master.STOCKS[0]]})
    assert master.STOCKS==before


def test_freshness_and_liquidity_gate():
    today=date.today()
    row={'status':'ready','buy_eligible':True,'as_of':today.isoformat(),'avg_turnover':1e10}
    assert scan.usable_entry(row,today)
    for patch in [{'buy_eligible':False},{'status':'partial'},{'status':'unavailable'},
                  {'as_of':(today-timedelta(days=10)).isoformat()},{'avg_turnover':0}]:
        assert not scan.usable_entry({**row,**patch},today)


def test_candidates_exclude_stale_removed_and_master_halted(monkeypatch):
    stocks=[{'symbol':'001.KS','buy_eligible':True},{'symbol':'002.KQ','buy_eligible':False}]
    monkeypatch.setattr(master,'STOCKS',stocks)
    monkeypatch.setattr(master,'master_info',lambda:{'stale':False})
    base={'status':'ready','buy_eligible':True,'as_of':date.today().isoformat(),'avg_turnover':1e10,'score':5}
    monkeypatch.setattr(scan,'get_scan',AsyncMock(return_value={'entries':{s:{**base,'symbol':s} for s in ['001.KS','002.KQ','removed.KS']}}))
    assert [s['symbol'] for s in asyncio.run(scan.candidate_stocks())]==['001.KS']


def test_checkpoint_continues_after_one_symbol_failure(monkeypatch):
    import app.lib.redis_cache as redis_module
    class Redis:
        async def set(self,*args,**kw):return True
        async def eval(self,*args):return 1
    monkeypatch.setattr(redis_module,'get_redis',lambda:Redis())
    stocks=[{'symbol':f'{i:06}.KS','code':f'{i:06}','market':'KOSPI','name':str(i),'sector':'업종','buy_eligible':True} for i in range(3)]
    monkeypatch.setattr(master,'STOCKS',stocks)
    monkeypatch.setattr(master,'refresh_master',AsyncMock())
    monkeypatch.setattr(master,'master_info',lambda:{'revision':'test','stale':False})
    cache={}
    async def get(k,**kw):return cache.get(k)
    async def put(k,v):cache[k]=v
    monkeypatch.setattr(scan,'cache_get',get);monkeypatch.setattr(scan,'cache_set',put)
    async def analyse(s):
        if s['code']=='000001':raise TimeoutError('provider')
        return {'symbol':s['symbol'],'status':'ready','buy_eligible':True,'as_of':date.today().isoformat(),'avg_turnover':1e10}
    monkeypatch.setattr(scan,'analyse_stock',analyse)
    async def run():
        first=await scan.run_scan_chunk(2)
        second=await scan.run_scan_chunk(2)
        return first,second
    first,second=asyncio.run(run())
    assert first['processed']==2 and first['unavailable']==1
    assert second['processed']==3 and second['ready']==2
    assert cache[scan.SCAN_KEY]['complete'] is True


def test_scan_does_not_use_broker_quotes_or_orders(monkeypatch):
    from app.services import ohlcv_store,stock
    monkeypatch.setattr(ohlcv_store,'configured',lambda:True)
    monkeypatch.setattr(settings,'QUANT_KOREA_INGEST_MISSING',False)
    monkeypatch.setattr(stock,'get_quote',AsyncMock(side_effect=AssertionError('must not fetch broker quote')))
    row=next(r for r in master.STOCKS if r['symbol']=='005930.KS')
    import math,time
    candles=[{'time':int(time.time())-(100-i)*86400,'close':100+math.sin(i/4)*5,'high':110,'low':90,'open':100,'volume':10000000} for i in range(100)]
    monkeypatch.setattr(ohlcv_store,'read_store',AsyncMock(return_value=candles))
    monkeypatch.setattr(scan,'cache_get',AsyncMock(return_value=None));put=AsyncMock();monkeypatch.setattr(scan,'cache_set',put)
    result=asyncio.run(scan.analyse_stock(row))
    assert result['status']=='ready' and set(result['patterns'])=={'lightgbm','ma','rsi','bollinger'}
    stock.get_quote.assert_not_awaited()
    assert put.await_count==2


def test_list_search_and_pagination_reach_beyond_legacy_universe(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routes.stocks import router
    monkeypatch.setattr(master,'refresh_master',AsyncMock())
    app=FastAPI();app.include_router(router)
    client=TestClient(app)
    first=client.get('/api/stocks/quant/list?limit=30&offset=0').json()
    second=client.get('/api/stocks/quant/list?limit=30&offset=30').json()
    assert first['total']>2000 and len(first['stocks'])==len(second['stocks'])==30
    assert {s['symbol'] for s in first['stocks']}.isdisjoint(s['symbol'] for s in second['stocks'])
    found=client.get('/api/stocks/quant/list?q=한국전력').json()
    assert any(s['symbol']=='015760.KS' for s in found['stocks'])
    assert all(s['market']=='KOSDAQ' for s in client.get('/api/stocks/quant/list?market=KOSDAQ&limit=30').json()['stocks'])


def test_allowed_candidates_are_filtered_before_pool_limit(monkeypatch):
    stocks = [{'symbol': f'{i:06}.KS', 'buy_eligible': True} for i in range(5)]
    monkeypatch.setattr(master, 'STOCKS', stocks)
    monkeypatch.setattr(master, 'master_info', lambda: {'stale': False})
    base = {'status': 'ready', 'buy_eligible': True, 'as_of': date.today().isoformat(), 'avg_turnover': 1e10}
    entries = {s['symbol']: {**base, 'symbol': s['symbol'], 'score': 10-i} for i, s in enumerate(stocks)}
    monkeypatch.setattr(scan, 'get_scan', AsyncMock(return_value={'entries': entries}))
    permitted = {'000003.KS', '000004.KS'}
    assert [s['symbol'] for s in asyncio.run(scan.candidate_stocks(2, allowed_symbols=permitted))] == ['000003.KS', '000004.KS']
    assert asyncio.run(scan.candidate_stocks(2, allowed_symbols=set())) == []
    entries['000003.KS']['status'] = 'stale'
    assert [s['symbol'] for s in asyncio.run(scan.candidate_stocks(2, allowed_symbols=permitted))] == ['000004.KS']


def test_sell_candidates_filter_and_rank_before_limit_without_changing_buy(monkeypatch):
    stocks = [{'symbol':s,'buy_eligible':True} for s in ['buy','weak','sell','strong','liquid']]
    monkeypatch.setattr(master,'STOCKS',stocks)
    monkeypatch.setattr(master,'master_info',lambda:{'stale':False})
    values = [('buy',4,1e10),('weak',-2,1e10),('sell',-3,1e10),('strong',-5,1e10),('liquid',-5,2e10)]
    rows = {s:{'symbol':s,'score':score,'avg_turnover':turnover,'status':'ready','buy_eligible':True,'as_of':date.today().isoformat()} for s,score,turnover in values}
    monkeypatch.setattr(scan,'get_scan',AsyncMock(return_value={'entries':rows}))
    assert [s['symbol'] for s in asyncio.run(scan.candidate_stocks(2,signal_side='sell'))] == ['liquid','strong']
    assert [s['symbol'] for s in asyncio.run(scan.candidate_stocks(1))] == ['buy']
    rows['strong']['status'] = 'stale'
    assert [s['symbol'] for s in asyncio.run(scan.candidate_stocks(10,signal_side='sell'))] == ['liquid','sell']
