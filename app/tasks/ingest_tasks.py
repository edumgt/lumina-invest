"""데이터 인제스트 Celery 태스크.

HTTP 워커를 수 분간 블로킹하던 인제스트 작업을 백그라운드로 분리한다.
각 태스크는 독립 이벤트 루프에서 DB 커넥션을 직접 관리한다.
"""
import asyncio
import logging

from app.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="ingest.financial",
    max_retries=0,
    time_limit=600,
)
def financial_ingest_task(self) -> dict:
    """CSV 금융 데이터(개인CB·기업CB·금융상품) 전체 인제스트."""

    async def _async() -> dict:
        from app.database.postgres import connect_postgres, close_postgres, get_session_factory
        from app.lib.redis_cache import connect_redis, close_redis
        from app.services.financial_ingest import run_full_ingest

        await connect_redis()
        await connect_postgres()
        log: list[str] = []
        try:
            session_factory = get_session_factory()
            async with session_factory() as db:
                result = await run_full_ingest(db, log)
            return {"ok": True, "result": result, "log": log}
        finally:
            await close_redis()
            await close_postgres()

    return asyncio.run(_async())


@celery_app.task(
    bind=True,
    name="ingest.crawl_auto",
    max_retries=1,
    time_limit=600,
)
def auto_crawl_task(self) -> dict:
    """등록된 URL 목록 자동 크롤링 → Qdrant RAG 인제스트."""

    async def _async() -> dict:
        from app.config import settings
        from app.database.postgres import connect_postgres, close_postgres, get_session_factory
        from app.lib.redis_cache import connect_redis, close_redis
        from app.lib.llm_client import get_llm_client
        from app.services.crawl import run_auto_crawl

        await connect_redis()
        await connect_postgres()
        log: list[str] = []
        try:
            ollama = get_llm_client()
            session_factory = get_session_factory()
            async with session_factory() as db:
                result = await run_auto_crawl(db, ollama, log)
            return {"ok": True, "result": result, "log": log}
        finally:
            await close_redis()
            await close_postgres()

    try:
        return asyncio.run(_async())
    except Exception as exc:
        logger.exception("자동 크롤링 태스크 실패: %s", exc)
        raise self.retry(exc=exc, countdown=30)


@celery_app.task(
    bind=True,
    name="ingest.crawl_url",
    max_retries=1,
    time_limit=120,
)
def url_crawl_task(self, url: str) -> dict:
    """단일 URL 크롤링 → Qdrant RAG 인제스트."""

    async def _async() -> dict:
        from app.config import settings
        from app.database.postgres import connect_postgres, close_postgres, get_session_factory
        from app.lib.redis_cache import connect_redis, close_redis
        from app.lib.llm_client import get_llm_client
        from app.services.crawl import crawl_url

        await connect_redis()
        await connect_postgres()
        log: list[str] = []
        try:
            ollama = get_llm_client()
            session_factory = get_session_factory()
            async with session_factory() as db:
                chunks = await crawl_url(url, db, ollama, log)
            return {"ok": True, "chunks": chunks, "log": log}
        finally:
            await close_redis()
            await close_postgres()

    try:
        return asyncio.run(_async())
    except Exception as exc:
        logger.exception("URL 크롤링 태스크 실패 (%s): %s", url, exc)
        raise self.retry(exc=exc, countdown=10)


@celery_app.task(
    bind=True,
    name="ingest.translation",
    max_retries=0,
    time_limit=900,
)
def translation_ingest_task(
    self,
    data_type: str = "labeled",
    categories: list | None = None,
    languages: list | None = None,
    max_docs: int = 0,
) -> dict:
    """다국어 번역 데이터 ZIP → Qdrant translation_docs 인제스트."""

    async def _async() -> dict:
        from app.config import settings
        from app.lib.redis_cache import connect_redis, close_redis
        from app.lib.llm_client import get_llm_client
        from app.services.translation_ingest import run_translation_ingest

        await connect_redis()
        log: list[str] = []
        try:
            ollama = get_llm_client()
            result = await run_translation_ingest(
                ollama, log,
                data_type=data_type,
                categories=categories or None,
                languages=languages or None,
                max_docs=max_docs,
            )
            return {"ok": True, "result": result, "log": log}
        finally:
            await close_redis()

    return asyncio.run(_async())
