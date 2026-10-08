"""화면(기본 인디케이터 전략)에서 직접 내는 KIS 모의투자 주문.

자동매매와 같은 경로를 쓰되, 실전 환경·비상정지·잘못된 수량은 주문 전에 막는다.
"""
import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services import auto_trade
from app.services import kis_quickstart as qs

UID = uuid.UUID("0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0")


class Result:
    def __init__(self, row=None): self._row = row
    def scalar_one_or_none(self): return self._row


class FakeDb:
    def __init__(self, row=None): self.row = row
    async def execute(self, *_a, **_k): return Result(self.row)


def _route(configured=True, environment="paper"):
    return qs.Route(via="stock-coin-trade", environment=environment, configured=configured, detail="테스트 경로")


def _call(db=None, **kw):
    body = {"symbol": "005930.KS", "name": "삼성전자", "side": "buy", "quantity": 3}
    body.update(kw)
    return asyncio.run(auto_trade.place_manual_kis_order(db if db is not None else FakeDb(), str(UID), **body))


def _blocked(reason, **kw):
    with pytest.raises(auto_trade.ManualOrderBlocked) as e:
        _call(**kw)
    assert e.value.reason == reason
    return e.value


@pytest.fixture
def gateway_ok():
    """게이트웨이 연결·모의환경·현재가 조회가 모두 정상인 기본 상태."""
    with patch.object(qs, "resolve_route", AsyncMock(return_value=_route())), \
         patch.object(auto_trade.gateway, "is_configured", lambda: True), \
         patch("app.services.stock.get_quote", AsyncMock(return_value={"price": 70_000.0})), \
         patch.object(auto_trade, "audit", AsyncMock()), \
         patch.object(auto_trade, "_place_live_order_via_gateway",
                      AsyncMock(return_value={"status": "submitted", "broker": "kis", "via": "stock-coin-trade",
                                              "environment": "paper", "order_no": "A1"})) as place:
        yield place


def test_rejects_bad_side_and_quantity(gateway_ok):
    _blocked("bad_side", side="hold")
    _blocked("bad_quantity", quantity=0)
    _blocked("bad_quantity", quantity=auto_trade.MANUAL_ORDER_MAX_QTY + 1)
    gateway_ok.assert_not_awaited()


def test_rejects_real_environment(gateway_ok):
    with patch.object(qs, "resolve_route", AsyncMock(return_value=_route(environment="real"))):
        _blocked("real_environment")
    gateway_ok.assert_not_awaited()


def test_rejects_when_not_connected(gateway_ok):
    with patch.object(qs, "resolve_route", AsyncMock(return_value=_route(configured=False))):
        _blocked("not_connected")
    gateway_ok.assert_not_awaited()


def test_rejects_when_kill_switch_on(gateway_ok):
    db = FakeDb(SimpleNamespace(risk_kill_switch=True, risk_halt_reason="일손실 한도"))
    with pytest.raises(auto_trade.ManualOrderBlocked) as e:
        _call(db=db)
    assert e.value.reason == "kill_switch" and "일손실 한도" in e.value.message
    gateway_ok.assert_not_awaited()


def test_rejects_when_price_unavailable(gateway_ok):
    with patch("app.services.stock.get_quote", AsyncMock(return_value={"price": None})):
        _blocked("no_price")
    gateway_ok.assert_not_awaited()


def test_places_order_with_server_side_price(gateway_ok):
    """가격은 서버가 현재가로 정한다 — 클라이언트는 수량만 보낸다."""
    out = _call(quantity=3)
    assert out["status"] == "submitted" and out["order_no"] == "A1"
    assert out["quantity"] == 3 and out["price"] == 70_000.0 and out["amount"] == 210_000
    args = gateway_ok.await_args.args
    assert args[2:7] == ("005930.KS", "삼성전자", "buy", 3, 70_000.0)


def test_sell_side_goes_through(gateway_ok):
    assert _call(side="sell")["status"] == "submitted"
    assert gateway_ok.await_args.args[4] == "sell"


# ── 라우트 ────────────────────────────────────────────────────────────────
def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.database.postgres import get_pg_session
    from app.lib.session import get_current_user
    from app.routes import stocks as sr

    app = FastAPI(); app.include_router(sr.router)
    app.dependency_overrides[get_current_user] = lambda: {"id": str(UID), "email": "t@t"}
    app.dependency_overrides[get_pg_session] = lambda: FakeDb()
    return TestClient(app)


def test_route_returns_422_with_user_message_when_blocked():
    with patch.object(auto_trade, "place_manual_kis_order",
                      AsyncMock(side_effect=auto_trade.ManualOrderBlocked("real_environment", "실전이라 막습니다."))):
        r = _client().post("/api/stocks/quant/manual-order",
                           json={"symbol": "005930.KS", "name": "삼성전자", "side": "buy", "quantity": 1})
    assert r.status_code == 422 and r.json()["detail"] == "실전이라 막습니다."


def test_route_validates_body_before_touching_the_broker():
    c = _client()
    with patch.object(auto_trade, "place_manual_kis_order", AsyncMock()) as place:
        assert c.post("/api/stocks/quant/manual-order",
                      json={"symbol": "005930.KS", "side": "hold", "quantity": 1}).status_code == 422
        assert c.post("/api/stocks/quant/manual-order",
                      json={"symbol": "005930.KS", "side": "buy", "quantity": 0}).status_code == 422
        place.assert_not_awaited()


def test_route_passes_quantity_and_returns_result():
    with patch.object(auto_trade, "place_manual_kis_order",
                      AsyncMock(return_value={"status": "submitted", "quantity": 7, "amount": 490_000})) as place:
        r = _client().post("/api/stocks/quant/manual-order",
                           json={"symbol": "005930.KS", "name": "삼성전자", "side": "buy", "quantity": 7})
    assert r.status_code == 200 and r.json()["quantity"] == 7
    assert place.await_args.args[3:6] == ("삼성전자", "buy", 7)


def test_order_readiness_reports_blocking_reason():
    ready = {"connected": True, "environment": "real", "kill_switch": False, "route": "stock-coin-trade",
             "route_detail": "게이트웨이 → KIS 실전"}
    with patch.object(qs, "readiness", AsyncMock(return_value=ready)):
        r = _client().get("/api/stocks/quant/order-readiness")
    body = r.json()
    assert r.status_code == 200 and body["can_order"] is False and body["reason"] == "real_environment"
    assert body["max_quantity"] == auto_trade.MANUAL_ORDER_MAX_QTY
