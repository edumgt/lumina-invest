"""시스템 관리 대시보드 라우터."""
from fastapi import APIRouter, Depends
from app.lib.session import get_current_user
from app.services.system_info import get_system_status
from app.services import sync_scheduler
from app.services.data_cache import cache_info, is_internet_available

router = APIRouter(prefix="/api/system")


@router.get("/status")
async def system_status(user=Depends(get_current_user)):
    return await get_system_status()


@router.get("/sync-status")
async def sync_status(_user=Depends(get_current_user)):
    """Return data sync scheduler state and per-key cache freshness."""
    keys = [
        "market_indices",
        "macro_indicators",
        "sector_etfs",
        "us_stocks",
    ]
    cache_infos = {}
    for k in keys:
        info = await cache_info(k)
        cache_infos[k] = info

    online = await is_internet_available()
    return {
        "online": online,
        "scheduler": sync_scheduler.get_sync_status(),
        "cache": cache_infos,
    }


@router.post("/sync")
async def trigger_sync(_user=Depends(get_current_user)):
    """Manually trigger a full data sync now."""
    result = await sync_scheduler.sync_all(force=True)
    return result
