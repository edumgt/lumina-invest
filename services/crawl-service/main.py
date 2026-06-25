from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.database.mongo import connect_mongo, close_mongo
from app.lib.redis_cache import connect_redis, close_redis
from app.routes import ingest


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_redis()
    await connect_mongo()
    yield
    await close_redis()
    await close_mongo()


app = FastAPI(
    title="Crawl Service",
    description="크롤링 / 문서 인제스트 / 금융 데이터 수집",
    version="1.0.0",
    lifespan=lifespan,
    root_path="/api",
)

app.include_router(ingest.router)
