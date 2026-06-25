from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.database.mongo import connect_mongo, close_mongo, ensure_indexes
from app.lib.redis_cache import connect_redis, close_redis
from app.routes import auth


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await connect_redis()
    except Exception as e:
        print(f"[WARN] Redis 연결 실패: {e}")
    try:
        await connect_mongo()
        await ensure_indexes()
    except Exception as e:
        print(f"[WARN] MongoDB 연결 실패: {e}")
    yield
    await close_redis()
    await close_mongo()


app = FastAPI(
    title="Auth Service",
    description="회원가입 / 로그인 / 로그아웃 / 세션 관리",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(auth.router)
