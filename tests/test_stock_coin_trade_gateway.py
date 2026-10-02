"""stock-coin-trade KIS 게이트웨이 클라이언트: 2단계 주문, 멱등키, 호가 보정, 오류 매핑, 재시도 정책.

httpx.MockTransport 로 stock-coin-trade 서버를 흉내 낸다. 실제 네트워크·DB 없음.
"""
import asyncio
import json
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))

import httpx
import pytest

from app.config import settings
from app.services.brokers import stock_coin_trade_gateway as gw


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_BASE_URL", "https://sct.test")
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_API_KEY", "key-123")
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_KIS_ENVIRONMENT", "paper")
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_ORDER_TYPE", "LIMIT")
    yield
    gw.set_transport(None)


class FakeServer:
    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []
        self.fail_orders_with: tuple[int, dict] | None = None
        self.unreachable_paths: set[str] = set()
        self.unreachable_hits: dict[str, int] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}") if request.content else {}
        path = request.url.path
        self.calls.append((request.method, path, body))
        assert request.headers["Authorization"] == "Bearer key-123"
        if path in self.unreachable_paths:
            self.unreachable_hits[path] = self.unreachable_hits.get(path, 0) + 1
            raise httpx.ConnectError("boom", request=request)
        if path == "/openapi/v1/kis/order-approval":
            return httpx.Response(200, json={"ok": True, "approvalToken": "tok-1", "expiresIn": 60, "intent": body})
        if path == "/openapi/v1/kis/orders" and request.method == "POST":
            if self.fail_orders_with:
                status, payload = self.fail_orders_with
                return httpx.Response(status, json=payload)
            assert body["approvalToken"] == "tok-1"
            return httpx.Response(200, json={"ok": True, "duplicate": False, "order": {
                "clientOrderId": body["clientOrderId"], "orderNo": "0000001234", "status": "ACCEPTED",
                "symbol": body["symbol"], "side": body["side"], "quantity": body["quantity"], "price": body["price"]}})
        if path.startswith("/openapi/v1/kis/orders/"):
            return httpx.Response(200, json={"ok": True, "order": {"orderNo": path.rsplit("/", 1)[-1], "status": "FILLED", "filledQuantity": 2, "avgFilledPrice": 70000}})
        if path == "/openapi/v1/kis/balance":
            return httpx.Response(200, json={"ok": True, "balance": {"cashBalance": 100, "holdings": []}})
        return httpx.Response(404, json={"ok": False, "error": "NOT_FOUND", "message": path})


@pytest.fixture
def server():
    srv = FakeServer()
    gw.set_transport(httpx.MockTransport(srv.handler))
    return srv


def test_symbol_side_and_tick_alignment():
    assert gw.normalize_symbol("005930.KS") == "005930"
    assert gw.normalize_symbol("035720.KQ") == "035720"
    with pytest.raises(gw.GatewayError):
        gw.normalize_symbol("AAPL")
    assert gw.align_price_to_tick(70_010.5, "buy") == 70_100     # 매수: 호가 올림
    assert gw.align_price_to_tick(70_010.5, "sell") == 70_000    # 매도: 호가 내림
    assert gw.align_price_to_tick(1_999.4, "buy") == 2_000
    assert gw.align_price_to_tick(70_000, "buy") == 70_000       # 이미 호가 단위면 그대로


def test_client_order_id_is_stable_within_minute_and_short():
    uid = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
    at = datetime(2026, 10, 2, 10, 31, 5, tzinfo=timezone(timedelta(hours=9)))
    first = gw.make_client_order_id(uid, "005930.KS", "buy", now=at)
    second = gw.make_client_order_id(uid, "005930.KS", "buy", now=at.replace(second=59))
    assert first == second == "0f1e2d3c4b5a:005930:B:202610021031"
    assert len(first) <= 64


def test_place_order_is_two_step_and_intent_is_identical(server):
    result = asyncio.run(gw.place_order("005930.KS", "buy", 2, 70_010.0, client_order_id="cid-1"))
    assert result["order"]["orderNo"] == "0000001234" and result["duplicate"] is False
    methods_paths = [(m, p) for m, p, _ in server.calls]
    assert methods_paths == [("POST", "/openapi/v1/kis/order-approval"), ("POST", "/openapi/v1/kis/orders")]
    approval_body, order_body = server.calls[0][2], server.calls[1][2]
    assert {k: v for k, v in order_body.items() if k != "approvalToken"} == approval_body
    assert approval_body == {"environment": "paper", "symbol": "005930", "side": "BUY", "orderType": "LIMIT",
                             "quantity": 2, "price": 70_100, "clientOrderId": "cid-1"}


def test_gateway_error_maps_contract_code(server):
    server.fail_orders_with = (403, {"ok": False, "error": "APPROVAL_INVALID", "message": "만료"})
    with pytest.raises(gw.GatewayError) as exc:
        asyncio.run(gw.place_order("005930", "sell", 1, 70_000, client_order_id="cid-2"))
    assert exc.value.code == "APPROVAL_INVALID" and exc.value.status_code == 403

    server.fail_orders_with = (502, {"ok": False, "error": "KIS_APBK0919", "message": "주문가능금액 부족", "order": {"status": "REJECTED"}})
    with pytest.raises(gw.GatewayError) as exc:
        asyncio.run(gw.place_order("005930", "sell", 1, 70_000, client_order_id="cid-3"))
    assert exc.value.code == "KIS_APBK0919" and exc.value.details["order"]["status"] == "REJECTED"


def test_order_post_is_never_retried_but_approval_is(server):
    server.unreachable_paths = {"/openapi/v1/kis/orders"}
    with pytest.raises(gw.GatewayError) as exc:
        asyncio.run(gw.place_order("005930", "buy", 1, 70_000, client_order_id="cid-4"))
    assert exc.value.code == "GATEWAY_UNREACHABLE"
    assert server.unreachable_hits["/openapi/v1/kis/orders"] == 1

    server.unreachable_paths = {"/openapi/v1/kis/order-approval"}
    with pytest.raises(gw.GatewayError):
        asyncio.run(gw.place_order("005930", "buy", 1, 70_000, client_order_id="cid-5"))
    assert server.unreachable_hits["/openapi/v1/kis/order-approval"] == 2


def test_status_and_balance(server):
    order = asyncio.run(gw.get_order_status("0000001234"))
    assert order["status"] == "FILLED" and order["filledQuantity"] == 2
    assert server.calls[-1][1] == "/openapi/v1/kis/orders/0000001234"
    assert asyncio.run(gw.get_balance())["cashBalance"] == 100


def test_not_configured_raises(monkeypatch):
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_BASE_URL", "")
    assert not gw.is_configured()
    with pytest.raises(gw.GatewayError) as exc:
        asyncio.run(gw.get_balance())
    assert exc.value.code == "GATEWAY_NOT_CONFIGURED"


def test_krx_market_hours():
    assert gw.is_krx_market_open(datetime(2026, 10, 2, 9, 0, tzinfo=KST))
    assert gw.is_krx_market_open(datetime(2026, 10, 2, 15, 29, tzinfo=KST))
    assert not gw.is_krx_market_open(datetime(2026, 10, 2, 15, 30, tzinfo=KST))
    assert not gw.is_krx_market_open(datetime(2026, 10, 2, 8, 59, tzinfo=KST))
    assert not gw.is_krx_market_open(datetime(2026, 10, 3, 10, 0, tzinfo=KST))   # 토요일
    assert gw.is_krx_market_open(datetime(2026, 10, 2, 1, 0, tzinfo=timezone.utc))  # UTC 01:00 = KST 10:00


def test_krx_holidays_close_the_market(monkeypatch):
    assert not gw.is_krx_market_open(datetime(2026, 10, 9, 10, 0, tzinfo=KST))   # 한글날(금)
    assert not gw.is_krx_market_open(datetime(2026, 12, 31, 10, 0, tzinfo=KST))  # 연말 휴장(목)
    assert gw.is_krx_market_open(datetime(2026, 10, 8, 10, 0, tzinfo=KST))
    monkeypatch.setattr(settings, "KRX_EXTRA_HOLIDAYS", "2026-10-08, 2026-11-02")
    assert not gw.is_krx_market_open(datetime(2026, 10, 8, 10, 0, tzinfo=KST))
    assert "2026-11-02" in gw.krx_holidays() and "2026-05-01" in gw.krx_holidays()


def test_krx_2026_holidays_match_published_list():
    assert len(gw.KRX_HOLIDAYS_2026) == 17
    assert "2026-07-17" in gw.KRX_HOLIDAYS_2026 and "2026-09-28" not in gw.KRX_HOLIDAYS_2026
    assert not gw.is_krx_market_open(datetime(2026, 7, 17, 10, 0, tzinfo=KST))
