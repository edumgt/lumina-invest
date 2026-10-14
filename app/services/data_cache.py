"""PostgreSQL-backed data cache with internet connectivity detection.

When internet is available, callers can fetch live data and store it here.
When offline, callers read stale-but-valid cached data.
"""
import logging
from datetime import datetime, timezone, timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database.postgres import get_session_factory
from app.models import DataCache

logger = logging.getLogger(__name__)

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
        session_factory = get_session_factory()
        async with session_factory() as db:
            result = await db.execute(select(DataCache).where(DataCache.key == key))
            row = result.scalar_one_or_none()
        if not row:
            return None
        updated_at = row.updated_at
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - updated_at
        if age > timedelta(hours=max_age_hours):
            return None
        return row.data
    except Exception as e:
        logger.warning("[cache_get] %s: %s", key, e)
        return None


async def cache_set(key: str, data) -> None:
    """Upsert a cached payload."""
    try:
        session_factory = get_session_factory()
        async with session_factory() as db:
            stmt = pg_insert(DataCache).values(key=key, data=data, updated_at=func.now())
            stmt = stmt.on_conflict_do_update(
                index_elements=[DataCache.key], set_={"data": data, "updated_at": func.now()}
            )
            await db.execute(stmt)
            await db.commit()
    except Exception as e:
        logger.warning("[cache_set] %s: %s", key, e)


async def cache_info(key: str) -> dict | None:
    """Return {updated_at, age_minutes} for a cache key, or None."""
    try:
        session_factory = get_session_factory()
        async with session_factory() as db:
            result = await db.execute(select(DataCache.updated_at).where(DataCache.key == key))
            updated_at = result.scalar_one_or_none()
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


async def cache_get_many(keys: list[str], max_age_hours: float = 24) -> dict:
    """Fetch persisted analysis in one DB query rather than one query per equity."""
    if not keys:
        return {}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    try:
        async with get_session_factory()() as db:
            result = await db.execute(select(DataCache.key, DataCache.data).where(
                DataCache.key.in_(keys), DataCache.updated_at >= cutoff))
            return dict(result.all())
    except Exception as exc:
        logger.warning('Bulk cache read unavailable: %s', type(exc).__name__)
        return {}
