"""HTTP 레벨: GET /api/quant/settings 에 kis_managed 만 내려가고 키 원문이 없는지, 원클릭 라우트 2개가 동작하는지."""
import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.database.postgres import get_pg_session
from app.lib.session import get_current_user
from app.routes import stocks as sr
from app.services import kis_credentials as kc
from app.services import kis_quickstart as qs

UID = uuid.UUID("0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0")


class Result:
    def __init__(self, one=None): self._one = one
    def scalar_one_or_none(self): return self._one


class FakeDb:
    def __init__(self, row=None): self.row, self.commits = row, 0
    def add(self, obj): self.row = obj
    async def flush(self): pass
    async def commit(self): self.commits += 1
    async def execute(self, stmt, *_a, **_k): return Result(one=self.row)


def kis_row():
    return SimpleNamespace(
        user_id=UID, broker="kis", quant_mode="paper", paper=True, app_key="LEFTOVER-KEY", app_secret="LEFTOVER-SECRET",
        account_no="5012345601", quant_symbol_source="ai", quant_selected_symbols=[], quant_ai_top_n=3,
        quant_per_trade_budget=1_000_000.0, quant_buy_ratio=1.0, quant_sell_ratio=0.5,
        risk_daily_loss_limit_pct=3.0, risk_max_position_pct=30.0, risk_max_orders_per_day=20, risk_cooldown_min=30,
        risk_kill_switch=False, risk_halt_reason="", quant_auto_enabled=False, quant_strategy_id="", quant_strategy_version=0,
    )


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_BASE_URL", "https://sct.test")
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_API_KEY", "key")
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_KIS_ENVIRONMENT", "paper")
    monkeypatch.setattr(settings, "KIS_SECRETS_NAME", "lumina-invest/prod/kis")
    for k in ("KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT_NO"):
        monkeypatch.setattr(settings, k, "")
    kc.invalidate()
    db = FakeDb(kis_row())
    app = FastAPI()
    app.include_router(sr.router)
    app.dependency_overrides[get_current_user] = lambda: {"id": str(UID), "email": "t@t"}
    app.dependency_overrides[get_pg_session] = lambda: db
    with patch.object(kc, "_fetch_secret_json", return_value={"app_key": "SM-KEY", "app_secret": "SM-SECRET", "account_no": "5012345601", "environment": "paper"}), \
         patch.object(sr.strategy_loader, "is_configured", return_value=False), \
         patch.object(sr.strategy_loader, "list_strategies", AsyncMock(return_value=[])):
        yield TestClient(app), db
    kc.invalidate()


def test_get_quant_settings_exposes_only_connection_status(client):
    c, _db = client
    r = c.get("/api/quant/settings")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["broker"] == "kis" and body["connected"] is True and body["app_key"] == ""
    assert body["account_no"] == "5012****01"
    km = body["kis_managed"]
    assert km["configured"] and km["source"] == "secrets-manager" and km["environment"] == "paper"
    assert "managed_brokers" in body and "kis" in body["managed_brokers"]
    for secret in ("LEFTOVER-KEY", "LEFTOVER-SECRET", "SM-KEY", "SM-SECRET"):
        assert secret not in r.text


def test_post_quant_settings_with_kis_ignores_user_keys(client):
    c, db = client
    r = c.post("/api/quant/settings", json={"mode": "paper", "broker": "kis", "app_key": "USER-KEY", "app_secret": "USER-SECRET",
                                             "account_no": "999", "symbol_source": "ai"})
    assert r.status_code == 200, r.text
    assert (db.row.app_key, db.row.app_secret, db.row.account_no) == ("", "", "")
    assert db.commits == 1


def test_quickstart_readiness_and_start(client):
    c, db = client
    r = c.get("/api/quant/kis/quickstart")
    assert r.status_code == 200 and r.json()["ready"] is True and r.json()["route"] == "stock-coin-trade"
    with patch.object(qs.auto_trade, "start_auto_trade", AsyncMock(return_value=True)) as start, patch.object(qs, "audit", AsyncMock()):
        r = c.post("/api/quant/kis/quickstart")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["started"] and body["environment"] == "paper" and body["settings"]["symbol_source"] == "ai"
    assert db.row.quant_mode == "live" and db.row.broker == "kis" and db.row.quant_symbol_source == "ai"
    assert db.row.quant_per_trade_budget == 500_000.0 and db.row.risk_max_position_pct == 20.0
    start.assert_awaited_once()
    r = c.get("/api/quant/kis/quickstart")
    assert r.json()["already_started"] is False   # DB 플래그는 start_auto_trade(모킹)가 켜므로 여기선 False


def test_quickstart_409_when_real(client, monkeypatch):
    c, db = client
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_KIS_ENVIRONMENT", "real")
    r = c.post("/api/quant/kis/quickstart")
    assert r.status_code == 409 and "실전" in r.json()["detail"]
    assert db.row.quant_mode == "paper" and db.commits == 0
