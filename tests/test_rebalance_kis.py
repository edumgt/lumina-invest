"""리밸런싱 엔진 계좌 소스 kis: KIS Testbed 잔고 스냅샷, KIS 시세 제안, 게이트웨이 실주문 실행."""
import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from app.models import RebalanceRun
from app.services import rebalance as rb
from app.services.brokers import stock_coin_trade_gateway as gw

UID = uuid.UUID("0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0")


class FakeDb:
    def __init__(self): self.added = []
    def add(self, o): self.added.append(o)
    async def flush(self): pass
    async def commit(self): pass


def plan(targets, **over):
    base = dict(id=uuid.uuid4(), targets=targets, drift_enabled=True, drift_threshold_pct=5.0, min_order_amount=10_000.0,
                time_period="none", next_run_at=None, last_run_at=None, auto_execute=False, is_active=True)
    base.update(over)
    return SimpleNamespace(**base)


BAL = {"cashBalance": 1_000_000, "totalEvalAmount": 1_600_000,
       "holdings": [{"symbol": "240810", "name": "원익IPS", "quantity": 2, "avgPrice": 100_000, "currentPrice": 150_000, "evalAmount": 300_000},
                    {"symbol": "999999", "name": "미등록종목", "quantity": 3, "avgPrice": 90_000, "currentPrice": 100_000, "evalAmount": 300_000}]}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_BASE_URL", "https://sct.test")
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_API_KEY", "key")
    store = {}
    async def cget(key, max_age_hours=24): return store.get(key)
    async def cset(key, data): store[key] = data
    with patch.object(rb, "cache_get", cget), patch.object(rb, "cache_set", cset), \
         patch.object(gw, "get_balance", AsyncMock(return_value=BAL)), \
         patch.object(rb.pt, "get_account", AsyncMock(side_effect=AssertionError("paper 계좌를 읽으면 안 된다"))), \
         patch.object(rb.pt, "stock_positions", AsyncMock(side_effect=AssertionError("paper 포지션을 읽으면 안 된다"))):
        yield store


def test_account_source_roundtrip_and_validation(env, monkeypatch):
    assert asyncio.run(rb.get_account_source(UID)) == "kis"          # 미설정 + 게이트웨이 있음 → kis 기본
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_BASE_URL", "")
    assert asyncio.run(rb.get_account_source(UID)) == "paper"        # 게이트웨이 없으면 paper
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_BASE_URL", "https://sct.test")
    assert asyncio.run(rb.set_account_source(UID, "kis")) == "kis"
    assert asyncio.run(rb.get_account_source(UID)) == "kis"
    with pytest.raises(rb.RebalanceError):
        asyncio.run(rb.set_account_source(UID, "robinhood"))
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_BASE_URL", "")
    with pytest.raises(rb.RebalanceError, match="게이트웨이"):
        asyncio.run(rb.set_account_source(UID, "kis"))


def test_snapshot_from_kis_balance_maps_symbols_and_weights(env):
    p = plan([{"symbol": "240810.KQ", "name": "원익IPS", "weight_pct": 50.0}])
    snap = asyncio.run(rb.snapshot(FakeDb(), UID, p, "kis"))
    assert snap["account_source"] == "kis" and snap["total_asset"] == 1_600_000 and snap["cash"] == 1_000_000
    rows = {r["symbol"]: r for r in snap["rows"]}
    assert rows["240810.KQ"]["quantity"] == 2 and rows["240810.KQ"]["price"] == 150_000 and rows["240810.KQ"]["current_weight_pct"] == 18.75
    assert rows["240810.KQ"]["target_weight_pct"] == 50.0 and rows["240810.KQ"]["in_plan"] is True
    assert "999999.KS" in rows and rows["999999.KS"]["in_plan"] is False      # 유니버스 밖 코드는 .KS 로
    assert snap["cash_target_pct"] == 50.0 and snap["max_drift_pct"] == 31.25 and snap["drift_exceeded"] is True


def test_propose_uses_kis_price_for_unheld_target(env):
    p = plan([{"symbol": "240810.KQ", "name": "원익IPS", "weight_pct": 50.0}, {"symbol": "005930.KS", "name": "삼성전자", "weight_pct": 25.0}])
    with patch.object(rb, "_kis_price", AsyncMock(return_value=200_000.0)) as kp:
        prop = asyncio.run(rb.propose(FakeDb(), UID, p, source="kis"))
    kp.assert_awaited_once_with("005930.KS")
    by = {o["symbol"]: o for o in prop["orders"]}
    assert by["999999.KS"]["side"] == "SELL" and by["999999.KS"]["quantity"] == 3          # 플랜 밖 → 전량 매도
    assert by["240810.KQ"]["side"] == "BUY" and by["240810.KQ"]["quantity"] == 3            # 800k 목표 − 300k = 500k / 150k
    assert by["005930.KS"]["side"] == "BUY" and by["005930.KS"]["quantity"] == 2            # 400k / 200k
    assert prop["account_source"] == "kis" and prop["orders"][0]["side"] == "SELL"           # 매도 먼저


def test_execute_kis_routes_orders_through_gateway(env):
    p = plan([{"symbol": "240810.KQ", "name": "원익IPS", "weight_pct": 50.0}])
    results = iter([{"status": "submitted", "order_no": "0000040001", "client_order_id": "c1", "environment": "paper"},
                    {"status": "skipped", "reason": "market_closed", "environment": "paper"}])
    calls = []
    async def place(db, broker_row, symbol, name, side, qty, price, user_id):
        calls.append((broker_row, symbol, side, qty, price, user_id)); return next(results)
    from app.services import auto_trade
    db = FakeDb()
    asyncio.run(rb.set_account_source(UID, "kis"))
    with patch.object(auto_trade, "_place_live_order_via_gateway", place):
        run = asyncio.run(rb.execute(db, UID, p, "MANUAL", None, "화면 실행"))
    assert isinstance(run, RebalanceRun) and run.status == "executed" and run.note.startswith("[kis] ")
    assert [c[1:4] for c in calls] == [("999999.KS", "sell", 3), ("240810.KQ", "buy", 3)]
    assert calls[0][0] is None and calls[0][5] == str(UID)
    st = {o["symbol"]: o["status"] for o in run.orders}
    assert st == {"999999.KS": "submitted", "240810.KQ": "skipped"}
    assert run.orders[0]["live_order"]["order_no"] == "0000040001" and run.orders[1]["error"] == "market_closed"
    assert db.added and db.added[0] is run


def test_execute_kis_all_skipped_marks_run_skipped(env):
    p = plan([{"symbol": "240810.KQ", "name": "원익IPS", "weight_pct": 50.0}])
    from app.services import auto_trade
    asyncio.run(rb.set_account_source(UID, "kis"))
    with patch.object(auto_trade, "_place_live_order_via_gateway", AsyncMock(return_value={"status": "error", "error": "GATEWAY_UNREACHABLE"})):
        run = asyncio.run(rb.execute(FakeDb(), UID, p, "MANUAL", None, ""))
    assert run.status == "skipped" and all(o["status"] == "failed" for o in run.orders)


def test_snapshot_kis_without_gateway_raises(env, monkeypatch):
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_BASE_URL", "")
    with pytest.raises(rb.RebalanceError, match="게이트웨이"):
        asyncio.run(rb.snapshot(FakeDb(), UID, plan([]), "kis"))



# ── 섹터 목표 ─────────────────────────────────────────────────────────────
def test_normalize_targets_accepts_sector_items_and_validates():
    items = rb.normalize_targets([{"symbol": "SECTOR:반도체", "weight_pct": 40}, {"symbol": "SECTOR:IT", "weight_pct": 35},
                                  {"symbol": "005930.KS", "name": "삼성전자", "weight_pct": 10}])
    sectors, symbols = rb.split_targets(items)
    assert sectors == {"반도체": 40.0, "IT": 35.0} and symbols["005930.KS"]["weight_pct"] == 10.0
    with pytest.raises(rb.RebalanceError, match="지원하지 않는 섹터"):
        rb.normalize_targets([{"symbol": "SECTOR:자동차", "weight_pct": 10}])
    with pytest.raises(rb.RebalanceError, match="섹터 목표.*초과"):
        rb.normalize_targets([{"symbol": "SECTOR:반도체", "weight_pct": 10}, {"symbol": "005930.KS", "weight_pct": 20}])
    with pytest.raises(rb.RebalanceError, match="100%"):
        rb.normalize_targets([{"symbol": "SECTOR:반도체", "weight_pct": 60}, {"symbol": "SECTOR:IT", "weight_pct": 50}])


def test_effective_targets_split_sector_among_held_and_fill_empty_sector():
    targets = [{"symbol": "SECTOR:반도체", "weight_pct": 40}, {"symbol": "SECTOR:K뷰티", "weight_pct": 20}, {"symbol": "005930.KS", "name": "삼성전자", "weight_pct": 10}]
    eff = rb.effective_symbol_targets(targets, held_symbols=["005930.KS", "000660.KS", "240810.KQ", "999999.KS"])
    assert eff["005930.KS"]["weight_pct"] == 10.0 and eff["005930.KS"]["derived"] is False
    assert eff["000660.KS"]["weight_pct"] == 15.0 and eff["240810.KQ"]["weight_pct"] == 15.0      # (40-10)/2
    kb = {s: v for s, v in eff.items() if v["sector"] == "K뷰티"}
    assert len(kb) == rb.SECTOR_FILL_CANDIDATES and all(v["weight_pct"] == 10.0 and v["derived"] for v in kb.values())
    assert "999999.KS" not in eff                                                                 # 기타 섹터 보유는 목표 0


def test_snapshot_kis_sector_aggregation(env):
    p = plan([{"symbol": "SECTOR:반도체", "weight_pct": 50}, {"symbol": "SECTOR:IT", "weight_pct": 25}])
    snap = asyncio.run(rb.snapshot(FakeDb(), UID, p, "kis"))
    sec = {x["sector"]: x for x in snap["sectors"]}
    assert sec["반도체"]["current_weight_pct"] == 18.75 and sec["반도체"]["target_weight_pct"] == 50.0 and sec["반도체"]["symbols"] == ["240810.KQ"]
    assert sec["IT"]["current_weight_pct"] == 0 and sec["IT"]["target_weight_pct"] == 25.0 and len(sec["IT"]["symbols"]) == rb.SECTOR_FILL_CANDIDATES
    assert sec["기타"]["current_weight_pct"] == 18.75 and sec["기타"]["target_weight_pct"] == 0.0 and sec["기타"]["explicit"] is False
    rows = {r["symbol"]: r for r in snap["rows"]}
    assert rows["240810.KQ"]["target_weight_pct"] == 50.0 and rows["240810.KQ"]["target_derived"] is True
    assert snap["cash_target_pct"] == 25.0 and snap["max_drift_pct"] == 37.5   # 현금 62.5% vs 목표 25%


def test_execute_records_sector_weights(env):
    p = plan([{"symbol": "SECTOR:반도체", "weight_pct": 50}])
    from app.services import auto_trade
    asyncio.run(rb.set_account_source(UID, "kis"))
    with patch.object(auto_trade, "_place_live_order_via_gateway", AsyncMock(return_value={"status": "submitted", "order_no": "1"})):
        run = asyncio.run(rb.execute(FakeDb(), UID, p, "MANUAL", None, ""))
    assert run.target_weights["SECTOR:반도체"] == 50.0 and run.before_weights["SECTOR:반도체"] == 18.75
