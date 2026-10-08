"""공격 모드(QUANT_AGGRESSIVE_MODE): 5분봉 시그널, 매수·매도 계획, 한도 덮어쓰기, 사이클 통합."""
import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.config import settings
from app.models import BrokerSettings, LiveOrder, Portfolio, QuantVirtualAccount
from app.services import aggressive_mode as ag
from app.services import auto_trade
from app.services.brokers import stock_coin_trade_gateway as gw
from app.services.risk_guard import RiskLimits

UID = uuid.UUID("0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0")


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setattr(settings, "QUANT_AGGRESSIVE_MODE", True)
    monkeypatch.setattr(settings, "QUANT_AGGRESSIVE_COOLDOWN_MIN", 5)
    monkeypatch.setattr(settings, "QUANT_AGGRESSIVE_MAX_ORDERS_PER_DAY", 200)
    monkeypatch.setattr(settings, "QUANT_AGGRESSIVE_MAX_BUYS_PER_CYCLE", 2)
    monkeypatch.setattr(settings, "QUANT_AGGRESSIVE_MAX_SELLS_PER_CYCLE", 3)
    monkeypatch.setattr(settings, "QUANT_AGGRESSIVE_FORCE_BUY", True)
    monkeypatch.setattr(settings, "QUANT_AGGRESSIVE_TAKE_PROFIT_PCT", 1.5)
    monkeypatch.setattr(settings, "QUANT_AGGRESSIVE_STOP_LOSS_PCT", 1.0)
    monkeypatch.setattr(settings, "QUANT_AGGRESSIVE_ORDER_TYPE", "MARKET")
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_BASE_URL", "https://sct.test")
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_API_KEY", "key")
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_KIS_ENVIRONMENT", "paper")
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_ORDER_TYPE", "LIMIT")
    monkeypatch.setattr(settings, "MARKET_DATA_SOURCE", "yahoo")   # 이 파일은 Yahoo 경로(get_candles 모킹)를 검증
    yield
    gw.set_transport(None)


# ── 시그널 ────────────────────────────────────────────────────────────────
def test_score_uptrend_is_buy_with_positive_momentum():
    closes = [100 + i * 0.3 for i in range(40)]            # 완만한 상승 → MA5>MA20, 30분 모멘텀 +
    sig = ag.score_intraday(closes, [1000] * 40)
    assert sig["action"] in ("매수", "강력 매수") and sig["score"] >= 1 and sig["momentum_pct"] > 0.3


def test_score_downtrend_is_sell():
    closes = [120 - i * 0.3 for i in range(40)]
    sig = ag.score_intraday(closes, [1000] * 40)
    assert sig["action"] in ("매도", "강력 매도") and sig["score"] <= -1 and sig["momentum_pct"] < 0


def test_score_flat_short_series_is_hold():
    sig = ag.score_intraday([100.0, 100.0, 100.0], [None, None, None])
    assert sig["action"] == "관망" and sig["score"] == 0


def test_volume_surge_adds_point_only_with_positive_momentum():
    closes = [100 + i * 0.2 for i in range(30)]
    base = ag.score_intraday(closes, [1000] * 30)
    surge = ag.score_intraday(closes, [1000] * 29 + [5000])
    assert surge["score"] == base["score"] + 1 and "거래량 급증(상승)" in surge["reasons"]


def test_get_intraday_indicators_uses_minute_candles_with_short_cache(monkeypatch):
    seen = {}
    async def candles(symbol, period="1y", interval="1d", max_age_hours=6):
        seen.update(symbol=symbol, period=period, interval=interval, max_age_hours=max_age_hours)
        return {"candles": [{"close": 100 + i, "volume": 10} for i in range(30)]}
    monkeypatch.setattr(ag, "get_candles", candles)
    out = asyncio.run(ag.get_intraday_indicators("005930.KS"))
    assert seen["interval"] == "5m" and seen["period"] == "5d" and seen["max_age_hours"] < 0.1
    assert out["current_price"] == 129 and out["intraday"] is True and out["signal"]["action"] in ("매수", "강력 매수")


def test_get_intraday_indicators_without_candles_is_not_a_judgement(monkeypatch):
    """데이터가 없으면 '관망' 으로 판단한 척하지 않고 '판단 불가' + 사유를 남긴다 (근거 없는 판단 금지)."""
    monkeypatch.setattr(ag, "get_candles", AsyncMock(return_value={"candles": []}))
    out = asyncio.run(ag.get_intraday_indicators("X"))
    assert out["current_price"] is None
    assert out["signal"]["action"] == "판단 불가" and out["signal"]["error"] == "분봉 없음"
    assert out["signal"]["action"] not in ("매수", "강력 매수", "매도")   # 거래로 이어지지 않는다
    assert out["as_of"] is None


def test_get_intraday_indicators_reports_data_basis(monkeypatch):
    """판단 근거가 어느 시장 데이터에서 나왔는지(출처·봉 종류·봉 수·마지막 봉 시각)를 함께 낸다."""
    candles = [{"close": 100 + i, "volume": 10, "time": 1700000000 + i * 300} for i in range(30)]
    monkeypatch.setattr(ag, "get_candles", AsyncMock(return_value={"candles": candles}))
    out = asyncio.run(ag.get_intraday_indicators("X"))
    assert out["source"] == "yahoo" and out["bars"] == 30
    assert out["as_of"] == candles[-1]["time"] and out["price_source"] == "last_close"
    assert out["signal"]["reasons"], "근거 문구가 실제 지표에서 생성돼야 한다"


# ── 한도 ──────────────────────────────────────────────────────────────────
def test_apply_limits_overrides_only_cooldown_and_daily_orders():
    base = RiskLimits(daily_loss_limit_pct=3.0, max_position_pct=20.0, max_orders_per_day=10, cooldown_min=30, kill_switch=False)
    out = ag.apply_limits(base)
    assert out.cooldown_min == 5 and out.max_orders_per_day == 200
    assert out.max_position_pct == 20.0 and out.daily_loss_limit_pct == 3.0 and out.kill_switch is False


def test_order_type_market_by_default_and_fallback(monkeypatch):
    assert ag.order_type() == "MARKET" and auto_trade._live_order_type() == "MARKET"
    monkeypatch.setattr(settings, "QUANT_AGGRESSIVE_ORDER_TYPE", "")
    assert ag.order_type() is None and auto_trade._live_order_type() == "LIMIT"
    monkeypatch.setattr(settings, "QUANT_AGGRESSIVE_MODE", False)
    monkeypatch.setattr(settings, "QUANT_AGGRESSIVE_ORDER_TYPE", "MARKET")
    assert auto_trade._live_order_type() == "LIMIT"


# ── 계획 ──────────────────────────────────────────────────────────────────
def ind(score, mom=0.0):
    return {"signal": {"action": "매수" if score >= 1 else ("매도" if score <= -1 else "관망"), "score": score,
                       "momentum_pct": mom, "reasons": ["r"]}}


def test_plan_buys_top_scored_up_to_max_and_sells_take_profit_full():
    im = {"A": ind(3, 1.0), "B": ind(2, 0.5), "C": ind(1, 0.1), "D": ind(0), "H": ind(0)}
    prices = {"A": 100.0, "B": 100.0, "C": 100.0, "D": 100.0, "H": 102.0}
    out = ag.plan(im, ["A", "B", "C", "D"], {"H": (10, 100.0)}, prices)
    assert out["buy"] == ["A", "B"]                                  # max_buys=2
    assert out["sell"]["H"]["ratio"] == 1.0 and "익절" in out["sell"]["H"]["reason"]


def test_plan_stop_loss_full_and_weak_signal_partial():
    im = {"H1": ind(0), "H2": ind(-2)}
    out = ag.plan(im, [], {"H1": (5, 100.0), "H2": (5, 100.0)}, {"H1": 98.9, "H2": 100.2})
    assert out["sell"]["H1"]["ratio"] == 1.0 and "손절" in out["sell"]["H1"]["reason"]
    assert out["sell"]["H2"]["ratio"] is None and "약세" in out["sell"]["H2"]["reason"]
    assert out["buy"] == []                                            # 대상 없음 → 강제 매수도 없음


def test_plan_force_buys_top_momentum_when_no_signal(monkeypatch):
    im = {"A": ind(0, 0.1), "B": ind(0, 0.25), "C": ind(-1, 0.9)}
    out = ag.plan(im, ["A", "B", "C"], {}, {"A": 1.0, "B": 1.0, "C": 1.0})
    assert out["buy"] == ["B"] and any("로테이션" in n for n in out["notes"])  # score 0 중 모멘텀 최고
    monkeypatch.setattr(settings, "QUANT_AGGRESSIVE_FORCE_BUY", False)
    assert ag.plan(im, ["A", "B", "C"], {}, {"A": 1.0, "B": 1.0, "C": 1.0})["buy"] == []


def test_plan_never_buys_what_it_sells_and_caps_sells(monkeypatch):
    monkeypatch.setattr(settings, "QUANT_AGGRESSIVE_MAX_SELLS_PER_CYCLE", 1)
    im = {"A": ind(2), "B": ind(2)}
    out = ag.plan(im, ["A", "B"], {"A": (1, 100.0), "B": (1, 100.0)}, {"A": 110.0, "B": 105.0})
    assert list(out["sell"]) == ["A"] and out["buy"] == ["B"] and out["notes"]


def test_plan_skips_holdings_without_price():
    out = ag.plan({}, [], {"H": (1, 100.0)}, {})
    assert out["sell"] == {} and out["buy"] == []


# ── 사이클 통합 ────────────────────────────────────────────────────────────
STOCKS = [{"symbol": "005930.KS", "name": "삼성전자"}, {"symbol": "000660.KS", "name": "SK하이닉스"}, {"symbol": "035420.KS", "name": "NAVER"}]


def broker(**over):
    base = dict(quant_mode="live", paper=False, broker="kis", app_key="", app_secret="", account_no="",
                quant_symbol_source="ai", quant_selected_symbols=[], quant_ai_top_n=1,
                quant_per_trade_budget=300_000.0, quant_buy_ratio=1.0, quant_sell_ratio=0.5,
                risk_daily_loss_limit_pct=3.0, risk_max_position_pct=30.0, risk_max_orders_per_day=10, risk_cooldown_min=30,
                risk_kill_switch=False, risk_halt_reason="", quant_auto_enabled=True, quant_strategy_id="", quant_strategy_version=0)
    base.update(over)
    return SimpleNamespace(**base)


class Result:
    def __init__(self, one=None, many=()): self._one, self._many = one, list(many)
    def scalar_one_or_none(self): return self._one
    def scalars(self): return self
    def all(self): return self._many


class FakeDb:
    def __init__(self, broker_row, portfolio=()):
        self.broker_row, self.portfolio, self.added, self.commits = broker_row, list(portfolio), [], 0
    def add(self, obj): self.added.append(obj)
    async def commit(self): self.commits += 1
    async def execute(self, stmt, *_a, **_k):
        entity = stmt.column_descriptions[0]["entity"]
        if entity is QuantVirtualAccount:
            return Result(one=SimpleNamespace(cash_balance=9_700_000.0, initial_capital=10_000_000.0))
        if entity is BrokerSettings:
            return Result(one=self.broker_row)
        if entity is Portfolio:
            # 종목 조건이 있는 매도 조회는 첫 보유 행, 장부 전체 조회는 전부
            crit = str(stmt.whereclause) if stmt.whereclause is not None else ""
            if "portfolio.symbol" in crit:
                return Result(one=self.portfolio[0] if self.portfolio else None)
            return Result(many=self.portfolio)
        if entity is LiveOrder:
            return Result(many=[])
        raise AssertionError(f"unexpected select: {entity}")


class Factory:
    def __init__(self, db): self.db = db
    def __call__(self): return self
    async def __aenter__(self): return self.db
    async def __aexit__(self, *a): return False


def fake_gateway():
    calls = []
    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        calls.append((request.method, request.url.path, body))
        if request.url.path.endswith("/balance"):
            return httpx.Response(200, json={"ok": True, "balance": {"totalEvalAmount": 10_000_000, "cashBalance": 1, "holdings": []}})
        if request.url.path.endswith("/order-approval"):
            return httpx.Response(200, json={"ok": True, "approvalToken": "t", "expiresIn": 60})
        if request.url.path.endswith("/kis/orders") and request.method == "POST":
            return httpx.Response(200, json={"ok": True, "duplicate": False, "order": {"orderNo": "0000032000", "status": "ACCEPTED", "message": "ok"}})
        return httpx.Response(404, json={"ok": False, "error": "NOT_FOUND", "message": request.url.path})
    gw.set_transport(httpx.MockTransport(handler))
    return calls


def run_cycle(broker_row, intraday, portfolio=()):
    db = FakeDb(broker_row, portfolio)
    persisted, slots = {}, []
    async def persist(uid, log): persisted.update(log)
    async def virtual_trade(db_, uid, symbol, name, action, price, qty, reason):
        return {"status": "filled", "symbol": symbol, "action": action, "quantity": qty, "price": price, "reason": reason}
    async def acquire(user_id, symbol, side, cooldown):
        slots.append((symbol, side, cooldown)); return True
    with patch.object(auto_trade, "get_session_factory", return_value=Factory(db)), \
         patch.object(auto_trade, "QUANT_STOCKS", STOCKS), \
         patch.object(ag, "get_intraday_indicators", intraday), \
         patch.object(auto_trade, "get_quant_indicators", AsyncMock(side_effect=AssertionError("일봉 지표를 쓰면 안 된다"))), \
         patch.object(auto_trade, "_execute_virtual_trade", virtual_trade), \
         patch.object(auto_trade, "_equity_snapshot", AsyncMock(return_value=(9_700_000.0, 10_000_000.0, {}))), \
         patch.object(auto_trade, "_persist_cycle", persist), \
         patch.object(auto_trade.risk_guard, "day_start_equity", AsyncMock(return_value=10_000_000.0)), \
         patch.object(auto_trade.risk_guard, "orders_today", AsyncMock(return_value=0)), \
         patch.object(auto_trade.risk_guard, "acquire_order_slot", acquire), \
         patch.object(auto_trade.risk_guard, "increment_orders_today", AsyncMock(return_value=1)), \
         patch.object(auto_trade.risk_guard, "release_order_slot", AsyncMock()), \
         patch.object(auto_trade.risk_guard, "_redis", return_value=object()), \
         patch.object(auto_trade.notification, "notify_auto_trade_executed", AsyncMock()), \
         patch.object(auto_trade.notification, "notify_order_placed", AsyncMock()), \
         patch.object(auto_trade.notification, "notify_order_error", AsyncMock()), \
         patch.object(auto_trade.notification, "notify_risk_skip", AsyncMock()), \
         patch.object(gw, "is_krx_market_open", return_value=True):
        asyncio.run(auto_trade._run_quant_cycle(str(UID)))
    return db, persisted, slots


async def all_hold(symbol):
    mom = {"005930.KS": 0.1, "000660.KS": 0.4, "035420.KS": -0.2}[symbol]
    return {"signal": {"action": "관망", "score": 0, "reasons": ["중립"], "momentum_pct": mom}, "current_price": 100_000.0, "intraday": True}


def test_cycle_force_buys_top_momentum_with_market_order_when_all_hold():
    calls = fake_gateway()
    db, log, slots = run_cycle(broker(), all_hold)
    trades = [t for t in log["trades"] if t.get("type") == "auto"]
    assert [t["symbol"] for t in trades] == ["000660.KS"] and trades[0]["action"] == "buy" and trades[0]["quantity"] == 3
    assert "공격 모드 매수" in trades[0]["reason"]
    assert log["aggressive"]["buy"] == ["000660.KS"] and any("로테이션" in n for n in log["aggressive"]["notes"])
    assert log["settings"]["aggressive"] is True and log["risk"]["limits"]["cooldown_min"] == 5 and log["risk"]["limits"]["max_orders_per_day"] == 200
    assert slots == [("000660.KS", "buy", 5)]
    rows = [a for a in db.added if isinstance(a, LiveOrder)]
    assert rows and rows[0].order_type == "MARKET" and rows[0].status == "ACCEPTED"
    order_body = next(b for m, p, b in calls if p.endswith("/kis/orders"))
    assert b'"orderType": "MARKET"' in order_body or b'"orderType":"MARKET"' in order_body


def test_cycle_sells_take_profit_holding_in_full_even_outside_targets():
    fake_gateway()
    held = SimpleNamespace(symbol="035420.KS", name="NAVER", quantity=7, avg_price=98_000.0, book="QUANT", user_id=UID)
    db, log, _ = run_cycle(broker(), all_hold, portfolio=[held])       # 현재가 100,000 → +2.04% ≥ 1.5% 익절
    sells = [t for t in log["trades"] if t.get("type") == "auto" and t["action"] == "sell"]
    assert len(sells) == 1 and sells[0]["symbol"] == "035420.KS" and sells[0]["quantity"] == 7   # sell_ratio 0.5 무시, 전량
    assert "익절" in log["aggressive"]["sell"]["035420.KS"]
    buys = [t for t in log["trades"] if t.get("type") == "auto" and t["action"] == "buy"]
    assert [t["symbol"] for t in buys] == ["000660.KS"]


def test_cycle_respects_max_buys_and_ignores_base_signals_outside_plan(monkeypatch):
    fake_gateway()
    monkeypatch.setattr(settings, "QUANT_AGGRESSIVE_MAX_BUYS_PER_CYCLE", 1)
    async def all_buy(symbol):
        score = {"005930.KS": 2, "000660.KS": 3, "035420.KS": 1}[symbol]
        return {"signal": {"action": "매수", "score": score, "reasons": ["x"], "momentum_pct": 0.5}, "current_price": 50_000.0, "intraday": True}
    db, log, _ = run_cycle(broker(), all_buy)
    buys = [t for t in log["trades"] if t.get("type") == "auto" and t["action"] == "buy"]
    assert [t["symbol"] for t in buys] == ["000660.KS"]
    holds = [s for s in log["signals"] if s["action"] == "관망"]
    assert {s["symbol"] for s in holds} == {"005930.KS", "035420.KS"}


def test_cycle_disabled_mode_untouched(monkeypatch):
    monkeypatch.setattr(settings, "QUANT_AGGRESSIVE_MODE", False)
    assert ag.is_enabled() is False
    base = RiskLimits(cooldown_min=30, max_orders_per_day=10)
    assert ag.apply_limits(base).cooldown_min == 5      # apply_limits 자체는 조건 없음; 호출 여부는 사이클이 결정
