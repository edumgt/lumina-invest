"""auto_trade ↔ 게이트웨이 경로: live_orders 기록, 실패 상태 매핑, 장시간 가드, 체결 확인, 비상 정지 취소.

DB 는 add/commit/execute 만 흉내 내는 가짜 AsyncSession, 게이트웨이는 httpx.MockTransport.
"""
import asyncio
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.config import settings
from app.models import LiveOrder
from app.services import auto_trade
from app.services.brokers import stock_coin_trade_gateway as gw

KST = timezone(timedelta(hours=9))
OPEN_AT = datetime(2026, 10, 2, 10, 30, tzinfo=KST)   # 금요일 장중
UID = uuid.UUID("0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0")


class FakeBroker:
    quant_mode = "live"
    broker = "kis"
    app_key = app_secret = account_no = ""


class FakeResult:
    def __init__(self, rows): self._rows = rows
    def scalars(self): return self
    def all(self): return list(self._rows)


class FakeDb:
    def __init__(self, rows=None):
        self.added: list = []
        self.commits = 0
        self.rows = rows or []
    def add(self, obj): self.added.append(obj)
    async def commit(self): self.commits += 1
    async def execute(self, *_a, **_k): return FakeResult(self.rows)


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_BASE_URL", "https://sct.test")
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_API_KEY", "key")
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_KIS_ENVIRONMENT", "paper")
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_ENFORCE_MARKET_HOURS", True)
    with patch.object(auto_trade.notification, "notify_order_placed", AsyncMock()) as placed, \
         patch.object(auto_trade.notification, "notify_order_error", AsyncMock()) as errored, \
         patch.object(auto_trade.notification, "notify_order_filled", AsyncMock()) as filled, \
         patch.object(gw, "is_krx_market_open", return_value=True):
        yield {"placed": placed, "errored": errored, "filled": filled}
    gw.set_transport(None)


def _server(order_response):
    calls = []
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/order-approval"):
            return httpx.Response(200, json={"ok": True, "approvalToken": "t", "expiresIn": 60})
        if request.url.path.endswith("/kis/orders") and request.method == "POST":
            if isinstance(order_response, Exception):
                raise order_response
            return httpx.Response(order_response[0], json=order_response[1])
        if request.method == "DELETE":
            return httpx.Response(200, json={"ok": True, "order": {"orderNo": request.url.path.rsplit("/", 1)[-1], "status": "CANCEL_REQUESTED"}})
        if "/kis/orders/" in request.url.path:
            return httpx.Response(200, json={"ok": True, "order": {"orderNo": "0000001234", "status": "FILLED", "filledQuantity": 2, "avgFilledPrice": 70000}})
        return httpx.Response(404, json={"ok": False, "error": "NOT_FOUND", "message": "x"})
    gw.set_transport(httpx.MockTransport(handler))
    return calls


def _place(db):
    return asyncio.run(auto_trade._place_live_order(FakeBroker(), "005930.KS", "삼성전자", "buy", 2, 70_010.0, str(UID), db=db))


def test_accepted_order_is_tracked_in_live_orders(env):
    calls = _server((200, {"ok": True, "duplicate": False, "order": {"orderNo": "0000001234", "status": "ACCEPTED", "message": "ok"}}))
    db = FakeDb()
    result = _place(db)
    assert result["status"] == "submitted" and result["via"] == "stock-coin-trade" and result["order_no"] == "0000001234"
    row = db.added[0]
    assert isinstance(row, LiveOrder) and row.status == "ACCEPTED" and row.order_no == "0000001234"
    assert row.price == 70_100 and row.side == "BUY" and row.user_id == UID   # 호가 보정 가격이 기록됨
    assert db.commits == 2   # PENDING 선기록 + 결과 갱신
    assert [m for m, _ in calls] == ["POST", "POST"]
    env["placed"].assert_awaited_once()


def test_rejected_order_marks_error_and_notifies(env):
    _server((502, {"ok": False, "error": "KIS_APBK0919", "message": "주문가능금액 부족", "order": {"status": "REJECTED"}}))
    db = FakeDb()
    result = _place(db)
    assert result["status"] == "error" and result["code"] == "KIS_APBK0919"
    assert db.added[0].status == "ERROR" and "APBK0919" in db.added[0].message
    env["errored"].assert_awaited_once()


def test_unreachable_gateway_leaves_unknown_for_confirm_fills(env):
    _server(httpx.ConnectError("down"))
    db = FakeDb()
    result = _place(db)
    assert result["code"] == "GATEWAY_UNREACHABLE"
    assert db.added[0].status == "UNKNOWN"


def test_market_closed_skips_without_db_write(env):
    with patch.object(gw, "is_krx_market_open", return_value=False):
        db = FakeDb()
        result = _place(db)
    assert result["status"] == "skipped" and result["reason"] == "market_closed"
    assert db.added == [] and db.commits == 0


def test_legacy_path_when_gateway_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_BASE_URL", "")
    for k in ("KIS_SECRETS_NAME", "KIS_APP_KEY", "KIS_APP_SECRET"):
        monkeypatch.setattr(settings, k, "")
    from app.services import kis_credentials
    kis_credentials.invalidate()
    broker = FakeBroker()
    broker.app_key = broker.app_secret = ""
    # KIS 자격증명은 서버 관리(Secrets Manager). 미연동이면 레거시 경로는 주문을 내지 않고 생략 사유를 남긴다
    out = asyncio.run(auto_trade._place_live_order(broker, "005930.KS", "삼성전자", "buy", 1, 70_000.0, str(UID), db=FakeDb()))
    assert out == {"status": "skipped", "broker": "kis", "reason": "kis_credentials_not_configured"}
    # 서버 관리 대상이 아닌 증권사는 종전대로 자격증명 없으면 None
    broker.broker = "kb"
    assert asyncio.run(auto_trade._place_live_order(broker, "005930.KS", "삼성전자", "buy", 1, 70_000.0, str(UID), db=FakeDb())) is None


def test_confirm_live_fills_updates_rows_and_notifies(env):
    _server((200, {"ok": True}))
    row = LiveOrder(user_id=UID, client_order_id="c1", environment="paper", symbol="005930.KS", name="삼성전자", side="BUY",
                    order_type="LIMIT", quantity=2, price=70_000, order_no="0000001234", status="ACCEPTED", filled_quantity=0, avg_filled_price=0)
    db = FakeDb(rows=[row])

    class Factory:
        def __call__(self): return self
        async def __aenter__(self): return db
        async def __aexit__(self, *a): return False

    with patch.object(auto_trade, "get_session_factory", return_value=Factory()):
        summary = asyncio.run(auto_trade.confirm_live_fills())
    assert {k: summary[k] for k in ("checked", "updated", "filled", "errors", "skipped")} == {"checked": 1, "updated": 1, "filled": 1, "errors": 0, "skipped": 0}
    assert row.status == "FILLED" and row.filled_quantity == 2 and row.avg_filled_price == 70_000
    env["filled"].assert_awaited_once()


def test_emergency_halt_cancels_open_live_orders(env):
    _server((200, {"ok": True}))
    open_row = LiveOrder(user_id=UID, client_order_id="c2", environment="paper", symbol="005930.KS", name="삼성전자", side="BUY",
                         order_type="LIMIT", quantity=1, price=70_000, order_no="0000009999", status="ACCEPTED")
    db = FakeDb(rows=[open_row])
    with patch.object(auto_trade.notification, "notify_risk_halt", AsyncMock()), patch.object(auto_trade, "audit", AsyncMock()):
        asyncio.run(auto_trade.emergency_halt(db, None, str(UID), "일손실 한도 초과", -3.5))
    assert open_row.status == "CANCEL_REQUESTED" and "비상 정지" in open_row.message


def _factory(db):
    class Factory:
        def __call__(self): return self
        async def __aenter__(self): return db
        async def __aexit__(self, *a): return False
    return Factory()


def test_confirm_fills_logs_slippage_on_fill(env):
    _server((200, {"ok": True}))
    row = LiveOrder(user_id=UID, client_order_id="c3", environment="paper", symbol="005930.KS", name="삼성전자", side="BUY",
                    order_type="LIMIT", quantity=2, price=69_000, order_no="0000001234", status="ACCEPTED", filled_quantity=0, avg_filled_price=0)
    db = FakeDb(rows=[row])
    with patch.object(auto_trade, "get_session_factory", return_value=_factory(db)):
        summary = asyncio.run(auto_trade.confirm_live_fills())
    assert summary["slippage_pct"] == [round((70_000 / 69_000 - 1) * 100, 3)]
    assert "슬리피지" in row.message


def test_confirm_fills_cancels_stale_open_orders(env, monkeypatch):
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_CANCEL_OPEN_AFTER_MIN", 30)
    calls = []
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "DELETE":
            return httpx.Response(200, json={"ok": True, "order": {"orderNo": "77", "status": "CANCEL_REQUESTED"}})
        return httpx.Response(200, json={"ok": True, "order": {"orderNo": "77", "status": "ACCEPTED", "filledQuantity": 0, "avgFilledPrice": 0}})
    gw.set_transport(httpx.MockTransport(handler))
    stale = LiveOrder(user_id=UID, client_order_id="c4", environment="paper", symbol="005930.KS", name="삼성전자", side="BUY",
                      order_type="LIMIT", quantity=1, price=70_000, order_no="77", status="ACCEPTED", filled_quantity=0, avg_filled_price=0)
    stale.created_at = datetime.now(timezone.utc) - timedelta(minutes=45)
    fresh = LiveOrder(user_id=UID, client_order_id="c5", environment="paper", symbol="005930.KS", name="삼성전자", side="BUY",
                      order_type="LIMIT", quantity=1, price=70_000, order_no="77", status="ACCEPTED", filled_quantity=0, avg_filled_price=0)
    fresh.created_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    db = FakeDb(rows=[stale, fresh])
    with patch.object(auto_trade, "get_session_factory", return_value=_factory(db)):
        summary = asyncio.run(auto_trade.confirm_live_fills())
    assert summary.get("cancelled") == 1
    assert stale.status == "CANCEL_REQUESTED" and fresh.status == "ACCEPTED"
    assert [m for m, _ in calls].count("DELETE") == 1


def test_live_account_daily_loss_uses_gateway_balance(env):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "balance": {"totalEvalAmount": 9_600_000, "cashBalance": 1}})
    gw.set_transport(httpx.MockTransport(handler))
    with patch.object(auto_trade.risk_guard, "day_start_equity", AsyncMock(return_value=10_000_000.0)):
        result = asyncio.run(auto_trade.live_account_daily_loss(str(UID), 3.0))
    assert result["day_pnl_pct"] == -4.0 and result["breached"] is True and result["environment"] == "paper"
    gw.set_transport(httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("down", request=r))))
    failed = asyncio.run(auto_trade.live_account_daily_loss(str(UID), 3.0))
    assert failed["breached"] is False and "GATEWAY_UNREACHABLE" in failed["error"]


# ── UNKNOWN 해소: 멱등 재전송 / LOST (2026-10-06 브이티 시장가 UNKNOWN 사고) ─────────────────────────
def _factory(db):
    class Factory:
        def __call__(self): return self
        async def __aenter__(self): return db
        async def __aexit__(self, *a): return False
    return Factory()


def _unknown_row(age_min: int):
    return LiveOrder(user_id=UID, client_order_id="u:018290:B:202610061151", environment="paper", symbol="018290.KQ", name="브이티",
                     side="BUY", order_type="MARKET", quantity=23, price=12_520.0, order_no=None, status="UNKNOWN",
                     filled_quantity=0, avg_filled_price=0, created_at=datetime.now(timezone.utc) - timedelta(minutes=age_min))


def test_unknown_within_window_is_resubmitted_idempotently(env, monkeypatch):
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_UNKNOWN_RESUBMIT_MIN", 10)
    bodies = []
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/order-approval"):
            return httpx.Response(200, json={"ok": True, "approvalToken": "t", "expiresIn": 60})
        if request.url.path.endswith("/kis/orders") and request.method == "POST":
            bodies.append(request.read())
            return httpx.Response(200, json={"ok": True, "duplicate": True,
                                             "order": {"orderNo": "0000030777", "status": "FILLED", "filledQuantity": 23, "avgFilledPrice": 12_530}})
        return httpx.Response(404, json={"ok": False, "error": "NOT_FOUND", "message": "x"})
    gw.set_transport(httpx.MockTransport(handler))
    row = _unknown_row(age_min=3)
    db = FakeDb(rows=[row])
    with patch.object(auto_trade, "get_session_factory", return_value=_factory(db)):
        summary = asyncio.run(auto_trade.confirm_live_fills())
    assert summary["resubmitted"] == 1 and summary["filled"] == 1 and summary["errors"] == 0
    assert row.status == "FILLED" and row.order_no == "0000030777" and row.filled_quantity == 23
    assert "체결 완료" in row.message            # FILLED 로 확정되면 체결 메시지가 재전송 메모를 덮는다
    import json as _json
    sent = _json.loads(bodies[0])
    assert sent["clientOrderId"] == "u:018290:B:202610061151" and sent["orderType"] == "MARKET" and sent["symbol"] == "018290"
    assert sent["quantity"] == 23 and sent["price"] == 0 and sent["environment"] == "paper"
    env["filled"].assert_awaited_once()


def test_unknown_past_window_becomes_lost_and_notifies(env, monkeypatch):
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_UNKNOWN_RESUBMIT_MIN", 10)
    calls = _server((200, {"ok": True}))
    row = _unknown_row(age_min=25)
    db = FakeDb(rows=[row])
    with patch.object(auto_trade, "get_session_factory", return_value=_factory(db)):
        summary = asyncio.run(auto_trade.confirm_live_fills())
    assert summary["lost"] == 1 and row.status == "LOST" and "재전송 창" in row.message
    assert calls == []                       # 게이트웨이 호출 없음
    env["errored"].assert_awaited_once()
    from app.models import LIVE_ORDER_OPEN_STATUSES
    assert "LOST" not in LIVE_ORDER_OPEN_STATUSES


def test_unknown_market_closed_waits(env, monkeypatch):
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_UNKNOWN_RESUBMIT_MIN", 10)
    calls = _server((200, {"ok": True}))
    row = _unknown_row(age_min=2)
    db = FakeDb(rows=[row])
    with patch.object(gw, "is_krx_market_open", return_value=False), \
         patch.object(auto_trade, "get_session_factory", return_value=_factory(db)):
        summary = asyncio.run(auto_trade.confirm_live_fills())
    assert summary["skipped"] == 1 and row.status == "UNKNOWN" and calls == []


def test_order_calls_use_longer_timeout(env, monkeypatch):
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_TIMEOUT", 15.0)
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_ORDER_TIMEOUT", 45.0)
    seen = []
    real_client = gw._client
    def spy(timeout=None):
        seen.append(timeout); return real_client(timeout)
    _server((200, {"ok": True, "order": {"orderNo": "1", "status": "ACCEPTED"}}))
    with patch.object(gw, "_client", spy):
        asyncio.run(gw.place_order("005930.KS", "buy", 1, 70_000.0, client_order_id="c"))
        asyncio.run(gw.get_order_status("1"))
    assert seen[:2] == [45.0, 45.0] and seen[2] is None
