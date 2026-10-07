"""KIS 모의투자 백그라운드 배치(kis_batch): 계정·로그인 없이 시스템 행을 켜고 끄는 규칙."""
import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from app.models import BrokerSettings
from app.models.base import SYSTEM_USER_ID
from app.services import kis_batch as kb
from app.services import kis_credentials as kc
from app.services import kis_quickstart as qs


class Result:
    def __init__(self, one=None): self._one = one
    def scalar_one_or_none(self): return self._one
    def scalars(self): return self
    def all(self): return []          # _other_live_kis_rows 기본: 다른 kis·live 행 없음


class FakeDb:
    def __init__(self, row=None):
        self.row, self.added, self.commits = row, [], 0
    def add(self, obj): self.added.append(obj); self.row = obj
    async def commit(self): self.commits += 1
    async def execute(self, stmt, *_a, **_k): return Result(one=self.row)


def row(**over):
    base = dict(user_id=SYSTEM_USER_ID, broker="kis", quant_mode="live", paper=False, app_key="", app_secret="", account_no="",
                quant_symbol_source="ai", quant_selected_symbols=[], quant_ai_top_n=3, quant_per_trade_budget=300_000.0,
                quant_buy_ratio=1.0, quant_sell_ratio=0.5, risk_daily_loss_limit_pct=3.0, risk_max_position_pct=20.0,
                risk_max_orders_per_day=10, risk_cooldown_min=30, risk_kill_switch=False, risk_halt_reason="",
                quant_auto_enabled=False)
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(settings, "KIS_PAPER_BATCH_ENABLED", True)
    monkeypatch.setattr(settings, "KIS_PAPER_BATCH_EXCLUSIVE", True)
    monkeypatch.setattr(settings, "KIS_PAPER_BATCH_SYMBOLS", "")
    monkeypatch.setattr(settings, "KIS_PAPER_BATCH_AI_TOP_N", 3)
    monkeypatch.setattr(settings, "KIS_PAPER_BATCH_PER_TRADE_BUDGET", 300_000)
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_BASE_URL", "https://sct.test")
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_API_KEY", "key")
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_KIS_ENVIRONMENT", "paper")
    for k in ("KIS_SECRETS_NAME", "KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT_NO"):
        monkeypatch.setattr(settings, k, "")
    kc.invalidate()
    with patch.object(kb, "audit", AsyncMock()):
        yield
    kc.invalidate()


def run(db): return asyncio.run(kb.ensure_system_batch(db))


def test_disabled_env_does_nothing_without_row(monkeypatch):
    monkeypatch.setattr(settings, "KIS_PAPER_BATCH_ENABLED", False)
    db = FakeDb(None)
    out = run(db)
    assert out == {"enabled": False, "running": False, "action": "none"}
    assert db.added == [] and db.commits == 0


def test_disabled_env_turns_off_running_batch_row(monkeypatch):
    monkeypatch.setattr(settings, "KIS_PAPER_BATCH_ENABLED", False)
    r = row(quant_auto_enabled=True)
    out = run(FakeDb(r))
    assert out["action"] == "disabled" and r.quant_auto_enabled is False


def test_disabled_env_leaves_non_batch_system_row_alone(monkeypatch):
    """예전 quant_system paper 루프가 쓰던 행(mock/paper)은 배치 스위치와 무관."""
    monkeypatch.setattr(settings, "KIS_PAPER_BATCH_ENABLED", False)
    r = row(broker="mock", quant_mode="paper", quant_auto_enabled=True)
    out = run(FakeDb(r))
    assert out["action"] == "none" and r.quant_auto_enabled is True


def test_creates_system_row_and_starts_via_gateway_paper():
    db = FakeDb(None)
    out = run(db)
    assert out["running"] and out["created"] and out["started"]
    assert out["route"] == "stock-coin-trade" and out["environment"] == "paper"
    r = db.added[0]
    assert isinstance(r, BrokerSettings) and r.user_id == SYSTEM_USER_ID
    assert r.broker == "kis" and r.quant_mode == "live" and r.paper is False and r.quant_auto_enabled is True
    assert r.quant_symbol_source == "ai" and r.quant_selected_symbols == [] and r.quant_ai_top_n == 3
    assert r.quant_per_trade_budget == 300_000.0 and r.risk_max_position_pct == 20.0 and r.risk_max_orders_per_day == 10
    assert r.app_key == "" and r.app_secret == "" and r.account_no == ""
    assert db.commits == 1
    kb.audit.assert_awaited_once()
    assert kb.audit.await_args.args[2] == "quant.kis_batch.start"


def test_manual_symbols_from_env(monkeypatch):
    monkeypatch.setattr(settings, "KIS_PAPER_BATCH_SYMBOLS", "005930.KS, 035720.KS")
    monkeypatch.setattr(settings, "KIS_PAPER_BATCH_PER_TRADE_BUDGET", 500_000)
    db = FakeDb(None)
    run(db)
    r = db.added[0]
    assert r.quant_symbol_source == "manual" and r.quant_selected_symbols == ["005930.KS", "035720.KS"]
    assert r.quant_per_trade_budget == 500_000.0


def test_existing_row_follows_env_for_budget_and_symbols_but_keeps_limits():
    """2026-10-07: env 가 배치 행의 정본. 투자금·종목은 env 로 맞추고 위험 한도(쿨다운 등)는 그대로 둔다."""
    r = row(quant_auto_enabled=False, quant_per_trade_budget=123_000.0, risk_cooldown_min=5, risk_max_position_pct=15.0,
            quant_symbol_source="manual", quant_selected_symbols=["000660.KS"])
    db = FakeDb(r)
    out = run(db)
    assert out["running"] and out["started"] and not out["created"]
    assert r.quant_auto_enabled is True
    assert r.quant_per_trade_budget == 300_000.0 and r.quant_symbol_source == "ai" and r.quant_selected_symbols == []
    assert set(out["synced"]) == {"quant_per_trade_budget", "quant_symbol_source", "quant_selected_symbols"}
    assert r.risk_cooldown_min == 5 and r.risk_max_position_pct == 15.0
    assert db.commits == 1


def test_running_row_budget_updates_when_env_changes(monkeypatch):
    """운영 중인 배치 행(30만 원)이 env 를 50만 원으로 바꾸면 다음 사이클에 50만 원이 된다. 재시작(audit)은 아니다."""
    monkeypatch.setattr(settings, "KIS_PAPER_BATCH_PER_TRADE_BUDGET", 500_000)
    r = row(quant_auto_enabled=True, quant_per_trade_budget=300_000.0)
    db = FakeDb(r)
    out = run(db)
    assert out["running"] and not out["started"] and out["synced"] == ["quant_per_trade_budget"]
    assert r.quant_per_trade_budget == 500_000.0 and db.commits == 1
    kb.audit.assert_not_awaited()


def test_idempotent_when_already_running():
    r = row(quant_auto_enabled=True)
    db = FakeDb(r)
    out = run(db)
    assert out["running"] and not out["started"] and out["synced"] == [] and db.commits == 0
    kb.audit.assert_not_awaited()


def test_forces_kis_live_on_existing_row():
    r = row(broker="mock", quant_mode="paper", paper=True, quant_auto_enabled=True)
    run(FakeDb(r))
    assert r.broker == "kis" and r.quant_mode == "live" and r.paper is False


def test_kill_switch_blocks_restart():
    r = row(risk_kill_switch=True, risk_halt_reason="일손실 한도 초과", quant_auto_enabled=False)
    out = run(FakeDb(r))
    assert out["running"] is False and out["reason"] == "kill_switch" and out["kill_reason"] == "일손실 한도 초과"
    assert r.quant_auto_enabled is False


def test_real_environment_never_starts_and_disables(monkeypatch):
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_KIS_ENVIRONMENT", "real")
    r = row(quant_auto_enabled=True)
    db = FakeDb(r)
    out = run(db)
    assert out["running"] is False and out["reason"] == "real_environment" and out["action"] == "disabled"
    assert r.quant_auto_enabled is False and db.commits == 1
    db2 = FakeDb(None)
    assert run(db2)["running"] is False and db2.added == []


def test_not_connected_does_not_start(monkeypatch):
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_BASE_URL", "")
    db = FakeDb(None)
    out = run(db)
    assert out["running"] is False and out["reason"] == "not_connected" and db.added == []


def test_exclusive_disables_other_live_kis_rows(monkeypatch):
    other = row(user_id=uuid.UUID("11111111-1111-1111-1111-111111111111"), quant_auto_enabled=True)
    with patch.object(kb, "_other_live_kis_rows", AsyncMock(return_value=[other])):
        out = run(FakeDb(None))
    assert out["running"] and out["exclusive_disabled_users"] == [str(other.user_id)]
    assert other.quant_auto_enabled is False
    actions = [c.args[2] for c in kb.audit.await_args_list]
    assert "quant.kis_batch.exclusive" in actions and "quant.kis_batch.start" in actions


def test_exclusive_off_leaves_other_rows(monkeypatch):
    monkeypatch.setattr(settings, "KIS_PAPER_BATCH_EXCLUSIVE", False)
    other = row(user_id=uuid.UUID("11111111-1111-1111-1111-111111111111"), quant_auto_enabled=True)
    with patch.object(kb, "_other_live_kis_rows", AsyncMock(return_value=[other])):
        out = run(FakeDb(row(quant_auto_enabled=True)))
    assert out["exclusive_disabled_users"] == [] and other.quant_auto_enabled is True


def test_system_status_shape():
    st = asyncio.run(kb.system_status(FakeDb(row(quant_auto_enabled=True))))
    assert st["enabled"] is True and st["running"] is True and st["kill_switch"] is False
    assert st["user_id"] == str(SYSTEM_USER_ID)


def test_readiness_includes_system_batch():
    st = asyncio.run(qs.readiness(FakeDb(None), SYSTEM_USER_ID))
    assert st["system_batch"]["enabled"] is True and st["system_batch"]["running"] is False


def test_run_cycle_calls_ensure_then_runs_system_user(monkeypatch):
    """celery-beat 진입점: 배치 점검 → enabled 행 조회 → 사이클. 시스템 행이 켜지면 사이클에 포함된다."""
    from app.services import auto_trade

    class Scalars:
        def __init__(self, v): self.v = v
        def scalars(self): return self
        def all(self): return self.v

    class Db:
        async def execute(self, stmt, *_a, **_k): return Scalars([SYSTEM_USER_ID])
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    monkeypatch.setattr(auto_trade, "get_session_factory", lambda: (lambda: Db()))
    ensure = AsyncMock(return_value={"enabled": True, "running": True})
    cycle = AsyncMock()
    with patch.object(kb, "ensure_system_batch", ensure), patch.object(auto_trade, "_run_quant_cycle", cycle):
        out = asyncio.run(auto_trade.run_cycle_for_enabled_users())
    ensure.assert_awaited_once()
    cycle.assert_awaited_once_with(str(SYSTEM_USER_ID))
    assert out["enabled_users"] == 1 and out["ran"] == 1 and out["kis_batch"]["running"] is True


def test_run_cycle_survives_batch_check_failure(monkeypatch):
    from app.services import auto_trade

    class Scalars:
        def scalars(self): return self
        def all(self): return []

    class Db:
        async def execute(self, *_a, **_k): return Scalars()
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    monkeypatch.setattr(auto_trade, "get_session_factory", lambda: (lambda: Db()))
    with patch.object(kb, "ensure_system_batch", AsyncMock(side_effect=RuntimeError("db down"))):
        out = asyncio.run(auto_trade.run_cycle_for_enabled_users())
    assert out["ran"] == 0 and out["kis_batch"]["error"] == "db down"
