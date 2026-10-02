"""통합 대시보드 /api/dashboard/accounts — 사이트별 투자액 탭. KIS 가 첫 탭이고, 한 탭의 실패가 다른 탭을 막지 않는다."""
import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.config import settings
from app.models import BrokerSettings, LiveOrder, Portfolio, QuantVirtualAccount
from app.routes import dashboard as d
from app.services import kis_credentials as kc
from app.services.brokers import stock_coin_trade_gateway as gw

UID = uuid.UUID("0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0")


class Result:
    def __init__(self, one=None, many=()): self._one, self._many = one, list(many)
    def scalar_one_or_none(self): return self._one
    def scalars(self): return self
    def all(self): return self._many


class FakeDb:
    def __init__(self, broker=None, live=(), acc=None, positions=()):
        self.broker, self.live, self.acc, self.positions = broker, list(live), acc, list(positions)
    async def execute(self, stmt, *_a, **_k):
        entity = stmt.column_descriptions[0]["entity"]
        if entity is BrokerSettings: return Result(one=self.broker)
        if entity is LiveOrder: return Result(many=self.live)
        if entity is QuantVirtualAccount: return Result(one=self.acc)
        if entity is Portfolio: return Result(many=self.positions)
        raise AssertionError(entity)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_BASE_URL", "https://sct.test")
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_API_KEY", "key")
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_KIS_ENVIRONMENT", "paper")
    monkeypatch.setattr(settings, "ALPACA_API_KEY", "")
    monkeypatch.setattr(settings, "ALPACA_SECRET_KEY", "")
    for k in ("KIS_SECRETS_NAME", "KIS_APP_KEY", "KIS_APP_SECRET"):
        monkeypatch.setattr(settings, k, "")
    kc.invalidate()
    yield
    gw.set_transport(None)
    kc.invalidate()


def _gateway(balance):
    def handler(req: httpx.Request):
        assert req.url.path.endswith("/openapi/v1/kis/balance")
        return httpx.Response(200, json={"ok": True, "balance": balance})
    gw.set_transport(httpx.MockTransport(handler))


SNAP = {"cash": 7_000_000.0, "stockEval": 2_000_000.0, "cryptoEval": 500_000.0, "alternativeEval": 500_000.0,
        "totalAsset": 10_000_000.0, "totalPnl": 0.0, "totalPnlRate": 0.0, "counts": {"stocks": 2, "crypto": 1, "alternatives": 1}}


def test_tabs_order_and_kis_first_via_gateway():
    _gateway({"environment": "paper", "cashBalance": 9_500_000, "totalEvalAmount": 10_120_000, "totalProfitLoss": 120_000,
              "holdings": [{"symbol": "005930", "quantity": 3, "evalAmount": 214_500}, {"symbol": "035720", "quantity": 10, "evalAmount": 405_500}]})
    broker = SimpleNamespace(quant_auto_enabled=True, quant_mode="live", broker="kis")
    live = [SimpleNamespace(status="ACCEPTED")]
    db = FakeDb(broker=broker, live=live, acc=SimpleNamespace(cash_balance=9_700_000.0, initial_capital=10_000_000.0),
                positions=[SimpleNamespace(symbol="005930.KS", quantity=1, avg_price=275_000.0)])
    with patch.object(d.paper_trading, "account_snapshot", AsyncMock(return_value=SNAP)), \
         patch.object(d, "get_quote", AsyncMock(return_value={"price": 280_000})):
        out = asyncio.run(d.accounts(user={"id": str(UID)}, db=db))
    keys = [t["key"] for t in out["tabs"]]
    assert keys == ["kis", "quant", "paper", "us"] and keys[0] == "kis"
    kis = out["tabs"][0]
    assert kis["connected"] and kis["route"] == "stock-coin-trade" and kis["environment"] == "paper"
    assert kis["cash"] == 9_500_000 and kis["invested"] == 620_000 and kis["total"] == 10_120_000 and kis["positions"] == 2
    assert kis["auto_trade_running"] is True and kis["open_live_orders"] == 1
    quant = out["tabs"][1]
    assert quant["connected"] and quant["invested"] == 280_000 and quant["total"] == 9_980_000 and quant["pnl_pct"] == -0.2
    paper = out["tabs"][2]
    assert paper["connected"] and paper["invested"] == 3_000_000 and paper["positions"] == 4 and paper["breakdown"]["crypto"] == 500_000
    us = out["tabs"][3]
    assert us["connected"] is False and "ALPACA" in us["note"] and us["currency"] == "USD"


def test_kis_not_connected_when_no_gateway_and_no_secret(monkeypatch):
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_BASE_URL", "")
    db = FakeDb(broker=None, acc=None)
    with patch.object(d.paper_trading, "account_snapshot", AsyncMock(return_value=SNAP)):
        out = asyncio.run(d.accounts(user={"id": str(UID)}, db=db))
    kis = out["tabs"][0]
    assert kis["connected"] is False and "연동 없음" in kis["note"] and kis["error"] == ""
    quant = out["tabs"][1]
    assert quant["connected"] is False and quant["cash"] == 0.0 and quant["pnl_pct"] is None


def test_kis_direct_uses_managed_credentials(monkeypatch):
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_BASE_URL", "")
    monkeypatch.setattr(settings, "KIS_SECRETS_NAME", "x")

    class Bal:
        total_eval, total_buy, total_gain = 1_000_000.0, 900_000.0, 100_000.0
        holdings = [SimpleNamespace(quantity=2, current_price=200_000.0, eval_amount=None)]

    class Client:
        async def get_balance(self, account_no):
            assert account_no == "5012345601"
            return Bal()

    with patch.object(kc, "_fetch_secret_json", return_value={"app_key": "a", "app_secret": "b", "account_no": "5012345601"}), \
         patch.object(d, "get_broker_client", return_value=Client()), \
         patch.object(d.paper_trading, "account_snapshot", AsyncMock(return_value=SNAP)):
        out = asyncio.run(d.accounts(user={"id": str(UID)}, db=FakeDb()))
    kis = out["tabs"][0]
    assert kis["connected"] and kis["route"] == "kis-direct" and kis["invested"] == 400_000 and kis["cash"] == 600_000 and kis["positions"] == 1


def test_one_tab_failure_does_not_break_others():
    def boom(req): return httpx.Response(500, json={"ok": False, "error": "KIS_DOWN", "message": "down"})
    gw.set_transport(httpx.MockTransport(boom))
    with patch.object(d.paper_trading, "account_snapshot", AsyncMock(return_value=SNAP)):
        out = asyncio.run(d.accounts(user={"id": str(UID)}, db=FakeDb()))
    kis, paper = out["tabs"][0], out["tabs"][2]
    assert kis["connected"] is False and kis["error"]
    assert paper["connected"] is True and paper["total"] == 10_000_000


def test_us_tab_with_alpaca_keys(monkeypatch):
    monkeypatch.setattr(settings, "ALPACA_API_KEY", "k")
    monkeypatch.setattr(settings, "ALPACA_SECRET_KEY", "s")
    acct = {"status": "ACTIVE", "cash": "40000", "long_market_value": "60000", "short_market_value": "0", "equity": "100000", "last_equity": "99000", "buying_power": "80000"}
    import app.routes.paper as paper_routes
    with patch.object(paper_routes, "_alpaca_get", AsyncMock(return_value=acct)), \
         patch.object(d.paper_trading, "account_snapshot", AsyncMock(return_value=SNAP)):
        _gateway({"cashBalance": 1, "totalEvalAmount": 1, "holdings": []})
        out = asyncio.run(d.accounts(user={"id": str(UID)}, db=FakeDb()))
    us = out["tabs"][3]
    assert us["connected"] and us["invested"] == 60_000 and us["cash"] == 40_000 and us["total"] == 100_000 and us["pnl"] == 1_000 and us["pnl_pct"] == 1.01
