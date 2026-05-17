"""거시경제 지표 · 산업 분석 · 재무 분석 API."""
from fastapi import APIRouter, Depends, Query
from app.lib.session import get_current_user
from app.services.stock import get_candles, _yahoo_chart
from app.services.data_cache import cache_get, cache_set
from app.services.sync_scheduler import (
    KEY_MACRO_INDICATORS, KEY_SECTOR_ETFS, KEY_US_STOCKS,
    MACRO_SYMBOLS, SECTOR_ETFS, US_STOCKS,
)

router = APIRouter(prefix="/api/macro")


async def _get_latest(symbol: str, unit: str) -> dict:
    data = await _yahoo_chart(symbol, "1d", "5d")
    if not data:
        return {"symbol": symbol, "error": "데이터 없음"}
    meta = data.get("meta", {})
    price = meta.get("regularMarketPrice") or meta.get("previousClose", 0)
    prev  = meta.get("previousClose") or meta.get("chartPreviousClose") or price
    chg   = price - prev
    chg_p = (chg / prev * 100) if prev else 0
    return {
        "symbol":   symbol,
        "price":    price,
        "change":   round(chg, 4),
        "change_p": round(chg_p, 2),
        "unit":     unit,
    }


@router.get("/indicators")
async def macro_indicators(_user=Depends(get_current_user)):
    """거시경제 지표 현황 (금리·물가·유가·환율·주가지수). MongoDB cache-first."""
    import asyncio
    cached = await cache_get(KEY_MACRO_INDICATORS, max_age_hours=2)
    if cached is not None:
        return {"indicators": cached, "from_cache": True}

    tasks = [_get_latest(m["symbol"], m["unit"]) for m in MACRO_SYMBOLS]
    results = await asyncio.gather(*tasks)
    enriched = [{**m, **r} for m, r in zip(MACRO_SYMBOLS, results)]
    await cache_set(KEY_MACRO_INDICATORS, enriched)
    return {"indicators": enriched, "from_cache": False}


@router.get("/industry")
async def macro_industry(_user=Depends(get_current_user)):
    """미국 섹터 ETF 기반 산업 분석. MongoDB cache-first."""
    import asyncio
    cached = await cache_get(KEY_SECTOR_ETFS, max_age_hours=2)
    if cached is not None:
        return {"sectors": cached, "from_cache": True}

    tasks = [_get_latest(s["symbol"], "") for s in SECTOR_ETFS]
    results = await asyncio.gather(*tasks)
    enriched = [{**s, **r} for s, r in zip(SECTOR_ETFS, results)]
    enriched.sort(key=lambda x: x.get("change_p", 0), reverse=True)
    await cache_set(KEY_SECTOR_ETFS, enriched)
    return {"sectors": enriched, "from_cache": False}


@router.get("/us-stocks")
async def macro_us_stocks(_user=Depends(get_current_user)):
    """미국 주요 종목 시세. MongoDB cache-first."""
    import asyncio
    cached = await cache_get(KEY_US_STOCKS, max_age_hours=2)
    if cached is not None:
        return {"stocks": cached, "from_cache": True}

    tasks = [_get_latest(s["symbol"], "USD") for s in US_STOCKS]
    results = await asyncio.gather(*tasks)
    enriched = [{**s, **r} for s, r in zip(US_STOCKS, results)]
    await cache_set(KEY_US_STOCKS, enriched)
    return {"stocks": enriched, "from_cache": False}


@router.get("/fundamental")
async def macro_fundamental(
    symbol: str = Query("005930.KS"),
    _user=Depends(get_current_user),
):
    """
    재무제표 기반 밸류에이션 지표 (DCF/EVA/FCF 개념 설명 포함).

    실제 재무 데이터는 Yahoo Finance summary 메타에서 추출.
    """
    data = await _yahoo_chart(symbol, "1d", "1d")
    if not data:
        return {"symbol": symbol, "error": "데이터 없음"}

    meta = data.get("meta", {})
    price = meta.get("regularMarketPrice", 0)

    # Yahoo Finance는 상세 재무제표를 v8/chart API에서 직접 제공하지 않으므로
    # 실무에서는 yfinance .info 또는 별도 재무 API를 사용합니다.
    # 여기서는 개념 설명 + 목업 지표를 반환합니다.
    dcf_mock = round(price * 1.15, 2)  # 내재가치 (단순 성장률 15% 가정)
    fcf_yield = round(100 / max(price, 1) * 1000, 2)  # 모의 FCF 수익률

    return {
        "symbol": symbol,
        "price":  price,
        "valuation": {
            "DCF_intrinsic_value": dcf_mock,
            "FCF_yield_pct":       fcf_yield,
            "concept": {
                "DCF":  "미래 현금흐름을 현재가치로 할인 (Discounted Cash Flow)",
                "EVA":  "경제적 부가가치 = NOPAT - 자본비용 (Economic Value Added)",
                "FCF":  "잉여현금흐름 = 영업현금흐름 - 설비투자 (Free Cash Flow)",
            },
        },
        "note": "실제 재무제표 연동은 DART API 또는 yfinance .info를 활용하세요.",
    }
