import logging
import uuid
import httpx
from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.postgres import get_pg_session
from app.models import Portfolio, Order, BrokerSettings, CustomIndicator
from app.lib.session import get_current_user
from app.services.stock import (
    get_quote, get_candles, get_market_summary,
    get_quant_indicators, get_fundamentals, QUANT_STOCKS,
)
from app.services.krx_companies import search_companies
from app.services import auto_trade
from app.services.quant_pipeline import backtest_custom_indicator
from app.services.investment_research import backtest_strategy, screen_pattern
from app.services.brokers.factory import get_broker_client
from app.services.brokers.catalog import get_broker_catalog, get_broker_codes
from app.services import notification
from app.services.audit import audit
from app.services import paper_trading
from app.services.data_cache import cache_get, cache_set
from app.services.sync_scheduler import KEY_MARKET_INDICES

router = APIRouter(prefix="/api")
DEFAULT_BROKER = "mock"


def _uid(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except Exception:
        raise HTTPException(400, "유효하지 않은 사용자 ID입니다.")


@router.get("/stocks/market")
async def market_summary():
    cached = await cache_get(KEY_MARKET_INDICES, max_age_hours=2)
    if cached is not None:
        return {"indices": cached, "from_cache": True}
    indices = await get_market_summary()
    if indices:
        await cache_set(KEY_MARKET_INDICES, indices)
    return {"indices": indices, "from_cache": False}


@router.get("/stocks/quote")
async def stock_quote(symbol: str = Query(...)):
    return await get_quote(symbol)


@router.get("/stocks/candles")
async def stock_candles(
    symbol: str = Query(...),
    period: str = Query("1y"),
    interval: str = Query("1d"),
):
    cache_key = f"candles:{symbol}:{period}:{interval}"
    cached = await cache_get(cache_key, max_age_hours=6)
    if cached is not None:
        cached["from_cache"] = True
        return cached
    data = await get_candles(symbol, period=period, interval=interval)
    if data.get("candles"):
        await cache_set(cache_key, data)
    return data


@router.get("/stocks/quant/indicators")
async def quant_indicators(
    symbol: str = Query(...),
    period: str = Query("2y"),
):
    cache_key = f"indicators:{symbol}:{period}"
    cached = await cache_get(cache_key, max_age_hours=6)
    if cached is not None:
        cached["from_cache"] = True
        return cached
    data = await get_quant_indicators(symbol, period=period)
    if not data.get("error"):
        await cache_set(cache_key, data)
    return data


@router.get("/stocks/quant/list")
async def quant_stock_list():
    return {"stocks": QUANT_STOCKS}


@router.get("/stocks/search")
async def stock_search(q: str = Query(..., min_length=1)):
    """종목 검색: 국내 상장사는 KRX 상장법인목록(로컬 엔진)을 우선 쓰고,
    결과가 없으면 Yahoo Finance 자동완성으로 보완한다(해외 종목 등).

    Yahoo 자동완성 API가 일부 한글 검색어("카카오" 등)에서 "Invalid Search Query"
    400을 반환하는 문제가 있어, 한글 종목명/코드 검색은 KRX 목록에서 직접
    부분일치로 찾는 로컬 엔진이 더 안정적이다.
    """
    krx_results = await search_companies(q)
    if krx_results:
        return {"results": krx_results}

    url = "https://query1.finance.yahoo.com/v1/finance/search"
    params = {
        "q": q,
        "lang": "ko-KR",
        "region": "KR",
        "quotesCount": 10,
        "newsCount": 0,
        "listsCount": 0,
    }
    headers = {"User-Agent": "Mozilla/5.0 (compatible; FinAgent/1.0)"}
    try:
        async with httpx.AsyncClient(timeout=8.0, headers=headers) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
        quotes = data.get("quotes", [])
        results = [
            {
                "symbol": item.get("symbol", ""),
                "name": item.get("longname") or item.get("shortname") or item.get("symbol", ""),
                "exchange": item.get("exchDisp", ""),
                "type": item.get("typeDisp", ""),
            }
            for item in quotes
            if item.get("symbol")
        ]
        return {"results": results}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"종목 검색 실패: {exc}") from exc


@router.get("/stocks/fundamentals")
async def stock_fundamentals(symbol: str = Query(..., description="예: 005930.KS")):
    """PER/PBR/ROE/분기실적 등 실제 기업 펀더멘털 (Yahoo Finance quoteSummary)."""
    data = await get_fundamentals(symbol)
    if data.get("error"):
        raise HTTPException(502, data["error"])
    return data


@router.get("/stocks/signals")
async def stock_signals(
    signal: str = Query("all", description="all | buy | sell"),
    model: str = Query("lightgbm", description="lightgbm | rsi | ma | bollinger"),
    min_confidence: int = Query(65, ge=0, le=100),
):
    """선택한 패턴 모델을 적용한 대표 종목 스크리닝."""
    rows = []
    for stock in QUANT_STOCKS:
        candles = (await get_candles(stock["symbol"], period="1y", interval="1d")).get("candles", [])
        result = screen_pattern(candles, model)
        if result.get("error"):
            continue
        quote = await get_quote(stock["symbol"])
        row = {
            "symbol": stock["symbol"],
            "name": stock["name"],
            "sector": stock.get("sector", ""),
            "model": model,
            "signal": result["signal"],
            "confidence": result["confidence"],
            "score": result["score"],
            "reason": result["reason"],
            "rsi": result["rsi"],
            "price": quote.get("price") or result["price"],
            "change_pct": quote.get("change_pct"),
        }
        rows.append(row)

    signal_filter = (signal or "all").lower()
    if signal_filter in ("buy", "sell"):
        rows = [r for r in rows if r["signal"].lower() == signal_filter]
    rows = [r for r in rows if r["confidence"] >= int(min_confidence)]
    rows.sort(key=lambda x: (x["confidence"], abs(x["score"])), reverse=True)
    return {"signals": rows, "count": len(rows)}


# ── 포트폴리오 ─────────────────────────────────────────────────────────

class HoldingBody(BaseModel):
    symbol: str
    name: str
    quantity: int
    avg_price: float


def _portfolio_to_dict(h: Portfolio) -> dict:
    return {
        "symbol": h.symbol, "name": h.name, "quantity": h.quantity,
        "avg_price": h.avg_price, "updated_at": h.updated_at.isoformat(),
    }


@router.get("/portfolio")
async def get_portfolio(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_pg_session),
):
    result = await db.execute(
        select(Portfolio).where(Portfolio.user_id == _uid(user["id"])).order_by(Portfolio.updated_at.desc())
    )
    holdings = [_portfolio_to_dict(h) for h in result.scalars().all()]
    return {"holdings": holdings}


@router.post("/portfolio")
async def upsert_holding(
    body: HoldingBody,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_pg_session),
):
    stmt = pg_insert(Portfolio).values(
        user_id=_uid(user["id"]), symbol=body.symbol, name=body.name,
        quantity=body.quantity, avg_price=body.avg_price,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[Portfolio.user_id, Portfolio.symbol],
        set_={"name": body.name, "quantity": body.quantity, "avg_price": body.avg_price},
    )
    await db.execute(stmt)
    await db.commit()
    return {"ok": True}


@router.delete("/portfolio/{symbol}")
async def delete_holding(
    symbol: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_pg_session),
):
    result = await db.execute(
        select(Portfolio).where(Portfolio.user_id == _uid(user["id"]), Portfolio.symbol == symbol)
    )
    holding = result.scalar_one_or_none()
    if holding:
        await db.delete(holding)
        await db.commit()
    return {"ok": True}


# ── 수동 주문 ─────────────────────────────────────────────────────────

class OrderBody(BaseModel):
    symbol: str
    name: str
    order_type: str   # buy | sell
    quantity: int
    price: float
    broker: str = "virtual"


async def _apply_portfolio(db: AsyncSession, user_id: uuid.UUID, symbol: str, name: str,
                           order_type: str, quantity: int, price: float) -> None:
    """주문 체결분을 포트폴리오에 반영한다. 커밋은 호출자가 담당(주문 삽입과 한 트랜잭션)."""
    result = await db.execute(
        select(Portfolio).where(Portfolio.user_id == user_id, Portfolio.symbol == symbol)
    )
    existing = result.scalar_one_or_none()

    if order_type == "buy":
        if existing:
            new_qty = existing.quantity + quantity
            existing.avg_price = (existing.avg_price * existing.quantity + price * quantity) / new_qty
            existing.quantity = new_qty
        else:
            db.add(Portfolio(user_id=user_id, symbol=symbol, name=name, quantity=quantity, avg_price=price))
    elif order_type == "sell" and existing:
        new_qty = existing.quantity - quantity
        if new_qty <= 0:
            await db.delete(existing)
        else:
            existing.quantity = new_qty


@router.post("/orders")
async def place_order(
    body: OrderBody,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_pg_session),
):
    uid = _uid(user["id"])
    # 가상 매매는 모의투자 계좌(PaperAccount)의 현금과 연동한다 — 매수 시 차감, 매도 시 가산.
    if body.broker == "virtual":
        delta = -body.price * body.quantity if body.order_type == "buy" else body.price * body.quantity
        try:
            await paper_trading.apply_cash(db, uid, delta)
        except paper_trading.PaperTradeError as exc:
            await db.rollback()
            raise HTTPException(400, str(exc))
    db.add(Order(
        user_id=uid, symbol=body.symbol, name=body.name, order_type=body.order_type,
        quantity=body.quantity, price=body.price, status="filled", broker=body.broker, source="WEB",
    ))
    await _apply_portfolio(db, uid, body.symbol, body.name,
                           body.order_type, body.quantity, body.price)
    await db.commit()
    await audit(user["id"], "", "order.manual", {
        "symbol": body.symbol, "order_type": body.order_type,
        "quantity": body.quantity, "price": body.price, "broker": body.broker,
    })
    return {"ok": True, "status": "filled"}


@router.get("/orders")
async def order_history(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_pg_session),
):
    result = await db.execute(
        select(Order).where(Order.user_id == _uid(user["id"])).order_by(Order.created_at.desc()).limit(200)
    )
    orders = [{
        "symbol": o.symbol, "name": o.name, "order_type": o.order_type,
        "quantity": o.quantity, "price": o.price, "status": o.status,
        "broker": o.broker, "created_at": o.created_at.isoformat(),
    } for o in result.scalars().all()]
    return {"orders": orders}


# ── 증권사 API 설정 (PostgreSQL) ────────────────────────────────────────

class BrokerSettingsBody(BaseModel):
    """브로커 설정 저장용 입력 모델.

    legacy 프론트(iapi)에서 broker_type/paper_trading 키를 보내므로
    alias를 통해 신규 키(broker/paper)와 함께 병행 지원한다.
    """

    # 레거시/신규 프론트 혼재 환경에서 미사용 필드가 들어와도 저장 API가 깨지지 않도록 무시.
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    # 레거시 프론트(iapi 영역)의 broker_type/paper_trading 페이로드를 계속 수용.
    broker: str = Field(default=DEFAULT_BROKER, alias="broker_type")
    app_key: str = ""
    app_secret: str = ""
    account_no: str = ""
    paper: bool = Field(default=True, alias="paper_trading")


class QuantSettingsBody(BaseModel):
    """퀀트 자동매매 설정 저장용 입력 모델."""

    model_config = ConfigDict(extra="ignore")

    mode: str = Field(default="paper", description="paper | live")
    broker: str = DEFAULT_BROKER
    app_key: str = ""
    app_secret: str = ""
    account_no: str = ""
    symbol_source: str = Field(default="ai", description="ai | manual")
    selected_symbols: list[str] = Field(default_factory=list)
    ai_top_n: int = Field(default=3, ge=1, le=5)
    per_trade_budget: float = Field(default=1_000_000, ge=10_000, le=10_000_000)
    buy_ratio: float = Field(default=1.0, ge=0.1, le=1.0)
    sell_ratio: float = Field(default=0.5, ge=0.1, le=1.0)


async def _get_broker_settings_row(db: AsyncSession, user_id: uuid.UUID) -> BrokerSettings | None:
    result = await db.execute(select(BrokerSettings).where(BrokerSettings.user_id == user_id))
    return result.scalar_one_or_none()


async def _get_or_create_broker_settings_row(db: AsyncSession, user_id: uuid.UUID) -> BrokerSettings:
    row = await _get_broker_settings_row(db, user_id)
    if row is None:
        row = BrokerSettings(user_id=user_id)
        db.add(row)
        await db.flush()
    return row


@router.get("/broker/catalog")
async def broker_catalog():
    return {"brokers": get_broker_catalog()}


@router.post("/broker/settings")
async def save_broker_settings(
    body: BrokerSettingsBody,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_pg_session),
):
    broker = (body.broker or DEFAULT_BROKER).strip().lower()
    if broker not in get_broker_codes():
        raise HTTPException(422, f"지원하지 않는 broker: {broker}")

    row = await _get_or_create_broker_settings_row(db, _uid(user["id"]))
    row.broker = broker
    row.app_key = body.app_key
    row.app_secret = body.app_secret
    row.account_no = body.account_no
    row.paper = body.paper
    await db.commit()
    await audit(user["id"], "", "broker.settings.save", {"broker": broker, "paper": body.paper})
    return {"ok": True}


@router.get("/broker/settings")
async def get_broker_settings(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_pg_session),
):
    catalog = get_broker_catalog()
    row = await _get_broker_settings_row(db, _uid(user["id"]))
    if not row:
        return {
            "broker": DEFAULT_BROKER,
            "connected": False,
            "account_no": "",
            "paper": True,
            "brokers": catalog,
        }
    masked = row.app_key[:4] + "****" if row.app_key else ""
    return {
        "broker":     row.broker,
        "connected":  bool(row.app_key),
        "app_key":    masked,
        "account_no": row.account_no,
        "paper":      row.paper,
        "brokers": catalog,
    }


@router.get("/quant/settings")
async def get_quant_settings(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_pg_session),
):
    catalog = get_broker_catalog()
    stocks = QUANT_STOCKS
    row = await _get_broker_settings_row(db, _uid(user["id"]))
    if not row:
        return {
            "mode": "paper", "broker": DEFAULT_BROKER, "connected": False, "app_key": "",
            "account_no": "", "paper": True, "symbol_source": "ai", "selected_symbols": [],
            "ai_top_n": 3, "per_trade_budget": 1_000_000.0, "buy_ratio": 1.0, "sell_ratio": 0.5,
            "brokers": catalog, "stocks": stocks,
        }

    mode = row.quant_mode if row.quant_mode in ("paper", "live") else ("live" if row.paper is False else "paper")
    source = row.quant_symbol_source if row.quant_symbol_source in ("ai", "manual") else "ai"
    masked = row.app_key[:4] + "****" if row.app_key else ""

    return {
        "mode": mode,
        "broker": row.broker,
        "connected": bool(row.app_key),
        "app_key": masked,
        "account_no": row.account_no,
        "paper": mode == "paper",
        "symbol_source": source,
        "selected_symbols": list(row.quant_selected_symbols or []),
        "ai_top_n": row.quant_ai_top_n,
        "per_trade_budget": row.quant_per_trade_budget,
        "buy_ratio": row.quant_buy_ratio,
        "sell_ratio": row.quant_sell_ratio,
        "brokers": catalog,
        "stocks": stocks,
    }


@router.post("/quant/settings")
async def save_quant_settings(
    body: QuantSettingsBody,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_pg_session),
):
    broker = (body.broker or DEFAULT_BROKER).strip().lower()
    if broker not in get_broker_codes():
        raise HTTPException(422, f"지원하지 않는 broker: {broker}")

    mode = (body.mode or "paper").strip().lower()
    if mode not in ("paper", "live"):
        raise HTTPException(422, "mode는 paper 또는 live 이어야 합니다.")

    symbol_source = (body.symbol_source or "ai").strip().lower()
    if symbol_source not in ("ai", "manual"):
        raise HTTPException(422, "symbol_source는 ai 또는 manual 이어야 합니다.")

    valid_symbols = {s["symbol"] for s in QUANT_STOCKS}
    selected = [s for s in (body.selected_symbols or []) if s in valid_symbols]

    row = await _get_or_create_broker_settings_row(db, _uid(user["id"]))
    row.broker = broker
    row.app_key = body.app_key
    row.app_secret = body.app_secret
    row.account_no = body.account_no
    row.paper = mode == "paper"
    row.quant_mode = mode
    row.quant_symbol_source = symbol_source
    row.quant_selected_symbols = selected
    row.quant_ai_top_n = body.ai_top_n
    row.quant_per_trade_budget = body.per_trade_budget
    row.quant_buy_ratio = body.buy_ratio
    row.quant_sell_ratio = body.sell_ratio
    await db.commit()
    await audit(user["id"], "", "quant.settings.save", {
        "broker": broker, "mode": mode, "symbol_source": symbol_source,
    })
    return {"ok": True}


async def _get_broker_client(user: dict, db: AsyncSession):
    row = await _get_broker_settings_row(db, _uid(user["id"]))
    if not row:
        return get_broker_client("mock")
    return get_broker_client(
        broker     = row.broker or "mock",
        app_key    = row.app_key,
        app_secret = row.app_secret,
        paper      = row.paper,
    )


# ── 증권사 API 실시간 조회 ────────────────────────────────────────────

@router.get("/broker/price")
async def broker_price(
    symbol: str = Query(...),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_pg_session),
):
    client = await _get_broker_client(user, db)
    try:
        info = await client.get_price(symbol)
        return {
            "symbol": info.symbol, "name": info.name,
            "current": info.current, "open": info.open,
            "high": info.high, "low": info.low,
            "volume": info.volume, "change": info.change, "change_pct": info.change_pct,
        }
    except Exception as e:
        raise HTTPException(502, f"증권사 API 오류: {e}")


@router.get("/broker/balance")
async def broker_balance(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_pg_session),
):
    client = await _get_broker_client(user, db)
    row = await _get_broker_settings_row(db, _uid(user["id"]))
    account_no = row.account_no if row else ""
    try:
        bal = await client.get_balance(account_no)
        return {
            "total_eval": bal.total_eval,
            "total_buy":  bal.total_buy,
            "total_gain": bal.total_gain,
            "holdings": [
                {"symbol": h.symbol, "name": h.name, "quantity": h.quantity,
                 "avg_price": h.avg_price, "current_price": h.current_price,
                 "eval_amount": h.eval_amount, "gain_loss": h.gain_loss,
                 "gain_pct": h.gain_pct}
                for h in bal.holdings
            ],
        }
    except Exception as e:
        raise HTTPException(502, f"증권사 API 오류: {e}")


@router.get("/broker/ohlcv")
async def broker_ohlcv(
    symbol: str = Query(...),
    start: str = Query(...),
    end: str = Query(...),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_pg_session),
):
    client = await _get_broker_client(user, db)
    try:
        rows = await client.get_daily_ohlcv(symbol, start, end)
        return {"candles": rows}
    except Exception as e:
        raise HTTPException(502, f"증권사 API 오류: {e}")


class BrokerOrderBody(BaseModel):
    symbol:   str
    side:     str
    quantity: int
    price:    float


@router.post("/broker/order")
async def broker_order(
    body: BrokerOrderBody,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_pg_session),
):
    client = await _get_broker_client(user, db)
    row = await _get_broker_settings_row(db, _uid(user["id"]))
    account_no = row.account_no if row else ""
    broker_name = row.broker if row else "mock"

    # ── 매수 주문 시 예수금 사전 확인 ──────────────────────────────────────
    if body.side == "buy":
        bal_err: Exception | None = None
        try:
            bal = await client.get_balance(account_no)
        except Exception as e:
            # 잔고 조회 실패 시 주문은 계속 진행 (경고 로그만)
            logging.getLogger(__name__).warning("잔고 조회 실패 (주문 진행): %s", e)
            bal_err = e

        if bal_err is None:
            required = body.price * body.quantity
            if bal.cash < required:
                await notification.notify_insufficient_funds(
                    symbol    = body.symbol,
                    side      = body.side,
                    quantity  = body.quantity,
                    price     = body.price,
                    required  = required,
                    available = bal.cash,
                    user_id   = user["id"],
                )
                raise HTTPException(
                    422,
                    f"예수금 부족: 필요 {required:,.0f}원 / 가용 {bal.cash:,.0f}원",
                )

    try:
        result = await client.place_order(account_no, body.symbol, body.side,
                                          body.quantity, body.price)
        await notification.notify_order_placed(
            symbol   = body.symbol,
            side     = body.side,
            quantity = body.quantity,
            price    = body.price,
            user_id  = user["id"],
        )
        await audit(user["id"], "", "order.broker", {
            "broker": broker_name, "symbol": body.symbol,
            "side": body.side, "quantity": body.quantity, "price": body.price,
        })
        return {"ok": True, "result": result}
    except Exception as e:
        await notification.notify_order_error(
            symbol   = body.symbol,
            side     = body.side,
            quantity = body.quantity,
            price    = body.price,
            error    = str(e),
            user_id  = user["id"],
        )
        await audit(user["id"], "", "order.broker.error", {
            "broker": broker_name, "symbol": body.symbol,
            "side": body.side, "error": str(e),
        })
        raise HTTPException(502, f"증권사 API 주문 오류: {e}")


@router.get("/broker/test")
async def broker_test(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_pg_session),
):
    """증권사 연결 테스트용 간단 시세 조회."""
    client = await _get_broker_client(user, db)
    try:
        info = await client.get_price("005930.KS")
        return {"ok": True, "broker_price": {"symbol": info.symbol, "current": info.current}}
    except Exception as e:
        raise HTTPException(502, f"증권사 API 연결 테스트 오류: {e}")


# ── 자동매매 제어 ─────────────────────────────────────────────────────

@router.post("/auto-trade/start")
async def start_auto_trade(user=Depends(get_current_user)):
    started = auto_trade.start_auto_trade(user.get("id", "quant_system"))
    return {"ok": True, "started": started}


@router.post("/auto-trade/stop")
async def stop_auto_trade(user=Depends(get_current_user)):
    stopped = auto_trade.stop_auto_trade()
    return {"ok": True, "stopped": stopped}


@router.get("/auto-trade/status")
async def auto_trade_status(user=Depends(get_current_user)):
    return auto_trade.get_status()


@router.post("/quant/auto/start")
async def quant_auto_start(user=Depends(get_current_user)):
    """기존 프론트 호환 경로."""
    started = auto_trade.start_auto_trade(user.get("id", "quant_system"))
    return {"ok": True, "started": started}


@router.post("/quant/auto/stop")
async def quant_auto_stop(user=Depends(get_current_user)):
    """기존 프론트 호환 경로."""
    stopped = auto_trade.stop_auto_trade()
    return {"ok": True, "stopped": stopped}


@router.get("/quant/auto/status")
async def quant_auto_status(user=Depends(get_current_user)):
    """모의 투자 의사결정 UI용 최근 사이클 결과."""
    status = auto_trade.get_status()
    logs, signals = [], []
    for cycle in status.get("log", [])[-10:]:
        for sig in cycle.get("signals", []):
            action = sig.get("action", "관망")
            signals.append({
                **sig,
                "signal": "BUY" if "매수" in action else "SELL" if "매도" in action else "HOLD",
            })
        account = cycle.get("account")
        if account:
            logs.append({
                "time": cycle.get("time", ""),
                "message": f"모의계좌 평가 {account.get('total_equity', 0):,.0f}원 / 손익 {account.get('pnl_pct', 0):+.2f}%",
            })
        for trade in cycle.get("trades", []):
            logs.append({
                "time": trade.get("time", cycle.get("time", "")),
                "message": f"{trade.get('name', trade.get('symbol', ''))} {trade.get('action', '').upper()} "
                           f"{trade.get('quantity', 0)}주 — {trade.get('reason', '')}",
            })
    return {"running": status["running"], "logs": logs[-50:], "signals": signals[-20:]}


@router.get("/quant/pipeline")
async def quant_pipeline_indicator_backtest(
    symbol: str = Query("005930.KS"),
    period: str = Query("10y"),
    base: str = Query("rsi_ma", description="rsi_ma | macd_bb | volume_rsi | triple_ma"),
    short: int = Query(5, ge=2, le=30),
    mid: int = Query(20, ge=3, le=120),
    rsi: int = Query(14, ge=5, le=40),
    buy_th: float = Query(35.0, ge=5.0, le=50.0),
    strategy: str = Query("custom", description="custom | rsi | ma | bollinger | composite"),
    cost_bps: float = Query(10.0, ge=0.0, le=500.0),
    _user=Depends(get_current_user),
):
    """커스텀 인디케이터 실백테스트."""
    candle_data = await get_candles(symbol, period=period, interval="1d")
    candles = candle_data.get("candles", [])
    if not candles:
        raise HTTPException(404, f"종목 데이터 없음: {symbol}")
    if strategy != "custom":
        if strategy not in ("rsi", "ma", "bollinger", "composite"):
            raise HTTPException(422, "strategy는 custom, rsi, ma, bollinger, composite 중 하나여야 합니다.")
        result = backtest_strategy(candles, strategy=strategy, cost_bps=cost_bps)
    else:
        result = backtest_custom_indicator(
            candles=candles, base=base, short_window=short, mid_window=mid,
            rsi_period=rsi, buy_threshold=buy_th,
        )
    if "error" in result:
        raise HTTPException(422, result["error"])
    result["symbol"] = symbol
    result["period"] = period
    return result


# ── 커스텀 인디케이터 저장/불러오기 ────────────────────────────────────

class CustomIndicatorBody(BaseModel):
    name:          str = Field(..., min_length=1, max_length=60)
    base:          str = Field("rsi_ma", description="rsi_ma | macd_bb | volume_rsi | triple_ma")
    short_window:  int = Field(5,  ge=2,  le=30)
    mid_window:    int = Field(20, ge=3,  le=120)
    rsi_period:    int = Field(14, ge=5,  le=40)
    buy_threshold: float = Field(35.0, ge=5.0, le=50.0)


def _oid(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except Exception:
        raise HTTPException(400, "유효하지 않은 ID입니다.")


def _custom_indicator_to_dict(row: CustomIndicator) -> dict:
    return {
        "id": str(row.id), "name": row.name, "base": row.base,
        "short_window": row.short_window, "mid_window": row.mid_window,
        "rsi_period": row.rsi_period, "buy_threshold": row.buy_threshold,
        "created_at": row.created_at.isoformat(),
    }


@router.get("/custom-indicators")
async def list_custom_indicators(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_pg_session),
):
    """내가 저장한 커스텀 인디케이터 목록."""
    result = await db.execute(
        select(CustomIndicator)
        .where(CustomIndicator.user_id == _uid(user["id"]))
        .order_by(CustomIndicator.created_at.desc())
    )
    items = [_custom_indicator_to_dict(row) for row in result.scalars().all()]
    return {"items": items}


@router.post("/custom-indicators")
async def save_custom_indicator(
    body: CustomIndicatorBody,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_pg_session),
):
    """커스텀 인디케이터 파라미터 조합을 저장."""
    if body.base not in ("rsi_ma", "macd_bb", "volume_rsi", "triple_ma"):
        raise HTTPException(422, "base는 rsi_ma, macd_bb, volume_rsi, triple_ma 중 하나여야 합니다.")
    row = CustomIndicator(
        user_id=_uid(user["id"]), name=body.name, base=body.base,
        short_window=body.short_window, mid_window=body.mid_window,
        rsi_period=body.rsi_period, buy_threshold=body.buy_threshold,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _custom_indicator_to_dict(row)


@router.delete("/custom-indicators/{indicator_id}")
async def delete_custom_indicator(
    indicator_id: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_pg_session),
):
    result = await db.execute(
        select(CustomIndicator).where(
            CustomIndicator.id == _oid(indicator_id), CustomIndicator.user_id == _uid(user["id"])
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "저장된 인디케이터를 찾을 수 없습니다.")
    await db.delete(row)
    await db.commit()
    return {"ok": True}
