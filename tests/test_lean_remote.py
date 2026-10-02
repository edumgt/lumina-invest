"""LEAN 원격 위임: 설정 시 domain-rag-lab 호출, 422 는 검증 오류로, 연결 실패는 로컬 폴백."""
import asyncio
from datetime import date

import httpx
import pytest

from app.config import settings
from app.services import lean_remote
from app.services.lean_backtest import LeanBacktestError

PAYLOAD = {"ticker": "005930.KS", "start_date": date(2024, 1, 2), "end_date": date(2025, 12, 30), "compare_start_date": date(2024, 1, 2),
           "compare_end_date": date(2025, 12, 30), "initial_cash": 10000.0, "strategy": "ma_cross", "short_window": 5, "long_window": 20,
           "dca_interval_days": 21, "breakout_window": 20}


@pytest.fixture(autouse=True)
def cfg(monkeypatch):
    monkeypatch.setattr(settings, "DOMAIN_RAG_LAB_BASE_URL", "https://rag.test")
    monkeypatch.setattr(settings, "DOMAIN_RAG_LAB_API_KEY", "k")
    yield
    lean_remote.set_transport(None)


async def local_run(**kw):
    return {"ticker": kw["ticker"], "engine": "local", "strategy": kw["strategy"], "strategy_return_pct": 1.0, "max_drawdown_pct": -1.0,
            "sharpe_ratio": 0.1, "start_date": str(kw["start_date"]), "end_date": str(kw["end_date"]), "lean_ok": False, "lean_mode": "local"}


def test_remote_result_is_normalized_and_dates_serialized():
    seen = {}
    def handler(r: httpx.Request) -> httpx.Response:
        seen["path"], seen["key"], seen["body"] = r.url.path, r.headers.get("X-API-Key"), r.read()
        return httpx.Response(200, json={"ticker": "005930.KS", "engine": "QuantConnect LEAN + yfinance", "strategy": "ma_cross",
                                         "strategy_return_pct": 12.3, "max_drawdown_pct": -8.0, "sharpe_ratio": 0.9, "trade_count": 10})
    lean_remote.set_transport(httpx.MockTransport(handler))
    out = asyncio.run(lean_remote.run_or_fallback(PAYLOAD, local_run))
    import json
    sent = json.loads(seen["body"])
    assert seen["path"] == "/backtests/run" and seen["key"] == "k" and sent["start_date"] == "2024-01-02" and sent["strategy"] == "ma_cross"
    assert out["lean_ok"] is True and out["lean_mode"] == "remote:domain-rag-lab" and out["start_date"] == "2024-01-02"


def test_validation_error_is_not_retried_locally():
    lean_remote.set_transport(httpx.MockTransport(lambda r: httpx.Response(422, json={"detail": "시작일은 종료일보다 앞서야 합니다."})))
    with pytest.raises(LeanBacktestError):
        asyncio.run(lean_remote.run_or_fallback(PAYLOAD, local_run))


def test_connection_failure_falls_back_to_local():
    def boom(r: httpx.Request): raise httpx.ConnectError("down", request=r)
    lean_remote.set_transport(httpx.MockTransport(boom))
    out = asyncio.run(lean_remote.run_or_fallback(PAYLOAD, local_run))
    assert out["engine"] == "local" and "fallback_reason" in out


def test_disabled_uses_local(monkeypatch):
    monkeypatch.setattr(settings, "DOMAIN_RAG_LAB_BASE_URL", "")
    assert asyncio.run(lean_remote.run_or_fallback(PAYLOAD, local_run))["engine"] == "local"
    assert lean_remote.remote_status()["enabled"] is False
