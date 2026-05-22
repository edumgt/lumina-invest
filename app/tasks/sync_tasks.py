"""시장 데이터 주기 동기화 Celery Beat 태스크.

기존 sync_scheduler.py 의 asyncio 기반 루프를 Celery Beat으로 보완한다.
- FastAPI 이벤트 루프와 완전히 분리되어 독립적으로 실행
- 워커 재시작·장애 시에도 Beat 스케줄에 따라 자동 재실행
- celery_app.py beat_schedule 에서 주기 설정 (1h / 24h)
"""
import asyncio
import logging

from app.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="sync.market_data", time_limit=300)
def sync_market_data() -> dict:
    """시장 지수·매크로·섹터 ETF·미국 주식 데이터를 캐싱한다 (1시간 주기)."""

    async def _async() -> dict:
        from app.database.mongo import connect_mongo, close_mongo
        from app.lib.redis_cache import connect_redis, close_redis
        from app.services.sync_scheduler import sync_all

        await connect_redis()
        await connect_mongo()
        try:
            # force=True: 오프라인 체크 없이 항상 실행
            return await sync_all(force=True)
        finally:
            await close_redis()
            await close_mongo()

    result = asyncio.run(_async())
    logger.info("[beat] sync_market_data 완료: %s", result)
    return result


@celery_app.task(name="sync.stock_candles", time_limit=600)
def sync_stock_candles() -> dict:
    """주요 종목 캔들 데이터와 퀀트 지표를 일 1회 캐싱한다."""

    async def _async() -> dict:
        from app.database.mongo import connect_mongo, close_mongo
        from app.lib.redis_cache import connect_redis, close_redis
        from app.services.sync_scheduler import _sync_stock_candles

        await connect_redis()
        await connect_mongo()
        try:
            await _sync_stock_candles()
            return {"ok": True}
        finally:
            await close_redis()
            await close_mongo()

    result = asyncio.run(_async())
    logger.info("[beat] sync_stock_candles 완료")
    return result
