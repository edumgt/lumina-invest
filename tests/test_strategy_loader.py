"""domain-rag-lab 전략 스펙 로더: 미설정 폴백, TTL 캐시, 404 → None."""
import asyncio

import httpx
import pytest

from app.config import settings
from app.services import strategy_loader as sl


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    sl.clear_cache()
    monkeypatch.setattr(settings, "DOMAIN_RAG_LAB_BASE_URL", "https://rag.test")
    monkeypatch.setattr(settings, "DOMAIN_RAG_LAB_API_KEY", "k")
    yield
    sl.set_transport(None)
    sl.clear_cache()


def test_unconfigured_returns_empty(monkeypatch):
    monkeypatch.setattr(settings, "DOMAIN_RAG_LAB_BASE_URL", "")
    assert asyncio.run(sl.list_strategies()) == []
    assert asyncio.run(sl.get_strategy("x")) is None


def test_list_and_get_with_cache():
    hits = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        hits["n"] += 1
        assert request.headers["X-API-Key"] == "k"
        if request.url.path == "/backtests/strategies":
            return httpx.Response(200, json={"strategies": [{"strategy_id": "ma_cross", "version": 2}]})
        if request.url.path == "/backtests/strategies/ma_cross":
            return httpx.Response(200, json={"strategy_id": "ma_cross", "version": 2, "universe": ["005930"]})
        return httpx.Response(404, json={"detail": "not found"})

    sl.set_transport(httpx.MockTransport(handler))
    assert asyncio.run(sl.list_strategies())[0]["strategy_id"] == "ma_cross"
    assert asyncio.run(sl.list_strategies())[0]["version"] == 2
    assert hits["n"] == 1  # 캐시 적중
    assert asyncio.run(sl.get_strategy("ma_cross"))["universe"] == ["005930"]
    assert asyncio.run(sl.get_strategy("missing")) is None
