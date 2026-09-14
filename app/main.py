"""금융 AI Agent - FastAPI 메인 엔트리포인트."""
import asyncio
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig

from app.database.postgres import connect_postgres, close_postgres
from app.database.neo4j import connect_neo4j, close_neo4j, ensure_graph_schema
from app.lib.redis_cache import connect_redis, close_redis
from app.routes import auth, health, chat, stocks, library, admin, system, quant, ml, macro, documents, notification, graph, conversations, tasks, ingest, paper, openapi, lean
from app.services.graph_service import seed_graph
from app.services.sync_scheduler import start_sync_scheduler, stop_sync_scheduler


def _run_migrations() -> None:
    """PostgreSQL 스키마를 최신 Alembic revision으로 맞춘다 (Mongo ensure_indexes()의 후신)."""
    root = os.path.join(os.path.dirname(__file__), "..")
    cfg = AlembicConfig(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "alembic"))
    alembic_command.upgrade(cfg, "head")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 시작
    try:
        await connect_redis()
    except Exception as e:
        print(f"[WARN] Redis 연결 실패 (세션 비활성): {e}")
    try:
        # alembic의 command.upgrade()는 내부적으로 asyncio.run()을 새로 여는데,
        # 이미 실행 중인 uvicorn 이벤트 루프 안에서 그대로 부르면 충돌한다.
        # 별도 스레드에서 돌려 독립된 루프를 갖게 한다.
        await asyncio.get_event_loop().run_in_executor(None, _run_migrations)
        await connect_postgres()
    except Exception as e:
        print(f"[WARN] PostgreSQL 연결 실패 (인증 비활성): {e}")
    try:
        await connect_neo4j()
        await ensure_graph_schema()
        await seed_graph()
        print("[fin-agent] Neo4j 연결 및 그래프 시드 완료")
    except Exception as e:
        print(f"[WARN] Neo4j 연결 실패 (그래프 기능 비활성): {e}")
    start_sync_scheduler()
    print("[fin-agent] 서버 시작 완료. JWT + PostgreSQL + 대화이력 기능 활성화")
    yield
    # 종료
    stop_sync_scheduler()
    await close_redis()
    await close_postgres()
    await close_neo4j()


app = FastAPI(
    title="금융 AI Agent",
    description="개인/기업 CB 분석 · 금융상품 · 주가 · 퀀트 자동매매",
    version="1.0.0",
    lifespan=lifespan,
)

# 라우터 등록
# auth/ingest는 프로덕션(AWS)에서 auth-service/crawl-service Lambda로도 분리 배포되지만,
# 로컬 docker-compose 단일 앱 실행 시에도 동작하도록 메인 앱에도 등록한다.
app.include_router(auth.router)
app.include_router(ingest.router)
app.include_router(health.router)
app.include_router(chat.router)
app.include_router(stocks.router)
app.include_router(library.router)
app.include_router(admin.router)
app.include_router(system.router)
app.include_router(quant.router)
app.include_router(ml.router)
app.include_router(macro.router)
app.include_router(documents.router)
app.include_router(notification.router)
app.include_router(graph.router)
app.include_router(conversations.router)
app.include_router(tasks.router)
# 모의투자(주식·코인·대체자산) + Open API 키 — stock-coin-trade 이식
app.include_router(paper.router)
app.include_router(openapi.router)
# QuantConnect LEAN 백테스트 — domain-rag-lab 이식
app.include_router(lean.router)

# 정적 파일 (프론트엔드)
_public = os.path.join(os.path.dirname(__file__), "..", "public")
if os.path.isdir(_public):
    app.mount("/js", StaticFiles(directory=os.path.join(_public, "js")), name="js")

    @app.get("/", include_in_schema=False)
    async def index():
        return RedirectResponse(url="/login.html")

    @app.get("/login.html", include_in_schema=False)
    async def login_page():
        return FileResponse(os.path.join(_public, "login.html"))

    @app.get("/register.html", include_in_schema=False)
    async def register_page():
        return FileResponse(os.path.join(_public, "register.html"))

    @app.get("/app.html", include_in_schema=False)
    async def app_page():
        return FileResponse(os.path.join(_public, "app.html"))
