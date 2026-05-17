"""MongoDB-backed data cache with internet connectivity detection.

When internet is available, callers can fetch live data and store it here.
When offline, callers read stale-but-valid cached data.
"""
import logging
from datetime import datetime, timezone, timedelta

import httpx

from app.database.mongo import get_mongo_db

logger = logging.getLogger(__name__)

# MongoDB collection that holds all cached payloads
CACHE_COL = "data_cache"

# Connectivity probe: lightweight HEAD request to Yahoo Finance
_PROBE_URL = "https://query2.finance.yahoo.com/v8/finance/chart/AAPL?range=1d&interval=1d"


async def is_internet_available() -> bool:
    """Return True if the external internet is reachable."""
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            r = await client.head(_PROBE_URL)
            return r.status_code < 500
    except Exception:
        return False


async def cache_get(key: str, max_age_hours: float = 24) -> dict | None:
    """Return cached payload if it exists and is fresher than max_age_hours.

    Returns None when the key is missing or stale.
    """
    try:
        db = get_mongo_db()
        doc = await db[CACHE_COL].find_one({"key": key})
        if not doc:
            return None
        updated_at: datetime | None = doc.get("updated_at")
        if not updated_at:
            return None
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - updated_at
        if age > timedelta(hours=max_age_hours):
            return None
        return doc.get("data")
    except Exception as e:
        logger.warning("[cache_get] %s: %s", key, e)
        return None


async def cache_set(key: str, data) -> None:
    """Upsert a cached payload."""
    try:
        db = get_mongo_db()
        now = datetime.now(timezone.utc)
        await db[CACHE_COL].update_one(
            {"key": key},
            {"$set": {"data": data, "updated_at": now}},
            upsert=True,
        )
    except Exception as e:
        logger.warning("[cache_set] %s: %s", key, e)


async def cache_info(key: str) -> dict | None:
    """Return {updated_at, age_minutes} for a cache key, or None."""
    try:
        db = get_mongo_db()
        doc = await db[CACHE_COL].find_one({"key": key}, {"data": 0})
        if not doc:
            return None
        updated_at: datetime | None = doc.get("updated_at")
        if not updated_at:
            return None
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - updated_at
        return {
            "key": key,
            "updated_at": updated_at.isoformat(),
            "age_minutes": round(age.total_seconds() / 60, 1),
        }
    except Exception:
        return None


async def ensure_cache_index() -> None:
    """Create index on the cache collection (called at startup)."""
    try:
        db = get_mongo_db()
        await db[CACHE_COL].create_index("key", unique=True)
        await db[CACHE_COL].create_index("updated_at")
    except Exception as e:
        logger.warning("[cache] index creation failed: %s", e)
