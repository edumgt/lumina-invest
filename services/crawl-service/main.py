from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.database.postgres import connect_postgres, close_postgres
from app.lib.redis_cache import connect_redis, close_redis
from app.lib.session import SessionCookieRefreshMiddleware
from app.routes import ingest

# 스키마 마이그레이션은 메인 앱/배포 파이프라인에서 1회 실행 (services/auth-service/main.py 참고).


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await connect_redis()
    except Exception as e:
        print(f"[WARN] Redis 연결 실패: {e}")
    try:
        await connect_postgres()
    except Exception as e:
        print(f"[WARN] PostgreSQL 연결 실패: {e}")
    yield
    await close_redis()
    await close_postgres()


app = FastAPI(
    title="Crawl Service",
    description="크롤링 / 문서 인제스트 / 금융 데이터 수집",
    version="1.0.0",
    lifespan=lifespan,
)

# 세션 슬라이딩 만료: 서버 TTL 이 연장된 요청의 응답에 세션 쿠키를 다시 실어 브라우저 쿠키 만료도 연장한다.
app.add_middleware(SessionCookieRefreshMiddleware)

app.include_router(ingest.router)
