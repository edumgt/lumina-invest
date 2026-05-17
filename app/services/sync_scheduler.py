"""Background data sync scheduler.

Runs an asyncio loop that wakes every SYNC_INTERVAL_SEC seconds.
When the internet is reachable it fetches all external data sources
and stores the results in MongoDB via data_cache so the app stays
functional when the network goes down.

Start/stop is managed by main.py lifespan.
"""
import asyncio
import logging
from datetime import datetime, timezone

from app.services.data_cache import cache_set, is_internet_available
from app.services.stock import (
    _yahoo_chart,
    get_market_summary,
    get_quant_indicators,
    QUANT_STOCKS,
    MARKET_INDICES,
)

logger = logging.getLogger(__name__)

SYNC_INTERVAL_SEC = 3600  # 1 hour for live market data
CANDLE_INTERVAL_SEC = 86400  # 24 hours for historical candles

# Keys used across the codebase — keep in sync with routes
KEY_MARKET_INDICES = "market_indices"
KEY_MACRO_INDICATORS = "macro_indicators"
KEY_SECTOR_ETFS = "sector_etfs"
KEY_US_STOCKS = "us_stocks"


# ── Macro symbols (mirrors macro.py) ─────────────────────────────────────────

MACRO_SYMBOLS = [
    {"symbol": "^TNX",     "name": "미 국채 10년 금리",   "unit": "%"},
    {"symbol": "^IRX",     "name": "미 국채 3개월 금리",  "unit": "%"},
    {"symbol": "CL=F",     "name": "WTI 원유",            "unit": "USD/bbl"},
    {"symbol": "GC=F",     "name": "금 선물",             "unit": "USD/oz"},
    {"symbol": "KRW=X",    "name": "USD/KRW 환율",        "unit": "KRW"},
    {"symbol": "DX-Y.NYB", "name": "달러 인덱스(DXY)",    "unit": ""},
    {"symbol": "^VIX",     "name": "VIX 공포지수",        "unit": ""},
    {"symbol": "^GSPC",    "name": "S&P 500",             "unit": ""},
    {"symbol": "^KS11",    "name": "KOSPI",               "unit": ""},
]

SECTOR_ETFS = [
    {"symbol": "XLK",  "name": "기술",     "sector": "Technology"},
    {"symbol": "XLF",  "name": "금융",     "sector": "Financials"},
    {"symbol": "XLE",  "name": "에너지",   "sector": "Energy"},
    {"symbol": "XLV",  "name": "헬스케어", "sector": "Health Care"},
    {"symbol": "XLI",  "name": "산업재",   "sector": "Industrials"},
    {"symbol": "XLY",  "name": "소비재",   "sector": "Consumer Disc."},
    {"symbol": "XLP",  "name": "필수소비", "sector": "Consumer Staples"},
    {"symbol": "XLB",  "name": "소재",     "sector": "Materials"},
    {"symbol": "XLRE", "name": "리츠",     "sector": "Real Estate"},
    {"symbol": "XLU",  "name": "유틸리티", "sector": "Utilities"},
    {"symbol": "XLC",  "name": "통신",     "sector": "Communication"},
]

# US blue-chip stocks tracked in the US dashboard
US_STOCKS = [
    {"symbol": "AAPL",  "name": "Apple",           "sector": "Technology"},
    {"symbol": "MSFT",  "name": "Microsoft",        "sector": "Technology"},
    {"symbol": "GOOGL", "name": "Alphabet",         "sector": "Technology"},
    {"symbol": "AMZN",  "name": "Amazon",           "sector": "Consumer Disc."},
    {"symbol": "NVDA",  "name": "NVIDIA",           "sector": "Technology"},
    {"symbol": "TSLA",  "name": "Tesla",            "sector": "Consumer Disc."},
    {"symbol": "META",  "name": "Meta",             "sector": "Communication"},
    {"symbol": "BRK-B", "name": "Berkshire Hathaway", "sector": "Financials"},
    {"symbol": "JPM",   "name": "JPMorgan",         "sector": "Financials"},
    {"symbol": "V",     "name": "Visa",             "sector": "Financials"},
]

# ── State ────────────────────────────────────────────────────────────────────

_sync_task: asyncio.Task | None = None
_last_sync: datetime | None = None
_last_sync_ok: bool = False
_syncing: bool = False


def get_sync_status() -> dict:
    return {
        "running": _sync_task is not None and not _sync_task.done(),
        "syncing": _syncing,
        "last_sync": _last_sync.isoformat() if _last_sync else None,
        "last_sync_ok": _last_sync_ok,
    }


# ── Sync helpers ─────────────────────────────────────────────────────────────

async def _yahoo_latest(symbol: str, unit: str) -> dict:
    data = await _yahoo_chart(symbol, "1d", "5d")
    if not data:
        return {"symbol": symbol, "error": "데이터 없음", "unit": unit}
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


async def _sync_market_indices() -> None:
    indices = await get_market_summary()
    await cache_set(KEY_MARKET_INDICES, indices)
    logger.info("[sync] market_indices: %d items", len(indices))


async def _sync_macro_indicators() -> None:
    tasks = [_yahoo_latest(m["symbol"], m["unit"]) for m in MACRO_SYMBOLS]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    enriched = []
    for m, r in zip(MACRO_SYMBOLS, results):
        if isinstance(r, Exception):
            enriched.append({**m, "error": str(r)})
        else:
            enriched.append({**m, **r})
    await cache_set(KEY_MACRO_INDICATORS, enriched)
    logger.info("[sync] macro_indicators: %d items", len(enriched))


async def _sync_sector_etfs() -> None:
    tasks = [_yahoo_latest(s["symbol"], "") for s in SECTOR_ETFS]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    enriched = []
    for s, r in zip(SECTOR_ETFS, results):
        if isinstance(r, Exception):
            enriched.append({**s, "error": str(r)})
        else:
            enriched.append({**s, **r})
    enriched.sort(key=lambda x: x.get("change_p", 0), reverse=True)
    await cache_set(KEY_SECTOR_ETFS, enriched)
    logger.info("[sync] sector_etfs: %d items", len(enriched))


async def _sync_us_stocks() -> None:
    tasks = [_yahoo_latest(s["symbol"], "USD") for s in US_STOCKS]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    enriched = []
    for s, r in zip(US_STOCKS, results):
        if isinstance(r, Exception):
            enriched.append({**s, "error": str(r)})
        else:
            enriched.append({**s, **r})
    await cache_set(KEY_US_STOCKS, enriched)
    logger.info("[sync] us_stocks: %d items", len(enriched))


async def _sync_stock_candles() -> None:
    """Cache 1y daily candles for all quant stocks (heavy — runs daily)."""
    from app.services.stock import get_candles
    for stock in QUANT_STOCKS:
        sym = stock["symbol"]
        try:
            data = await get_candles(sym, period="1y", interval="1d")
            if data.get("candles"):
                await cache_set(f"candles:{sym}:1y:1d", data)
                logger.info("[sync] candles %s: %d bars", sym, len(data["candles"]))
        except Exception as e:
            logger.warning("[sync] candles %s failed: %s", sym, e)

    # Also cache quant indicators for signal screening
    for stock in QUANT_STOCKS:
        sym = stock["symbol"]
        try:
            data = await get_quant_indicators(sym, period="1y")
            if not data.get("error"):
                await cache_set(f"indicators:{sym}:1y", data)
        except Exception as e:
            logger.warning("[sync] indicators %s failed: %s", sym, e)


# ── Public sync entrypoint ────────────────────────────────────────────────────

async def sync_all(force: bool = False) -> dict:
    """Run a full sync cycle.  Returns result summary."""
    global _last_sync, _last_sync_ok, _syncing

    if _syncing:
        return {"ok": False, "reason": "already_syncing"}

    online = await is_internet_available()
    if not online and not force:
        logger.info("[sync] offline — skipping")
        return {"ok": False, "reason": "offline"}

    _syncing = True
    errors = []
    try:
        for fn in (
            _sync_market_indices,
            _sync_macro_indicators,
            _sync_sector_etfs,
            _sync_us_stocks,
        ):
            try:
                await fn()
            except Exception as e:
                logger.exception("[sync] %s failed: %s", fn.__name__, e)
                errors.append(f"{fn.__name__}: {e}")

        # Candles are cached separately — run them too but don't block result
        asyncio.create_task(_sync_stock_candles())

        _last_sync = datetime.now(timezone.utc)
        _last_sync_ok = not errors
        return {"ok": True, "errors": errors, "synced_at": _last_sync.isoformat()}
    finally:
        _syncing = False


# ── Background scheduler loop ────────────────────────────────────────────────

async def _scheduler_loop() -> None:
    logger.info("[sync] scheduler started (interval=%ds)", SYNC_INTERVAL_SEC)
    # Immediate first sync at startup
    await asyncio.sleep(5)
    while True:
        try:
            result = await sync_all()
            logger.info("[sync] cycle done: %s", result)
        except Exception:
            logger.exception("[sync] unexpected scheduler error")
        await asyncio.sleep(SYNC_INTERVAL_SEC)


def start_sync_scheduler() -> None:
    global _sync_task
    if _sync_task is None or _sync_task.done():
        _sync_task = asyncio.create_task(_scheduler_loop())
        logger.info("[sync] scheduler task created")


def stop_sync_scheduler() -> None:
    global _sync_task
    if _sync_task and not _sync_task.done():
        _sync_task.cancel()
    _sync_task = None
    logger.info("[sync] scheduler stopped")
