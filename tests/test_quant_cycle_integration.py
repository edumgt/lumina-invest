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
    def __init__(self, broker_row, live_rows=(), portfolio=()):
        self.broker_row, self.live_rows, self.added, self.commits = broker_row, list(live_rows), [], 0
        self.portfolio, self.virtual_calls = list(portfolio), []
    def add(self, obj): self.added.append(obj)
    async def commit(self): self.commits += 1
    async def execute(self, stmt, *_a, **_k):
        entity = stmt.column_descriptions[0]["entity"]
        if entity is QuantVirtualAccount:
            return Result(one=SimpleNamespace(cash_balance=9_700_000.0, initial_capital=10_000_000.0))
        if entity is BrokerSettings:
            return Result(one=self.broker_row)
        if entity is Portfolio:
            return Result(one=self.portfolio[0] if self.portfolio else None, many=self.portfolio)
        if entity is LiveOrder:
            return Result(many=self.live_rows)
        raise AssertionError(f"unexpected select: {entity}")


class Factory:
    def __init__(self, db): self.db = db
    def __call__(self): return self
    async def __aenter__(self): return self.db
    async def __aexit__(self, *a): return False


def fake_gateway(holdings=(), balance_error=False, order_error=False):
    calls = []
    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        calls.append((request.method, request.url.path, body))
        if request.url.path.endswith("/balance"):
            if balance_error:
                return httpx.Response(502, json={"ok": False, "error": "KIS_UNAVAILABLE", "message": "잔고 조회 실패"})
            return httpx.Response(200, json={"ok": True, "balance": {"totalEvalAmount": 10_000_000, "cashBalance": 9_000_000, "holdings": list(holdings)}})
        if request.url.path.endswith("/order-approval"):
            return httpx.Response(200, json={"ok": True, "approvalToken": "t", "expiresIn": 60})
        if request.url.path.endswith("/kis/orders") and request.method == "POST":
            if order_error:
                return httpx.Response(409, json={"ok": False, "error": "INSUFFICIENT_HOLDINGS", "message": "보유수량 부족"})
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


def run_cycle(broker_row, live_rows=(), portfolio=(), market_open=True, signal_fn=indicators):
    db = FakeDb(broker_row, live_rows, portfolio)
    persisted = {}
    async def persist(uid, log): persisted.update(log)
    async def virtual_trade(db_, uid, symbol, name, action, price, qty, reason):
        db.virtual_calls.append((symbol, action, qty))
        return {"status": "filled", "symbol": symbol, "action": action, "quantity": qty, "price": price}
    with patch.object(auto_trade, "get_session_factory", return_value=Factory(db)), \
         patch.object(auto_trade, "QUANT_STOCKS", STOCKS), \
         patch.object(auto_trade, "get_quant_indicators", signal_fn), \
         patch("app.services.korea_market_scan.candidate_stocks", AsyncMock(return_value=STOCKS)), \
         patch("app.services.korea_universe.refresh_master", AsyncMock()), \
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
         patch.object(auto_trade.notification, "notify_risk_skip", AsyncMock()), \
         patch.object(gw, "is_krx_market_open", return_value=market_open):
        asyncio.run(auto_trade._run_quant_cycle(str(UID)))
    return db, persisted


def test_live_mode_routes_buy_signal_through_gateway_and_tracks_live_order():
    calls = fake_gateway()
    db, log = run_cycle(broker("live"))
    trades = [t for t in log["trades"] if t.get("type") == "auto"]
    assert len(trades) == 1 and trades[0]["quantity"] == 4           # 300,000 / 70,000 → 4주
    live = trades[0]["live_order"]
    assert db.virtual_calls == [] and trades[0]["status"] == "submitted"
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


async def sell_signal(symbol, period="2y"):
    return {"signal": {"action": "매도", "score": -3, "reasons": ["손절"]}, "current_price": 70_000.0}


def virtual_position(qty=24):
    return SimpleNamespace(symbol="005930.KS", name="삼성전자", quantity=qty, avg_price=70_000.0)


def actual_position(qty=3):
    return {"symbol": "005930", "name": "삼성전자", "quantity": qty, "avgPrice": 70_000, "currentPrice": 70_000}


def test_live_phantom_position_cannot_submit_sell_or_change_virtual_ledger():
    calls = fake_gateway()
    db, log = run_cycle(broker(), portfolio=[virtual_position()], signal_fn=sell_signal)
    assert not [c for c in calls if c[0] == "POST"]
    assert db.virtual_calls == [] and not db.added
    assert log["trades"][0]["status"] == "skipped"
    assert "보유수량 없음" in log["trades"][0]["reason"]


def test_market_closed_creates_neither_virtual_fill_nor_kis_order():
    calls = fake_gateway()
    db, log = run_cycle(broker(), market_open=False)
    assert calls == [] and not db.added and not db.virtual_calls and db.commits == 0
    assert log["skip_reason"] == "market_closed"


def test_balance_failure_blocks_cycle_without_virtual_fallback():
    calls = fake_gateway(balance_error=True)
    db, log = run_cycle(broker(), portfolio=[virtual_position()], signal_fn=sell_signal)
    assert not [c for c in calls if c[0] == "POST"]
    assert not db.virtual_calls and not db.added
    assert log["skip_reason"] == "balance_unavailable"


def test_live_sell_uses_actual_quantity_and_reserves_other_users_pending_sells():
    calls = fake_gateway(holdings=[actual_position(7)])
    pending = LiveOrder(user_id=uuid.uuid4(), environment="paper", symbol="005930.KS", side="SELL",
                        quantity=5, filled_quantity=1, status="PARTIALLY_FILLED", price=70_000)
    db, log = run_cycle(broker(quant_sell_ratio=1.0), live_rows=[pending],
                        portfolio=[virtual_position(24)], signal_fn=sell_signal)
    trades = [t for t in log["trades"] if t.get("type") == "auto"]
    assert len(trades) == 1 and trades[0]["quantity"] == 3  # 7 actual - 4 pending
    assert trades[0]["status"] == "submitted" and not db.virtual_calls
    assert [a for a in db.added if isinstance(a, LiveOrder)][0].quantity == 3


def test_failed_order_never_creates_virtual_fill():
    fake_gateway(holdings=[actual_position(3)], order_error=True)
    db, log = run_cycle(broker(quant_sell_ratio=1.0), portfolio=[virtual_position()], signal_fn=sell_signal)
    trade = next(t for t in log["trades"] if t.get("type") == "auto")
    assert trade["status"] == "error" and not db.virtual_calls
    assert db.portfolio[0].quantity == 24


def test_new_kis_buy_is_managed_after_actual_balance_confirms_it():
    fake_gateway(holdings=[actual_position(3)])
    buy = LiveOrder(user_id=UID, environment="paper", symbol="005930.KS", side="BUY", origin="user_auto",
                    order_no="123", quantity=3, filled_quantity=0, status="ACCEPTED", price=70_000)
    db, log = run_cycle(broker(quant_sell_ratio=1.0), live_rows=[buy], signal_fn=sell_signal)
    trade = next(t for t in log["trades"] if t.get("type") == "auto")
    assert trade["quantity"] == 3 and not db.virtual_calls


def test_unrelated_kis_holding_is_not_automatically_sold():
    calls = fake_gateway(holdings=[actual_position(3)])
    db, log = run_cycle(broker(quant_sell_ratio=1.0), signal_fn=sell_signal)
    assert not [c for c in calls if c[0] == "POST"] and not db.virtual_calls


@pytest.mark.parametrize("balance", [{}, {"holdings": [], "cashBalance": 1},
    {"holdings": [], "cashBalance": 1, "totalEvalAmount": float("nan")},
    {"holdings": [], "cashBalance": 1, "totalEvalAmount": 0}])
def test_incomplete_balance_is_not_treated_as_empty_account(balance):
    with pytest.raises((KeyError, ValueError)):
        auto_trade._validated_kis_balance(balance)


def test_sell_rechecks_balance_before_submission():
    fake_gateway(holdings=[actual_position(7)])
    start = {"totalEvalAmount": 10_000_000, "cashBalance": 9_000_000, "holdings": [actual_position(7)]}
    fresh = {**start, "holdings": []}
    with patch.object(gw, "get_balance", AsyncMock(side_effect=[start, fresh])):
        db, log = run_cycle(broker(quant_sell_ratio=1.0), portfolio=[virtual_position()], signal_fn=sell_signal)
    assert not db.added and not db.virtual_calls
    assert "실제 보유수량 없음" in log["trades"][0]["reason"]


def test_unresolved_sell_blocks_repeated_sale_without_rewriting_history():
    fake_gateway(holdings=[actual_position(7)])
    pending = LiveOrder(user_id=UID, environment="paper", symbol="005930.KS", side="SELL",
                        quantity=7, filled_quantity=0, status="LOST", price=70_000)
    db, log = run_cycle(broker(quant_sell_ratio=1.0), live_rows=[pending],
                        portfolio=[virtual_position()], signal_fn=sell_signal)
    assert not db.added and not db.virtual_calls and pending.status == "LOST"
    assert "미확정 매도 7주" in log["trades"][0]["reason"]
