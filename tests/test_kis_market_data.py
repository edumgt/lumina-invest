"""KIS 연계 시세(kis_market_data): st /api/kis-chart 응답 파싱, 5분 집계, time 클램프, 폴백."""
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.config import settings
from app.services import kis_market_data as kmd
from app.services import aggressive_mode as ag
from app.services import stock

KST = timezone(timedelta(hours=9))


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setattr(settings, "MARKET_DATA_SOURCE", "kis")
    monkeypatch.setattr(settings, "MARKET_DATA_FALLBACK_YAHOO", True)
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_BASE_URL", "https://st.test")
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_API_KEY", "key")
    monkeypatch.setattr(settings, "KIS_CHART_MINUTES", 240)
    yield
    kmd.set_transport(None)


def _minutes_body(n=12, start="2026-10-06 11:00", base=100.0):
    t0 = datetime.strptime(start, "%Y-%m-%d %H:%M").replace(tzinfo=KST)
    rows = []
    for i in range(n):
        t = t0 + timedelta(minutes=i)
        rows.append({"time": t.strftime("%Y-%m-%d %H:%M"), "timestamp": int(t.timestamp()), "open": base + i, "high": base + i + 0.5,
                     "low": base + i - 0.5, "close": base + i, "volume": 10})
    return {"ok": True, "symbol": "005930", "summary": {"name": "삼성전자", "price": 999.0, "prevClose": 990.0, "change": 9.0, "changeRate": 0.91},
            "candles": list(reversed(rows))}   # 최신→과거 순으로 와도 정렬되어야 함


def _server(seen):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, dict(request.url.params), request.headers.get("authorization")))
        if request.url.path.endswith("/minutes"):
            return httpx.Response(200, json=_minutes_body())
        if request.url.path.endswith("/candles"):
            return httpx.Response(200, json={"ok": True, "summary": {"name": "삼성전자", "price": 271000.0, "prevClose": 276000.0, "change": -5000.0, "changeRate": -1.81,
                                                                       "open": 278500.0, "high": 279000.0, "low": 271000.0, "volume": 6434148},
                                             "candles": [{"time": "2026-10-02", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 3},
                                                         {"time": "2026-10-06", "open": 1, "high": 2, "low": 0.5, "close": 1.7, "volume": 4}]})
        return httpx.Response(404, json={"ok": False, "error": "NOT_FOUND"})
    kmd.set_transport(httpx.MockTransport(handler))


def test_clamp_end_time():
    assert kmd.clamp_end_time(datetime(2026, 10, 6, 12, 12, 0, tzinfo=KST)) == "121200"
    assert kmd.clamp_end_time(datetime(2026, 10, 6, 8, 30, 0, tzinfo=KST)) == "153000"
    assert kmd.clamp_end_time(datetime(2026, 10, 6, 16, 0, 0, tzinfo=KST)) == "153000"
    assert kmd.clamp_end_time(datetime(2026, 10, 6, 3, 12, 0, tzinfo=timezone.utc)) == "121200"   # UTC → KST 변환


def test_is_krx_and_code():
    assert kmd.is_krx("005930.KS") and kmd.is_krx("058470.KQ") and kmd.is_krx("005930") and not kmd.is_krx("AAPL")
    assert kmd.code_of("058470.KQ") == "058470" and kmd.code_of("005930.KS") == "005930"


def test_aggregate_5m_buckets():
    one = _minutes_body(n=12)["candles"]
    one = sorted((kmd._norm(c) for c in one), key=lambda c: c["time"])
    five = kmd.aggregate(one, 5)
    assert len(five) == 3 and five[0]["open"] == 100.0 and five[0]["close"] == 104.0 and five[0]["high"] == 104.5 and five[0]["low"] == 99.5
    assert five[0]["volume"] == 50 and five[-1]["close"] == 111.0 and five[0]["time"] % 300 == 0


def test_fetch_minutes_sends_code_count_time_and_auth():
    seen = []; _server(seen)
    with patch.object(kmd, "clamp_end_time", return_value="121200"):
        out = asyncio.run(kmd.get_intraday_candles("005930.KS", 5))
    path, params, auth = seen[0]
    assert path == "/api/kis-chart/minutes" and params == {"symbol": "005930", "count": "240", "time": "121200"} and auth == "Bearer key"
    assert out["source"] == "kis" and out["price"] == 999.0 and out["name"] == "삼성전자" and len(out["candles"]) == 3
    assert out["candles"][0]["time"] < out["candles"][-1]["time"]


def test_daily_candles_and_quote_mapping():
    seen = []; _server(seen)
    d = asyncio.run(kmd.get_daily_candles("005930.KS", "2y"))
    assert seen[0][1] == {"symbol": "005930", "period": "D", "count": "300"} and len(d["candles"]) == 2 and d["source"] == "kis"
    q = asyncio.run(kmd.get_quote("005930.KS"))
    assert q["price"] == 271000.0 and q["prev_close"] == 276000.0 and q["change"] == -5000.0 and q["change_pct"] == -1.81
    assert q["currency"] == "KRW" and q["source"] == "kis" and q["name"] == "삼성전자"


def test_http_error_raises():
    kmd.set_transport(httpx.MockTransport(lambda r: httpx.Response(503, json={"ok": False, "message": "KIS 토큰 만료"})))
    with pytest.raises(kmd.KisMarketDataError, match="토큰"):
        asyncio.run(kmd.fetch_minutes("005930.KS"))


# ── 통합: aggressive_mode / stock 이 KIS 를 우선 쓰고 실패하면 Yahoo 로 폴백 ──
def test_aggressive_indicators_use_kis_price_and_source():
    seen = []; _server(seen)
    with patch.object(ag, "get_candles", AsyncMock(side_effect=AssertionError("Yahoo 를 쓰면 안 된다"))):
        out = asyncio.run(ag.get_intraday_indicators("005930.KS"))
    assert out["source"] == "kis" and out["current_price"] == 999.0 and out["bars"] == 3


def test_aggressive_indicators_fallback_to_yahoo_on_kis_failure():
    kmd.set_transport(httpx.MockTransport(lambda r: httpx.Response(500, json={"ok": False, "message": "down"})))
    yahoo = AsyncMock(return_value={"candles": [{"close": 100 + i, "volume": 1} for i in range(30)]})
    with patch.object(ag, "get_candles", yahoo):
        out = asyncio.run(ag.get_intraday_indicators("005930.KS"))
    assert out["source"] == "yahoo" and out["current_price"] == 129 and yahoo.await_count == 1


def test_aggressive_no_fallback_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "MARKET_DATA_FALLBACK_YAHOO", False)
    kmd.set_transport(httpx.MockTransport(lambda r: httpx.Response(500, json={"ok": False, "message": "down"})))
    with patch.object(ag, "get_candles", AsyncMock(side_effect=AssertionError("폴백 금지"))):
        out = asyncio.run(ag.get_intraday_indicators("005930.KS"))
    assert out["current_price"] is None and out["signal"]["action"] == "판단 불가"


def test_stock_get_candles_prefers_kis_and_caches_separately():
    seen = []; _server(seen)
    store = {}
    async def cget(key, max_age_hours=24): return store.get(key)
    async def cset(key, data): store[key] = data
    with patch.object(stock, "cache_get", cget), patch.object(stock, "cache_set", cset), \
         patch.object(stock, "_yahoo_chart", AsyncMock(side_effect=AssertionError("Yahoo 금지"))):
        out = asyncio.run(stock.get_candles("005930.KS", period="1y", interval="1d"))
        again = asyncio.run(stock.get_candles("005930.KS", period="1y", interval="1d"))
    assert out["source"] == "kis" and len(out["candles"]) == 2 and again is out or again == out
    assert any(k.startswith("candles:kis:") for k in store) and len(seen) == 1


def test_stock_get_candles_non_krx_or_intraday_uses_yahoo():
    seen = []; _server(seen)
    yahoo = AsyncMock(return_value={"timestamp": [1], "indicators": {"quote": [{"open": [1], "high": [1], "low": [1], "close": [1.0], "volume": [1]}]}})
    with patch.object(stock, "cache_get", AsyncMock(return_value=None)), patch.object(stock, "cache_set", AsyncMock()), \
         patch.object(stock, "_yahoo_chart", yahoo):
        asyncio.run(stock.get_candles("AAPL", period="1y", interval="1d"))
        asyncio.run(stock.get_candles("005930.KS", period="5d", interval="5m"))
    assert yahoo.await_count == 2 and seen == []


def test_stock_get_quote_kis_then_fallback():
    seen = []; _server(seen)
    q = asyncio.run(stock.get_quote("005930.KS"))
    assert q["source"] == "kis" and q["price"] == 271000.0
    kmd.set_transport(httpx.MockTransport(lambda r: httpx.Response(500, json={"ok": False, "message": "down"})))
    with patch.object(stock, "_yahoo_chart", AsyncMock(return_value={"meta": {"regularMarketPrice": 1.0, "shortName": "S"}})):
        q2 = asyncio.run(stock.get_quote("005930.KS"))
    assert q2["price"] == 1.0 and "source" not in q2
