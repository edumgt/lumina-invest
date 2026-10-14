"""수익율검증(app/services/return_review.py + /api/quant/return-review): 붙여넣기 파싱, 일별 변화·요약, 매매 귀속, 모드 귀속, 가이드, 라우트."""
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database.postgres import get_pg_session
from app.lib.session import get_current_user
from app.models import BrokerSettings, LiveOrder, Order
from app.models.base import SYSTEM_USER_ID
from app.models.review import KisEquitySnapshot
from app.routes import return_review as route
from app.services import return_review as rr, strategy_settings as ss

UID = uuid.UUID("0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0")
KST = timezone(timedelta(hours=9))


# ── 파싱 ─────────────────────────────────────────────────────────────────────

def test_parse_pasted_tabs_spaces_and_validation():
    rows = rr.parse_pasted("2026.10.12\t-6.66%\t-6,661,763\t100,000,000\t93,338,237\t4021\n# 주석\n2026-10-08 -2.04% -2,044,366 100,000,000 97,955,634\n")
    assert [r["snap_date"] for r in rows] == [date(2026, 10, 8), date(2026, 10, 12)]          # 날짜순 정렬
    assert rows[1]["raw_count"] == 4021 and rows[0]["raw_count"] is None and rows[1]["equity"] == 93_338_237
    with pytest.raises(ValueError, match="맞지 않습니다"):
        rr.parse_pasted("2026.10.12 -6.66% -6,661,763 100,000,000 90,000,000")
    with pytest.raises(ValueError, match="날짜 형식"):
        rr.parse_pasted("12/10 -6.66% -1 100 99")
    with pytest.raises(ValueError, match="5개 이상"):
        rr.parse_pasted("2026.10.12 -6.66%")
    with pytest.raises(ValueError, match="읽을 수 있는 행"):
        rr.parse_pasted("\n# only comment\n")


# ── 일별 변화·요약 ───────────────────────────────────────────────────────────

def test_enrich_and_summary_on_seed_rows():
    rows = rr.enrich(rr.SEED_ROWS)
    assert rows[0]["daily_change_pct"] is None and rows[-1]["snap_date"] == "2026-10-12"
    last = rows[-1]
    assert last["gap_days"] == 4 and round(last["daily_change_pct"], 2) == -4.71 and last["daily_pnl"] == -4_617_397
    s = rr.summary(rows)
    assert s["cum_return_pct"] == -6.66 and s["worst_day"]["date"] == "2026-10-12" and s["up_days"] == 2 and s["down_days"] == 6
    assert s["max_drawdown_pct"] < -6.8 and s["peak_equity"] == 100_187_460 and s["loss_share_recent_pct"] > 90
    assert rr.summary([]) == {"days": 0}


# ── 매매 귀속·통계 ───────────────────────────────────────────────────────────

def _t(day, hour, m=0):
    return datetime(2026, 10, day, hour, m, tzinfo=KST)


def test_trading_by_day_avg_cost_and_stats():
    live = [
        {"time": _t(7, 10), "symbol": "005930.KS", "side": "BUY", "qty": 10, "price": 100.0},
        {"time": _t(7, 11), "symbol": "005930", "side": "BUY", "qty": 10, "price": 120.0},    # 평균 110
        {"time": _t(8, 10), "symbol": "005930.KS", "side": "SELL", "qty": 10, "price": 121.0},  # +110
        {"time": _t(8, 14), "symbol": "005930.KS", "side": "SELL", "qty": 10, "price": 99.0},   # -110
        {"time": _t(8, 14, 30), "symbol": "000660.KS", "side": "SELL", "qty": 5, "price": 50.0},  # 보유 없음 → 왕복 아님
    ]
    all_live = [{"time": _t(7, 10), "status": "FILLED"}, {"time": _t(7, 11), "status": "FILLED"}, {"time": _t(8, 10), "status": "FILLED"},
                {"time": _t(8, 14), "status": "FILLED"}, {"time": _t(8, 14, 30), "status": "FILLED"}, {"time": _t(8, 15), "status": "CANCELLED"}]
    virtual = [{"time": _t(7, 10), "side": "buy"}, {"time": _t(8, 10), "side": "sell"}]
    by = rr.trading_by_day(live, all_live, virtual)
    assert by["2026-10-07"]["buys"] == 2 and by["2026-10-07"]["buy_amount"] == 2200 and by["2026-10-07"]["virtual_buys"] == 1
    d8 = by["2026-10-08"]
    assert d8["sells"] == 3 and d8["realized"] == 0 and d8["wins"] == 1 and d8["losses"] == 1 and d8["orders"] == 4 and d8["open_or_cancelled"] == 1
    assert [t["pnl"] for t in d8["round_trips"]] == [110, -110]
    st = rr.trade_stats(by)
    assert st["round_trips"] == 2 and st["win_rate_pct"] == 50.0 and st["payoff_ratio"] == 1.0 and st["expectancy"] == 0 and st["fill_rate_pct"] == 83.3
    assert rr.trade_stats({})["round_trips"] == 0 and rr.trade_stats({})["fill_rate_pct"] is None


def test_mode_by_day_uses_profile_active_at_close():
    profiles = [
        {"id": "p1", "seq": 1, "applied_at": "2026-10-06T00:30:00+00:00", "values": {"QUANT_AGGRESSIVE_MODE": True}, "row": {"strategy_id": ""}},
        {"id": "p2", "seq": 2, "applied_at": "2026-10-08T07:00:00+00:00", "values": {"QUANT_SIGNAL_MODE": "backtest"}, "row": {"strategy_id": "ma_cross_kr"}},
    ]
    m = rr.mode_by_day(profiles, ["2026-10-02", "2026-10-06", "2026-10-08", "2026-10-12"], now=datetime(2026, 10, 13, tzinfo=timezone.utc))
    assert m["2026-10-02"]["mode"] == "" and "이전" in m["2026-10-02"]["mode_label"]
    assert m["2026-10-06"]["mode"] == "aggressive" and m["2026-10-06"]["profile"] == "p1"          # 레거시 플래그 → 공격
    assert m["2026-10-08"]["mode"] == "aggressive"                                                  # 15:30 KST(06:30Z) 는 p2(07:00Z) 이전
    assert m["2026-10-12"]["mode"] == "backtest" and m["2026-10-12"]["strategy_id"] == "ma_cross_kr"


def test_pearson():
    assert rr.pearson([1, 2, 3, 4], [2, 4, 6, 8]) == 1.0 and rr.pearson([1, 2, 3, 4], [8, 6, 4, 2]) == -1.0
    assert rr.pearson([1, 2, 3], [1, 2, 3]) is None and rr.pearson([1, 1, 1, 1], [1, 2, 3, 4]) is None


# ── 가이드 ───────────────────────────────────────────────────────────────────

def test_guide_on_seed_detects_acceleration_limit_breach_and_overtrading():
    rows = rr.enrich(rr.SEED_ROWS)
    eff = {"QUANT_AGGRESSIVE_MODE": True, "QUANT_AGGRESSIVE_TAKE_PROFIT_PCT": 1.5, "QUANT_AGGRESSIVE_STOP_LOSS_PCT": 1.0}
    g = rr.build_guide(rows, {}, rr.trade_stats({}), {}, {"daily_loss_limit_pct": 3.0}, eff)
    ids = {f["id"]: f for f in g}
    assert ids["overview"]["level"] == "bad" and "-6.66%" in ids["overview"]["title"]
    assert ids["acceleration"]["level"] == "bad"
    assert ids["mode"]["level"] == "info" and "프로필" in ids["mode"]["title"]                      # 프로필 없음 → 비교 불가 안내
    assert ids["overtrading"]["level"] == "warn" and any("건수 열" in e for e in ids["overtrading"]["evidence"])   # raw_count 상관
    assert ids["daily-limit"]["level"] == "warn" and "2026-10-12" in ids["daily-limit"]["evidence"][0]
    assert ids["expectancy"]["level"] == "info"
    assert ids["coverage"]["level"] == "info"
    assert ids["roadmap"]["evidence"][0].startswith("1단계") and any(a["view"] == "external:pr" for a in ids["roadmap"]["actions"])
    assert all(a.get("field") is None or isinstance(a["field"], str) for f in g for a in f["actions"])


def test_guide_mode_comparison_and_expectancy():
    rows = rr.enrich(rr.SEED_ROWS)
    modes = {r["snap_date"]: {"mode": "aggressive", "mode_label": "공격 모드"} for r in rows}
    for d in ("2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"):
        modes[d] = {"mode": "custom", "mode_label": "사용자 설정"}
    by = {"2026-10-12": {"fills": 40, "orders": 50, "round_trips": [{"pnl": -300.0, "pnl_pct": -1.0}] * 6 + [{"pnl": 150.0, "pnl_pct": 1.5}] * 4}}
    for d in ("2026-10-07", "2026-10-08"):
        by[d] = {"fills": 20, "orders": 25, "round_trips": []}
    for d in ("2026-10-01", "2026-10-02", "2026-10-06"):
        by[d] = {"fills": 5, "orders": 5, "round_trips": []}
    for v in by.values():
        v.update({"buys": 0, "sells": 0, "realized": 0, "wins": 0, "losses": 0, "open_or_cancelled": 0, "virtual_buys": 0, "virtual_sells": 0})
    st = rr.trade_stats(by)
    assert st["win_rate_pct"] == 40.0 and st["expectancy"] < 0
    g = {f["id"]: f for f in rr.build_guide(rows, by, st, modes, {"daily_loss_limit_pct": 3.0}, {"QUANT_AGGRESSIVE_MODE": True, "QUANT_AGGRESSIVE_TAKE_PROFIT_PCT": 1.5, "QUANT_AGGRESSIVE_STOP_LOSS_PCT": 1.0})}
    assert g["mode"]["level"] == "bad" and "공격 모드" in g["mode"]["title"] and any(a["field"] == "QUANT_SIGNAL_MODE" for a in g["mode"]["actions"])
    assert g["overtrading"]["level"] == "warn" and "r = -" in g["overtrading"]["evidence"][0]
    assert g["expectancy"]["level"] == "bad" and any("손익분기 승률 = 40%" in e for e in g["expectancy"]["evidence"])
    assert rr.build_guide([], {}, rr.trade_stats({}), {})[0]["id"] == "no-data"


# ── 라우트 ───────────────────────────────────────────────────────────────────

class _Result:
    def __init__(self, many=(), one=None): self._many, self._one = list(many), one
    def scalars(self): return self
    def all(self): return self._many
    def first(self): return self._many[0] if self._many else None
    def scalar_one_or_none(self): return self._one


class FakeDb:
    def __init__(self, snaps=(), live=(), orders=(), broker=None):
        self.snaps, self.live, self.orders, self.broker = list(snaps), list(live), list(orders), broker
        self.added, self.deleted, self.committed = [], [], 0
    async def execute(self, stmt, *_a, **_k):
        entity = stmt.column_descriptions[0]["entity"]
        if entity is KisEquitySnapshot:
            crit = str(stmt.whereclause) if stmt.whereclause is not None else ""
            if "snap_date =" in crit:
                params = stmt.whereclause.compile(compile_kwargs={"literal_binds": True}).string
                hit = next((s for s in self.snaps if s.snap_date.isoformat() in params), None)
                return _Result(one=hit)
            return _Result(sorted(self.snaps, key=lambda s: s.snap_date, reverse="DESC" in str(stmt).upper()))
        if entity is LiveOrder: return _Result(self.live)
        if entity is Order: return _Result(self.orders)
        if entity is BrokerSettings: return _Result(one=self.broker)
        raise AssertionError(entity)
    def add(self, obj): self.added.append(obj); self.snaps.append(obj)
    async def delete(self, obj): self.deleted.append(obj); self.snaps.remove(obj)
    async def commit(self): self.committed += 1


def _snap(d, equity, principal=100_000_000.0, cnt=None):
    return KisEquitySnapshot(environment="paper", snap_date=d, principal=principal, equity=equity, cum_pnl=equity - principal,
                             cum_return_pct=round((equity / principal - 1) * 100, 4), raw_count=cnt, source="paste", note="")


@pytest.fixture
def client():
    snaps = [_snap(date(2026, 10, 6), 100_074_154.0, cnt=1902), _snap(date(2026, 10, 7), 99_499_854.0, cnt=2927), _snap(date(2026, 10, 8), 97_955_634.0, cnt=3443)]
    live = [SimpleNamespace(created_at=_t(7, 10), symbol="005930", side="BUY", status="FILLED", filled_quantity=10, avg_filled_price=100.0),
            SimpleNamespace(created_at=_t(8, 10), symbol="005930", side="SELL", status="FILLED", filled_quantity=10, avg_filled_price=90.0),
            SimpleNamespace(created_at=_t(8, 11), symbol="000660", side="BUY", status="CANCELLED", filled_quantity=0, avg_filled_price=0.0)]
    orders = [SimpleNamespace(created_at=_t(7, 10), order_type="buy", status="filled")]
    broker = SimpleNamespace(risk_daily_loss_limit_pct=3.0, risk_max_position_pct=20.0, risk_cooldown_min=30, risk_max_orders_per_day=10,
                             quant_strategy_id="", quant_strategy_version=0, quant_buy_ratio=1.0, quant_sell_ratio=0.5)
    db = FakeDb(snaps, live, orders, broker)
    store: dict = {}
    async def cget(key, max_age_hours=24):
        if key.startswith("quant:cycle_log:"):
            return {"cycles": [{"time": "2026-10-08 10:00", "settings": {"profile_id": "p9"}, "trades": [{"status": "filled"}], "risk": {"skipped": [{}]}}]}
        return store.get(key)
    async def cset(key, data): store[key] = data
    app = FastAPI(); app.include_router(route.router)
    app.dependency_overrides[get_current_user] = lambda: {"id": str(UID), "email": "t@t"}
    app.dependency_overrides[get_pg_session] = lambda: db
    async def load_and_apply(): return {"values": {}, "profiles": [{"id": "p1", "seq": 1, "applied_at": "2026-10-06T00:00:00+00:00", "values": {"QUANT_AGGRESSIVE_MODE": True}, "row": {}}]}
    with patch.object(ss, "load_and_apply", load_and_apply), patch.object(route, "audit", AsyncMock()), \
         patch.object(route, "cache_get", cget), patch.object(route, "cache_set", cset), \
         patch.object(route.strategy_loader, "list_strategies", AsyncMock(return_value=[])), \
         patch.object(route.signal_mode, "resolve", lambda sid="": "aggressive"):
        yield TestClient(app), db, store


def test_get_review_joins_rows_trading_and_modes(client):
    c, db, _ = client
    r = c.get("/api/quant/return-review"); assert r.status_code == 200, r.text
    d = r.json()
    assert [x["snap_date"] for x in d["rows"]] == ["2026-10-06", "2026-10-07", "2026-10-08"]
    r8 = d["rows"][2]
    assert r8["trading"]["sells"] == 1 and r8["trading"]["realized"] == -100 and r8["trading"]["orders"] == 2 and r8["trading"]["open_or_cancelled"] == 1
    assert d["rows"][1]["trading"]["virtual_buys"] == 1 and r8["mode"]["mode"] == "aggressive" and r8["mode"]["profile"] == "p1"
    assert d["summary"]["cum_return_pct"] == pytest.approx(-2.0444, abs=1e-3) and d["trade_stats"]["round_trips"] == 1
    assert {f["id"] for f in d["guide"]} >= {"overview", "mode", "expectancy", "roadmap"} and d["limits"]["daily_loss_limit_pct"] == 3.0
    assert d["plans"]["recommended"] in ("A", "B") and [p["id"] for p in d["plans"]["plans"]] == ["A", "B", "C"]
    assert d["plans"]["plans"][2]["applicable"] is False and "합격" in d["plans"]["plans"][2]["blocker"]
    assert d["applied_plans"] == [] and d["progress_log"] == []


def test_post_rows_upserts_and_delete(client):
    c, db, _ = client
    r = c.post("/api/quant/return-review/rows", json={"text": "2026.10.08 -2.04% -2,044,366 100,000,000 97,955,634 3443\n2026.10.12 -6.66% -6,661,763 100,000,000 93,338,237 4021", "note": "KIS 화면"})
    assert r.status_code == 200, r.text
    assert r.json()["inserted"] == 1 and r.json()["updated"] == 1 and db.committed == 1
    assert any(s.snap_date == date(2026, 10, 12) and s.raw_count == 4021 and s.note == "KIS 화면" for s in db.snaps)
    assert c.post("/api/quant/return-review/rows", json={"text": "bad line"}).status_code == 422
    assert c.post("/api/quant/return-review/rows", json={"rows": [{"snap_date": "2026-10-13", "principal": 100_000_000, "equity": 95_000_000}]}).json()["inserted"] == 1
    assert c.delete("/api/quant/return-review/rows/2026-10-13").status_code == 200 and len(db.deleted) == 1
    assert c.delete("/api/quant/return-review/rows/2026-01-01").status_code == 404
    assert c.delete("/api/quant/return-review/rows/notadate").status_code == 422


def test_snapshot_uses_gateway_balance_and_last_principal(client):
    c, db, _ = client
    from app.services.brokers import stock_coin_trade_gateway as gw
    with patch.object(gw, "is_configured", lambda: True), patch.object(gw, "environment", lambda: "paper"), \
         patch.object(gw, "get_balance", AsyncMock(return_value={"totalEvalAmount": 93_000_000})):
        r = c.post("/api/quant/return-review/snapshot", json={})
    assert r.status_code == 200, r.text
    row = r.json()["row"]
    assert row["principal"] == 100_000_000 and row["equity"] == 93_000_000 and row["cum_return_pct"] == -7.0 and r.json()["inserted"] == 1
    with patch.object(gw, "is_configured", lambda: False):
        assert c.post("/api/quant/return-review/snapshot", json={}).status_code == 502


# ── 방안·승인·진행 로그 ───────────────────────────────────────────────────────

def test_build_plans_recommendation_and_changes():
    rows = rr.enrich(rr.SEED_ROWS); s = rr.summary(rows); t = rr.trade_stats({})
    eff = {"QUANT_AGGRESSIVE_TAKE_PROFIT_PCT": 1.5, "QUANT_AGGRESSIVE_STOP_LOSS_PCT": 1.0, "QUANT_AGGRESSIVE_MAX_BUYS_PER_CYCLE": 2, "QUANT_AGGRESSIVE_FORCE_BUY": True,
           "QUANT_AGGRESSIVE_CANDLE_INTERVAL": "5m", "QUANT_AGGRESSIVE_WEAK_SELL_SCORE": -1, "QUANT_AGGRESSIVE_COOLDOWN_MIN": 3, "QUANT_AGGRESSIVE_MAX_ORDERS_PER_DAY": 300,
           "QUANT_CUSTOM_BUY_SCORE": 1, "QUANT_CUSTOM_SELL_SCORE": -1, "QUANT_CUSTOM_TREND_FILTER": False, "QUANT_BASE_TAKE_PROFIT_PCT": 0, "QUANT_BASE_STOP_LOSS_PCT": 0,
           "STOCK_COIN_TRADE_ORDER_TYPE": "LIMIT", "KIS_PAPER_BATCH_AI_TOP_N": 3, "QUANT_SPEC_UNIVERSE_STRICT": False, "KIS_PAPER_BATCH_SYMBOLS": ""}
    row = {"risk_daily_loss_limit_pct": 3.0, "risk_cooldown_min": 30, "risk_max_orders_per_day": 10, "strategy_id": ""}
    p = rr.build_plans(s, t, {"acceleration", "overtrading"}, eff, row, [], ["005930.KS", "069500.KS"], "aggressive")
    assert p["recommended"] == "B" and [x["recommended"] for x in p["plans"]] == [False, True, False]
    a = {c["key"]: c["to"] for c in p["plans"][0]["changes"]}
    assert a["QUANT_AGGRESSIVE_MAX_BUYS_PER_CYCLE"] == 0 and a["QUANT_AGGRESSIVE_TAKE_PROFIT_PCT"] == 2.0 and a["risk_daily_loss_limit_pct"] == 2.0 and "QUANT_SIGNAL_MODE" not in a
    b = {c["key"]: c["to"] for c in p["plans"][1]["changes"]}
    assert b["QUANT_SIGNAL_MODE"] == "custom" and b["QUANT_CUSTOM_BUY_SCORE"] == 3 and b["QUANT_CUSTOM_TREND_FILTER"] is True and b["risk_cooldown_min"] == 60
    assert p["plans"][2]["applicable"] is False and p["plans"][2]["changes"] == []
    # 합격 전략이 있으면 C 권장 + 스펙 universe ∩ 거래 유니버스를 수동 종목으로
    strat = [{"strategy_id": "sample_x", "version": 1, "universe": ["005930"], "backtest_result": {"engine": "sample", "sharpe_ratio": 9}},
             {"strategy_id": "ma_cross_kospi200", "version": 2, "name": "MA 20/60", "universe": ["069500", "000660"],
              "backtest_result": {"annualized_return_pct": 9, "max_drawdown_pct": -12, "sharpe_ratio": 0.8, "trade_count": 40, "engine": "lean"}}]
    p = rr.build_plans(s, t, set(), eff, row, strat, ["005930.KS", "069500.KS"], "aggressive")
    assert p["recommended"] == "C"
    c = {x["key"]: x["to"] for x in p["plans"][2]["changes"]}
    assert c["QUANT_SIGNAL_MODE"] == "backtest" and c["strategy_id"] == "ma_cross_kospi200" and c["QUANT_SPEC_UNIVERSE_STRICT"] is True and c["KIS_PAPER_BATCH_SYMBOLS"] == "069500.KS"
    assert rr.pick_strategy(strat)["strategy_id"] == "ma_cross_kospi200"          # 샘플 제외
    # 변화 없는 값은 변경 항목에서 빠진다
    p = rr.build_plans(s, t, set(), {**eff, "QUANT_AGGRESSIVE_FORCE_BUY": False, "QUANT_AGGRESSIVE_CANDLE_INTERVAL": "15m"}, row, [], [], "aggressive")
    assert not any(ch["key"] in ("QUANT_AGGRESSIVE_FORCE_BUY", "QUANT_AGGRESSIVE_CANDLE_INTERVAL") for ch in p["plans"][0]["changes"])


def test_evaluate_applied_verdicts_and_escalation():
    rows = rr.enrich(rr.SEED_ROWS)
    base = {"equity": 100_074_154, "avg_daily_change_pct": 0.05}
    ev = rr.evaluate_applied([{"plan_id": "A", "applied_at": "2026-10-06T07:00:00+00:00", "baseline": base}], rows, {"2026-10-07": {"fills": 20}})
    assert ev[0]["verdict"] == "not_improved" and ev[0]["next_plan"] == "B" and ev[0]["observed_days"] == 3 and ev[0]["fills_per_day_after"] == 6.7
    ev = rr.evaluate_applied([{"plan_id": "B", "applied_at": "2026-10-08T07:00:00+00:00", "baseline": base}], rows, {})
    assert ev[0]["verdict"] == "observing" and ev[0]["observed_days"] == 1 and ev[0]["next_plan"] is None
    up = rr.enrich([{"snap_date": "2026-10-01", "principal": 100, "equity": 100, "cum_return_pct": 0, "cum_pnl": 0},
                    {"snap_date": "2026-10-02", "principal": 100, "equity": 101, "cum_return_pct": 1, "cum_pnl": 1},
                    {"snap_date": "2026-10-03", "principal": 100, "equity": 102, "cum_return_pct": 2, "cum_pnl": 2},
                    {"snap_date": "2026-10-04", "principal": 100, "equity": 102.5, "cum_return_pct": 2.5, "cum_pnl": 2.5}])
    ev = rr.evaluate_applied([{"plan_id": "C", "applied_at": "2026-10-01T07:00:00+00:00", "baseline": {"equity": 100, "avg_daily_change_pct": -1.0}}], up, {})
    assert ev[0]["verdict"] == "improved" and ev[0]["cum_change_after_pct"] == 2.5


def test_apply_plan_saves_settings_and_logs_progress(client):
    c, db, store = client
    fake_result = {"ok": True, "effective": {"QUANT_SIGNAL_MODE": "custom"}, "row": {"strategy_id": ""}, "profile": {"id": "p9"}, "note": "다음 사이클부터 적용됩니다."}
    with patch.object(route.ss_route, "save_settings", AsyncMock(return_value=fake_result)) as save:
        r = c.post("/api/quant/return-review/plans/b/apply", json={"note": "손실 차단"})
        assert r.status_code == 200, r.text
        body = save.await_args.args[0]
        assert body.values["QUANT_SIGNAL_MODE"] == "custom" and body.values["QUANT_CUSTOM_BUY_SCORE"] == 3 and body.values["risk_daily_loss_limit_pct"] == 2.0
        assert body.label.startswith("수익율검증 방안 B 승인") and "손실 차단" in body.label
    d = r.json()
    assert d["plan_id"] == "B" and d["applied"]["profile_id"] == "p9" and d["applied"]["baseline"]["cum_return_pct"] == pytest.approx(-2.0444, abs=1e-3)
    assert [x["event"] for x in store[route.PROGRESS_LOG_KEY]["items"]] == ["approve", "apply"]
    assert store[rr.APPLIED_PLANS_KEY]["items"][0]["plan_id"] == "B"
    # GET: 적용 이력 + 프로필별 진행(사이클 캐시) + 로그(최신순)
    g = c.get("/api/quant/return-review").json()
    ap = g["applied_plans"][0]
    assert ap["verdict"] == "observing" and ap["progress"] == {"cycles": 1, "virtual_fills": 1, "risk_skipped": 1, "halted_cycles": 0, "first_cycle": "2026-10-08 10:00", "last_cycle": "2026-10-08 10:00", "last_note": ""}
    assert g["progress_log"][0]["event"] == "apply" and g["progress_log"][1]["event"] == "approve"
    # 적용 불가 방안(C: 합격 전략 없음) → 422 + error 로그
    r = c.post("/api/quant/return-review/plans/C/apply", json={})
    assert r.status_code == 422 and "합격" in r.json()["detail"]
    assert store[route.PROGRESS_LOG_KEY]["items"][-1]["event"] == "error"
    assert c.post("/api/quant/return-review/plans/Z/apply", json={}).status_code == 404
    # 평가 판정이 바뀌면 evaluate 로그가 1회 남는다
    store[rr.APPLIED_PLANS_KEY]["items"][0]["applied_at"] = "2026-10-05T00:00:00+00:00"   # 이후 관측일 2일(10/07·10/08, 둘 다 하락)
    with patch.object(rr, "OBSERVE_DAYS", 2):
        g = c.get("/api/quant/return-review").json()
        assert g["applied_plans"][0]["verdict"] == "not_improved" and g["applied_plans"][0]["next_plan"] == "C" and g["applied_plans"][0]["observed_days"] == 2
        assert g["progress_log"][0]["event"] == "evaluate" and g["progress_log"][0]["next_plan"] == "C"
        n = len(store[route.PROGRESS_LOG_KEY]["items"])
        c.get("/api/quant/return-review")
        assert len(store[route.PROGRESS_LOG_KEY]["items"]) == n        # 같은 판정은 다시 기록하지 않음


# ── 장중 실시간 평가액 (6-58) ─────────────────────────────────────────────────
def test_market_phase_kst():
    from datetime import datetime, timedelta, timezone
    kst = timezone(timedelta(hours=9))
    assert rr.market_phase(datetime(2026, 10, 14, 12, 0, tzinfo=kst)) == "open"        # 수요일 장중
    assert rr.market_phase(datetime(2026, 10, 14, 15, 31, tzinfo=kst)) == "closed"
    assert rr.market_phase(datetime(2026, 10, 14, 8, 59, tzinfo=kst)) == "closed"
    assert rr.market_phase(datetime(2026, 10, 17, 12, 0, tzinfo=kst)) == "closed"      # 토요일
    assert rr.market_phase(datetime(2026, 10, 14, 3, 0, tzinfo=timezone.utc)) == "open"  # UTC 03:00 = KST 12:00


def test_live_equity_compares_with_last_snapshot_and_never_writes():
    import asyncio
    from datetime import datetime, timedelta, timezone
    from app.services.brokers import stock_coin_trade_gateway as gw
    kst = timezone(timedelta(hours=9))
    now = datetime(2026, 10, 14, 12, 0, tzinfo=kst)
    rows = [{"snap_date": "2026-10-08", "principal": 100_000_000, "equity": 97_955_634}, {"snap_date": "2026-10-12", "principal": 100_000_000, "equity": 93_338_237}]
    balance = {"totalEvalAmount": 89_412_545, "cashBalance": -31_826_005, "totalProfitLoss": -7_342_552, "holdings": [{}] * 19}
    with patch.object(gw, "is_configured", lambda: True), patch.object(gw, "environment", lambda: "paper"), \
         patch.object(gw, "get_balance", AsyncMock(return_value=balance)) as gb:
        live = asyncio.run(rr.live_equity(rows, now=now))
    gb.assert_awaited_once_with("paper")
    assert live["ok"] and live["market"] == "open" and live["equity"] == 89_412_545 and live["holdings"] == 19
    assert live["last_snap_date"] == "2026-10-12" and live["change_pnl"] == 89_412_545 - 93_338_237
    assert live["change_pct"] == pytest.approx((89_412_545 / 93_338_237 - 1) * 100, abs=1e-3)
    assert live["cum_return_pct"] == pytest.approx(-10.5875, abs=1e-3) and live["today_recorded"] is False
    with patch.object(gw, "is_configured", lambda: True), patch.object(gw, "environment", lambda: "paper"), \
         patch.object(gw, "get_balance", AsyncMock(return_value=balance)):
        today = asyncio.run(rr.live_equity(rows + [{"snap_date": "2026-10-14", "principal": 100_000_000, "equity": 90_000_000}], now=now))
    assert today["today_recorded"] is True and today["last_snap_date"] == "2026-10-14"
    with patch.object(gw, "is_configured", lambda: False), patch.object(gw, "environment", lambda: "paper"):
        off = asyncio.run(rr.live_equity(rows, now=now))
    assert off["ok"] is False and "게이트웨이" in off["error"] and off["market"] == "open"
    with patch.object(gw, "is_configured", lambda: True), patch.object(gw, "environment", lambda: "paper"), \
         patch.object(gw, "get_balance", AsyncMock(side_effect=gw.GatewayError("timeout", code="E_TIMEOUT"))):
        err = asyncio.run(rr.live_equity(rows, now=now))
    assert err["ok"] is False and err["error"].startswith("[E_TIMEOUT]")
    with patch.object(gw, "is_configured", lambda: True), patch.object(gw, "environment", lambda: "paper"), \
         patch.object(gw, "get_balance", AsyncMock(return_value=balance)):
        none = asyncio.run(rr.live_equity([], now=now))
    assert none["ok"] and none["principal"] == rr.DEFAULT_PRINCIPAL and none["last_snap_date"] is None and none["change_pnl"] is None


def test_get_review_includes_live_block(client):
    c, db, _ = client
    from app.services.brokers import stock_coin_trade_gateway as gw
    with patch.object(gw, "is_configured", lambda: True), patch.object(gw, "environment", lambda: "paper"), \
         patch.object(gw, "get_balance", AsyncMock(return_value={"totalEvalAmount": 95_000_000, "holdings": []})):
        d = c.get("/api/quant/return-review").json()
    assert d["live"]["ok"] and d["live"]["equity"] == 95_000_000 and d["live"]["last_snap_date"] == "2026-10-08"
    assert d["live"]["change_pnl"] == 95_000_000 - 97_955_634 and len(d["rows"]) == 3            # DB 행은 늘지 않는다
    with patch.object(gw, "is_configured", lambda: False), patch.object(gw, "environment", lambda: "paper"):
        d2 = c.get("/api/quant/return-review").json()
    assert d2["live"]["ok"] is False and d2["rows"] and d2["summary"]["days"] == 3                 # 실패해도 화면 데이터는 그대로
