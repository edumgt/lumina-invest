"""외부 시스템용 Open API — stock-coin-trade의 /openapi/v1 이식.

인증: `Authorization: Bearer <api_key>` (키는 /api/paper/api-keys 로 발급, DB에는 SHA-256 해시만 저장)
제한: 키당 분당 OPENAPI_RATE_LIMIT_MAX 회 (Redis INCR, Redis 미연결 시 프로세스 메모리 폴백)
응답 오류 형식은 원본과 같이 {"error": CODE, "message": ...}
"""
from __future__ import annotations

import hashlib
import threading
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.postgres import get_pg_session
from app.lib.redis_cache import RedisCache
from app.models import ApiKey
from app.services import paper_trading as pt
from app.services.audit import audit
from app.services.krx_companies import get_krx_companies

router = APIRouter(prefix="/openapi/v1", tags=["open-api"])

_rate_cache = RedisCache("openapi_rl")
_mem_buckets: dict[str, list[float]] = {}
_mem_lock = threading.Lock()


class OpenApiError(HTTPException):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(status_code=status_code, detail={"error": code, "message": message})


async def _check_rate_limit(key_id: str) -> bool:
    window, limit = settings.OPENAPI_RATE_LIMIT_WINDOW, settings.OPENAPI_RATE_LIMIT_MAX
    bucket = f"{key_id}:{int(time.time() // window)}"
    try:
        count = await _rate_cache.incr(bucket, ttl=window)
        return count <= limit
    except Exception:
        now = time.time()
        with _mem_lock:
            hits = [t for t in _mem_buckets.get(key_id, []) if now - t < window]
            if len(hits) >= limit:
                _mem_buckets[key_id] = hits
                return False
            hits.append(now)
            _mem_buckets[key_id] = hits
            return True


async def require_api_key(request: Request, db: AsyncSession = Depends(get_pg_session)) -> dict:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise OpenApiError(401, "UNAUTHORIZED", "Authorization: Bearer <api_key> 헤더가 필요합니다.")
    raw = auth[len("Bearer "):].strip()
    if not raw:
        raise OpenApiError(401, "UNAUTHORIZED", "API 키가 비어 있습니다.")
    key_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    key = (await db.execute(select(ApiKey).where(ApiKey.key_hash == key_hash, ApiKey.is_active.is_(True)))).scalar_one_or_none()
    if not key:
        raise OpenApiError(401, "UNAUTHORIZED", "유효하지 않거나 폐기된 API 키입니다.")
    if not await _check_rate_limit(str(key.id)):
        raise OpenApiError(429, "RATE_LIMITED", f"분당 {settings.OPENAPI_RATE_LIMIT_MAX}회 호출 제한을 초과했습니다.")
    key.last_used_at = datetime.now(timezone.utc)
    key.call_count = (key.call_count or 0) + 1
    await db.commit()
    return {"user_id": key.user_id, "key_id": str(key.id), "label": key.label}


def _uid(ctx: dict) -> uuid.UUID:
    return ctx["user_id"]


# ── 엔드포인트 ─────────────────────────────────────────────────────────────

@router.get("/stocks")
async def list_stocks(limit: int = Query(30, ge=1, le=100), market: str = Query("", description="KOSPI | KOSDAQ | KONEX"),
                      ctx=Depends(require_api_key)):
    try:
        companies = await get_krx_companies()
    except Exception as exc:
        raise OpenApiError(503, "MARKET_DATA_UNAVAILABLE", str(exc))
    # KRX 목록의 시장 라벨은 한글("코스피"/"코스닥"/"코넥스")이므로 영문 코드도 함께 받는다.
    market_alias = {"KOSPI": "코스피", "KOSDAQ": "코스닥", "KONEX": "코넥스"}
    market = market_alias.get(market.upper(), market)
    if market:
        companies = [c for c in companies if c.get("market", "") == market or c.get("market", "").upper() == market.upper()]
    return {"stocks": [{"symbol": c["symbol"], "code": c["code"], "name": c["name"], "market": c["market"]} for c in companies[:limit]]}


@router.get("/quote/{symbol}")
async def quote(symbol: str, ctx=Depends(require_api_key)):
    try:
        info = await pt.resolve_stock(symbol)
    except pt.PaperTradeError as exc:
        raise OpenApiError(404, "NOT_FOUND", str(exc))
    prev = info.get("prev_close") or info["price"]
    change = info["price"] - prev
    return {"symbol": info["symbol"], "code": info["code"], "name": info["name"], "market": info["market"],
            "price": info["price"], "prevClose": prev, "change": change,
            "changeRate": round(change / prev * 100, 2) if prev else 0, "currency": info.get("currency", "KRW")}


@router.get("/account")
async def account(ctx=Depends(require_api_key), db: AsyncSession = Depends(get_pg_session)):
    snap = await pt.account_snapshot(db, _uid(ctx))
    await db.commit()
    return snap


@router.get("/positions")
async def positions(ctx=Depends(require_api_key), db: AsyncSession = Depends(get_pg_session)):
    return {"positions": await pt.stock_positions(db, _uid(ctx))}


class OpenApiOrderBody(BaseModel):
    symbol: str
    side: str = Field(..., description="BUY | SELL")
    quantity: int = Field(..., ge=1)


@router.post("/orders")
async def place_order(body: OpenApiOrderBody, ctx=Depends(require_api_key), db: AsyncSession = Depends(get_pg_session)):
    try:
        result = await pt.stock_order(db, _uid(ctx), body.symbol, body.side, body.quantity, source="OPENAPI")
    except pt.PaperTradeError as exc:
        await db.rollback()
        raise OpenApiError(400, "INVALID_REQUEST", str(exc))
    await db.commit()
    await audit(str(_uid(ctx)), "", "openapi.order", {"symbol": result["symbol"], "side": result["side"],
                                                       "quantity": body.quantity, "price": result["price"], "key": ctx["label"]})
    return result


@router.get("/orders")
async def order_history(limit: int = Query(50, ge=1, le=200), ctx=Depends(require_api_key), db: AsyncSession = Depends(get_pg_session)):
    return {"orders": await pt.stock_order_history(db, _uid(ctx), limit=limit)}


@router.get("/crypto/hold")
async def crypto_hold(ctx=Depends(require_api_key), db: AsyncSession = Depends(get_pg_session)):
    data = await pt.crypto_holdings(db, _uid(ctx))
    await db.commit()
    return data


@router.get("/alternatives/positions")
async def alt_positions(ctx=Depends(require_api_key), db: AsyncSession = Depends(get_pg_session)):
    rows = await pt.alt_positions(db, _uid(ctx))
    return {"positions": rows, "totalEvalAmount": sum(r["evalAmount"] for r in rows)}


@router.get("/docs-summary", include_in_schema=False)
async def docs_summary():
    """프론트(Open API 화면)에서 보여줄 엔드포인트 요약."""
    return {"baseUrl": "/openapi/v1", "auth": "Authorization: Bearer <api_key>",
            "rateLimit": f"{settings.OPENAPI_RATE_LIMIT_MAX} req / {settings.OPENAPI_RATE_LIMIT_WINDOW}s per key",
            "endpoints": [
                {"method": "GET", "path": "/stocks?limit=30&market=KOSPI", "desc": "KRX 상장 종목 목록"},
                {"method": "GET", "path": "/quote/005930", "desc": "현재가 (6자리 코드 또는 야후 티커)"},
                {"method": "GET", "path": "/account", "desc": "모의투자 계좌 스냅샷 (현금·평가액·수익률)"},
                {"method": "GET", "path": "/positions", "desc": "주식 보유 종목"},
                {"method": "POST", "path": "/orders  {symbol, side, quantity}", "desc": "시장가 모의 주문 (source=OPENAPI)"},
                {"method": "GET", "path": "/orders?limit=50", "desc": "주문 이력"},
                {"method": "GET", "path": "/crypto/hold", "desc": "코인 보유 현황"},
                {"method": "GET", "path": "/alternatives/positions", "desc": "대체자산 포지션"},
            ]}
