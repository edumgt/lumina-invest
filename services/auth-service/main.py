from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.database.postgres import connect_postgres, close_postgres
from app.lib.redis_cache import connect_redis, close_redis
from app.lib.session import SessionCookieRefreshMiddleware
from app.routes import auth

# 스키마 마이그레이션(alembic upgrade head)은 이 Lambda의 콜드스타트가 아니라
# 배포 파이프라인(메인 앱 기동 또는 별도 마이그레이션 잡)에서 1회 실행한다.
# 동시에 여러 Lambda 인스턴스가 콜드스타트될 때 매번 마이그레이션을 트리거하지 않기 위함.


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
    title="Auth Service",
    description="회원가입 / 로그인 / 로그아웃 / 세션 관리",
    version="1.0.0",
    lifespan=lifespan,
)

# 세션 슬라이딩 만료: 서버 TTL 이 연장된 요청의 응답에 세션 쿠키를 다시 실어 브라우저 쿠키 만료도 연장한다.
app.add_middleware(SessionCookieRefreshMiddleware)

app.include_router(auth.router)
