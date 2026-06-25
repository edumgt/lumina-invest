from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.database.mongo import connect_mongo, close_mongo
from app.lib.redis_cache import connect_redis, close_redis
from app.routes import ingest


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await connect_redis()
    except Exception as e:
        print(f"[WARN] Redis 연결 실패: {e}")
    try:
        await connect_mongo()
    except Exception as e:
        print(f"[WARN] MongoDB 연결 실패: {e}")
    yield
    await close_redis()
    await close_mongo()


app = FastAPI(
    title="Crawl Service",
    description="크롤링 / 문서 인제스트 / 금융 데이터 수집",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(ingest.router)
