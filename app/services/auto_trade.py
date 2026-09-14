"""10분 주기 자동매매 Agentic AI - PostgreSQL 기반."""
import asyncio
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.stock import get_quant_indicators, QUANT_STOCKS
from app.database.postgres import get_session_factory
from app.models import BrokerSettings, Order, Portfolio, QuantVirtualAccount
from app.models.base import SYSTEM_USER_ID
from app.services import notification
from app.services.audit import audit
from app.services.brokers.factory import get_broker_client

logger = logging.getLogger(__name__)

_auto_trade_task: asyncio.Task | None = None
_trade_log: list[dict] = []
_is_running = False
_auto_trade_user_id = "quant_system"
_INTERVAL_SEC = 600
_INITIAL_CAPITAL = 10_000_000


def _resolve_user_id(raw: str) -> uuid.UUID:
    if raw == "quant_system" or not raw:
        return SYSTEM_USER_ID
    return uuid.UUID(raw)


def get_status() -> dict:
    return {
        "running":      _is_running,
        "user_id":      _auto_trade_user_id,
        "interval_sec": _INTERVAL_SEC,
        "log":          _trade_log[-50:],
    }


def is_running() -> bool:
    """자동매매 루프 실행 상태만 반환."""
    return _is_running


async def _execute_virtual_trade(
    db: AsyncSession,
    user_id: uuid.UUID,
    symbol: str,
    name: str,
    action: str,
    price: float,
    quantity: int,
    reason: str,
) -> dict:
    """가상계좌 체결. 주문 삽입 + 포트폴리오 갱신 + 현금 갱신을 한 트랜잭션으로 커밋한다."""
    now = datetime.now(timezone.utc).isoformat()

    result = await db.execute(select(QuantVirtualAccount).where(QuantVirtualAccount.user_id == user_id))
    account = result.scalar_one_or_none()
    if not account:
        account = QuantVirtualAccount(
            user_id=user_id, initial_capital=float(_INITIAL_CAPITAL), cash_balance=float(_INITIAL_CAPITAL),
        )
        db.add(account)
        await db.flush()

    cash_balance = account.cash_balance
    executed_quantity = quantity

    if action == "buy":
        cost = price * quantity
        if cash_balance < cost:
            max_qty = int(cash_balance // price) if price > 0 else 0
            if max_qty <= 0:
                shortfall = cost - cash_balance
                await db.commit()
                return {
                    "time": now, "symbol": symbol, "name": name,
                    "action": action, "quantity": 0, "price": price,
                    "reason": (
                        f"{reason} | 잔고 부족으로 미체결 "
                        f"(필요 {cost:,.0f}원 / 부족 {shortfall:,.0f}원 / 가용현금 {cash_balance:,.0f}원)"
                    ),
                    "status": "skipped",
                    "cash_balance": round(cash_balance, 2),
                }
            executed_quantity = max_qty

    port_result = await db.execute(select(Portfolio).where(Portfolio.user_id == user_id, Portfolio.symbol == symbol))
    existing = port_result.scalar_one_or_none()

    if action == "sell":
        if not existing or existing.quantity <= 0:
            await db.commit()
            return {
                "time": now, "symbol": symbol, "name": name,
                "action": action, "quantity": 0, "price": price,
                "reason": f"{reason} | 보유 수량 없음",
                "status": "skipped",
                "cash_balance": round(cash_balance, 2),
            }
        executed_quantity = min(quantity, existing.quantity)

    db.add(Order(
        user_id=user_id, symbol=symbol, name=name, order_type=action,
        quantity=executed_quantity, price=price, status="filled", broker="quant_ai", source="QUANT",
    ))

    if action == "buy":
        if existing:
            new_qty = existing.quantity + executed_quantity
            existing.avg_price = (existing.avg_price * existing.quantity + price * executed_quantity) / new_qty
            existing.quantity = new_qty
        else:
            db.add(Portfolio(user_id=user_id, symbol=symbol, name=name, quantity=executed_quantity, avg_price=price))
        cash_balance -= price * executed_quantity
    elif action == "sell":
        new_qty = max(0, existing.quantity - executed_quantity)
        if new_qty == 0:
            await db.delete(existing)
        else:
            existing.quantity = new_qty
        cash_balance += price * executed_quantity

    account.cash_balance = cash_balance
    await db.commit()

    return {
        "time": now, "symbol": symbol, "name": name,
        "action": action, "quantity": executed_quantity, "price": price, "reason": reason,
        "status": "filled",
        "cash_balance": round(cash_balance, 2),
    }


async def _place_live_order(
    broker_row: BrokerSettings | None,
    symbol: str,
    name: str,
    side: str,
    quantity: int,
    price: float,
    user_id: str,
) -> dict | None:
    """실전(live) 모드에서 가상계좌 체결과 별도로 실제 증권사에 주문을 전송한다.

    가상계좌 기록(포트폴리오/현금)은 앱 대시보드 표시용으로 항상 남기고,
    이 함수는 그 위에 실제 브로커 주문을 얹는다. 브로커 미승인/오류 시에도
    자동매매 사이클 자체는 계속 진행되도록 예외를 여기서 흡수한다.
    """
    if not broker_row or broker_row.quant_mode != "live":
        return None
    broker = (broker_row.broker or "mock").strip().lower()
    app_key = broker_row.app_key
    app_secret = broker_row.app_secret
    account_no = broker_row.account_no
    if broker == "mock" or not app_key or not app_secret or not account_no:
        return None

    client = get_broker_client(broker, app_key, app_secret, paper=False)
    try:
        result = await client.place_order(account_no, symbol, side, quantity, price)
        await notification.notify_order_placed(
            symbol=symbol, side=side, quantity=quantity, price=price, user_id=user_id,
        )
        return {"status": "submitted", "broker": broker, "response": result}
    except Exception as e:
        logger.warning("실전 자동매매 주문 실패 (%s, %s %s): %s", broker, side, symbol, e)
        await notification.notify_order_error(
            symbol=symbol, side=side, quantity=quantity, price=price,
            error=str(e), user_id=user_id,
        )
        return {"status": "error", "broker": broker, "error": str(e)}


async def _run_quant_cycle(user_id: str = "quant_system") -> None:
    global _trade_log
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    cycle_log: dict = {"time": now_str, "trades": [], "signals": []}

    try:
        session_factory = get_session_factory()
    except Exception:
        return  # PostgreSQL 미연결 시 스킵

    uid = _resolve_user_id(user_id)

    async with session_factory() as db:
        # 가상계좌 idempotent 초기화
        result = await db.execute(select(QuantVirtualAccount).where(QuantVirtualAccount.user_id == uid))
        if not result.scalar_one_or_none():
            db.add(QuantVirtualAccount(
                user_id=uid, initial_capital=float(_INITIAL_CAPITAL), cash_balance=float(_INITIAL_CAPITAL),
            ))
            await db.commit()

        bs_result = await db.execute(select(BrokerSettings).where(BrokerSettings.user_id == uid))
        broker_row = bs_result.scalar_one_or_none()

        mode = broker_row.quant_mode if broker_row and broker_row.quant_mode in ("paper", "live") else (
            "live" if broker_row and broker_row.paper is False else "paper"
        )
        symbol_source = broker_row.quant_symbol_source if broker_row and broker_row.quant_symbol_source in ("ai", "manual") else "ai"
        selected_symbols = list(broker_row.quant_selected_symbols or []) if broker_row else []
        ai_top_n = max(1, min(int(broker_row.quant_ai_top_n if broker_row else 3), len(QUANT_STOCKS)))
        per_trade_budget = float(broker_row.quant_per_trade_budget if broker_row else 1_000_000)
        per_trade_budget = max(10_000.0, min(per_trade_budget, 10_000_000.0))
        buy_ratio = float(broker_row.quant_buy_ratio if broker_row else 1.0)
        buy_ratio = max(0.1, min(buy_ratio, 1.0))
        sell_ratio = float(broker_row.quant_sell_ratio if broker_row else 0.5)
        sell_ratio = max(0.1, min(sell_ratio, 1.0))

        price_map: dict[str, float] = {}
        indicator_map: dict[str, dict] = {}
        stock_map = {s["symbol"]: s for s in QUANT_STOCKS}

        for stock in QUANT_STOCKS:
            try:
                indicators = await get_quant_indicators(stock["symbol"], "2y")
                indicator_map[stock["symbol"]] = indicators
                signal = indicators.get("signal", {})
                price = indicators.get("current_price")
                if not price:
                    continue
                price_map[stock["symbol"]] = float(price)

            except Exception:
                logger.exception("자동매매 지표 계산 실패: %s", stock["symbol"])
                cycle_log["signals"].append({"symbol": stock["symbol"], "error": "지표 계산 실패"})

        if symbol_source == "manual":
            target_symbols = [s for s in selected_symbols if s in stock_map]
            if not target_symbols:
                target_symbols = [s["symbol"] for s in QUANT_STOCKS[:ai_top_n]]
        else:
            ranked = []
            for stock in QUANT_STOCKS:
                indicators = indicator_map.get(stock["symbol"], {})
                sig = indicators.get("signal", {})
                score = sig.get("score", 0)
                ranked.append((stock["symbol"], score))
            ranked.sort(key=lambda item: item[1], reverse=True)
            target_symbols = [sym for sym, _ in ranked[:ai_top_n]]

        cycle_log["settings"] = {
            "mode": mode,
            "symbol_source": symbol_source,
            "symbols": target_symbols,
            "per_trade_budget": per_trade_budget,
            "buy_ratio": buy_ratio,
            "sell_ratio": sell_ratio,
        }

        for symbol in target_symbols:
            stock = stock_map.get(symbol)
            if not stock:
                continue
            indicators = indicator_map.get(symbol) or {}
            signal = indicators.get("signal", {})
            price = indicators.get("current_price")
            if not price:
                continue

            action = signal.get("action", "관망")
            reasons = signal.get("reasons", [])
            score = signal.get("score", 0)
            cycle_log["signals"].append({
                "symbol": stock["symbol"], "name": stock["name"],
                "price": price, "action": action, "score": score,
            })

            if action in ("강력 매수", "매수"):
                budget = per_trade_budget * buy_ratio
                qty = max(1, int(budget / price))
                trade = await _execute_virtual_trade(
                    db, uid, stock["symbol"], stock["name"],
                    "buy", price, qty, f"[{mode}] " + " | ".join(reasons),
                )
                cycle_log["trades"].append({**trade, "type": "auto"})
                if trade.get("status") == "filled":
                    await notification.notify_auto_trade_executed(
                        symbol   = stock["symbol"],
                        name     = stock["name"],
                        action   = "buy",
                        quantity = trade.get("quantity", qty),
                        price    = price,
                        reason   = " | ".join(reasons),
                        user_id  = user_id,
                    )
                    live_result = await _place_live_order(
                        broker_row, stock["symbol"], stock["name"], "buy",
                        trade.get("quantity", qty), price, user_id,
                    )
                    if live_result:
                        cycle_log["trades"][-1]["live_order"] = live_result

            elif action in ("강력 매도", "매도"):
                port_result = await db.execute(
                    select(Portfolio).where(Portfolio.user_id == uid, Portfolio.symbol == stock["symbol"])
                )
                existing = port_result.scalar_one_or_none()
                if existing and existing.quantity > 0:
                    qty = max(1, int(existing.quantity * sell_ratio))
                    trade = await _execute_virtual_trade(
                        db, uid, stock["symbol"], stock["name"],
                        "sell", price, qty, f"[{mode}] " + " | ".join(reasons),
                    )
                    cycle_log["trades"].append({**trade, "type": "auto"})
                    if trade.get("status") == "filled":
                        await notification.notify_auto_trade_executed(
                            symbol   = stock["symbol"],
                            name     = stock["name"],
                            action   = "sell",
                            quantity = trade.get("quantity", qty),
                            price    = price,
                            reason   = " | ".join(reasons),
                            user_id  = user_id,
                        )
                        live_result = await _place_live_order(
                            broker_row, stock["symbol"], stock["name"], "sell",
                            trade.get("quantity", qty), price, user_id,
                        )
                        if live_result:
                            cycle_log["trades"][-1]["live_order"] = live_result

        acc_result = await db.execute(select(QuantVirtualAccount).where(QuantVirtualAccount.user_id == uid))
        account = acc_result.scalar_one_or_none()
        cash_balance = float(account.cash_balance) if account else float(_INITIAL_CAPITAL)

        holdings_value = 0.0
        pf_result = await db.execute(select(Portfolio).where(Portfolio.user_id == uid))
        for p in pf_result.scalars().all():
            if p.quantity <= 0:
                continue
            if p.symbol not in price_map:
                logger.warning("현재가 미수신되어 평균단가 사용: user=%s symbol=%s", user_id, p.symbol)
            mark_price = float(price_map.get(p.symbol, p.avg_price))
            holdings_value += p.quantity * mark_price

    total_equity = cash_balance + holdings_value
    initial_capital = float(account.initial_capital) if account else float(_INITIAL_CAPITAL)
    pnl_pct = round((total_equity / initial_capital - 1) * 100, 2) if initial_capital > 0 else None
    cycle_log["account"] = {
        "initial_capital": initial_capital,
        "cash_balance": round(cash_balance, 2),
        "holdings_value": round(holdings_value, 2),
        "total_equity": round(total_equity, 2),
        "pnl_pct": pnl_pct,
    }

    _trade_log.append(cycle_log)
    if len(_trade_log) > 100:
        _trade_log = _trade_log[-100:]


async def _auto_trade_loop(user_id: str) -> None:
    global _is_running
    _is_running = True
    try:
        while True:
            await _run_quant_cycle(user_id)
            await asyncio.sleep(_INTERVAL_SEC)
    except asyncio.CancelledError:
        pass
    finally:
        _is_running = False


def start_auto_trade(user_id: str = "quant_system") -> bool:
    global _auto_trade_task, _is_running, _auto_trade_user_id
    if _auto_trade_task and not _auto_trade_task.done():
        return False
    _auto_trade_user_id = user_id or "quant_system"
    _auto_trade_task = asyncio.create_task(_auto_trade_loop(_auto_trade_user_id))
    asyncio.create_task(notification.notify_auto_trade_started(user_id=_auto_trade_user_id))
    asyncio.create_task(audit(_auto_trade_user_id, "", "auto_trade.start", {}))
    return True


def stop_auto_trade() -> bool:
    global _auto_trade_task
    if _auto_trade_task and not _auto_trade_task.done():
        _auto_trade_task.cancel()
        asyncio.create_task(notification.notify_auto_trade_stopped(user_id=_auto_trade_user_id))
        asyncio.create_task(audit(_auto_trade_user_id, "", "auto_trade.stop", {}))
        return True
    return False
