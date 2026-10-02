"""레거시 직접 호출 경로(게이트웨이 미설정)에서 KIS 는 DB 키가 아니라 Secrets Manager 자격증명을 쓴다."""
import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from app.services import auto_trade
from app.services import kis_credentials as kc

UID = uuid.UUID("0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_BASE_URL", "")
    for k in ("KIS_SECRETS_NAME", "KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT_NO"):
        monkeypatch.setattr(settings, k, "")
    monkeypatch.setattr(settings, "KIS_ENVIRONMENT", "paper")
    kc.invalidate()
    yield
    kc.invalidate()


def _row():
    return SimpleNamespace(quant_mode="live", broker="kis", app_key="dbkey", app_secret="dbsec", account_no="dbacct")


def test_kis_without_managed_credentials_is_skipped_even_if_db_has_keys():
    out = asyncio.run(auto_trade._place_live_order(_row(), "005930.KS", "삼성전자", "buy", 1, 70_000.0, str(UID)))
    assert out == {"status": "skipped", "broker": "kis", "reason": "kis_credentials_not_configured"}


def test_kis_uses_secrets_manager_credentials_and_paper_endpoint(monkeypatch):
    monkeypatch.setattr(settings, "KIS_SECRETS_NAME", "x")
    captured = {}

    class FakeClient:
        async def place_order(self, account_no, symbol, side, quantity, price):
            captured["account_no"] = account_no
            return {"rt_cd": "0"}

    def fake_factory(broker, app_key, app_secret, paper):
        captured.update(broker=broker, app_key=app_key, app_secret=app_secret, paper=paper)
        return FakeClient()

    with patch.object(kc, "_fetch_secret_json", return_value={"app_key": "smk", "app_secret": "sms", "account_no": "5012345601", "environment": "paper"}), \
         patch.object(auto_trade, "get_broker_client", side_effect=fake_factory), \
         patch.object(auto_trade.notification, "notify_order_placed", AsyncMock()):
        out = asyncio.run(auto_trade._place_live_order(_row(), "005930.KS", "삼성전자", "buy", 1, 70_000.0, str(UID)))
    assert out["status"] == "submitted"
    assert captured == {"broker": "kis", "app_key": "smk", "app_secret": "sms", "paper": True, "account_no": "5012345601"}
