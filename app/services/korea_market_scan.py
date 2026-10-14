"""Checkpointed equity scan. It collects/analyses data and never places orders."""
from __future__ import annotations
import asyncio
import uuid
import httpx
from datetime import date, datetime, timedelta, timezone
from app.config import settings
from app.services.data_cache import cache_get, cache_set
from app.services import korea_universe as master
from app.services import ohlcv_store

SCAN_KEY = 'korea:scan:v1'
LOCK_KEY = 'lumina:korea:scan:lock'
KST = timezone(timedelta(hours=9))


def usable_entry(row: dict, today: date | None = None) -> bool:
    if row.get('status') != 'ready' or not row.get('buy_eligible'):
        return False
    try:
        age = ((today or datetime.now(KST).date()) - date.fromisoformat(row['as_of'])).days
        return 0 <= age <= settings.QUANT_KOREA_MAX_DATA_AGE_DAYS and row.get('avg_turnover', 0) >= settings.QUANT_KOREA_MIN_TURNOVER_KRW
    except (KeyError, TypeError, ValueError):
        return False


async def get_scan() -> dict:
    doc = await cache_get(SCAN_KEY, max_age_hours=24*30) or {}
    valid = {s['symbol'] for s in master.STOCKS}
    entries = {k:v for k,v in (doc.get('entries') or {}).items() if k in valid}
    counts = {state:sum(e.get('status')==state for e in entries.values()) for state in ['ready','partial','stale','unavailable']}
    return {**doc, 'entries':entries, 'master':master.master_info(), 'coverage':{
        'total':len(valid), 'processed':len(entries), **counts,
        'candidate_ready':sum(usable_entry(e) for e in entries.values()),
        'not_processed':len(valid)-len(entries)}}


async def candidate_stocks(limit: int | None = None) -> list[dict]:
    doc = await get_scan()
    if master.master_info().get('stale'):
        return []
    index = {s['symbol']:s for s in master.STOCKS}
    rows = [e for e in doc['entries'].values() if e.get('symbol') in index and usable_entry(e) and index[e['symbol']].get('buy_eligible') and index[e['symbol']].get('order_supported',True)]
    # Rank only observed, fresh data; master order never becomes a buy fallback.
    rows.sort(key=lambda e: (e.get('score',0), e.get('avg_turnover',0), e['symbol']), reverse=True)
    return [index[r['symbol']] for r in rows[:limit or settings.QUANT_KOREA_CANDIDATE_POOL]]


async def analyse_stock(stock: dict) -> dict:
    from app.services.stock import calculate_quant_indicators
    from app.services.investment_research import screen_pattern
    from app.services.sector_indicators import _closes_by_date, _ret, _TRADING_DAYS
    now = datetime.now(KST)
    end = now.date()
    start = end-timedelta(days=800)
    symbol = stock['symbol']
    async def read():
        for attempt in range(3):
            try: return await ohlcv_store.read_store(stock['code'], start, end)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in (429,502,503,504) or attempt==2: raise
                await asyncio.sleep(0.5 * (attempt+1))
    candles = await read() if ohlcv_store.configured() else None
    source = 'system_ohlcv'
    last = max((datetime.fromtimestamp(c['time'],timezone.utc).date() for c in candles or []),default=None)
    missing_or_stale = not candles or last is None or (end-last).days > settings.QUANT_KOREA_MAX_DATA_AGE_DAYS or (now.hour >= 16 and last < end)
    if missing_or_stale and settings.QUANT_KOREA_INGEST_MISSING:
        fetch_start = last+timedelta(days=1) if last else start
        if ohlcv_store.can_ingest():
            ingested = await ohlcv_store.ingest_store(symbol, fetch_start, end)
            if ingested: candles = await read()
        last = max((datetime.fromtimestamp(c['time'],timezone.utc).date() for c in candles or []),default=None)
        if not candles or last is None or (end-last).days > settings.QUANT_KOREA_MAX_DATA_AGE_DAYS:
            extra = await ohlcv_store.fetch_yahoo(symbol, fetch_start, end)
            if extra:
                candles = (candles or []) + extra
                source = 'yahoo_fallback'
    candles = sorted({c['time']:c for c in (candles or []) if c.get('close') and c.get('close') > 0}.values(),key=lambda c:c['time'])
    # Daily analysis uses completed bars; today's open-session bar is not a closed candle.
    if now.hour < 16:
        candles = [c for c in candles if datetime.fromtimestamp(c['time'],timezone.utc).date() < end]
    if len(candles) < 60:
        return {'symbol':symbol, 'status':'partial' if candles else 'unavailable', 'bars':len(candles), 'buy_eligible':False, 'source':source}
    ind = calculate_quant_indicators(symbol, {'candles':candles,'source':source})
    closes = _closes_by_date(candles)
    values = list(closes.values())
    as_of = max(closes)
    age = (end-date.fromisoformat(as_of)).days
    state = 'ready' if 0 <= age <= settings.QUANT_KOREA_MAX_DATA_AGE_DAYS else 'stale'
    turnover = sum(float(c.get('volume') or 0)*float(c['close']) for c in candles[-20:])/20
    member = {**stock, '_closes':dict(list(closes.items())[-504:]), 'cap':stock.get('market_cap_eok'),
              'price':ind['current_price'], 'price_as_of':as_of, 'candle_source':source,
              'rsi14':ind['current_rsi'], 'above_ma20':values[-1]>ind['ma20'][-1], 'above_ma60':values[-1]>ind['ma60'][-1],
              'errors':[] if state=='ready' else ['일봉이 오래되었습니다']}
    member.update({f'ret_{k}':_ret(values,d) for k,d in _TRADING_DAYS.items()})
    fund = await cache_get(f'fundamentals:v6:{symbol}',max_age_hours=168)
    if fund:
        member.update({k:v for k,v in fund.items() if k not in ['name','symbol','sector','price','cap']})
    else: member['errors'].append('재무정보 미수집 — 가격 지표만 계산')
    patterns = {model:screen_pattern(candles,model) for model in ['lightgbm','rsi','ma','bollinger']}
    for result in patterns.values():
        result.update({'symbol':symbol,'name':stock['name'],'sector':stock['sector'],'price':ind['current_price'],
                       'change_pct':(values[-1]/values[-2]-1)*100,'source':source,'as_of':as_of})
    await cache_set(f'korea:member:{symbol}',member)
    await cache_set(f'korea:indicator:{symbol}',ind)
    return {**{k:stock[k] for k in ['symbol','name','sector','market','buy_eligible']}, 'buy_eligible':stock.get('buy_eligible',False) and stock.get('order_supported',True), 'status':state,'bars':len(candles),'source':source,
            'as_of':as_of,'score':ind['signal'].get('score',0),'avg_turnover':turnover,'patterns':patterns}


async def run_scan_chunk(batch_size: int | None = None, force: bool = False) -> dict:
    from app.lib.redis_cache import get_redis
    redis = get_redis()
    token = str(uuid.uuid4())
    if not await redis.set(LOCK_KEY,token,nx=True,ex=180):
        return {'status':'already_running'}
    try:
        await master.refresh_master()
        doc = await cache_get(SCAN_KEY,max_age_hours=24*30) or {}
        now = datetime.now(KST)
        day = now.date().isoformat()+(':closed' if now.hour >= 17 else ':session')
        revision = master.master_info().get('revision')
        if force or doc.get('day') != day or doc.get('revision') != revision:
            doc = {'day':day,'revision':revision,'cursor':0,'entries':{},'started_at':datetime.now(timezone.utc).isoformat()}
        retry = bool(doc.get('complete'))
        start = doc.get('retry_cursor',0) if retry else doc.get('cursor',0)
        count = max(1,min(batch_size or settings.QUANT_KOREA_BATCH_SIZE,200))
        if retry:
            if not doc.get('retry_targets'):
                if doc.get('retry_round',0) >= 3 or datetime.now(timezone.utc).timestamp() < doc.get('retry_after',0):
                    return (await get_scan())['coverage']
                doc['retry_targets'] = [s['symbol'] for s in master.STOCKS if doc['entries'].get(s['symbol'],{}).get('status') in ('stale','unavailable')]
                doc['retry_round'] = doc.get('retry_round',0)+1
                start = 0
                if not doc['retry_targets']: return (await get_scan())['coverage']
            index = {s['symbol']:s for s in master.STOCKS}
            targets = [index[s] for s in doc['retry_targets'][start:start+count]]
        else:
            targets = list(master.STOCKS[start:start+count])
        sem = asyncio.Semaphore(max(1,min(settings.QUANT_KOREA_CONCURRENCY,8)))
        async def one(s):
            async with sem:
                try: return await asyncio.wait_for(analyse_stock(s),timeout=12)
                except Exception as exc: return {'symbol':s['symbol'],'status':'unavailable','buy_eligible':False,'error':type(exc).__name__,'http_status':getattr(getattr(exc,'response',None),'status_code',None)}
        # One chunk remains below the lease duration, even on provider timeouts.
        tasks = [asyncio.create_task(one(s)) for s in targets]
        done, pending = await asyncio.wait(tasks, timeout=100) if tasks else (set(),set())
        for t in pending: t.cancel()
        if pending: await asyncio.gather(*pending,return_exceptions=True)
        for s,t in zip(targets,tasks):
            doc['entries'][s['symbol']] = t.result() if t in done else {'symbol':s['symbol'],'status':'unavailable','buy_eligible':False,'error':'chunk_deadline'}
        if retry:
            doc['retry_cursor'] = start+len(targets)
            if doc['retry_cursor'] >= len(doc['retry_targets']):
                doc['retry_targets'] = []
                doc['retry_after'] = datetime.now(timezone.utc).timestamp()+3600
        else:
            doc.update(cursor=start+len(targets),complete=start+len(targets)>=len(master.STOCKS))
            if doc['complete']: doc['retry_after'] = datetime.now(timezone.utc).timestamp()+3600
        doc['updated_at'] = datetime.now(timezone.utc).isoformat()
        await cache_set(SCAN_KEY,doc)
        persisted = await cache_get(SCAN_KEY,max_age_hours=24*30)
        if not persisted or persisted.get('updated_at') != doc['updated_at']: raise RuntimeError('scan checkpoint was not persisted')
        return (await get_scan())['coverage']
    finally:
        await redis.eval("if redis.call('get',KEYS[1]) == ARGV[1] then return redis.call('del',KEYS[1]) else return 0 end",1,LOCK_KEY,token)
