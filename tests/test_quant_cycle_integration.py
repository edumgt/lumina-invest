"""_run_quant_cycle 통합: 매수 시그널 → 가상 체결 → 게이트웨이 실주문 → live_orders 기록 (live) / 게이트웨이 미호출 (paper).

DB 는 select 대상 엔티티별로 응답하는 가짜 AsyncSession, 지표·가상체결·알림·Redis 는 패치, 게이트웨이는 MockTransport.
"""
import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.config import settings
from app.models import BrokerSettings, LiveOrder, Portfolio, QuantVirtualAccount
from app.services import auto_trade
from app.services.brokers import stock_coin_trade_gateway as gw

UID = uuid.UUID("0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0")
STOCKS = [{"symbol": "005930.KS", "name": "삼성전자"}, {"symbol": "000660.KS", "name": "SK하이닉스"}]


def broker(mode="live", **over):
    base = dict(quant_mode=mode, paper=(mode == "paper"), broker="kis", app_key="", app_secret="", account_no="",
                quant_symbol_source="manual", quant_selected_symbols=["005930.KS"], quant_ai_top_n=1,
                quant_per_trade_budget=300_000.0, quant_buy_ratio=1.0, quant_sell_ratio=0.5,
                risk_daily_loss_limit_pct=3.0, risk_max_position_pct=30.0, risk_max_orders_per_day=20, risk_cooldown_min=30,
                risk_kill_switch=False, risk_halt_reason="", quant_auto_enabled=True, quant_strategy_id="", quant_strategy_version=0)
    base.update(over)
    return SimpleNamespace(**base)


class Result:
    def __init__(self, one=None, many=()): self._one, self._many = one, list(many)
    def scalar_one_or_none(self): return self._one
    def scalars(self): return self
    def all(self): return self._many


class FakeDb:
    def __init__(self, broker_row, live_rows=()):
        self.broker_row, self.live_rows, self.added, self.commits = broker_row, list(live_rows), [], 0
    def add(self, obj): self.added.append(obj)
    async def commit(self): self.commits += 1
    async def execute(self, stmt, *_a, **_k):
        entity = stmt.column_descriptions[0]["entity"]
        if entity is QuantVirtualAccount:
            return Result(one=SimpleNamespace(cash_balance=9_700_000.0, initial_capital=10_000_000.0))
        if entity is BrokerSettings:
            return Result(one=self.broker_row)
        if entity is Portfolio:
            return Result(one=None, many=[])
        if entity is LiveOrder:
            return Result(many=self.live_rows)
        raise AssertionError(f"unexpected select: {entity}")


class Factory:
    def __init__(self, db): self.db = db
    def __call__(self): return self
    async def __aenter__(self): return self.db
    async def __aexit__(self, *a): return False


def fake_gateway():
    calls = []
    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        calls.append((request.method, request.url.path, body))
        if request.url.path.endswith("/balance"):
            return httpx.Response(200, json={"ok": True, "balance": {"totalEvalAmount": 10_000_000, "cashBalance": 1, "holdings": []}})
        if request.url.path.endswith("/order-approval"):
            return httpx.Response(200, json={"ok": True, "approvalToken": "t", "expiresIn": 60})
        if request.url.path.endswith("/kis/orders") and request.method == "POST":
            return httpx.Response(200, json={"ok": True, "duplicate": False, "order": {"orderNo": "0000031000", "status": "ACCEPTED", "message": "ok"}})
        return httpx.Response(404, json={"ok": False, "error": "NOT_FOUND", "message": request.url.path})
    gw.set_transport(httpx.MockTransport(handler))
    return calls


async def indicators(symbol, period="2y"):
    if symbol == "005930.KS":
        return {"signal": {"action": "매수", "score": 3, "reasons": ["골든크로스"]}, "current_price": 70_000.0}
    return {"signal": {"action": "관망", "score": 0, "reasons": []}, "current_price": 200_000.0}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_BASE_URL", "https://sct.test")
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_API_KEY", "key")
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_KIS_ENVIRONMENT", "paper")
    yield
    gw.set_transport(None)


def run_cycle(broker_row, live_rows=()):
    db = FakeDb(broker_row, live_rows)
    persisted = {}
    async def persist(uid, log): persisted.update(log)
    async def virtual_trade(db_, uid, symbol, name, action, price, qty, reason):
        return {"status": "filled", "symbol": symbol, "action": action, "quantity": qty, "price": price}
    with patch.object(auto_trade, "get_session_factory", return_value=Factory(db)), \
         patch.object(auto_trade, "QUANT_STOCKS", STOCKS), \
         patch.object(auto_trade, "get_quant_indicators", indicators), \
         patch.object(auto_trade, "_execute_virtual_trade", virtual_trade), \
         patch.object(auto_trade, "_equity_snapshot", AsyncMock(return_value=(9_700_000.0, 10_000_000.0, {}))), \
         patch.object(auto_trade, "_persist_cycle", persist), \
         patch.object(auto_trade.risk_guard, "day_start_equity", AsyncMock(return_value=10_000_000.0)), \
         patch.object(auto_trade.risk_guard, "orders_today", AsyncMock(return_value=0)), \
         patch.object(auto_trade.risk_guard, "acquire_order_slot", AsyncMock(return_value=True)), \
         patch.object(auto_trade.risk_guard, "increment_orders_today", AsyncMock(return_value=1)), \
         patch.object(auto_trade.risk_guard, "release_order_slot", AsyncMock()), \
         patch.object(auto_trade.risk_guard, "_redis", return_value=object()), \
         patch.object(auto_trade.notification, "notify_auto_trade_executed", AsyncMock()), \
         patch.object(auto_trade.notification, "notify_order_placed", AsyncMock()), \
         patch.object(auto_trade.notification, "notify_order_error", AsyncMock()), \
         patch.object(gw, "is_krx_market_open", return_value=True):
        asyncio.run(auto_trade._run_quant_cycle(str(UID)))
    return db, persisted


def test_live_mode_routes_buy_signal_through_gateway_and_tracks_live_order():
    calls = fake_gateway()
    db, log = run_cycle(broker("live"))
    trades = [t for t in log["trades"] if t.get("type") == "auto"]
    assert len(trades) == 1 and trades[0]["quantity"] == 4           # 300,000 / 70,000 → 4주
    live = trades[0]["live_order"]
    assert live["status"] == "submitted" and live["order_no"] == "0000031000" and live["environment"] == "paper"
    paths = [p for _, p, _ in calls]
    assert paths == ["/openapi/v1/kis/balance", "/openapi/v1/kis/order-approval", "/openapi/v1/kis/orders"]
    rows = [a for a in db.added if isinstance(a, LiveOrder)]
    assert len(rows) == 1 and rows[0].status == "ACCEPTED" and rows[0].quantity == 4 and rows[0].symbol == "005930.KS"
    assert log["risk"]["live"]["breached"] is False and log["settings"]["mode"] == "live"


def test_paper_mode_never_calls_gateway():
    calls = fake_gateway()
    db, log = run_cycle(broker("paper"))
    trades = [t for t in log["trades"] if t.get("type") == "auto"]
    assert len(trades) == 1 and "live_order" not in trades[0]
    assert calls == [] and not any(isinstance(a, LiveOrder) for a in db.added)


def test_open_live_buy_orders_count_toward_position_cap():
    fake_gateway()
    # 이미 ACCEPTED 매수 실주문 40주×70,000 = 2,800,000 이 열려 있고 한도 30%(3,000,000) → 남은 여유 200,000 → 2주만 허용
    open_row = LiveOrder(user_id=UID, client_order_id="c0", environment="paper", symbol="005930.KS", name="삼성전자", side="BUY",
                         order_type="LIMIT", quantity=40, price=70_000.0, status="ACCEPTED", filled_quantity=0)
    db, log = run_cycle(broker("live"), live_rows=[open_row])
    assert log["risk"]["open_live_exposure"] == {"005930.KS": 2_800_000.0}
    trades = [t for t in log["trades"] if t.get("type") == "auto"]
    assert trades[0]["quantity"] == 2


def test_kill_switch_blocks_cycle_without_orders():
    calls = fake_gateway()
    with patch.object(auto_trade, "set_enabled", AsyncMock()):
        db, log = run_cycle(broker("live", risk_kill_switch=True, risk_halt_reason="수동 정지"))
    assert log["risk"]["halted"] is True and log.get("trades") == [] and calls == []
