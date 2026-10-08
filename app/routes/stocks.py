import logging
import uuid
import httpx
from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.postgres import get_pg_session
from app.models.base import SYSTEM_USER_ID
from app.models import Portfolio, Order, BrokerSettings, CustomIndicator, QuantVirtualAccount, LiveOrder, PORTFOLIO_BOOK_PAPER, PORTFOLIO_BOOK_QUANT
from app.lib.session import get_current_user
from app.services.stock import (
    get_quote, get_candles, get_market_summary,
    get_quant_indicators, get_fundamentals, QUANT_STOCKS,
)
from app.services.krx_companies import search_companies
from app.services import auto_trade
from app.services import risk_guard
from app.services import strategy_loader
from app.services.brokers import stock_coin_trade_gateway
from app.services import kis_credentials
from app.services import kis_quickstart
from app.services.quant_pipeline import backtest_custom_indicator
from app.services.investment_research import backtest_strategy, screen_pattern
from app.services.brokers.factory import get_broker_client
from app.services.brokers.catalog import get_broker_catalog, get_broker_codes
from app.services import notification
from app.services.audit import audit
from app.services import paper_trading
from app.services.data_cache import cache_get, cache_set
from app.services.sync_scheduler import KEY_MARKET_INDICES

logger = logging.getLogger(__name__)
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


@router.get("/stocks/sectors")
async def stock_sectors(force: bool = Query(False, description="캐시 무시하고 재계산")):
    """섹터별 투자 인디케이터 — 밸류에이션·수익성·성장·안정성·가격모멘텀·시장폭 + 판단 근거.

    QUANT_STOCKS 유니버스를 섹터로 묶어 실제 펀더멘털·일봉에서 계산한다(1시간 캐시).
    """
    from app.services import sector_indicators
    try:
        return await sector_indicators.sector_overview(force=force)
    except Exception as exc:
        logger.exception("섹터 인디케이터 계산 실패")
        raise HTTPException(502, f"섹터 지표 계산 실패: {exc}") from exc


@router.get("/stocks/signals")
async def stock_signals(
    signal: str = Query("all", description="all | buy | sell"),
    model: str = Query("lightgbm", description="lightgbm | rsi | ma | bollinger"),
    min_confidence: int = Query(65, ge=0, le=100),
    symbols: str | None = Query(None, description="쉼표 구분 종목 코드. 주면 그 종목만(화면의 1종목씩 진행용)"),
):
    """선택한 패턴 모델을 적용한 대표 종목 스크리닝. `symbols` 로 부분 집합만 계산할 수 있다."""
    universe = QUANT_STOCKS
    if symbols:
        wanted = {x.strip().upper() for x in symbols.split(",") if x.strip()}
        universe = [s for s in QUANT_STOCKS if s["symbol"].upper() in wanted]
    rows = []
    for stock in universe:
        candles = (await get_candles(stock["symbol"], period="1y", interval="1d")).get("candles", [])
        if not candles:   # KIS·Yahoo 모두 빈 응답(예: 012510.KQ Yahoo 폴백) → 이 종목만 건너뜀 (이전엔 KeyError → 500)
            logging.getLogger(__name__).warning("스크리닝 캔들 없음 %s", stock["symbol"])
            continue
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
        # XAI: 최근 3시간 내 계산된 LightGBM 설명이 캐시에 있으면 요약만 첨부 (없으면 /api/ml/explain으로 온디맨드 계산)
        cached_ai = await cache_get(f"ai_predict:v3:{stock['symbol']}", max_age_hours=3)
        if cached_ai and cached_ai.get("explanation"):
            ex = cached_ai["explanation"]
            row["xai"] = {"signal_label": ex["signal_label"], "probability_pct": ex["probability_pct"],
                          "summary": ex["summary"], "top_positive": ex["top_positive"][:2], "top_negative": ex["top_negative"][:2]}
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
        select(Portfolio).where(Portfolio.user_id == _uid(user["id"]), Portfolio.book == PORTFOLIO_BOOK_PAPER).order_by(Portfolio.updated_at.desc())
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
        quantity=body.quantity, avg_price=body.avg_price, book=PORTFOLIO_BOOK_PAPER,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[Portfolio.user_id, Portfolio.symbol, Portfolio.book],
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
        select(Portfolio).where(Portfolio.user_id == _uid(user["id"]), Portfolio.symbol == symbol, Portfolio.book == PORTFOLIO_BOOK_PAPER)
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
        select(Portfolio).where(Portfolio.user_id == user_id, Portfolio.symbol == symbol, Portfolio.book == PORTFOLIO_BOOK_PAPER)
    )
    existing = result.scalar_one_or_none()

    if order_type == "buy":
        if existing:
            new_qty = existing.quantity + quantity
            existing.avg_price = (existing.avg_price * existing.quantity + price * quantity) / new_qty
            existing.quantity = new_qty
        else:
            db.add(Portfolio(user_id=user_id, symbol=symbol, name=name, quantity=quantity, avg_price=price, book=PORTFOLIO_BOOK_PAPER))
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


QUANT_MAX_SELECTED_SYMBOLS = 10


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
    # domain-rag-lab 합격 전략 (비우면 기존 규칙)
    strategy_id: str = Field(default="", max_length=40)
    strategy_version: int = Field(default=0, ge=0)
    # 위험관리
    risk_daily_loss_limit_pct: float = Field(default=3.0, ge=0, le=50, description="0이면 비활성")
    risk_max_position_pct: float = Field(default=30.0, ge=0, le=100, description="0이면 비활성")
    risk_max_orders_per_day: int = Field(default=20, ge=0, le=500, description="0이면 비활성")
    risk_cooldown_min: int = Field(default=30, ge=0, le=1440, description="0이면 비활성")


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


def _apply_credentials(row: BrokerSettings, broker: str, app_key: str, app_secret: str, account_no: str) -> None:
    """서버 관리 증권사(KIS)는 사용자가 보낸 키·계좌를 저장하지 않는다 — Secrets Manager 값을 쓴다."""
    if kis_credentials.is_managed(broker):
        row.app_key = row.app_secret = row.account_no = ""
        return
    row.app_key = app_key
    row.app_secret = app_secret
    row.account_no = account_no


async def _resolve_credentials(row: BrokerSettings | None) -> tuple[str, str, str, str, bool]:
    """(broker, app_key, app_secret, account_no, paper). KIS 는 Secrets Manager, 그 외는 DB 행."""
    if not row:
        return "mock", "", "", "", True
    broker = (row.broker or DEFAULT_BROKER).strip().lower()
    if kis_credentials.is_managed(broker):
        creds = await kis_credentials.get_credentials()
        if creds is None:
            return broker, "", "", "", True
        return broker, creds.app_key, creds.app_secret, creds.account_no, creds.paper
    return broker, row.app_key, row.app_secret, row.account_no, row.paper


async def _connection_view(row: BrokerSettings | None) -> dict:
    """화면 표시용 연동 정보. 키 원문은 포함하지 않는다."""
    if not row:
        return {"connected": False, "app_key": "", "account_no": "", "kis_managed": None}
    if kis_credentials.is_managed(row.broker):
        st = await kis_credentials.get_status()
        return {"connected": st["configured"], "app_key": "", "account_no": st["account_masked"], "kis_managed": st}
    return {"connected": bool(row.app_key), "app_key": row.app_key[:4] + "****" if row.app_key else "",
            "account_no": row.account_no, "kis_managed": None}


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
    _apply_credentials(row, broker, body.app_key, body.app_secret, body.account_no)
    row.paper = body.paper
    await db.commit()
    await audit(user["id"], "", "broker.settings.save", {"broker": broker, "paper": body.paper,
                                                          "managed": kis_credentials.is_managed(broker)})
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
    view = await _connection_view(row)
    return {
        "broker":     row.broker,
        "connected":  view["connected"],
        "app_key":    view["app_key"],
        "account_no": view["account_no"],
        "kis_managed": view["kis_managed"],
        "paper":      row.paper,
        "brokers": catalog,
    }


def _live_gateway_info() -> dict:
    """종목 선정 화면 표시용: live 모드 주문이 어디로 나가는지."""
    if stock_coin_trade_gateway.is_configured():
        return {"configured": True, "via": "stock-coin-trade", "environment": stock_coin_trade_gateway.environment(),
                "order_type": stock_coin_trade_gateway.default_order_type()}
    return {"configured": False, "via": "legacy-direct", "environment": "real", "order_type": "LIMIT"}


@router.get("/quant/strategies")
async def list_quant_strategies(user=Depends(get_current_user)):
    """domain-rag-lab 백테스트 합격 전략 목록 (종목 선정 화면 드롭다운)."""
    return {"configured": strategy_loader.is_configured(), "strategies": await strategy_loader.list_strategies()}


@router.get("/quant/live-orders")
async def list_live_orders(
    limit: int = Query(50, ge=1, le=200),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_pg_session),
):
    """게이트웨이 경유 KIS 실주문 추적 목록 (quant.confirm_fills 가 갱신).
    KIS 모의투자 배치가 실행 중이면 시스템 사용자(배치) 주문도 함께 반환한다(owner=batch)."""
    from app.services import kis_batch
    me = _uid(user["id"])
    owners = [me]
    batch = await kis_batch.system_status(db)
    if batch.get("running"):
        owners.append(SYSTEM_USER_ID)
    result = await db.execute(
        select(LiveOrder).where(LiveOrder.user_id.in_(owners)).order_by(LiveOrder.created_at.desc()).limit(limit)
    )
    rows = result.scalars().all()
    return {"gateway": _live_gateway_info(), "batch": batch, "orders": [
        {
            "owner": "batch" if r.user_id == SYSTEM_USER_ID else "me",
            "id": str(r.id), "client_order_id": r.client_order_id, "environment": r.environment, "symbol": r.symbol, "name": r.name,
            "side": r.side, "order_type": r.order_type, "quantity": r.quantity, "price": r.price, "order_no": r.order_no,
            "status": r.status, "filled_quantity": r.filled_quantity, "avg_filled_price": r.avg_filled_price,
            "message": r.message, "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        } for r in rows
    ]}


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
            "risk": risk_guard.RiskLimits().to_dict(), "risk_halt_reason": "",
            "strategy_id": "", "strategy_version": 0,
            "live_gateway": _live_gateway_info(),
            "kis_managed": None, "managed_brokers": sorted(kis_credentials.MANAGED_BROKERS),
            "brokers": catalog, "stocks": stocks,
        }

    mode = row.quant_mode if row.quant_mode in ("paper", "live") else ("live" if row.paper is False else "paper")
    source = row.quant_symbol_source if row.quant_symbol_source in ("ai", "manual") else "ai"
    view = await _connection_view(row)

    return {
        "mode": mode,
        "broker": row.broker,
        "connected": view["connected"],
        "app_key": view["app_key"],
        "account_no": view["account_no"],
        "kis_managed": view["kis_managed"],
        "managed_brokers": sorted(kis_credentials.MANAGED_BROKERS),
        "paper": mode == "paper",
        "symbol_source": source,
        "selected_symbols": list(row.quant_selected_symbols or []),
        "ai_top_n": row.quant_ai_top_n,
        "per_trade_budget": row.quant_per_trade_budget,
        "buy_ratio": row.quant_buy_ratio,
        "sell_ratio": row.quant_sell_ratio,
        "risk": risk_guard.RiskLimits.from_row(row).to_dict(),
        "risk_halt_reason": row.risk_halt_reason,
        "strategy_id": row.quant_strategy_id or "",
        "strategy_version": row.quant_strategy_version or 0,
        "live_gateway": _live_gateway_info(),
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
    requested = [str(s).strip() for s in (body.selected_symbols or []) if str(s).strip()]
    invalid = [s for s in requested if s not in valid_symbols]
    if invalid:
        raise HTTPException(422, f"지원하지 않는 종목코드: {', '.join(invalid[:5])}")
    if len(requested) > QUANT_MAX_SELECTED_SYMBOLS:
        raise HTTPException(422, f"직접 선택 종목은 최대 {QUANT_MAX_SELECTED_SYMBOLS}개까지 가능합니다.")
    if symbol_source == "manual" and not requested:
        raise HTTPException(422, "직접 선택 모드에서는 종목을 1개 이상 선택하세요.")
    selected = list(dict.fromkeys(requested))

    row = await _get_or_create_broker_settings_row(db, _uid(user["id"]))
    row.broker = broker
    _apply_credentials(row, broker, body.app_key, body.app_secret, body.account_no)
    row.paper = mode == "paper"
    row.quant_mode = mode
    row.quant_symbol_source = symbol_source
    row.quant_selected_symbols = selected
    row.quant_ai_top_n = body.ai_top_n
    row.quant_per_trade_budget = body.per_trade_budget
    row.quant_buy_ratio = body.buy_ratio
    row.quant_sell_ratio = body.sell_ratio
    strategy_id = (body.strategy_id or "").strip().lower()
    if strategy_id:
        spec = await strategy_loader.get_strategy(strategy_id, body.strategy_version or None)
        if spec is None:
            raise HTTPException(422, f"domain-rag-lab 에 합격한 전략이 없습니다: {strategy_id}")
        row.quant_strategy_id = strategy_id
        row.quant_strategy_version = int(spec.get("version") or body.strategy_version or 0)
    else:
        row.quant_strategy_id = ""
        row.quant_strategy_version = 0
    row.risk_daily_loss_limit_pct = body.risk_daily_loss_limit_pct
    row.risk_max_position_pct = body.risk_max_position_pct
    row.risk_max_orders_per_day = body.risk_max_orders_per_day
    row.risk_cooldown_min = body.risk_cooldown_min
    await db.commit()
    await audit(user["id"], "", "quant.settings.save", {
        "broker": broker, "mode": mode, "symbol_source": symbol_source,
        "managed": kis_credentials.is_managed(broker),
    })
    return {"ok": True}


async def _get_broker_client(user: dict, db: AsyncSession):
    row = await _get_broker_settings_row(db, _uid(user["id"]))
    broker, app_key, app_secret, _account_no, paper = await _resolve_credentials(row)
    return get_broker_client(broker=broker, app_key=app_key, app_secret=app_secret, paper=paper)


async def _get_broker_client_and_account(user: dict, db: AsyncSession):
    """(client, broker, account_no). KIS 는 계좌번호도 Secrets Manager 값을 쓴다."""
    row = await _get_broker_settings_row(db, _uid(user["id"]))
    broker, app_key, app_secret, account_no, paper = await _resolve_credentials(row)
    return get_broker_client(broker=broker, app_key=app_key, app_secret=app_secret, paper=paper), broker, account_no


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
    client, _broker_name, account_no = await _get_broker_client_and_account(user, db)
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


class ManualKisOrderBody(BaseModel):
    """「기본 인디케이터 전략」 화면의 수동 KIS 모의투자 주문."""
    symbol:   str
    name:     str | None = None
    side:     str = Field(pattern="^(buy|sell)$")
    quantity: int = Field(ge=1, le=10_000)


@router.post("/stocks/quant/manual-order")
async def quant_manual_order(body: ManualKisOrderBody, user=Depends(get_current_user),
                             db: AsyncSession = Depends(get_pg_session)):
    """화면에서 직접 내는 KIS 모의투자 주문 — 자동매매와 같은 게이트웨이·추적 경로를 쓴다.

    가격은 서버가 현재가로 정한다(클라이언트 값 불신). 실전 환경·비상정지는 422 로 막는다.
    """
    try:
        return await auto_trade.place_manual_kis_order(
            db, user["id"], body.symbol, body.name or body.symbol, body.side, body.quantity)
    except auto_trade.ManualOrderBlocked as exc:
        raise HTTPException(422, exc.message)


@router.get("/stocks/quant/order-readiness")
async def quant_order_readiness(user=Depends(get_current_user), db: AsyncSession = Depends(get_pg_session)):
    """거래 버튼을 켜도 되는지 — KIS 연동·환경(모의/실전)·비상정지·장 운영시간."""
    from app.services import kis_quickstart
    from app.services.brokers import stock_coin_trade_gateway as gw
    r = await kis_quickstart.readiness(db, uuid.UUID(user["id"]))
    market_open = gw.is_krx_market_open()
    blocked = (not r["connected"] and "not_connected") or \
              (r["environment"] != "paper" and "real_environment") or \
              (r["kill_switch"] and "kill_switch") or ""
    return {"can_order": not blocked, "reason": blocked, "environment": r["environment"],
            "route": r["route"], "route_detail": r["route_detail"], "connected": r["connected"],
            "kill_switch": r["kill_switch"], "market_open": market_open,
            "enforce_market_hours": gw.enforce_market_hours(),
            "max_quantity": auto_trade.MANUAL_ORDER_MAX_QTY}


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
    client, broker_name, account_no = await _get_broker_client_and_account(user, db)

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


# ── 차트 패턴 · 지지/저항 · 멀티타임프레임 ─────────────────────────────

@router.get("/stocks/patterns")
async def stock_patterns(symbol: str = Query("005930.KS"), period: str = Query("1y"), _user=Depends(get_current_user)):
    """캔들 패턴·지지/저항선·돌파 신호 (일봉)."""
    from app.services.patterns import pattern_summary
    candles = (await get_candles(symbol, period=period, interval="1d")).get("candles", [])
    if not candles:
        raise HTTPException(404, f"종목 데이터 없음: {symbol}")
    result = pattern_summary(candles)
    if "error" in result:
        raise HTTPException(422, result["error"])
    return {"symbol": symbol, "candles": candles, **result}


@router.get("/stocks/mtf-signal")
async def stock_mtf_signal(symbol: str = Query("005930.KS"), _user=Depends(get_current_user)):
    """분봉(60m)·일봉·주봉 지표를 종합한 매수·매도·관망 신호와 신뢰도."""
    from app.services.patterns import multi_timeframe_signal
    return await multi_timeframe_signal(symbol)


# ── 자동매매 제어 ─────────────────────────────────────────────────────

@router.post("/auto-trade/start")
async def start_auto_trade(user=Depends(get_current_user), db: AsyncSession = Depends(get_pg_session)):
    row = await _get_broker_settings_row(db, _uid(user["id"]))
    if row and row.risk_kill_switch:
        raise HTTPException(409, f"비상 정지 상태입니다. 해제 후 시작하세요. (사유: {row.risk_halt_reason or '수동 정지'})")
    started = await auto_trade.start_auto_trade(db, user["id"])
    return {"ok": True, "started": started, "scheduler": f"celery-beat ({kis_quickstart.interval_min()}분)"}


@router.post("/auto-trade/stop")
async def stop_auto_trade(user=Depends(get_current_user), db: AsyncSession = Depends(get_pg_session)):
    stopped = await auto_trade.stop_auto_trade_for(db, user["id"])
    return {"ok": True, "stopped": stopped}


@router.get("/auto-trade/status")
async def auto_trade_status(user=Depends(get_current_user), db: AsyncSession = Depends(get_pg_session)):
    return await auto_trade.get_status(db, _uid(user["id"]))


# ── 자동매매 위험관리 ─────────────────────────────────────────────────

class KillSwitchBody(BaseModel):
    enabled: bool
    reason: str = ""


@router.get("/quant/risk/status")
async def quant_risk_status(user=Depends(get_current_user), db: AsyncSession = Depends(get_pg_session)):
    """위험관리 한도·당일 손익·주문 수·비상정지 상태."""
    uid = _uid(user["id"])
    row = await _get_broker_settings_row(db, uid)
    limits = risk_guard.RiskLimits.from_row(row)
    user_key = user.get("id", "quant_system")
    # 자동매매 가상계좌 기준 현재 자산 (현재가 캐시 우선, 없으면 평균단가)
    acc = (await db.execute(select(QuantVirtualAccount).where(QuantVirtualAccount.user_id == uid))).scalar_one_or_none()
    equity = None
    if acc:
        equity = float(acc.cash_balance)
        for p in (await db.execute(select(Portfolio).where(Portfolio.user_id == uid, Portfolio.book == PORTFOLIO_BOOK_QUANT))).scalars().all():
            if p.quantity > 0:
                px = None
                try:
                    px = (await get_quote(p.symbol)).get("price")
                except Exception:
                    pass
                equity += p.quantity * float(px or p.avg_price)
    status = await risk_guard.risk_status(user_key, limits, equity, row.risk_halt_reason if row else "")
    status["auto_trade_running"] = bool(row and row.quant_auto_enabled)
    status["last_cycle_risk"] = (await auto_trade.get_status(db, uid)).get("risk", {})
    return status


@router.post("/quant/risk/kill-switch")
async def quant_kill_switch(body: KillSwitchBody, user=Depends(get_current_user), db: AsyncSession = Depends(get_pg_session)):
    """비상 정지 스위치 ON/OFF. ON이면 자동매매 루프를 즉시 중지한다."""
    row = await _get_or_create_broker_settings_row(db, _uid(user["id"]))
    row.risk_kill_switch = body.enabled
    row.risk_halt_reason = (body.reason or ("사용자 수동 비상 정지" if body.enabled else ""))[:300]
    await db.commit()
    stopped = False
    if body.enabled:
        stopped = await auto_trade.stop_auto_trade_for(db, user["id"])
        await notification.notify_risk_halt(row.risk_halt_reason, user_id=user.get("id"))
    await audit(user["id"], "", "quant.kill_switch", {"enabled": body.enabled, "reason": row.risk_halt_reason})
    return {"ok": True, "kill_switch": row.risk_kill_switch, "auto_trade_stopped": stopped}


# ── 통합 대시보드: KIS 모의투자 원클릭 ───────────────────────────────

@router.get("/quant/kis/quickstart")
async def kis_quickstart_readiness(user=Depends(get_current_user), db: AsyncSession = Depends(get_pg_session)):
    """시작 가능 여부(연동·환경·비상정지)와 현재 자동매매 상태."""
    return await kis_quickstart.readiness(db, _uid(user["id"]))


@router.post("/quant/kis/quickstart")
async def kis_quickstart_start(user=Depends(get_current_user), db: AsyncSession = Depends(get_pg_session)):
    """KIS 모의투자(Testbed) 원클릭 시작: 설정 저장(AI 추천 · live · Testbed 권장 한도) + 자동매매 ON."""
    try:
        return await kis_quickstart.start(db, user["id"])
    except kis_quickstart.QuickstartBlocked as e:
        raise HTTPException(409, e.message)


@router.post("/quant/auto/start")
async def quant_auto_start(user=Depends(get_current_user), db: AsyncSession = Depends(get_pg_session)):
    """기존 프론트 호환 경로."""
    row = await _get_broker_settings_row(db, _uid(user["id"]))
    if row and row.risk_kill_switch:
        raise HTTPException(409, f"비상 정지 상태입니다. 해제 후 시작하세요. (사유: {row.risk_halt_reason or '수동 정지'})")
    started = await auto_trade.start_auto_trade(db, user["id"])
    return {"ok": True, "started": started}


@router.post("/quant/auto/stop")
async def quant_auto_stop(user=Depends(get_current_user), db: AsyncSession = Depends(get_pg_session)):
    """기존 프론트 호환 경로."""
    stopped = await auto_trade.stop_auto_trade_for(db, user["id"])
    return {"ok": True, "stopped": stopped}


@router.get("/quant/auto/status")
async def quant_auto_status(user=Depends(get_current_user), db: AsyncSession = Depends(get_pg_session)):
    """모의 투자 의사결정 UI용 최근 사이클 결과.

    KIS 모의투자 백그라운드 배치(kis_batch)가 실행 중이면 시스템 사용자의 사이클도 합쳐서 보여준다 —
    배치 단독 실행으로 사용자 계정 자동매매가 꺼져 있어도 화면에서 거래가 보이도록. 배치 항목은 [배치] 로 표시.
    """
    from app.services import kis_batch
    status = await auto_trade.get_status(db, _uid(user["id"]))
    sources = [("", status)]
    batch = await kis_batch.system_status(db)
    if batch.get("running"):
        sources.append(("[배치] ", await auto_trade.get_status(db, SYSTEM_USER_ID)))
    logs, signals = [], []
    for prefix, st in sources:
        for cycle in st.get("log", [])[-10:]:
            for sig in cycle.get("signals", []):
                action = sig.get("action", "관망")
                signals.append({
                    **sig, "source": "batch" if prefix else "me", "cycle_time": cycle.get("time", ""),
                    # NONE = 시장 데이터를 못 받아 판단하지 않은 종목. 관망(HOLD)과 구분해야 근거 없는 판단으로 보이지 않는다.
                    "signal": "NONE" if sig.get("error") else
                              "BUY" if "매수" in action else "SELL" if "매도" in action else "HOLD",
                })
            account = cycle.get("account")
            if account:
                logs.append({
                    "time": cycle.get("time", ""),
                    "message": f"{prefix}모의계좌 평가 {account.get('total_equity', 0):,.0f}원 / 손익 {account.get('pnl_pct', 0):+.2f}%",
                })
            ag = cycle.get("aggressive") or {}
            for note in ag.get("notes", []):
                logs.append({"time": cycle.get("time", ""), "message": f"{prefix}[공격 모드] {note}"})
            for skip in (cycle.get("risk") or {}).get("skipped", []):
                logs.append({"time": cycle.get("time", ""),
                             "message": f"{prefix}[위험관리 생략] {skip.get('name', skip.get('symbol', ''))} {str(skip.get('side', '')).upper()} — {skip.get('reason', '')}"})
            for trade in cycle.get("trades", []):
                if trade.get("type") == "risk":
                    continue   # 위 skipped 로 이미 표시
                live = trade.get("live_order") or {}
                live_txt = f" · 실주문 {live.get('status')}" + (f"({live.get('reason') or live.get('error')})" if live.get("reason") or live.get("error") else "") if live else ""
                logs.append({
                    "time": trade.get("time", cycle.get("time", "")),
                    "message": f"{prefix}{trade.get('name', trade.get('symbol', ''))} {trade.get('action', '').upper()} "
                               f"{trade.get('quantity', 0)}주 — {trade.get('reason', '')}{live_txt}",
                })
    logs.sort(key=lambda x: x.get("time") or "")
    from app.services import reconciliation
    try:
        recon = await reconciliation.latest()
    except Exception:
        recon = None
    if recon and recon.get("issues"):
        for i in recon["issues"][:10]:
            logs.append({"time": recon.get("checked_at", ""), "message": f"[정합성] {i['type']} {i.get('symbol', '')} — " +
                         ", ".join(f"{k}={v}" for k, v in i.items() if k not in ("type", "symbol", "detail"))})
    return {"running": status["running"] or bool(batch.get("running")), "me_running": status["running"],
            "batch": batch, "reconcile": recon, "logs": logs[-90:], "signals": signals[-40:]}


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
    cost_bps: float = Query(10.0, ge=0.0, le=500.0, description="수수료(bp, 포지션 변동 시)"),
    slippage_bps: float = Query(0.0, ge=0.0, le=500.0, description="슬리피지(bp, 포지션 변동 시)"),
    stop_loss_pct: float | None = Query(None, ge=0.1, le=90.0, description="손절 % (진입가 대비)"),
    take_profit_pct: float | None = Query(None, ge=0.1, le=500.0, description="익절 % (진입가 대비)"),
    _user=Depends(get_current_user),
):
    """커스텀 인디케이터 실백테스트 (수수료·슬리피지·손절·익절 반영)."""
    candle_data = await get_candles(symbol, period=period, interval="1d")
    candles = candle_data.get("candles", [])
    if not candles:
        raise HTTPException(404, f"종목 데이터 없음: {symbol}")
    if strategy != "custom":
        if strategy not in ("rsi", "ma", "bollinger", "composite"):
            raise HTTPException(422, "strategy는 custom, rsi, ma, bollinger, composite 중 하나여야 합니다.")
        result = backtest_strategy(candles, strategy=strategy, cost_bps=cost_bps, slippage_bps=slippage_bps,
                                   stop_loss_pct=stop_loss_pct, take_profit_pct=take_profit_pct)
    else:
        result = backtest_custom_indicator(
            candles=candles, base=base, short_window=short, mid_window=mid,
            rsi_period=rsi, buy_threshold=buy_th, commission_bps=cost_bps, slippage_bps=slippage_bps,
            stop_loss_pct=stop_loss_pct, take_profit_pct=take_profit_pct,
        )
    if "error" in result:
        raise HTTPException(422, result["error"])
    result["symbol"] = symbol
    result["period"] = period
    # XAI: 같은 종목의 LightGBM 판단 근거(SHAP)가 캐시에 있으면 함께 내려준다 (없으면 /api/ml/explain으로 생성)
    cached_ai = await cache_get(f"ai_predict:v3:{symbol}", max_age_hours=3)
    result["explanation"] = cached_ai.get("explanation") if cached_ai else None
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
