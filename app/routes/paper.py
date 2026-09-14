"""모의투자 API — stock-coin-trade의 /api/stocks, /api/trade, /api/crypto, /api/alternatives,
/api/member/api-keys 를 lumina-invest(FastAPI + 세션/JWT 인증)로 이식.

접두어: /api/paper
"""
from __future__ import annotations

import hashlib
import secrets
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.postgres import get_pg_session
from app.lib.jwt_auth import get_current_user_any
from app.models import ApiKey
from app.services import paper_trading as pt
from app.services.audit import audit

router = APIRouter(prefix="/api/paper", tags=["paper-trading"])


def _uid(user: dict) -> uuid.UUID:
    try:
        return uuid.UUID(str(user["id"]))
    except Exception:
        raise HTTPException(400, "유효하지 않은 사용자 ID입니다.")


def _bad(exc: Exception) -> HTTPException:
    return HTTPException(400, str(exc))


# ── 계좌 ────────────────────────────────────────────────────────────────

@router.get("/account")
async def account(user=Depends(get_current_user_any), db: AsyncSession = Depends(get_pg_session)):
    """현금 + 주식/코인/대체자산 평가액 합산 스냅샷."""
    snap = await pt.account_snapshot(db, _uid(user))
    await db.commit()  # 최초 호출 시 계좌 행 생성
    return snap


@router.post("/account/reset")
async def account_reset(user=Depends(get_current_user_any), db: AsyncSession = Depends(get_pg_session)):
    acc = await pt.reset_account(db, _uid(user))
    await db.commit()
    await audit(user["id"], user.get("client_id", ""), "paper.account.reset", {"cash": acc.cash})
    return {"status": "ok", "cash": acc.cash}


# ── 국내주식 ─────────────────────────────────────────────────────────────

class StockOrderBody(BaseModel):
    symbol: str = Field(..., description="005930 또는 005930.KS (해외 티커도 가능)")
    side: str = Field(..., description="BUY | SELL")
    quantity: int = Field(..., ge=1)


@router.get("/stocks/quote")
async def stock_quote(symbol: str = Query(...)):
    try:
        return await pt.resolve_stock(symbol)
    except pt.PaperTradeError as exc:
        raise HTTPException(404, str(exc))


@router.get("/stocks/positions")
async def stock_positions(volatility: int = Query(0), user=Depends(get_current_user_any),
                          db: AsyncSession = Depends(get_pg_session)):
    return {"positions": await pt.stock_positions(db, _uid(user), include_volatility=bool(volatility))}


@router.post("/stocks/orders/preview")
async def stock_preview(body: StockOrderBody, user=Depends(get_current_user_any),
                        db: AsyncSession = Depends(get_pg_session)):
    try:
        return await pt.stock_preview(db, _uid(user), body.symbol, body.side, body.quantity)
    except pt.PaperTradeError as exc:
        raise _bad(exc)


async def _place_stock(body: StockOrderBody, user: dict, db: AsyncSession, source: str, side: str | None = None) -> dict:
    try:
        result = await pt.stock_order(db, _uid(user), body.symbol, side or body.side, body.quantity, source=source)
    except pt.PaperTradeError as exc:
        await db.rollback()
        raise _bad(exc)
    await db.commit()
    await audit(user["id"], user.get("client_id", ""), "paper.stock.order",
                {"symbol": result["symbol"], "side": result["side"], "quantity": body.quantity, "price": result["price"], "source": source})
    return result


@router.post("/stocks/orders")
async def stock_order(body: StockOrderBody, user=Depends(get_current_user_any), db: AsyncSession = Depends(get_pg_session)):
    return await _place_stock(body, user, db, "PAPER")


@router.post("/stocks/orders/buy")
async def stock_buy(body: StockOrderBody, user=Depends(get_current_user_any), db: AsyncSession = Depends(get_pg_session)):
    return await _place_stock(body, user, db, "PAPER", side="BUY")


@router.post("/stocks/orders/sell")
async def stock_sell(body: StockOrderBody, user=Depends(get_current_user_any), db: AsyncSession = Depends(get_pg_session)):
    return await _place_stock(body, user, db, "PAPER", side="SELL")


@router.post("/stocks/orders/pine")
async def stock_pine_order(body: StockOrderBody, user=Depends(get_current_user_any), db: AsyncSession = Depends(get_pg_session)):
    """Pine 전략 실습 화면용 주문. 시그널은 브라우저에서 만들고 체결은 서버가 기록(source=PINE)."""
    return await _place_stock(body, user, db, "PINE")


@router.get("/stocks/orders/history")
async def stock_history(limit: int = Query(200, ge=1, le=1000), user=Depends(get_current_user_any),
                        db: AsyncSession = Depends(get_pg_session)):
    return {"history": await pt.stock_order_history(db, _uid(user), limit=limit)}


# ── 코인 ─────────────────────────────────────────────────────────────────

class CryptoBuyBody(BaseModel):
    marketCode: str
    buyKrw: float = Field(..., gt=0)


class CryptoSellBody(BaseModel):
    marketCode: str
    sellCount: float = Field(..., gt=0)


class CryptoPreviewBody(BaseModel):
    marketCode: str
    side: str
    buyKrw: float | None = None
    sellCount: float | None = None


@router.get("/crypto/market-list")
async def crypto_market_list():
    markets = await pt.upbit_markets()
    return {"markets": markets, "marketCodes": [m["market"] for m in markets]}


@router.get("/crypto/rankings")
async def crypto_rankings(limit: int = Query(20, ge=1, le=100)):
    return {"rankings": await pt.crypto_rankings(limit)}


@router.get("/crypto/ticker")
async def crypto_ticker(markets: str = Query(..., description="KRW-BTC,KRW-ETH")):
    return {"tickers": await pt.upbit_tickers([m.strip().upper() for m in markets.split(",")])}


@router.get("/crypto/{code}/candles")
async def crypto_candles(code: str, unit: str = Query("days"), count: int = Query(120, ge=1, le=200)):
    if unit not in ("days", "weeks", "months", "minutes/60", "minutes/15", "minutes/1"):
        raise HTTPException(400, "unit은 days | weeks | months | minutes/60 | minutes/15 | minutes/1 중 하나여야 합니다.")
    try:
        return {"market": code.upper(), "candles": await pt.upbit_candles(code.upper(), unit, count)}
    except Exception as exc:
        raise HTTPException(502, f"Upbit 캔들 조회 실패: {exc}")


@router.get("/crypto/{code}/domestic-prices")
async def crypto_domestic_prices(code: str):
    return await pt.domestic_prices(code.upper())


@router.get("/crypto/{code}")
async def crypto_info(code: str, user=Depends(get_current_user_any), db: AsyncSession = Depends(get_pg_session)):
    market = await pt.upbit_market_info(code)
    if not market:
        raise HTTPException(404, f"존재하지 않는 마켓입니다: {code}")
    held = await pt._crypto_holding(db, _uid(user), market["market"])
    ticker = await pt.upbit_tickers([market["market"]])
    return {"marketCode": market["market"], "koreanName": market["koreanName"], "englishName": market["englishName"],
            "buyCryptoCount": held.quantity if held else 0, "ticker": ticker[0] if ticker else None}


@router.get("/trade/hold")
async def crypto_hold(user=Depends(get_current_user_any), db: AsyncSession = Depends(get_pg_session)):
    data = await pt.crypto_holdings(db, _uid(user))
    await db.commit()
    return data


@router.post("/trade/order/preview")
async def crypto_preview(body: CryptoPreviewBody, user=Depends(get_current_user_any), db: AsyncSession = Depends(get_pg_session)):
    try:
        return await pt.crypto_preview(db, _uid(user), body.marketCode, body.side, body.buyKrw, body.sellCount)
    except pt.PaperTradeError as exc:
        raise _bad(exc)


@router.post("/trade/order/buy")
async def crypto_buy(body: CryptoBuyBody, user=Depends(get_current_user_any), db: AsyncSession = Depends(get_pg_session)):
    try:
        result = await pt.crypto_buy(db, _uid(user), body.marketCode, float(body.buyKrw))
    except pt.PaperTradeError as exc:
        await db.rollback()
        raise _bad(exc)
    await db.commit()
    await audit(user["id"], user.get("client_id", ""), "paper.crypto.buy", {"market": result["marketCode"], "amount": body.buyKrw})
    return result


@router.post("/trade/order/sell")
async def crypto_sell(body: CryptoSellBody, user=Depends(get_current_user_any), db: AsyncSession = Depends(get_pg_session)):
    try:
        result = await pt.crypto_sell(db, _uid(user), body.marketCode, float(body.sellCount))
    except pt.PaperTradeError as exc:
        await db.rollback()
        raise _bad(exc)
    await db.commit()
    await audit(user["id"], user.get("client_id", ""), "paper.crypto.sell", {"market": result["marketCode"], "quantity": body.sellCount})
    return result


@router.get("/trade/order/history")
async def crypto_history(limit: int = Query(200, ge=1, le=1000), user=Depends(get_current_user_any),
                         db: AsyncSession = Depends(get_pg_session)):
    return {"history": await pt.crypto_history(db, _uid(user), limit=limit)}


# ── 대체자산 ─────────────────────────────────────────────────────────────

class AltOrderBody(BaseModel):
    symbol: str
    side: str
    quantity: int = Field(..., ge=1)


@router.get("/alternatives/markets")
async def alt_markets():
    return {"markets": [await pt.alt_quote(s) for s in pt.ALT_CATALOG],
            "notice": "교육용 기준 시세이며 실제 투자 권유 또는 실거래 가격이 아닙니다."}


@router.get("/alternatives/markets/{symbol}/chart")
async def alt_chart(symbol: str, days: int = Query(120, ge=30, le=365)):
    symbol = symbol.upper()
    if symbol not in pt.ALT_CATALOG:
        raise HTTPException(404, "지원하지 않는 상품입니다.")
    return {"symbol": symbol, "data": await pt.alt_chart(symbol, days)}


@router.get("/alternatives/positions")
async def alt_positions(volatility: int = Query(0), user=Depends(get_current_user_any), db: AsyncSession = Depends(get_pg_session)):
    rows = await pt.alt_positions(db, _uid(user), include_volatility=bool(volatility))
    return {"positions": rows, "totalEvalAmount": sum(r["evalAmount"] for r in rows)}


@router.get("/alternatives/orders/history")
async def alt_history(limit: int = Query(100, ge=1, le=1000), user=Depends(get_current_user_any), db: AsyncSession = Depends(get_pg_session)):
    return {"history": await pt.alt_history(db, _uid(user), limit=limit)}


@router.post("/alternatives/orders/preview")
async def alt_preview(body: AltOrderBody, user=Depends(get_current_user_any), db: AsyncSession = Depends(get_pg_session)):
    try:
        return await pt.alt_preview(db, _uid(user), body.symbol, body.side, body.quantity)
    except pt.PaperTradeError as exc:
        raise _bad(exc)


@router.post("/alternatives/orders")
async def alt_order(body: AltOrderBody, user=Depends(get_current_user_any), db: AsyncSession = Depends(get_pg_session)):
    try:
        result = await pt.alt_order(db, _uid(user), body.symbol, body.side, body.quantity)
    except pt.PaperTradeError as exc:
        await db.rollback()
        raise _bad(exc)
    await db.commit()
    await audit(user["id"], user.get("client_id", ""), "paper.alternative.order",
                {"symbol": result["symbol"], "side": result["side"], "quantity": body.quantity, "amount": result["amount"]})
    return result


# ── Open API 키 발급 (stock-coin-trade /api/member/api-keys) ──────────────

API_KEY_PREFIX = "lumina_live_"


class ApiKeyBody(BaseModel):
    label: str = "My API Key"


def _serialize_key(k: ApiKey) -> dict:
    return {"id": str(k.id), "label": k.label, "keyPrefix": k.key_prefix, "isActive": k.is_active,
            "callCount": k.call_count, "createdAt": k.created_at.isoformat() if k.created_at else None,
            "lastUsedAt": k.last_used_at.isoformat() if k.last_used_at else None}


@router.get("/api-keys")
async def list_api_keys(user=Depends(get_current_user_any), db: AsyncSession = Depends(get_pg_session)):
    rows = (await db.execute(select(ApiKey).where(ApiKey.user_id == _uid(user)).order_by(ApiKey.created_at.desc()))).scalars().all()
    return {"keys": [_serialize_key(k) for k in rows]}


@router.post("/api-keys")
async def create_api_key(body: ApiKeyBody, user=Depends(get_current_user_any), db: AsyncSession = Depends(get_pg_session)):
    """키 원문은 이 응답에서만 한 번 보여주고 DB에는 SHA-256 해시만 저장한다."""
    raw_key = API_KEY_PREFIX + secrets.token_urlsafe(32)
    key = ApiKey(user_id=_uid(user), label=(body.label or "").strip()[:100] or "My API Key",
                 key_prefix=raw_key[:16], key_hash=hashlib.sha256(raw_key.encode()).hexdigest())
    db.add(key)
    await db.commit()
    await db.refresh(key)
    await audit(user["id"], user.get("client_id", ""), "openapi.key.create", {"label": key.label, "prefix": key.key_prefix})
    return {**_serialize_key(key), "apiKey": raw_key}


@router.delete("/api-keys/{key_id}")
async def revoke_api_key(key_id: str, user=Depends(get_current_user_any), db: AsyncSession = Depends(get_pg_session)):
    try:
        kid = uuid.UUID(key_id)
    except ValueError:
        raise HTTPException(404, "존재하지 않는 키입니다.")
    key = (await db.execute(select(ApiKey).where(ApiKey.id == kid, ApiKey.user_id == _uid(user)))).scalar_one_or_none()
    if not key:
        raise HTTPException(404, "존재하지 않는 키입니다.")
    key.is_active = False
    await db.commit()
    await audit(user["id"], user.get("client_id", ""), "openapi.key.revoke", {"prefix": key.key_prefix})
    return {"status": "ok"}


# ── Alpaca Paper Trading 읽기 전용 연결 테스트 (stock-coin-trade alpaca_test) ──

ALPACA_PAPER_BASE = "https://paper-api.alpaca.markets/v2"


class AlpacaTestBody(BaseModel):
    api_key: str = ""
    secret_key: str = ""


def _alpaca_headers(body: AlpacaTestBody | None) -> dict:
    key = (body.api_key if body and body.api_key else settings.ALPACA_API_KEY).strip()
    secret = (body.secret_key if body and body.secret_key else settings.ALPACA_SECRET_KEY).strip()
    if not key or not secret:
        raise HTTPException(400, "Alpaca API 키가 없습니다. 입력하거나 ALPACA_API_KEY / ALPACA_SECRET_KEY 환경변수를 설정하세요.")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


async def _alpaca_get(path: str, headers: dict) -> dict | list:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{ALPACA_PAPER_BASE}{path}", headers=headers)
    try:
        data = resp.json()
    except ValueError:
        raise HTTPException(502, f"Alpaca 서버가 JSON 응답을 반환하지 않았습니다. (HTTP {resp.status_code})")
    if not resp.is_success:
        msg = data.get("message") if isinstance(data, dict) else None
        raise HTTPException(502, f"Alpaca API 인증 실패 (HTTP {resp.status_code}): {msg or '요청이 거부되었습니다.'}")
    return data


@router.post("/alpaca/account")
async def alpaca_account(body: AlpacaTestBody | None = None, user=Depends(get_current_user_any)):
    """읽기 전용 Paper 계정 상태 (계좌 식별자는 돌려주지 않는다)."""
    data = await _alpaca_get("/account", _alpaca_headers(body))
    return {"ok": True, "environment": "Paper Trading", "connection": "connected",
            "accountStatus": data.get("status"), "tradingBlocked": bool(data.get("trading_blocked")),
            "accountBlocked": bool(data.get("account_blocked")), "currency": data.get("currency"),
            "buyingPower": data.get("buying_power"), "portfolioValue": data.get("portfolio_value")}


@router.post("/alpaca/positions")
async def alpaca_positions(body: AlpacaTestBody | None = None, user=Depends(get_current_user_any)):
    rows = await _alpaca_get("/positions", _alpaca_headers(body))
    return {"ok": True, "count": len(rows), "positions": [
        {"symbol": r.get("symbol"), "qty": r.get("qty"), "avgEntryPrice": r.get("avg_entry_price"),
         "marketValue": r.get("market_value"), "unrealizedPl": r.get("unrealized_pl")} for r in rows]}
