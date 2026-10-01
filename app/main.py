"""금융 AI Agent - FastAPI 메인 엔트리포인트."""
import asyncio
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Cookie, FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig

from app.database.postgres import connect_postgres, close_postgres
from app.config import settings
from app.database.neo4j import connect_neo4j, close_neo4j, ensure_graph_schema
from app.lib.redis_cache import connect_redis, close_redis
from app.lib.session import COOKIE_NAME, SessionCookieRefreshMiddleware, get_session
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
        if settings.RUN_MIGRATIONS_ON_STARTUP:
            await asyncio.get_event_loop().run_in_executor(None, _run_migrations)
        else:
            print("[fin-agent] RUN_MIGRATIONS_ON_STARTUP=false — 마이그레이션은 scripts/migrate.sh 로 별도 실행")
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

# 세션 슬라이딩 만료: 서버 TTL 이 연장된 요청의 응답에 세션 쿠키를 다시 실어 브라우저 쿠키 만료도 연장한다.
app.add_middleware(SessionCookieRefreshMiddleware)


class StaticNoCacheMiddleware:
    """프런트 정적 파일(/js, /css, *.html)에 Cache-Control: no-cache 를 붙이는 순수 ASGI 미들웨어.

    StaticFiles 는 Cache-Control 을 보내지 않아 브라우저가 휴리스틱 캐시로 옛 common.js 를 재사용하고,
    새 HTML 이 옛 모듈을 import 해 "does not provide an export named ..." SyntaxError 가 난다.
    no-cache 는 매번 ETag 로 재검증(304)하므로 배포 직후에도 새 파일을 받는다.
    """

    _PREFIXES = ("/js/", "/css/")

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if not (path.startswith(self._PREFIXES) or path.endswith(".html") or path == "/"):
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"cache-control"]
                headers.append((b"cache-control", b"no-cache"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


app.add_middleware(StaticNoCacheMiddleware)

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
# 리밸런싱 엔진 (시간·이탈률·현금흐름 트리거)
from app.routes import rebalance as rebalance_routes  # noqa: E402
app.include_router(rebalance_routes.router)
# TradingView Webhook 수신 + Strategy Tester↔LEAN 교차 검증
from app.routes import tradingview as tradingview_routes  # noqa: E402
app.include_router(tradingview_routes.router)
# 자유 산식 커스텀 지표 (DSL · 버전 · 결과 저장)
from app.routes import formula as formula_routes  # noqa: E402
app.include_router(formula_routes.router)

# 정적 파일 (프론트엔드)
_public = os.path.join(os.path.dirname(__file__), "..", "public")
if os.path.isdir(_public):
    app.mount("/js", StaticFiles(directory=os.path.join(_public, "js")), name="js")
    app.mount("/css", StaticFiles(directory=os.path.join(_public, "css")), name="css")

    async def _has_valid_session(sid: Optional[str]) -> bool:
        """세션 쿠키가 Redis 에 살아 있는지 확인합니다 (Redis 장애 시 False)."""
        if not sid:
            return False
        try:
            return await get_session(sid) is not None
        except Exception:
            return False

    @app.get("/", include_in_schema=False)
    async def index(fin_session: Optional[str] = Cookie(default=None, alias=COOKIE_NAME)):
        # 로그인 세션이 살아 있으면 로그인 화면을 거치지 않고 바로 앱으로 보낸다.
        if await _has_valid_session(fin_session):
            return RedirectResponse(url="/app.html")
        return RedirectResponse(url="/login.html")

    @app.get("/login.html", include_in_schema=False)
    async def login_page(fin_session: Optional[str] = Cookie(default=None, alias=COOKIE_NAME)):
        if await _has_valid_session(fin_session):
            return RedirectResponse(url="/app.html")
        return FileResponse(os.path.join(_public, "login.html"))

    @app.get("/register.html", include_in_schema=False)
    async def register_page(fin_session: Optional[str] = Cookie(default=None, alias=COOKIE_NAME)):
        if await _has_valid_session(fin_session):
            return RedirectResponse(url="/app.html")
        return FileResponse(os.path.join(_public, "register.html"))

    @app.get("/app.html", include_in_schema=False)
    async def app_page():
        return FileResponse(os.path.join(_public, "app.html"))
