"""GET /api/quant/auto/status · /quant/live-orders: KIS 배치 실행 중이면 시스템 사용자의 사이클·주문을 합쳐 보여준다."""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database.postgres import get_pg_session
from app.lib.session import get_current_user
from app.models.base import SYSTEM_USER_ID
from app.routes import stocks as sr
from app.services import auto_trade, kis_batch

UID = uuid.UUID("0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0")


class Result:
    def __init__(self, many=()): self._many = list(many)
    def scalar_one_or_none(self): return None
    def scalars(self): return self
    def all(self): return self._many


class FakeDb:
    def __init__(self, rows=()): self.rows = list(rows); self.last_stmt = None
    async def execute(self, stmt, *_a, **_k): self.last_stmt = stmt; return Result(self.rows)


def cycle(user_tag, trade_symbol, price, note=None):
    c = {"time": f"2026-10-06 02:{'31' if user_tag == 'me' else '36'}:00",
         "signals": [{"symbol": trade_symbol, "name": trade_symbol, "price": price, "action": "매수", "score": 2}],
         "trades": [{"time": "", "symbol": trade_symbol, "name": trade_symbol, "action": "buy", "quantity": 3, "price": price,
                     "reason": f"{user_tag} reason", "status": "filled", "type": "auto", "live_order": {"status": "submitted"}}],
         "account": {"total_equity": 10_000_000, "pnl_pct": 0.1}, "risk": {"skipped": [{"symbol": "X", "name": "X", "side": "buy", "reason": "한도"}]}}
    if note:
        c["aggressive"] = {"notes": [note], "buy": [trade_symbol], "sell": {}}
    return c


@pytest.fixture
def client():
    db = FakeDb()
    app = FastAPI(); app.include_router(sr.router)
    app.dependency_overrides[get_current_user] = lambda: {"id": str(UID), "email": "t@t"}
    app.dependency_overrides[get_pg_session] = lambda: db
    yield TestClient(app), db


async def status_for(db, uid):
    if uid == SYSTEM_USER_ID:
        return {"running": True, "log": [cycle("batch", "000660.KS", 1_800_000, note="매수 시그널 없음 → 모멘텀 1위 로테이션 매수")]}
    return {"running": False, "log": [cycle("me", "005930.KS", 276_000)]}


def test_status_merges_batch_cycles_when_batch_running(client):
    c, _ = client
    with patch.object(auto_trade, "get_status", status_for), \
         patch.object(kis_batch, "system_status", AsyncMock(return_value={"enabled": True, "running": True, "kill_switch": False})):
        r = c.get("/api/quant/auto/status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["running"] is True and body["me_running"] is False and body["batch"]["running"] is True
    msgs = [l["message"] for l in body["logs"]]
    assert any(m.startswith("[배치] ") and "000660.KS BUY 3주" in m and "실주문 submitted" in m for m in msgs)
    assert any("005930.KS BUY 3주" in m and not m.startswith("[배치]") for m in msgs)
    assert any("[배치] [공격 모드] 매수 시그널 없음" in m for m in msgs)
    assert any("[위험관리 생략] X BUY" in m for m in msgs)
    assert {s["source"] for s in body["signals"]} == {"me", "batch"}
    assert [l["time"] for l in body["logs"]] == sorted(l["time"] for l in body["logs"])


def test_status_without_batch_only_shows_me(client):
    c, _ = client
    calls = []
    async def st(db, uid): calls.append(uid); return {"running": True, "log": [cycle("me", "005930.KS", 276_000)]}
    with patch.object(auto_trade, "get_status", st), \
         patch.object(kis_batch, "system_status", AsyncMock(return_value={"enabled": False, "running": False})):
        r = c.get("/api/quant/auto/status")
    body = r.json()
    assert calls == [UID] and body["running"] is True and body["me_running"] is True
    assert all(not l["message"].startswith("[배치]") for l in body["logs"])


def test_live_orders_include_system_rows_when_batch_running(client):
    c, db = client
    mine = SimpleNamespace(id=uuid.uuid4(), user_id=UID, client_order_id="a", environment="paper", symbol="005930.KS", name="삼성전자", side="BUY",
                           order_type="LIMIT", quantity=1, price=275_000.0, order_no="1", status="ACCEPTED", filled_quantity=0, avg_filled_price=None,
                           message="", created_at=None, updated_at=None)
    sys_row = SimpleNamespace(**{**vars(mine), "id": uuid.uuid4(), "user_id": SYSTEM_USER_ID, "symbol": "000660.KS", "order_type": "MARKET", "client_order_id": "b"})
    db.rows = [sys_row, mine]
    with patch.object(kis_batch, "system_status", AsyncMock(return_value={"enabled": True, "running": True})), \
         patch.object(sr, "_live_gateway_info", return_value={"configured": True}):
        r = c.get("/api/quant/live-orders")
    assert r.status_code == 200, r.text
    orders = r.json()["orders"]
    assert [o["owner"] for o in orders] == ["batch", "me"] and orders[0]["order_type"] == "MARKET"
    crit = str(db.last_stmt.whereclause)
    assert "IN" in crit.upper()
