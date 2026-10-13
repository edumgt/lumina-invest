"""전략설정(app/services/strategy_settings.py + /api/quant/strategy/*): 검증·런타임 반영·사이클 게이트·프로필·성과 귀속·라우트."""
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
from app.models import BrokerSettings, LiveOrder, Order
from app.models.base import SYSTEM_USER_ID
from app.routes import strategy_settings as route
from app.services import strategy_loader, strategy_settings as ss

UID = uuid.UUID("0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0")
NOW = datetime(2026, 10, 13, 5, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clean_overrides():
    ss._overridden.clear()
    yield
    ss.apply_runtime({})
    ss._overridden.clear()


# ── 검증 ─────────────────────────────────────────────────────────────────────

def test_validate_env_values_coerces_and_bounds():
    out = ss.validate_env_values({"QUANT_AGGRESSIVE_MODE": "false", "QUANT_AGGRESSIVE_MAX_BUYS_PER_CYCLE": "1",
                                  "QUANT_AGGRESSIVE_TAKE_PROFIT_PCT": 2.5, "QUANT_AGGRESSIVE_ORDER_TYPE": "LIMIT",
                                  "quant_sell_ratio": 0.3, "UNKNOWN": 1})
    assert out == {"QUANT_AGGRESSIVE_MODE": False, "QUANT_AGGRESSIVE_MAX_BUYS_PER_CYCLE": 1,
                   "QUANT_AGGRESSIVE_TAKE_PROFIT_PCT": 2.5, "QUANT_AGGRESSIVE_ORDER_TYPE": "LIMIT"}   # row 키·모르는 키 제외
    with pytest.raises(ValueError):
        ss.validate_env_values({"QUANT_AGGRESSIVE_MAX_BUYS_PER_CYCLE": 99})
    with pytest.raises(ValueError):
        ss.validate_env_values({"QUANT_AGGRESSIVE_ORDER_TYPE": "FOK"})
    with pytest.raises(ValueError):
        ss.validate_env_values({"QUANT_AGGRESSIVE_WEAK_SELL_SCORE": 0})


def test_cycle_sec_never_below_beat_default():
    base = int(ss.ENV_DEFAULTS["QUANT_CYCLE_SEC"])
    assert ss.validate_env_values({"QUANT_CYCLE_SEC": 60})["QUANT_CYCLE_SEC"] == max(60, base)
    assert ss.validate_env_values({"QUANT_CYCLE_SEC": base + 600})["QUANT_CYCLE_SEC"] == base + 600


def test_validate_row_values():
    out = ss.validate_row_values({"strategy_id": " Sample_MA ", "quant_sell_ratio": "0.5", "risk_daily_loss_limit_pct": 2, "QUANT_CYCLE_SEC": 1})
    assert out == {"strategy_id": "sample_ma", "quant_sell_ratio": 0.5, "risk_daily_loss_limit_pct": 2.0}


# ── 런타임 반영 ───────────────────────────────────────────────────────────────

def test_apply_runtime_overrides_then_restores_only_touched_keys(monkeypatch):
    monkeypatch.setattr(settings, "QUANT_AGGRESSIVE_FORCE_BUY", True)
    monkeypatch.setattr(settings, "QUANT_AGGRESSIVE_MODE", True)     # 테스트가 직접 세팅한 값 — 덮어쓰기 대상이 아니면 건드리지 않아야
    changed = ss.apply_runtime({"QUANT_AGGRESSIVE_FORCE_BUY": False, "QUANT_AGGRESSIVE_WEAK_SELL_SCORE": -2})
    assert set(changed) >= {"QUANT_AGGRESSIVE_FORCE_BUY"}
    assert settings.QUANT_AGGRESSIVE_FORCE_BUY is False and settings.QUANT_AGGRESSIVE_WEAK_SELL_SCORE == -2
    assert settings.QUANT_AGGRESSIVE_MODE is True
    ss.apply_runtime({})                                              # 덮어쓰기 해제 → env 기본값 복원, 미접촉 키 유지
    assert settings.QUANT_AGGRESSIVE_FORCE_BUY == ss.ENV_DEFAULTS["QUANT_AGGRESSIVE_FORCE_BUY"]
    assert settings.QUANT_AGGRESSIVE_WEAK_SELL_SCORE == ss.ENV_DEFAULTS["QUANT_AGGRESSIVE_WEAK_SELL_SCORE"]
    assert settings.QUANT_AGGRESSIVE_MODE is True


def test_load_and_apply_reads_cache(monkeypatch):
    import asyncio
    doc = {"values": {"QUANT_AGGRESSIVE_MAX_SELLS_PER_CYCLE": 1}, "profiles": [{"id": "p1"}]}
    with patch.object(ss, "cache_get", AsyncMock(return_value=doc)):
        out = asyncio.run(ss.load_and_apply())
    assert settings.QUANT_AGGRESSIVE_MAX_SELLS_PER_CYCLE == 1 and ss.current_profile(out)["id"] == "p1"


def test_load_and_apply_survives_cache_failure():
    import asyncio
    with patch.object(ss, "cache_get", AsyncMock(side_effect=RuntimeError("db down"))):
        out = asyncio.run(ss.load_and_apply())
    assert out["values"] == {} and out["profiles"] == []


# ── 사이클 게이트 ─────────────────────────────────────────────────────────────

def test_should_skip_cycle_only_when_longer_interval_requested():
    base = int(ss.ENV_DEFAULTS["QUANT_CYCLE_SEC"])
    assert ss.should_skip_cycle({"values": {}, "last_run_at": NOW.isoformat()}, NOW + timedelta(seconds=1)) == (False, "")
    doc = {"values": {"QUANT_CYCLE_SEC": base * 4}, "last_run_at": NOW.isoformat()}
    skip, why = ss.should_skip_cycle(doc, NOW + timedelta(seconds=base))
    assert skip and "건너뜀" in why
    assert ss.should_skip_cycle(doc, NOW + timedelta(seconds=base * 4))[0] is False
    assert ss.should_skip_cycle({"values": {"QUANT_CYCLE_SEC": base * 4}}, NOW)[0] is False   # 첫 실행


# ── 프로필 저장 ───────────────────────────────────────────────────────────────

def test_save_creates_profile_only_when_effective_changes():
    import asyncio
    store: dict = {}

    async def cget(key, max_age_hours=24): return store.get(key)
    async def cset(key, data): store[key] = data

    row = {k: None for k in ss.ROW_KEYS} | {"strategy_id": "", "quant_sell_ratio": 0.5}
    with patch.object(ss, "cache_get", cget), patch.object(ss, "cache_set", cset):
        r1 = asyncio.run(ss.save({"QUANT_AGGRESSIVE_FORCE_BUY": False}, row, "u1", label="둔화", equity_start={"virtual": 100.0}))
        assert r1["created"]["id"] == "p1" and r1["created"]["label"] == "둔화" and r1["created"]["equity_start"] == {"virtual": 100.0}
        r2 = asyncio.run(ss.save({"QUANT_AGGRESSIVE_FORCE_BUY": False}, row, "u1"))
        assert r2["created"] is None                                          # 같은 값 → 프로필 추가 없음
        r3 = asyncio.run(ss.save({"QUANT_AGGRESSIVE_FORCE_BUY": False}, row | {"quant_sell_ratio": 1.0}, "u1"))
        assert r3["created"]["seq"] == 2 and r3["created"]["row"]["quant_sell_ratio"] == 1.0   # 행 값 변경도 프로필
        r4 = asyncio.run(ss.reset("u1", row))
        assert r4["created"]["values"]["QUANT_AGGRESSIVE_FORCE_BUY"] == ss.ENV_DEFAULTS["QUANT_AGGRESSIVE_FORCE_BUY"]
    assert settings.QUANT_AGGRESSIVE_FORCE_BUY == ss.ENV_DEFAULTS["QUANT_AGGRESSIVE_FORCE_BUY"]
    assert len(store[ss.CACHE_KEY]["profiles"]) == 3


# ── 성과 귀속 ─────────────────────────────────────────────────────────────────

def _profiles():
    return [{"id": "p1", "seq": 1, "applied_at": (NOW - timedelta(hours=2)).isoformat(), "values": {"QUANT_AGGRESSIVE_MODE": True}, "row": {}, "equity_start": {"virtual": 1000.0, "live": 2000.0}},
            {"id": "p2", "seq": 2, "applied_at": (NOW - timedelta(hours=1)).isoformat(), "values": {"QUANT_AGGRESSIVE_MODE": False}, "row": {"strategy_id": "s"}, "equity_start": {"virtual": 1100.0, "live": 2100.0}}]


def test_attribute_trades_assigns_realized_to_profile_active_at_sell_time():
    rows = [
        {"time": NOW - timedelta(hours=3), "symbol": "005930.KS", "side": "buy", "qty": 2, "price": 100.0},    # 설정 이전 매수
        {"time": NOW - timedelta(minutes=90), "symbol": "005930.KS", "side": "sell", "qty": 1, "price": 110.0},  # p1 에서 매도 +10
        {"time": NOW - timedelta(minutes=30), "symbol": "005930", "side": "SELL", "qty": 1, "price": 90.0},     # p2 에서 매도 -10 (코드 정규화)
        {"time": NOW - timedelta(minutes=20), "symbol": "000660.KS", "side": "sell", "qty": 1, "price": 50.0},  # 보유 없는 매도 → 손익 0, 승패 제외
    ]
    periods = ss._periods(_profiles(), NOW)
    st = ss.attribute_trades(periods, rows)
    assert st["before"] == {"buys": 1, "sells": 0, "buy_amount": 200.0, "sell_amount": 0.0, "realized": 0.0, "wins": 0, "losses": 0, "symbols": 1, "win_rate_pct": None}
    assert st["p1"]["realized"] == 10.0 and st["p1"]["wins"] == 1 and st["p1"]["win_rate_pct"] == 100.0
    assert st["p2"]["realized"] == -10.0 and st["p2"]["losses"] == 1 and st["p2"]["sells"] == 2 and st["p2"]["symbols"] == 2


def test_build_performance_rows_newest_first_with_equity_change():
    cycles = [{"time": (NOW - timedelta(minutes=50)).strftime("%Y-%m-%d %H:%M:%S"), "risk": {"skipped": [{"symbol": "x"}]}},
              {"time": (NOW - timedelta(minutes=100)).strftime("%Y-%m-%d %H:%M:%S"), "risk": {}}]
    out = ss.build_performance(_profiles(), [], [], cycles, latest_equity={"virtual": 1210.0, "live": 2000.0}, now=NOW)
    assert [r["id"] for r in out] == ["p2", "p1", "before"]
    p2, p1 = out[0], out[1]
    assert p2["active"] and p2["cycles_recent"] == 1 and p2["risk_skipped_recent"] == 1 and p2["duration_min"] == 60
    assert p2["equity_change_pct_virtual"] == 10.0 and p2["equity_change_pct_live"] == pytest.approx(-4.76, abs=0.01)
    assert p1["equity_change_pct_virtual"] == 10.0 and p1["end"] == p2["start"] and p1["cycles_recent"] == 1


def test_equity_from_cycle():
    assert ss.equity_from_cycle({"time": "t", "account": {"total_equity": 9.0}, "risk": {"live": {"equity": 8.0}}}) == {"virtual": 9.0, "live": 8.0, "at": "t"}
    assert ss.equity_from_cycle(None) == {}


# ── 라우트 ───────────────────────────────────────────────────────────────────

class _Result:
    def __init__(self, many=(), one=None): self._many, self._one = list(many), one
    def scalars(self): return self
    def all(self): return self._many
    def scalar_one_or_none(self): return self._one


class FakeDb:
    def __init__(self, row=None, orders=(), live=()): self.row, self.orders, self.live, self.committed, self.added = row, list(orders), list(live), 0, []
    async def execute(self, stmt, *_a, **_k):
        entity = stmt.column_descriptions[0]["entity"]
        if entity is BrokerSettings: return _Result(one=self.row)
        if entity is Order: return _Result(self.orders)
        if entity is LiveOrder: return _Result(self.live)
        raise AssertionError(entity)
    async def commit(self): self.committed += 1
    async def flush(self): pass
    def add(self, obj): self.added.append(obj); self.row = obj


def _row():
    return SimpleNamespace(user_id=SYSTEM_USER_ID, quant_strategy_id="", quant_strategy_version=0, quant_buy_ratio=1.0, quant_sell_ratio=0.5,
                           risk_daily_loss_limit_pct=3.0, risk_max_position_pct=20.0, risk_cooldown_min=30, risk_max_orders_per_day=10,
                           quant_auto_enabled=True, risk_kill_switch=False)


@pytest.fixture
def client():
    store: dict = {}
    async def cget(key, max_age_hours=24):
        if key == ss.CACHE_KEY: return store.get(key)
        if key.startswith("quant:cycle_log:"): return {"cycles": [{"time": "2026-10-13 04:56:59", "account": {"total_equity": 9_180_710.0}, "risk": {"live": {"equity": 88_185_205.0}}}]}
        return None
    async def cset(key, data): store[key] = data
    db = FakeDb(row=_row(),
                orders=[SimpleNamespace(user_id=SYSTEM_USER_ID, source="QUANT", status="filled", created_at=NOW - timedelta(hours=3), symbol="005930.KS", order_type="buy", quantity=2, price=100.0)],
                live=[SimpleNamespace(user_id=SYSTEM_USER_ID, status="FILLED", created_at=NOW - timedelta(hours=3), symbol="005930", side="BUY", filled_quantity=2, avg_filled_price=100.0)])
    app = FastAPI(); app.include_router(route.router)
    app.dependency_overrides[get_current_user] = lambda: {"id": str(UID), "email": "t@t"}
    app.dependency_overrides[get_pg_session] = lambda: db
    strategies = [{"strategy_id": "sample_ma_cross_kr", "version": 1, "name": "[샘플]", "universe_size": 3}]
    with patch.object(ss, "cache_get", cget), patch.object(ss, "cache_set", cset), patch.object(route, "cache_get", cget), \
         patch.object(strategy_loader, "list_strategies", AsyncMock(return_value=strategies)), \
         patch.object(strategy_loader, "get_strategy", AsyncMock(side_effect=lambda sid, v=None: {"strategy_id": sid, "version": 1} if sid == "sample_ma_cross_kr" else None)), \
         patch.object(strategy_loader, "is_configured", lambda: True), \
         patch.object(route, "audit", AsyncMock()):
        yield TestClient(app), db, store


def test_get_settings_lists_fields_with_descriptions(client):
    c, db, _ = client
    r = c.get("/api/quant/strategy/settings"); assert r.status_code == 200, r.text
    d = r.json()
    keys = {f["key"] for f in d["fields"]}
    assert {"QUANT_AGGRESSIVE_MODE", "QUANT_AGGRESSIVE_WEAK_SELL_SCORE", "QUANT_BASE_STOP_LOSS_PCT", "strategy_id", "quant_sell_ratio", "risk_daily_loss_limit_pct"} <= keys
    assert all(f["description"] and f["group"] for f in d["fields"])
    assert [g["name"] for g in d["groups"]] == list(ss.GROUP_ORDER)
    assert d["row"]["quant_sell_ratio"] == 0.5 and d["row"]["strategy_id"] == "" and d["strategies"][0]["strategy_id"] == "sample_ma_cross_kr"
    assert d["profile"] is None and d["effective"]["QUANT_AGGRESSIVE_FORCE_BUY"] == settings.QUANT_AGGRESSIVE_FORCE_BUY


def test_save_applies_runtime_and_row_and_records_profile(client):
    c, db, store = client
    body = {"values": {"QUANT_AGGRESSIVE_FORCE_BUY": False, "QUANT_AGGRESSIVE_WEAK_SELL_SCORE": -2, "QUANT_AGGRESSIVE_MODE": settings.QUANT_AGGRESSIVE_MODE,
                       "strategy_id": "sample_ma_cross_kr", "quant_sell_ratio": 0.3, "risk_daily_loss_limit_pct": 2.0}, "label": "둔화 1단계"}
    r = c.post("/api/quant/strategy/settings", json=body); assert r.status_code == 200, r.text
    d = r.json()
    assert d["created"] and d["profile"]["id"] == "p1" and d["profile"]["label"] == "둔화 1단계"
    assert d["profile"]["equity_start"] == {"virtual": 9_180_710.0, "live": 88_185_205.0, "at": "2026-10-13 04:56:59"}
    assert settings.QUANT_AGGRESSIVE_FORCE_BUY is False and settings.QUANT_AGGRESSIVE_WEAK_SELL_SCORE == -2
    assert db.row.quant_strategy_id == "sample_ma_cross_kr" and db.row.quant_strategy_version == 1 and db.row.quant_sell_ratio == 0.3 and db.committed == 1
    assert store[ss.CACHE_KEY]["values"] == {"QUANT_AGGRESSIVE_FORCE_BUY": False, "QUANT_AGGRESSIVE_WEAK_SELL_SCORE": -2}   # 기본값과 같은 값은 저장 안 함
    # 다시 GET 하면 덮어쓰기·프로필이 보인다
    g = c.get("/api/quant/strategy/settings").json()
    assert g["overrides"] == store[ss.CACHE_KEY]["values"] and g["profile"]["id"] == "p1" and g["profile_count"] == 1


def test_save_rejects_bad_values_and_unknown_strategy(client):
    c, db, _ = client
    assert c.post("/api/quant/strategy/settings", json={"values": {"QUANT_AGGRESSIVE_MAX_BUYS_PER_CYCLE": 50}}).status_code == 422
    r = c.post("/api/quant/strategy/settings", json={"values": {"strategy_id": "nope"}})
    assert r.status_code == 422 and "합격한 전략" in r.json()["detail"]
    assert db.committed == 0


def test_reset_clears_overrides_and_keeps_row(client):
    c, db, store = client
    c.post("/api/quant/strategy/settings", json={"values": {"QUANT_AGGRESSIVE_FORCE_BUY": False, "quant_sell_ratio": 0.3}})
    r = c.post("/api/quant/strategy/settings/reset"); assert r.status_code == 200, r.text
    assert store[ss.CACHE_KEY]["values"] == {} and r.json()["profile"]["id"] == "p2"
    assert settings.QUANT_AGGRESSIVE_FORCE_BUY == ss.ENV_DEFAULTS["QUANT_AGGRESSIVE_FORCE_BUY"] and db.row.quant_sell_ratio == 0.3


def test_performance_endpoint(client):
    c, db, _ = client
    c.post("/api/quant/strategy/settings", json={"values": {"QUANT_AGGRESSIVE_FORCE_BUY": False}, "label": "A"})
    db.live.append(SimpleNamespace(user_id=SYSTEM_USER_ID, status="FILLED", created_at=datetime.now(timezone.utc) + timedelta(seconds=1), symbol="005930", side="SELL", filled_quantity=1, avg_filled_price=130.0))
    r = c.get("/api/quant/strategy/performance"); assert r.status_code == 200, r.text
    d = r.json()
    assert d["virtual_orders"] == 1 and d["live_fills"] == 2 and [p["id"] for p in d["profiles"]] == ["p1", "before"]
    p1, before = d["profiles"]
    assert p1["active"] and p1["label"] == "A" and p1["live"]["realized"] == 30.0 and p1["live"]["wins"] == 1
    assert before["live"]["buys"] == 1 and before["virtual"]["buys"] == 1 and before["equity_end"]["virtual"] == 9_180_710.0


def test_system_row_created_when_missing(client):
    c, db, _ = client
    db.row = None
    r = c.post("/api/quant/strategy/settings", json={"values": {"quant_sell_ratio": 0.8}})
    assert r.status_code == 200, r.text
    assert db.added and db.added[0].user_id == SYSTEM_USER_ID and db.added[0].quant_sell_ratio == 0.8 and db.added[0].quant_auto_enabled is False


# ── 운영 모드 3분할 · 백테스트 연동 ──────────────────────────────────────────────

def test_signal_mode_derives_aggressive_flag_and_fields_carry_hp_marks():
    out = ss.validate_env_values({"QUANT_SIGNAL_MODE": "aggressive"})
    assert out == {"QUANT_SIGNAL_MODE": "aggressive", "QUANT_AGGRESSIVE_MODE": True}
    assert ss.validate_env_values({"QUANT_SIGNAL_MODE": "backtest"})["QUANT_AGGRESSIVE_MODE"] is False
    with pytest.raises(ValueError):
        ss.validate_env_values({"QUANT_SIGNAL_MODE": "turbo"})
    fields = {f["key"]: f for f in ss.describe_fields()}
    assert fields["QUANT_AGGRESSIVE_MODE"]["hidden"] is True
    assert fields["QUANT_CUSTOM_BUY_SCORE"]["hp_like"] is True and fields["risk_daily_loss_limit_pct"]["hp_like"] is False
    groups = {g["name"]: g["modes"] for g in ss.describe_groups()}
    assert groups[ss.G_BT] == ["backtest"] and groups[ss.G_CUSTOM] == ["custom"] and groups[ss.G_BASE] == ["backtest", "custom"] and groups[ss.G_RISK] == []
    assert ss.HP_HELP["rows"] and "하이퍼파라미터" in ss.HP_HELP["title"]


def test_spec_health_flags_buy_hold_stale_and_versions():
    spec = {"entry": {"indicator": "buy_hold", "condition": "always"}, "universe": ["005930"],
            "backtest_result": {"period": {"start": "2021-01-01", "end": "2026-01-01"}, "engine": "lean"}, "position_sizing": {"max_symbols": 2, "max_position_pct": 20}}
    from datetime import date
    h = ss.spec_health(spec, latest_version=3, applied_version=1, target_symbols=["000660.KS"], today=date(2026, 10, 13))
    texts = " ".join(x["text"] for x in h)
    assert "buy_hold" in texts and "지났습니다" in texts and "v3" in texts and "교집합이 없습니다" in texts and "최대 2종목" in texts
    assert ss.spec_health(None)[0]["level"] == "warn"
    ok = ss.spec_health({"entry": {"indicator": "ma_cross"}, "universe": ["000660"], "backtest_result": {"period": {"end": "2026-10-10"}}}, 1, 1, ["000660.KS"], today=date(2026, 10, 13))
    assert all(x["level"] != "warn" for x in ok)


def test_get_settings_exposes_mode_hp_help_and_spec(client):
    c, db, _ = client
    db.row.quant_strategy_id, db.row.quant_strategy_version = "sample_ma_cross_kr", 1
    with patch.object(strategy_loader, "list_versions", AsyncMock(return_value={"latest_passed_version": 1, "versions": [{"version": 1, "passed": True}]})), \
         patch.object(strategy_loader, "public_url", lambda: "https://pr.test"):
        d = c.get("/api/quant/strategy/settings").json()
    assert d["signal_mode"] == "backtest" and d["signal_mode_explicit"] == ""
    assert [m["value"] for m in d["signal_modes"]] == ["aggressive", "backtest", "custom"]
    assert d["hp_help"]["rows"] and d["strategy_public_url"] == "https://pr.test"
    assert d["spec"]["strategy_id"] == "sample_ma_cross_kr" and d["spec_versions"]["versions"][0]["version"] == 1 and d["spec_latest_version"] == 1
    assert isinstance(d["spec_health"], list) and "005930" in d["stock_names"]


def test_save_backtest_mode_requires_strategy_and_custom_ignores_it(client):
    c, db, store = client
    r = c.post("/api/quant/strategy/settings", json={"values": {"QUANT_SIGNAL_MODE": "backtest", "strategy_id": ""}})
    assert r.status_code == 422 and "전략 스펙" in r.json()["detail"]
    r = c.post("/api/quant/strategy/settings", json={"values": {"QUANT_SIGNAL_MODE": "backtest", "strategy_id": "sample_ma_cross_kr"}})
    assert r.status_code == 200, r.text
    assert settings.QUANT_SIGNAL_MODE == "backtest" and settings.QUANT_AGGRESSIVE_MODE is False
    assert store[ss.CACHE_KEY]["values"]["QUANT_SIGNAL_MODE"] == "backtest" and db.row.quant_strategy_version == 1
    r = c.post("/api/quant/strategy/settings", json={"values": {"QUANT_SIGNAL_MODE": "custom", "strategy_id": "sample_ma_cross_kr"}})
    assert r.status_code == 200 and settings.QUANT_SIGNAL_MODE == "custom"
    assert c.get("/api/quant/strategy/settings").json()["signal_mode"] == "custom"


def test_spec_endpoint_and_revalidate_proxy(client):
    c, db, _ = client
    with patch.object(strategy_loader, "list_versions", AsyncMock(return_value={"latest_passed_version": 1, "versions": []})):
        r = c.get("/api/quant/strategy/spec", params={"strategy_id": "sample_ma_cross_kr"})
        assert r.status_code == 200 and r.json()["spec"]["version"] == 1
        assert c.get("/api/quant/strategy/spec", params={"strategy_id": "nope"}).status_code == 404
        assert c.get("/api/quant/strategy/spec").json()["spec"] is None
    with patch.object(strategy_loader, "revalidate", AsyncMock(return_value=(201, {"strategy_id": "sample_ma_cross_kr", "version": 2}))) as rv:
        r = c.post("/api/quant/strategy/revalidate", json={"strategy_id": "sample_ma_cross_kr", "end_date": "2026-10-13"})
        assert r.status_code == 200 and r.json()["spec"]["version"] == 2 and "v2" in r.json()["note"]
        assert rv.await_args.kwargs["end_date"] == "2026-10-13"
    with patch.object(strategy_loader, "revalidate", AsyncMock(return_value=(422, {"detail": {"message": "재검증 불합격.", "failed": ["max_drawdown_pct -33.9 > 20"]}}))):
        r = c.post("/api/quant/strategy/revalidate", json={"strategy_id": "sample_ma_cross_kr"})
        assert r.status_code == 422 and "max_drawdown_pct" in r.json()["detail"]
    with patch.object(strategy_loader, "revalidate", AsyncMock(return_value=(0, {"detail": "pr 연결 실패"}))):
        assert c.post("/api/quant/strategy/revalidate", json={"strategy_id": "sample_ma_cross_kr"}).status_code == 502
    with patch.object(strategy_loader, "is_configured", lambda: False):
        assert c.post("/api/quant/strategy/revalidate", json={"strategy_id": "x"}).status_code == 503
