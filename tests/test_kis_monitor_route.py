"""GET /api/quant/kis/monitor — KIS 모의투자결과 화면 데이터 조립."""
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.database.postgres import get_pg_session
from app.lib.session import get_current_user
from app.models import LiveOrder, Portfolio
from app.models.base import SYSTEM_USER_ID
from app.routes import kis_monitor as km
from app.services import kis_batch, reconciliation
from app.services.brokers import stock_coin_trade_gateway as gw

UID = uuid.UUID("0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0")
NOW = datetime.now(timezone.utc)


class Result:
    def __init__(self, many=()): self._many = list(many)
    def scalars(self): return self
    def all(self): return self._many


class FakeDb:
    def __init__(self, orders=(), portfolio=()): self.orders, self.portfolio = list(orders), list(portfolio)
    async def execute(self, stmt, *_a, **_k):
        entity = stmt.column_descriptions[0]["entity"]
        if entity is LiveOrder: return Result(self.orders)
        if entity is Portfolio: return Result(self.portfolio)
        raise AssertionError(entity)


def order(symbol, side, qty, status, avg=None, price=100.0, age_min=10, user=SYSTEM_USER_ID, order_type="MARKET"):
    return SimpleNamespace(user_id=user, client_order_id=f"c-{symbol}-{side}-{age_min}", environment="paper", symbol=symbol, name=symbol, side=side,
                           order_type=order_type, quantity=qty, price=price, order_no="1", status=status,
                           filled_quantity=qty if status == "FILLED" else 0, avg_filled_price=avg, message="", created_at=NOW - timedelta(minutes=age_min))


def test_realized_pnl_average_cost():
    rows = [order("240810.KQ", "BUY", 2, "FILLED", avg=100.0, age_min=50), order("240810.KQ", "BUY", 2, "FILLED", avg=110.0, age_min=40),
            order("240810.KQ", "SELL", 3, "FILLED", avg=120.0, age_min=30), order("018290.KQ", "BUY", 23, "LOST", age_min=20)]
    out = km.realized_pnl(rows)
    assert out["by_symbol"] == {"240810": 45.0} and out["total"] == 45.0          # 평균 105 → (120-105)*3
    assert out["open_cost"] == {"240810": {"qty": 1, "avg": 105.0}}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_BASE_URL", "https://sct.test")
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_API_KEY", "key")
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_KIS_ENVIRONMENT", "paper")
    monkeypatch.setattr(settings, "QUANT_AGGRESSIVE_MODE", True)
    orders = [order("240810.KQ", "BUY", 2, "FILLED", avg=100.3, price=100.0, age_min=30),
              order("018290.KQ", "BUY", 23, "LOST", age_min=20),
              order("005930.KS", "BUY", 1, "ACCEPTED", age_min=5, user=UID, order_type="LIMIT")]
    portfolio = [SimpleNamespace(symbol="240810.KQ", quantity=2, avg_price=100.0, name="원익IPS"), SimpleNamespace(symbol="018290.KQ", quantity=23, avg_price=12520.0, name="브이티")]
    db = FakeDb(orders, portfolio)
    app = FastAPI(); app.include_router(km.router)
    app.dependency_overrides[get_current_user] = lambda: {"id": str(UID), "email": "t@t"}
    app.dependency_overrides[get_pg_session] = lambda: db
    cycles = {"cycles": [{"time": "2026-10-06 03:02:23", "settings": {"symbols": ["192820.KS", "161890.KS"]},
                          "aggressive": {"buy": ["192820.KS"], "sell": {}, "notes": ["매수 시그널 없음 → 모멘텀 1위 192820.KS 로테이션 매수"]},
                          "trades": [{"type": "auto", "symbol": "192820.KS", "name": "코스맥스", "action": "buy", "quantity": 1, "price": 268500.0, "status": "filled", "live_order": {"status": "error", "error": "GATEWAY_UNREACHABLE"}},
                                     {"type": "risk", "status": "skipped"}],
                          "risk": {"skipped": [{"symbol": "X"}], "day_pnl_pct": -0.1}, "account": {"total_equity": 9_990_000}}]}
    async def cget(key, max_age_hours=24):
        if key.startswith("quant:cycle_log:"): return cycles
        if key == "quant:last_beat_run": return {"time": "2026-10-06T03:02:30+00:00", "ran": 1, "failed": 0}
        return None
    bal = {"cashBalance": 9_000_000, "totalEvalAmount": 10_500_000, "totalProfitLoss": 12_345,
           "holdings": [{"symbol": "005930", "name": "삼성전자", "quantity": 38, "avgPrice": 70000, "currentPrice": 271000, "evalAmount": 10_298_000, "profitLoss": 7_000_000, "profitLossRate": 200.0},
                        {"symbol": "240810", "name": "원익IPS", "quantity": 2, "avgPrice": 100.3, "currentPrice": 141700, "evalAmount": 283_400, "profitLoss": 283_000, "profitLossRate": 99.0}]}
    with patch.object(gw, "get_balance", AsyncMock(return_value=bal)), \
         patch.object(kis_batch, "system_status", AsyncMock(return_value={"enabled": True, "running": True, "kill_switch": False, "kill_reason": ""})), \
         patch.object(reconciliation, "latest", AsyncMock(return_value={"ok": False, "checked_at": "t", "issues": [{"type": "phantom_trade", "symbol": "192820.KS"}], "summary": {"baseline": "t0"}})), \
         patch.object(km, "cache_get", cget), patch.object(km, "_heartbeat_age", return_value=42.0):
        yield TestClient(app)


def test_monitor_assembles_everything(client):
    r = client.get("/api/quant/kis/monitor")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["environment"] == "paper" and d["batch"]["running"] is True and d["aggressive"]["enabled"] is True and d["heartbeat_age_sec"] == 42.0
    assert d["universe"]["count"] >= 28 and set(d["universe"]["sectors"]) == {"반도체", "IT", "K뷰티"}
    o = d["orders"]
    assert o["today_total"] == 3 and o["today_filled"] == 1 and o["fill_rate_pct"] == 33.3 and o["today_counts"] == {"FILLED": 1, "LOST": 1, "ACCEPTED": 1}
    assert [x["status"] for x in o["unresolved"]] == ["LOST"] and o["avg_slippage_pct"] == 0.3
    owners = {x["symbol"]: x["owner"] for x in o["recent"]}
    assert owners["005930.KS"] == "me" and owners["240810.KQ"] == "batch"
    assert o["today_owner_counts"] == {"batch": 2, "me": 1}
    assert {x["owner_label"] for x in o["recent"]} == {"봇(배치)", "사용자"}
    a = d["account"]
    assert a["connected"] and a["total"] == 10_500_000 and a["positions"] == 2 and a["bot_positions"] == 1
    bot = next(h for h in a["holdings"] if h["symbol"] == "240810")
    assert bot["bot_managed"] is True and bot["bot_quantity"] == 2
    assert next(h for h in a["holdings"] if h["symbol"] == "005930")["bot_managed"] is False
    assert d["virtual_positions"]["018290"]["quantity"] == 23
    c = d["cycles"]
    assert c["count"] == 1 and c["recent"][0]["trades"][0]["live"] == "error" and c["recent"][0]["skipped"] == 1 and c["recent"][0]["notes"]
    assert d["last_beat_run"]["ran"] == 1 and d["reconcile"]["ok"] is False


def test_monitor_without_gateway(client, monkeypatch):
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_BASE_URL", "")
    d = client.get("/api/quant/kis/monitor").json()
    assert d["gateway_configured"] is False and d["account"] == {"connected": False}
    assert d["orders"]["today_total"] == 3           # 주문 기록은 게이트웨이와 무관하게 보인다


def test_orders_search_owner_status_side_text(client):
    """봇 실주문 그리드 검색: 구분·상태·방향·텍스트·페이지."""
    d = client.get("/api/quant/kis/orders").json()
    assert d["total"] == 3 and len(d["rows"]) == 3 and d["counts_by_owner"] == {"batch": 2, "me": 1}
    assert d["counts_by_status"] == {"FILLED": 1, "LOST": 1, "ACCEPTED": 1} and d["rows"][0]["symbol"] == "005930.KS"   # 최신순
    assert d["rows"][0]["owner_label"] == "사용자" and d["rows"][1]["owner_label"] == "봇(배치)"
    assert [r["owner"] for r in client.get("/api/quant/kis/orders?owner=batch").json()["rows"]] == ["batch", "batch"]
    assert [r["symbol"] for r in client.get("/api/quant/kis/orders?owner=me").json()["rows"]] == ["005930.KS"]
    assert [r["status"] for r in client.get("/api/quant/kis/orders?status=lost,error").json()["rows"]] == ["LOST"]
    assert client.get("/api/quant/kis/orders?side=SELL").json()["total"] == 0
    assert [r["symbol"] for r in client.get("/api/quant/kis/orders?q=005930").json()["rows"]] == ["005930.KS"]      # 코드
    assert [r["symbol"] for r in client.get("/api/quant/kis/orders?q=018290.kq").json()["rows"]] == ["018290.KQ"]   # 대소문자 무시
    assert client.get("/api/quant/kis/orders?q=c-240810").json()["total"] == 1                                       # clientOrderId
    page = client.get("/api/quant/kis/orders?limit=1&offset=1").json()
    assert page["total"] == 3 and [r["symbol"] for r in page["rows"]] == ["018290.KQ"]   # 최신순 2번째(20분 전)


def test_orders_search_date_range_kst(client):
    from datetime import timedelta as td
    kst_today = (NOW + td(hours=9)).strftime("%Y-%m-%d")
    tomorrow = (NOW + td(hours=9) + td(days=1)).strftime("%Y-%m-%d")
    assert client.get(f"/api/quant/kis/orders?date_from={kst_today}&date_to={kst_today}").json()["total"] == 3
    assert client.get(f"/api/quant/kis/orders?date_from={tomorrow}").json()["total"] == 0
    assert client.get("/api/quant/kis/orders?date_from=2026/10/07").status_code == 400
    assert client.get(f"/api/quant/kis/orders?date_from={tomorrow}&date_to={kst_today}").status_code == 400
    assert client.get("/api/quant/kis/orders?owner=nobody").status_code == 422
