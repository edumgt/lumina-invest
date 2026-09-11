import os
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.postgres import get_pg_session
from app.lib.session import get_current_user
from app.lib.llm_client import get_llm_client
from app.models import CrawledDoc
from app.services.financial_ingest import run_full_ingest
from app.services.crawl import run_auto_crawl, crawl_url, crawl_naver_stock, _chunk_text, _store_qdrant
from app.services.translation_ingest import (
    run_translation_ingest,
    translation_search,
    TRANSLATION_COLLECTION,
)

router = APIRouter(prefix="/api")


@router.post("/ingest/financial")
async def ingest_financial(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_pg_session),
):
    log: list[str] = []
    result = await run_full_ingest(db, log)
    return {"ok": True, "result": result, "log": log}


@router.post("/ingest/crawl/auto")
async def crawl_auto(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_pg_session),
):
    ollama = get_llm_client()
    log: list[str] = []
    result = await run_auto_crawl(db, ollama, log)
    return {"ok": True, "result": result, "log": log}


class CrawlUrlBody(BaseModel):
    url: str


@router.post("/ingest/crawl/url")
async def crawl_manual(
    body: CrawlUrlBody,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_pg_session),
):
    ollama = get_llm_client()
    log: list[str] = []
    chunks = await crawl_url(body.url, db, ollama, log)
    return {"ok": True, "chunks": chunks, "log": log}


class CrawlNaverBody(BaseModel):
    code: str  # 6-digit stock code (e.g. 005930)


@router.post("/ingest/crawl/naver")
async def crawl_naver(
    body: CrawlNaverBody,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_pg_session),
):
    """네이버 금융 종목 페이지 전용 크롤링."""
    ollama = get_llm_client()
    log: list[str] = []
    chunks = await crawl_naver_stock(body.code, db, ollama, log)
    message = "네이버 주식 크롤링이 완료되었습니다." if chunks > 0 else "네이버 주식 크롤링 결과가 없습니다."
    return {"ok": True, "chunks": chunks, "message": message}


@router.post("/ingest/local-docs")
async def ingest_local_docs(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_pg_session),
):
    """data/raw/ 하위 로컬 Markdown 문서를 Qdrant RAG에 인제스트."""
    ollama = get_llm_client()
    log: list[str] = []
    total = 0

    raw_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "data", "raw")
    )

    for dirpath, _, files in os.walk(raw_root):
        for fname in files:
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(dirpath, fname)
            with open(fpath, encoding="utf-8") as f:
                content = f.read()
            if len(content) < 50:
                continue

            chunks = _chunk_text(content)
            rel_path = os.path.relpath(fpath, raw_root)
            url = f"local://{rel_path}"
            source = f"local:{rel_path}"
            meta = {"url": url, "title": fname, "source": source}
            stored = await _store_qdrant(chunks, meta, ollama)
            stmt = pg_insert(CrawledDoc).values(url=url, title=fname, content=content[:5000], source=source)
            stmt = stmt.on_conflict_do_update(
                index_elements=[CrawledDoc.url],
                set_={"title": fname, "content": content[:5000], "source": source},
            )
            await db.execute(stmt)
            await db.commit()
            log.append(f"✓ {rel_path} → {len(chunks)}청크 (Qdrant {stored}건)")
            total += len(chunks)

    return {"ok": True, "total_chunks": total, "log": log}


class TranslationIngestBody(BaseModel):
    data_type: str = "labeled"       # "labeled" | "source" | "all"
    categories: list[str] = []       # [] = 전체. e.g. ["news","report"]
    languages: list[str] = []        # [] = 전체. e.g. ["en","ja"]
    max_docs: int = 0                # 0 = 무제한


@router.post("/ingest/translation-data")
async def ingest_translation_data(
    body: TranslationIngestBody,
    user=Depends(get_current_user),
):
    """data/1.데이터 다국어 번역 ZIP → Qdrant translation_docs 컬렉션 인제스트."""
    ollama = get_llm_client()
    log: list[str] = []
    result = await run_translation_ingest(
        ollama,
        log,
        data_type=body.data_type,
        categories=body.categories or None,
        languages=body.languages or None,
        max_docs=body.max_docs,
    )
    return {"ok": True, "result": result, "log": log}


class TranslationSearchBody(BaseModel):
    query: str
    top_k: int = 5
    category: str | None = None
    target_language: str | None = None


@router.post("/ingest/translation-search")
async def search_translation(
    body: TranslationSearchBody,
    user=Depends(get_current_user),
):
    """translation_docs 컬렉션에서 한국어 쿼리로 유사 문서 검색."""
    ollama = get_llm_client()
    hits = await translation_search(
        body.query,
        ollama,
        top_k=body.top_k,
        category=body.category,
        target_language=body.target_language,
    )
    return {"ok": True, "hits": hits, "collection": TRANSLATION_COLLECTION}


# ── 비동기 인제스트 엔드포인트 (Celery) ──────────────────────────────────────────

@router.post("/ingest/financial/async", summary="금융 데이터 비동기 인제스트")
async def ingest_financial_async(user=Depends(get_current_user)):
    """CSV 금융 데이터 인제스트를 Celery 워커에 위임하고 task_id 를 반환한다.
    GET /api/tasks/{task_id} 로 완료 여부와 결과를 폴링한다.
    """
    from app.tasks.ingest_tasks import financial_ingest_task
    task = financial_ingest_task.delay()
    return {"task_id": task.id, "poll_url": f"/api/tasks/{task.id}"}


@router.post("/ingest/crawl/auto/async", summary="자동 크롤링 비동기 인제스트")
async def crawl_auto_async(user=Depends(get_current_user)):
    """자동 크롤링을 Celery 워커에 위임한다."""
    from app.tasks.ingest_tasks import auto_crawl_task
    task = auto_crawl_task.delay()
    return {"task_id": task.id, "poll_url": f"/api/tasks/{task.id}"}


@router.post("/ingest/crawl/url/async", summary="URL 크롤링 비동기 인제스트")
async def crawl_url_async(body: CrawlUrlBody, user=Depends(get_current_user)):
    """단일 URL 크롤링을 Celery 워커에 위임한다."""
    from app.tasks.ingest_tasks import url_crawl_task
    task = url_crawl_task.delay(url=body.url)
    return {"task_id": task.id, "poll_url": f"/api/tasks/{task.id}"}


@router.post("/ingest/translation-data/async", summary="번역 데이터 비동기 인제스트")
async def ingest_translation_async(
    body: TranslationIngestBody,
    user=Depends(get_current_user),
):
    """다국어 번역 데이터 인제스트를 Celery 워커에 위임한다."""
    from app.tasks.ingest_tasks import translation_ingest_task
    task = translation_ingest_task.delay(
        data_type=body.data_type,
        categories=body.categories or None,
        languages=body.languages or None,
        max_docs=body.max_docs,
    )
    return {"task_id": task.id, "poll_url": f"/api/tasks/{task.id}"}


@router.get("/ingest/crawl/list")
async def list_crawled(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_pg_session),
):
    result = await db.execute(
        select(CrawledDoc.url, CrawledDoc.title, CrawledDoc.source, CrawledDoc.crawled_at)
        .order_by(CrawledDoc.crawled_at.desc())
        .limit(100)
    )
    items = [
        {"url": r.url, "title": r.title, "source": r.source, "crawled_at": r.crawled_at.isoformat()}
        for r in result.all()
    ]
    return {"items": items}
