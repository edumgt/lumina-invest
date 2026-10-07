"""KIS 모의투자결과 — KIS Testbed 자동매매만 모니터링하는 전용 화면의 데이터 (로보어드바이저 > KIS 모의투자결과).

한 번의 호출로: 배치·공격모드·heartbeat 상태, KIS 계좌(잔고·보유, 봇 관리 종목 표시), 봇 실주문(최근·당일 상태 분포·체결률·
실현손익·슬리피지), 사이클 이력(시스템 사용자), 정합성 점검 결과. 인증 필수(로그인 사용자). 시스템 사용자(배치) 데이터가 중심이고
로그인 사용자의 kis·live 주문도 합친다.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.postgres import get_pg_session
from app.lib.session import get_current_user
from app.models import LiveOrder, Portfolio, PORTFOLIO_BOOK_QUANT
from app.models.base import SYSTEM_USER_ID
from app.services import kis_batch, reconciliation
from app.services.brokers import stock_coin_trade_gateway as gateway
from app.services.data_cache import cache_get
from app.services.stock import QUANT_STOCKS, QUANT_SECTORS

router = APIRouter(prefix="/api/quant/kis", tags=["kis-monitor"])
KST = timezone(timedelta(hours=9))


def _f(v, default=0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def realized_pnl(rows: list) -> dict:
    """FILLED 실주문을 시간순으로 평균단가법 처리해 종목별 실현손익·보유원가를 계산한다."""
    pos: dict[str, dict] = {}
    realized: dict[str, float] = {}
    for r in sorted(rows, key=lambda x: (x.created_at or datetime.min.replace(tzinfo=timezone.utc))):
        if r.status != "FILLED" or not r.filled_quantity or not r.avg_filled_price:
            continue
        code = gateway.normalize_symbol(r.symbol)
        qty, px = int(r.filled_quantity), float(r.avg_filled_price)
        p = pos.setdefault(code, {"qty": 0, "cost": 0.0})
        if str(r.side).upper() == "BUY":
            p["cost"] += qty * px
            p["qty"] += qty
        else:
            avg = (p["cost"] / p["qty"]) if p["qty"] > 0 else px
            sold = min(qty, p["qty"]) if p["qty"] > 0 else qty
            realized[code] = realized.get(code, 0.0) + (px - avg) * sold
            p["qty"] = max(0, p["qty"] - qty)
            p["cost"] = max(0.0, p["qty"] * avg)
    return {"by_symbol": {k: round(v, 2) for k, v in realized.items()}, "total": round(sum(realized.values()), 2),
            "open_cost": {k: {"qty": v["qty"], "avg": round(v["cost"] / v["qty"], 2) if v["qty"] else None} for k, v in pos.items() if v["qty"] > 0}}


def _order_dict(r: LiveOrder) -> dict:
    return {
        "owner": "batch" if r.user_id == SYSTEM_USER_ID else "me",
        "client_order_id": r.client_order_id, "environment": r.environment, "symbol": r.symbol, "name": r.name, "side": r.side,
        "order_type": r.order_type, "quantity": r.quantity, "price": r.price, "order_no": r.order_no, "status": r.status,
        "filled_quantity": r.filled_quantity, "avg_filled_price": r.avg_filled_price, "message": r.message,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _heartbeat_age() -> float | None:
    try:
        from app.tasks.beat_health import heartbeat_age_sec
        return heartbeat_age_sec()
    except Exception:
        return None


@router.get("/monitor")
async def kis_monitor(limit: int = Query(50, ge=1, le=200), cycles: int = Query(12, ge=1, le=48),
                      user=Depends(get_current_user), db: AsyncSession = Depends(get_pg_session)):
    me = uuid.UUID(str(user["id"]))
    now = datetime.now(timezone.utc)
    env = gateway.environment() if gateway.is_configured() else (settings.STOCK_COIN_TRADE_KIS_ENVIRONMENT or "paper")
    out: dict = {"checked_at": now.isoformat(), "environment": env, "gateway_configured": gateway.is_configured()}

    # 배치·공격모드·heartbeat
    out["batch"] = await kis_batch.system_status(db)
    out["aggressive"] = {
        "enabled": bool(settings.QUANT_AGGRESSIVE_MODE), "interval": settings.QUANT_AGGRESSIVE_CANDLE_INTERVAL,
        "take_profit_pct": settings.QUANT_AGGRESSIVE_TAKE_PROFIT_PCT, "stop_loss_pct": settings.QUANT_AGGRESSIVE_STOP_LOSS_PCT,
        "max_buys_per_cycle": settings.QUANT_AGGRESSIVE_MAX_BUYS_PER_CYCLE, "max_sells_per_cycle": settings.QUANT_AGGRESSIVE_MAX_SELLS_PER_CYCLE,
        "force_buy": bool(settings.QUANT_AGGRESSIVE_FORCE_BUY), "order_type": settings.QUANT_AGGRESSIVE_ORDER_TYPE or "LIMIT",
        "cooldown_min": settings.QUANT_AGGRESSIVE_COOLDOWN_MIN, "max_orders_per_day": settings.QUANT_AGGRESSIVE_MAX_ORDERS_PER_DAY,
    }
    out["cycle_sec"] = int(settings.QUANT_CYCLE_SEC)
    out["heartbeat_age_sec"] = _heartbeat_age()
    out["market_data_source"] = settings.MARKET_DATA_SOURCE
    out["universe"] = {"sectors": list(QUANT_SECTORS), "count": len(QUANT_STOCKS)}

    # 봇 실주문 (시스템 + 로그인 사용자)
    owners = [me, SYSTEM_USER_ID] if me != SYSTEM_USER_ID else [me]
    rows = list((await db.execute(select(LiveOrder).where(LiveOrder.user_id.in_(owners)).order_by(LiveOrder.created_at.desc()).limit(500))).scalars().all())
    day_start = now.astimezone(KST).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    today = [r for r in rows if r.created_at and r.created_at.astimezone(timezone.utc) >= day_start]
    counts: dict[str, int] = {}
    for r in today:
        counts[r.status] = counts.get(r.status, 0) + 1
    filled_today = [r for r in today if r.status == "FILLED"]
    slips = [((float(r.avg_filled_price) / float(r.price)) - 1) * 100 for r in filled_today if r.price and r.avg_filled_price]
    pnl = realized_pnl(rows)
    out["orders"] = {
        "recent": [_order_dict(r) for r in rows[:limit]],
        "today_counts": counts, "today_total": len(today), "today_filled": len(filled_today),
        "fill_rate_pct": round(len(filled_today) / len(today) * 100, 1) if today else None,
        "today_buy_amount": round(sum(float(r.avg_filled_price) * int(r.filled_quantity) for r in filled_today if str(r.side).upper() == "BUY" and r.avg_filled_price), 0),
        "today_sell_amount": round(sum(float(r.avg_filled_price) * int(r.filled_quantity) for r in filled_today if str(r.side).upper() == "SELL" and r.avg_filled_price), 0),
        "avg_slippage_pct": round(sum(slips) / len(slips), 3) if slips else None,
        "unresolved": [_order_dict(r) for r in today if r.status in ("UNKNOWN", "LOST", "ERROR")][:20],
        "realized_pnl": pnl,
    }

    # 가상 장부(시스템) — 봇 관리 종목 표시용
    virtual = {}
    for p in (await db.execute(select(Portfolio).where(Portfolio.user_id == SYSTEM_USER_ID, Portfolio.book == PORTFOLIO_BOOK_QUANT))).scalars().all():
        if p.quantity > 0:
            virtual[gateway.normalize_symbol(p.symbol)] = {"quantity": int(p.quantity), "avg_price": float(p.avg_price or 0), "name": p.name}
    out["virtual_positions"] = virtual

    # KIS 계좌
    account: dict = {"connected": False}
    if gateway.is_configured():
        try:
            bal = await gateway.get_balance()
            holdings = []
            bot_codes = set(virtual) | set(pnl["open_cost"])
            for h in bal.get("holdings") or []:
                code = gateway.normalize_symbol(str(h.get("symbol") or ""))
                holdings.append({
                    "symbol": code, "name": h.get("name"), "quantity": int(h.get("quantity") or 0), "avg_price": _f(h.get("avgPrice")),
                    "current_price": _f(h.get("currentPrice")), "eval_amount": _f(h.get("evalAmount")),
                    "profit_loss": _f(h.get("profitLoss")), "profit_loss_rate": _f(h.get("profitLossRate")),
                    "bot_managed": code in bot_codes, "bot_quantity": virtual.get(code, {}).get("quantity", 0),
                })
            cash = _f(bal.get("cashBalance"))
            invested = sum(x["eval_amount"] for x in holdings)
            account = {"connected": True, "cash": cash, "invested": invested, "total": _f(bal.get("totalEvalAmount"), cash + invested),
                       "profit_loss": _f(bal.get("totalProfitLoss")), "holdings": holdings, "positions": len(holdings),
                       "bot_positions": sum(1 for x in holdings if x["bot_managed"])}
        except gateway.GatewayError as e:
            account = {"connected": False, "error": f"[{e.code}] {e}"}
    out["account"] = account

    # 사이클 이력(시스템 사용자)
    log = await cache_get(f"quant:cycle_log:{SYSTEM_USER_ID}", max_age_hours=24 * 30) or {}
    all_cycles = log.get("cycles") or []
    recent = []
    for c in all_cycles[-cycles:]:
        ag = c.get("aggressive") or {}
        risk = c.get("risk") or {}
        trades = [t for t in c.get("trades", []) if t.get("type") == "auto"]
        recent.append({
            "time": c.get("time"), "symbols": (c.get("settings") or {}).get("symbols", []),
            "buy": ag.get("buy", []), "sell": ag.get("sell", {}), "notes": ag.get("notes", []),
            "trades": [{"symbol": t.get("symbol"), "name": t.get("name"), "action": t.get("action"), "quantity": t.get("quantity"),
                        "price": t.get("price"), "live": (t.get("live_order") or {}).get("status"),
                        "live_reason": (t.get("live_order") or {}).get("reason") or (t.get("live_order") or {}).get("error")} for t in trades],
            "skipped": len(risk.get("skipped") or []), "halted": bool(risk.get("halted")), "halt_reason": risk.get("reason"),
            "day_pnl_pct": risk.get("day_pnl_pct"), "equity": (c.get("account") or {}).get("total_equity"),
        })
    out["cycles"] = {"count": len(all_cycles), "last_time": all_cycles[-1].get("time") if all_cycles else None, "recent": list(reversed(recent))}
    out["last_beat_run"] = await cache_get("quant:last_beat_run", max_age_hours=24 * 7)

    # 정합성
    out["reconcile"] = await reconciliation.latest()
    return out
