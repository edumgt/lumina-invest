"""시스템 OHLCV 저장소 우선 → 없는 구간 적재 → Yahoo 폴백 (app/services/ohlcv_store)."""
import asyncio
import json
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from app.config import settings
from app.services import ohlcv_store as st


def _ts(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


class FakeWorld:
    """st 저장소(pg-stock)와 Yahoo 를 흉내 낸다. store: code → {date: close}, yahoo: symbol → {date: close}."""

    def __init__(self, store=None, yahoo=None, api_key_ok=True):
        self.store = store or {}
        self.yahoo = yahoo or {}
        self.ingests: list[dict] = []
        self.reads = 0
        self.api_key_ok = api_key_ok

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        if url.host == "query2.finance.yahoo.com":
            sym = url.path.rsplit("/", 1)[-1]
            p1, p2 = int(url.params["period1"]), int(url.params["period2"])
            days = sorted(d for d in self.yahoo.get(sym, {}) if p1 <= _ts(d) < p2)
            if not days:
                return httpx.Response(200, json={"chart": {"result": None}})
            closes = [self.yahoo[sym][d] for d in days]
            return httpx.Response(200, json={"chart": {"result": [{
                "timestamp": [_ts(d) + 3600 for d in days],
                "indicators": {"quote": [{"open": closes, "high": closes, "low": closes, "close": closes, "volume": [100] * len(days)}]}}]}})
        if url.path == "/openapi/v1/ohlcv/ingest":
            body = json.loads(request.content)
            self.ingests.append(body)
            sym = body["symbol"]; code = sym.split(".")[0]
            a, b = date.fromisoformat(body["start"]), date.fromisoformat(body["end"])
            rows = {d: c for d, c in self.yahoo.get(sym, {}).items() if a <= d < b}
            if not rows:
                return httpx.Response(200, json={"status": "empty", "rows": 0})
            self.store.setdefault(code, {}).update(rows)
            return httpx.Response(200, json={"status": "ingested", "rows": len(rows)})
        if url.path.startswith("/openapi/v1/ohlcv/"):
            self.reads += 1
            code = url.path.rsplit("/", 1)[-1]
            if code not in self.store:
                return httpx.Response(404, json={"error": "NOT_FOUND"})
            a, b = date.fromisoformat(url.params["date_from"]), date.fromisoformat(url.params["date_to"])
            limit, offset = int(url.params["limit"]), int(url.params["offset"])
            days = sorted(d for d in self.store[code] if a <= d <= b)
            page = days[offset:offset + limit]
            nxt = offset + len(page) if offset + len(page) < len(days) else None
            return httpx.Response(200, json={"rows": [{"trade_date": d.isoformat(), "open": self.store[code][d], "high": self.store[code][d],
                                                       "low": self.store[code][d], "close": self.store[code][d], "volume": 10} for d in page],
                                             "total": len(days), "nextOffset": nxt})
        return httpx.Response(500, text=f"unexpected {url}")


def _days(a: date, n: int) -> dict:
    return {a + timedelta(days=i): 100.0 + i for i in range(n)}


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_BASE_URL", "https://st.test")
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_API_KEY", "key")
    st.reset_known_empty()
    w = FakeWorld()
    st.set_transport(httpx.MockTransport(w.handler))
    yield w
    st.set_transport(None)
    st.reset_known_empty()


START, END = date(2026, 1, 1), date(2026, 3, 1)


def test_store_covers_range_no_ingest(world):
    world.store["005930"] = _days(START, 60)
    candles, meta = asyncio.run(st.daily_candles("005930.KS", START, END))
    assert len(candles) == 60 and meta["source"] == "db" and meta["db_rows_before"] == 60
    assert world.ingests == [] and meta["fetched"] == []
    assert candles[0]["time"] == _ts(START) and candles[0]["close"] == 100.0


def test_missing_tail_is_ingested_then_reread(world):
    world.store["005930"] = _days(START, 30)                       # 1/30 까지만
    world.yahoo["005930.KS"] = _days(START, 60)
    candles, meta = asyncio.run(st.daily_candles("005930.KS", START, END))
    assert len(world.ingests) == 1
    assert world.ingests[0]["start"] == (START + timedelta(days=30)).isoformat()   # 마지막 봉 다음날부터
    assert len(candles) == 60 and meta["source"] == "db+yahoo→store" and meta["stored_rows"] == 30


def test_unknown_ticker_ingests_everything(world):
    world.yahoo["035720.KQ"] = _days(START, 40)
    candles, meta = asyncio.run(st.daily_candles("035720.KQ", START, END))
    assert len(world.ingests) == 1 and world.ingests[0]["symbol"] == "035720.KQ"
    assert len(candles) == 40 and meta["source"] == "yahoo→store" and meta["db_rows_before"] == 0


def test_no_api_key_falls_back_to_direct_yahoo(world, monkeypatch):
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_API_KEY", "")
    world.store["005930"] = _days(START, 30)
    world.yahoo["005930.KS"] = _days(START, 60)
    candles, meta = asyncio.run(st.daily_candles("005930.KS", START, END))
    assert world.ingests == [] and len(candles) == 60 and meta["source"] == "db+yahoo"


def test_foreign_symbol_uses_yahoo_only(world):
    world.yahoo["AAPL"] = _days(START, 20)
    candles, meta = asyncio.run(st.daily_candles("AAPL", START, END))
    assert world.reads == 0 and world.ingests == []
    assert len(candles) == 20 and meta["source"] == "yahoo" and "국내 전용" in meta["note"]


def test_store_not_configured_uses_yahoo(world, monkeypatch):
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_BASE_URL", "")
    world.yahoo["005930.KS"] = _days(START, 20)
    candles, meta = asyncio.run(st.daily_candles("005930.KS", START, END))
    assert world.reads == 0 and len(candles) == 20 and meta["source"] == "yahoo" and meta["store"] is None


def test_store_error_falls_back_to_yahoo(world, monkeypatch):
    def boom(_r): raise httpx.ConnectError("down")
    st.set_transport(httpx.MockTransport(lambda r: boom(r) if r.url.host == "st.test" else world.handler(r)))
    world.yahoo["005930.KS"] = _days(START, 20)
    candles, meta = asyncio.run(st.daily_candles("005930.KS", START, END))
    assert len(candles) == 20 and meta["source"] == "yahoo" and "store_error" in meta


def test_head_gap_without_data_is_remembered(world):
    world.store["005930"] = _days(START + timedelta(days=30), 30)   # 상장일이 1/31 인 것처럼
    asyncio.run(st.daily_candles("005930.KS", START, END))
    assert len(world.ingests) == 1 and world.ingests[0]["start"] == START.isoformat()
    asyncio.run(st.daily_candles("005930.KS", START, END))
    assert len(world.ingests) == 1          # 두 번째는 앞 구간을 다시 묻지 않는다


def test_pagination_merges_pages(world, monkeypatch):
    monkeypatch.setattr(st, "PAGE_LIMIT", 25)
    world.store["005930"] = _days(START, 60)
    candles, _ = asyncio.run(st.daily_candles("005930.KS", START, END))
    assert len(candles) == 60 and world.reads == 3


def test_period_start_and_krx_code():
    today = date(2026, 10, 12)
    assert st.period_start("10y", today) == today - timedelta(days=3653)
    assert st.period_start("max", today) == date(1990, 1, 1)
    assert st.krx_code("005930.KS") == "005930" and st.krx_code("035720.KQ") == "035720" and st.krx_code("005930") == "005930"
    assert st.krx_code("AAPL") is None and st.krx_code("^KS11") is None and st.krx_code("BTC-USD") is None


def test_get_candles_daily_uses_store_first(monkeypatch):
    """get_candles 일봉 Yahoo 경로가 저장소 우선 함수를 거치고 출처 메타를 붙인다."""
    from unittest.mock import AsyncMock, patch
    from app.services import stock
    monkeypatch.setattr(settings, "MARKET_DATA_SOURCE", "yahoo")
    monkeypatch.setattr(settings, "OHLCV_STORE_FIRST", True)
    fake = [{"time": _ts(START + timedelta(days=i)), "open": 1, "high": 1, "low": 1, "close": 1.0 + i, "volume": 1} for i in range(5)]
    meta = {"source": "db", "db_rows_before": 5, "fetched": [], "stored_rows": 0, "rows": 5}
    with patch.object(stock, "cache_get", AsyncMock(return_value=None)), patch.object(stock, "cache_set", AsyncMock()) as cset, \
         patch.object(st, "daily_candles", AsyncMock(return_value=(fake, meta))) as dc, \
         patch.object(stock, "_yahoo_chart", AsyncMock(side_effect=AssertionError("저장소 우선이면 Yahoo range 조회를 하면 안 된다"))):
        out = asyncio.run(stock.get_candles("005930.KS", period="10y", interval="1d"))
    assert out["source"] == "db" and out["price_source"] == meta and len(out["candles"]) == 5
    assert dc.await_args.args[0] == "005930.KS" and cset.await_count == 1
