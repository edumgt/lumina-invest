"""정합성 점검(reconciliation): 로그(가상 장부·live_orders·사이클) vs 실거래(KIS 잔고)."""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from app.models import LiveOrder, Portfolio
from app.models.base import SYSTEM_USER_ID
from app.services import reconciliation as rc
from app.services.brokers import stock_coin_trade_gateway as gw

NOW = datetime(2026, 10, 6, 4, 0, tzinfo=timezone.utc)


class Result:
    def __init__(self, many=()): self._many = list(many)
    def scalars(self): return self
    def all(self): return self._many


class FakeDb:
    def __init__(self, portfolio=(), orders=()): self.portfolio, self.orders = list(portfolio), list(orders)
    async def execute(self, stmt, *_a, **_k):
        entity = stmt.column_descriptions[0]["entity"]
        if entity is Portfolio: return Result(self.portfolio)
        if entity is LiveOrder: return Result(self.orders)
        raise AssertionError(entity)


def pos(symbol, qty): return SimpleNamespace(symbol=symbol, quantity=qty, book="QUANT", user_id=SYSTEM_USER_ID)


def order(symbol, side, qty, status, filled=None, price=100.0, avg=None, age_min=5, cid=None):
    return SimpleNamespace(symbol=symbol, side=side, quantity=qty, status=status, filled_quantity=filled if filled is not None else (qty if status == "FILLED" else 0),
                           price=price, avg_filled_price=avg, created_at=NOW - timedelta(minutes=age_min), client_order_id=cid or f"c-{symbol}-{side}", message="")


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_BASE_URL", "https://sct.test")
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_API_KEY", "key")
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_KIS_ENVIRONMENT", "paper")
    monkeypatch.setattr(settings, "RECONCILE_ENABLED", True)
    monkeypatch.setattr(settings, "RECONCILE_NOTIFY", True)
    monkeypatch.setattr(settings, "RECONCILE_OPEN_ORDER_MAX_MIN", 30)
    monkeypatch.setattr(settings, "RECONCILE_SLIPPAGE_ALERT_PCT", 1.0)
    store = {}
    async def cget(key, max_age_hours=24): return store.get(key)
    async def cset(key, data): store[key] = data
    with patch.object(rc, "cache_get", cget), patch.object(rc, "cache_set", cset), \
         patch.object(rc.notification, "dispatch", AsyncMock()) as dispatch, \
         patch("app.services.reconciliation.datetime") as dt:
        dt.now.return_value = NOW
        dt.side_effect = lambda *a, **k: datetime(*a, **k)
        yield {"store": store, "dispatch": dispatch}


def run(db, holdings, cycles=None):
    bal = {"holdings": [{"symbol": k, "quantity": v, "avgPrice": 100, "name": k} for k, v in holdings.items()]}
    with patch.object(gw, "get_balance", AsyncMock(return_value=bal)):
        return asyncio.run(rc.run_reconciliation(db))


def test_first_run_captures_baseline_and_is_ok_when_consistent(env):
    # 기존 Testbed 보유(삼성 38주)는 기준선으로, 봇 체결(원익IPS 2주)은 가상=실주문=실제 모두 2주
    db = FakeDb(portfolio=[pos("240810.KQ", 2)], orders=[order("240810.KQ", "BUY", 2, "FILLED", avg=100.3)])
    env["store"]["quant:cycle_log:" + str(SYSTEM_USER_ID)] = {"cycles": [{"settings": {"mode": "live"}, "trades": [
        {"type": "auto", "status": "filled", "symbol": "240810.KQ", "action": "buy", "quantity": 2, "live_order": {"status": "submitted"}}]}]}
    r = run(db, {"005930": 38, "240810": 2})
    assert r["ok"] is True and r["issues"] == [] and r["summary"]["baseline"].startswith("지금")
    base = env["store"][rc._baseline_key(SYSTEM_USER_ID, "paper")]["holdings"]
    assert base == {"005930": 38}            # 봇 체결(원익IPS 2주)은 기준선에서 제외된다
    env["dispatch"].assert_not_awaited()


def test_detects_kis_mismatch_virtual_mismatch_and_phantom(env):
    env["store"][rc._baseline_key(SYSTEM_USER_ID, "paper")] = {"captured_at": "t0", "holdings": {"005930": 38}}
    # 가상 장부 23주(브이티) 있는데 실주문 체결 0, KIS 에도 없음 → virtual_vs_live + phantom ; 원익IPS 는 체결 2 인데 KIS 에 5주 → kis mismatch
    db = FakeDb(portfolio=[pos("018290.KQ", 23), pos("240810.KQ", 2)],
                orders=[order("240810.KQ", "BUY", 2, "FILLED", avg=100.0), order("018290.KQ", "BUY", 23, "LOST", filled=0, age_min=40)])
    env["store"]["quant:cycle_log:" + str(SYSTEM_USER_ID)] = {"cycles": [{"settings": {"mode": "live"}, "trades": [
        {"type": "auto", "status": "filled", "symbol": "018290.KQ", "action": "buy", "quantity": 23, "live_order": {"status": "error", "error": "GATEWAY_UNREACHABLE"}},
        {"type": "auto", "status": "filled", "symbol": "192820.KS", "action": "buy", "quantity": 1},
        {"type": "risk", "status": "skipped", "symbol": "X"}]}]}
    r = run(db, {"005930": 38, "240810": 5})
    types = [i["type"] for i in r["issues"]]
    assert r["ok"] is False
    assert types.count("kis_position_mismatch") == 1 and next(i for i in r["issues"] if i["type"] == "kis_position_mismatch")["symbol"] == "240810"
    vm = [i for i in r["issues"] if i["type"] == "virtual_vs_live_mismatch"]
    assert [i["symbol"] for i in vm] == ["018290"] and vm[0]["virtual"] == 23 and vm[0]["live_filled"] == 0
    ph = [i for i in r["issues"] if i["type"] == "phantom_trade"]
    assert {p["symbol"] for p in ph} == {"018290.KQ", "192820.KS"} and {p["live_status"] for p in ph} == {"error", "none"}
    assert any(i["type"] == "unresolved_order" and i["status"] == "LOST" for i in r["issues"])
    env["dispatch"].assert_awaited_once()
    assert "불일치" in env["dispatch"].await_args.args[0]


def test_stale_open_and_slippage(env):
    env["store"][rc._baseline_key(SYSTEM_USER_ID, "paper")] = {"captured_at": "t0", "holdings": {}}
    db = FakeDb(portfolio=[pos("005930.KS", 3)],
                orders=[order("005930.KS", "BUY", 3, "FILLED", price=100.0, avg=101.5), order("000660.KS", "BUY", 1, "ACCEPTED", age_min=45)])
    r = run(db, {"005930": 3})
    types = sorted(i["type"] for i in r["issues"])
    assert types == ["slippage", "stale_open_order"]
    assert next(i for i in r["issues"] if i["type"] == "slippage")["slippage_pct"] == 1.5
    assert next(i for i in r["issues"] if i["type"] == "stale_open_order")["age_min"] == 45


def test_notify_only_when_issue_set_changes(env):
    env["store"][rc._baseline_key(SYSTEM_USER_ID, "paper")] = {"captured_at": "t0", "holdings": {}}
    db = FakeDb(portfolio=[pos("005930.KS", 3)], orders=[])
    run(db, {}); run(db, {})
    assert env["dispatch"].await_count == 1
    db2 = FakeDb(portfolio=[pos("005930.KS", 4)], orders=[])
    run(db2, {})
    assert env["dispatch"].await_count == 2


def test_gateway_not_configured_or_disabled(env, monkeypatch):
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_BASE_URL", "")
    r = asyncio.run(rc.run_reconciliation(FakeDb()))
    assert r["ok"] is None and "미설정" in r["summary"]["note"]
    monkeypatch.setattr(settings, "RECONCILE_ENABLED", False)
    r2 = asyncio.run(rc.run_reconciliation(FakeDb()))
    assert r2["ok"] is None and "RECONCILE_ENABLED" in r2["summary"]["note"]


def test_balance_failure_is_reported_not_raised(env):
    with patch.object(gw, "get_balance", AsyncMock(side_effect=gw.GatewayError("down", "GATEWAY_UNREACHABLE", 503))):
        r = asyncio.run(rc.run_reconciliation(FakeDb()))
    assert r["ok"] is None and "잔고 조회 실패" in r["summary"]["note"]


def test_task_registered_and_scheduled():
    import app.tasks.sync_tasks  # noqa: F401
    from app.celery_app import celery_app
    assert "quant.reconcile" in celery_app.tasks
    entry = celery_app.conf.beat_schedule["quant-reconcile"]
    assert entry["task"] == "quant.reconcile" and entry["schedule"] >= 60


def test_shared_account_counts_other_users_fills_for_kis_but_not_virtual(env):
    """공용 Testbed 계좌: KIS 보유 대조는 전 사용자 체결 합산, 가상 장부 대조는 이 uid 체결만."""
    other = uuid.UUID("11111111-1111-1111-1111-111111111111")
    mine = order("240810.KQ", "BUY", 2, "FILLED", avg=100.0); mine.user_id = SYSTEM_USER_ID
    theirs = order("005930.KS", "BUY", 5, "FILLED", avg=70000.0, cid="c-user"); theirs.user_id = other
    db = FakeDb(portfolio=[pos("240810.KQ", 2)], orders=[mine, theirs])
    rep = run(db, {"240810": 2, "005930": 5})           # 실제 보유 = 내 2주 + 다른 사용자 5주, 기준선 0
    assert rep["ok"] is True, rep["issues"]
    assert rep["summary"]["account_net_filled"] == {"240810": 2, "005930": 5}
    assert rep["summary"]["bot_net_filled"] == {"240810": 2} and rep["summary"]["account_users"] == 2
    # 다른 사용자 체결을 내 가상 장부에 요구하지 않는다(virtual_vs_live_mismatch 없음)
    assert not [i for i in rep["issues"] if i["type"] == "virtual_vs_live_mismatch"]
