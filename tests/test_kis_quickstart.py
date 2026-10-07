"""통합 대시보드 「KIS 모의투자 시작」 원클릭: 경로 판정, 차단 조건, 설정 저장 + 자동매매 ON."""
import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from app.models import BrokerSettings
from app.services import kis_credentials as kc
from app.services import kis_quickstart as qs

UID = uuid.UUID("0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0")


class Result:
    def __init__(self, one=None): self._one = one
    def scalar_one_or_none(self): return self._one


class FakeDb:
    def __init__(self, row=None):
        self.row, self.added, self.commits = row, [], 0
    def add(self, obj): self.added.append(obj); self.row = obj
    async def commit(self): self.commits += 1
    async def execute(self, stmt, *_a, **_k):
        return Result(one=self.row)


def row(**over):
    base = dict(user_id=UID, broker="mock", quant_mode="paper", paper=True, app_key="k", app_secret="s", account_no="a",
                quant_symbol_source="manual", quant_selected_symbols=["005930.KS"], quant_ai_top_n=1,
                quant_per_trade_budget=1_000_000.0, quant_buy_ratio=1.0, quant_sell_ratio=0.5,
                risk_daily_loss_limit_pct=3.0, risk_max_position_pct=30.0, risk_max_orders_per_day=20, risk_cooldown_min=30,
                risk_kill_switch=False, risk_halt_reason="", quant_auto_enabled=False)
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_BASE_URL", "https://sct.test")
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_API_KEY", "key")
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_KIS_ENVIRONMENT", "paper")
    for k in ("KIS_SECRETS_NAME", "KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT_NO"):
        monkeypatch.setattr(settings, k, "")
    kc.invalidate()
    yield
    kc.invalidate()


def _patches(started=True):
    return (patch.object(qs.auto_trade, "start_auto_trade", AsyncMock(return_value=started)),
            patch.object(qs, "audit", AsyncMock()))


def test_readiness_ready_via_gateway_paper():
    st = asyncio.run(qs.readiness(FakeDb(None), UID))
    assert st["ready"] and st["route"] == "stock-coin-trade" and st["environment"] == "paper"
    assert st["running"] is False and st["already_started"] is False
    assert st["defaults"]["quant_per_trade_budget"] == 500_000.0 and st["defaults"]["risk_max_position_pct"] == 20.0


def test_readiness_blocked_when_nothing_connected(monkeypatch):
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_BASE_URL", "")
    st = asyncio.run(qs.readiness(FakeDb(None), UID))
    assert st["ready"] is False and st["reason"] == "not_connected" and st["connected"] is False


def test_readiness_blocked_when_gateway_is_real(monkeypatch):
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_KIS_ENVIRONMENT", "real")
    st = asyncio.run(qs.readiness(FakeDb(None), UID))
    assert st["ready"] is False and st["reason"] == "real_environment"


def test_readiness_uses_managed_kis_credentials_when_no_gateway(monkeypatch):
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_BASE_URL", "")
    monkeypatch.setattr(settings, "KIS_SECRETS_NAME", "x")
    with patch.object(kc, "_fetch_secret_json", return_value={"app_key": "a", "app_secret": "b", "account_no": "5012345601"}):
        st = asyncio.run(qs.readiness(FakeDb(None), UID))
    assert st["ready"] and st["route"] == "kis-direct" and st["environment"] == "paper"


def test_readiness_already_started_and_kill_switch():
    st = asyncio.run(qs.readiness(FakeDb(row(broker="kis", quant_mode="live", quant_auto_enabled=True)), UID))
    assert st["already_started"] is True and st["running"] is True
    st2 = asyncio.run(qs.readiness(FakeDb(row(risk_kill_switch=True, risk_halt_reason="일손실")), UID))
    assert st2["ready"] is False and st2["reason"] == "kill_switch" and st2["kill_reason"] == "일손실"


def test_start_creates_row_applies_testbed_defaults_and_enables():
    db = FakeDb(None)
    p1, p2 = _patches(started=True)
    with p1 as start, p2 as audit:
        out = asyncio.run(qs.start(db, str(UID)))
    r = db.row
    assert isinstance(r, BrokerSettings) and db.commits == 1
    assert (r.broker, r.quant_mode, r.paper, r.quant_symbol_source, r.quant_ai_top_n) == ("kis", "live", False, "ai", 3)
    assert r.quant_per_trade_budget == 500_000.0 and r.risk_max_position_pct == 20.0 and r.risk_max_orders_per_day == 10
    assert (r.app_key, r.app_secret, r.account_no) == ("", "", "")
    start.assert_awaited_once_with(db, str(UID))
    audit.assert_awaited_once()
    assert out["ok"] and out["started"] and out["route"] == "stock-coin-trade" and out["settings"]["symbol_source"] == "ai"


def test_start_on_existing_row_clears_user_keys_and_reports_already_running():
    db = FakeDb(row(quant_auto_enabled=True))
    p1, p2 = _patches(started=False)
    with p1, p2:
        out = asyncio.run(qs.start(db, str(UID)))
    assert db.added == [] and db.row.broker == "kis" and db.row.quant_mode == "live"
    assert (db.row.app_key, db.row.app_secret, db.row.account_no) == ("", "", "")
    assert db.row.quant_selected_symbols == []
    assert out["started"] is False and out["already_running"] is True


def test_start_blocked_not_connected(monkeypatch):
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_BASE_URL", "")
    db = FakeDb(None)
    with pytest.raises(qs.QuickstartBlocked) as ei:
        asyncio.run(qs.start(db, str(UID)))
    assert ei.value.reason == "not_connected" and db.commits == 0


def test_start_blocked_real_environment(monkeypatch):
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_KIS_ENVIRONMENT", "real")
    db = FakeDb(row())
    with pytest.raises(qs.QuickstartBlocked) as ei:
        asyncio.run(qs.start(db, str(UID)))
    assert ei.value.reason == "real_environment" and db.commits == 0 and db.row.broker == "mock"


def test_start_blocked_kill_switch():
    db = FakeDb(row(risk_kill_switch=True, risk_halt_reason="수동"))
    with pytest.raises(qs.QuickstartBlocked) as ei:
        asyncio.run(qs.start(db, str(UID)))
    assert ei.value.reason == "kill_switch" and db.commits == 0


def test_route_returns_409_on_block(monkeypatch):
    from fastapi import HTTPException
    from app.routes import stocks as sr
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_BASE_URL", "")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(sr.kis_quickstart_start(user={"id": str(UID)}, db=FakeDb(None)))
    assert ei.value.status_code == 409 and "설정되지 않았" in ei.value.detail


def test_start_blocked_when_batch_exclusive_running(monkeypatch):
    """배치 단독 실행 모드에서 배치가 돌면 사용자 KIS 시작은 409 사유 batch_exclusive. 기본값(false)에서는 막지 않는다."""
    from app.services import kis_batch
    monkeypatch.setattr(settings, "KIS_PAPER_BATCH_EXCLUSIVE", True)
    with patch.object(kis_batch, "system_status", AsyncMock(return_value={"enabled": True, "running": True, "kill_switch": False, "kill_reason": ""})):
        db = FakeDb(row())
        with pytest.raises(qs.QuickstartBlocked) as ei:
            asyncio.run(qs.start(db, str(UID)))
        assert ei.value.reason == "batch_exclusive" and db.commits == 0
        st = asyncio.run(qs.readiness(db, UID))
        assert st["ready"] is False and st["reason"] == "batch_exclusive"
    monkeypatch.setattr(settings, "KIS_PAPER_BATCH_EXCLUSIVE", False)
    p_start, p_audit = _patches()
    with patch.object(kis_batch, "system_status", AsyncMock(return_value={"enabled": True, "running": True, "kill_switch": False, "kill_reason": ""})), p_start, p_audit:
        db = FakeDb(row())
        out = asyncio.run(qs.start(db, str(UID)))
        assert out["ok"] and db.row.quant_mode == "live" and db.row.broker == "kis"
        assert asyncio.run(qs.readiness(db, UID))["ready"] is True


def test_batch_exclusive_default_is_off():
    assert type(settings).model_fields["KIS_PAPER_BATCH_EXCLUSIVE"].default is False
