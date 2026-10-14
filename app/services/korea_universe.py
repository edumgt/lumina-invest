"""KOSPI/KOSDAQ listed equity master, from KIS public fixed-width files.

ETF/ETN/bonds/funds/rights are excluded. Listed ordinary/preferred shares,
SPACs, REIT shares and foreign/depositary shares remain searchable. Suspended,
managed and liquidation stocks stay in the master, but are not buy candidates.
"""
from __future__ import annotations
import asyncio
import hashlib
import io
import json
import logging
import re
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
import httpx
from app.services.data_cache import cache_get, cache_set

log = logging.getLogger(__name__)
DATA_DIR = Path(__file__).resolve().parents[1] / 'data'
BASE = 'https://new.real.download.dws.co.kr/common/master/'
MASTER_KEY = 'korea:master:v1'
EQUITY_GROUPS = {'ST', 'FS', 'DR', 'RT'}
STOCKS: list[dict] = []
SECTORS: list[str] = []
_metadata: dict = {}
_last_check = 0.0
_layouts = json.loads((DATA_DIR / 'korea-master-layouts.json').read_text())

def parse_sectors(blob: bytes) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        raw = z.read('idxcode.mst')
    return {line[1:5].decode('ascii'): line[5:].decode('cp949').strip() for line in raw.splitlines() if len(line) >= 6}

def parse_master(blob: bytes, market: str, sectors: dict[str, str]) -> list[dict]:
    key = market.lower()
    widths, columns = _layouts[key]['field_specs'], _layouts[key]['part2_columns']
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        raw = z.read(f'{key}_code.mst')
    rows = []
    for line in raw.splitlines():
        if len(line) != 61 + sum(widths):
            raise ValueError(f'{market}: unexpected master record length {len(line)}')
        code = line[:9].decode('ascii').strip()
        if not re.fullmatch('[0-9A-Z]{6}', code):
            continue
        fields, offset = {}, 61
        for width, col in zip(widths, columns):
            fields[col.replace(' ', '')] = line[offset:offset+width].decode('cp949').strip()
            offset += width
        group = fields.get('그룹코드') or fields.get('증권그룹구분코드')
        if group not in EQUITY_GROUPS:
            continue
        mid = fields['지수업종중분류' if key == 'kospi' else '지수업종중분류코드']
        big = fields['지수업종대분류' if key == 'kospi' else '지수업종대분류코드']
        sector = sectors.get(mid) if mid != '0000' else sectors.get(big) if big != '0000' else None
        def pick(*keys):
            return next((fields[k] for k in keys if k in fields), '')
        suspended = pick('거래정지', '거래정지여부') == 'Y'
        managed = pick('관리종목', '관리종목여부') == 'Y'
        liquidation = pick('정리매매', '정리매매여부') == 'Y'
        cap = pick('시가총액', '전일기준시가총액(억)')
        rows.append({'symbol': f'{code}.{"KS" if key == "kospi" else "KQ"}', 'code': code,
                     'isin': line[9:21].decode('ascii').strip(), 'name': line[21:61].decode('cp949').strip(),
                     'market': market.upper(), 'sector': sector or ('리츠' if group == 'RT' else f'{market.upper()} 업종미분류'),
                     'industry_code': mid, 'security_type': group, 'preferred': pick('우선주', '우선주구분코드') not in ('', '0'),
                     'spac': pick('SPAC', '기업인수목적회사여부') == 'Y',
                     'suspended': suspended, 'managed': managed, 'liquidation': liquidation,
                     'buy_eligible': not (suspended or managed or liquidation), 'order_supported':code.isdigit(),
                     'market_cap_eok': int(cap) if cap.isdigit() else None})
    return rows

def apply_master(doc: dict) -> None:
    global _metadata
    rows = doc.get('stocks', [])
    if not rows or len({r['symbol'] for r in rows}) != len(rows):
        raise ValueError('empty/duplicate equity master')
    if {r['market'] for r in rows} != {'KOSPI', 'KOSDAQ'}:
        raise ValueError('both markets are required')
    STOCKS[:] = sorted([{**r, 'order_supported':r.get('order_supported',r['code'].isdigit())} for r in rows], key=lambda x: (x['market'], x['code']))
    SECTORS[:] = sorted({r['sector'] for r in rows})
    _metadata = {k: v for k, v in doc.items() if k != 'stocks'}

def master_info() -> dict:
    age = None
    try: age = (datetime.now(timezone.utc) - datetime.fromisoformat(_metadata['updated_at'])).total_seconds()/3600
    except (ValueError, KeyError): pass
    return {**_metadata, 'count': len(STOCKS), 'markets': {m: sum(r['market']==m for r in STOCKS) for m in ['KOSPI','KOSDAQ']},
            'sectors': list(SECTORS), 'stale': age is None or age > 48,
            'scope': 'KOSPI/KOSDAQ equities; excludes ETF/ETN/bonds/funds/rights'}

async def refresh_master(force: bool = False) -> dict:
    global _last_check
    if not force and STOCKS and time.monotonic()-_last_check < 900:
        return master_info()
    _last_check = time.monotonic()
    cached = await cache_get(MASTER_KEY, max_age_hours=24 if not force else 0)
    if cached and not force:
        apply_master(cached)
        return master_info()
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            responses = await asyncio.gather(*(client.get(BASE+name) for name in ['kospi_code.mst.zip','kosdaq_code.mst.zip','idxcode.mst.zip']))
        for r in responses: r.raise_for_status()
        sectors = parse_sectors(responses[2].content)
        rows = parse_master(responses[0].content, 'KOSPI', sectors)+parse_master(responses[1].content, 'KOSDAQ', sectors)
        counts = {m: sum(r['market']==m for r in rows) for m in ['KOSPI','KOSDAQ']}
        if any(counts[m] < 500 for m in counts): raise ValueError('incomplete master download')
        doc = {'stocks':rows, 'source':'KIS official public master', 'updated_at':datetime.now(timezone.utc).isoformat(),
               'revision':hashlib.sha256(json.dumps(rows,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:16]}
        apply_master(doc)
        await cache_set(MASTER_KEY, doc)
    except Exception as exc:
        log.warning('Equity master refresh failed: %s', type(exc).__name__)
        old = await cache_get(MASTER_KEY, max_age_hours=24*30)
        if old: apply_master(old)
        return {**master_info(), 'refresh_error':type(exc).__name__}
    return master_info()

bootstrap = DATA_DIR / 'korea-universe.json'
if bootstrap.exists(): apply_master(json.loads(bootstrap.read_text()))
