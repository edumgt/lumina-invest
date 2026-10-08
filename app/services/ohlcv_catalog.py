"""stock-coin-trade OHLCV 저장소(pg-stock) 종목 카탈로그 클라이언트.

종목 검색의 1차 소스다. 저장소에는 국내 상장 2,700여 종목의 코드·한글명·시장이
일봉과 함께 들어 있어, 야후 자동완성보다 국내 종목에 정확하고 한글 검색어에 안전하다
(야후 `/v1/finance/search` 는 "카카오" 같은 한글 질의에 400 을 돌려준다).

저장소에 없는 종목은 호출자가 야후로 찾은 뒤 ``ingest`` 로 저장소를 채운다 —
다음 검색부터는 1차 소스에서 바로 나온다.

계약: stock-coin-trade `GET /openapi/v1/ohlcv/search`(공개) ·
      `POST /openapi/v1/ohlcv/ingest`(Bearer API 키).
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# 저장소 시장 구분 → 야후 티커 접미사. 프론트엔드와 /stocks/* 는 `005930.KS` 형태를 쓴다.
_SUFFIX = {"KOSPI": "KS", "KOSDAQ": "KQ", "KOSDAQ GLOBAL": "KQ"}

SEARCH_TIMEOUT = 5.0
# 6년치 일봉을 받아 upsert 하는 동안 기다린다. 검색 중 동기 적재라 사용자가 보고 있다.
INGEST_TIMEOUT = 60.0


def configured() -> bool:
    return bool(settings.STOCK_COIN_TRADE_BASE_URL)


def _url(path: str) -> str:
    return f"{settings.STOCK_COIN_TRADE_BASE_URL.rstrip('/')}{path}"


def to_symbol(ticker_code: str, market: str) -> str:
    suffix = _SUFFIX.get((market or "").upper())
    return f"{ticker_code}.{suffix}" if suffix else ticker_code


def _as_result(row: dict[str, Any]) -> dict[str, str]:
    """저장소 행을 `/api/stocks/search` 응답 형태로 맞춘다(프론트엔드 수정 불필요)."""
    code = str(row.get("ticker_code") or "")
    market = str(row.get("market") or "")
    return {
        "symbol": to_symbol(code, market),
        "name": str(row.get("name") or code),
        "exchange": market or "한국",
        "type": "주식",
    }


async def search(query: str, limit: int = 10) -> list[dict[str, str]]:
    """OHLCV 저장소에서 종목을 찾는다. 저장소가 없거나 실패하면 빈 리스트(검색은 계속된다)."""
    if not configured():
        return []
    try:
        async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT) as client:
            resp = await client.get(_url("/openapi/v1/ohlcv/search"), params={"q": query, "limit": limit})
            resp.raise_for_status()
            rows = resp.json().get("tickers", [])
    except Exception as exc:
        logger.warning("OHLCV 종목 검색 실패 q=%r: %s", query, exc)
        return []
    return [_as_result(row) for row in rows if row.get("ticker_code")]


async def ingest(symbol: str, name: str = "") -> dict[str, Any] | None:
    """야후에서 찾은 종목의 일봉을 OHLCV 저장소에 채운다.

    실패해도 예외를 올리지 않는다 — 적재는 다음 검색을 빠르게 하려는 부가 작업이고,
    이번 검색 결과는 이미 야후에서 확보했으므로 적재 실패로 검색을 깨뜨리지 않는다.
    해외 종목은 저장소가 받지 않아 ``status=skipped`` 로 돌아온다.
    """
    if not configured() or not settings.STOCK_COIN_TRADE_API_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=INGEST_TIMEOUT) as client:
            resp = await client.post(
                _url("/openapi/v1/ohlcv/ingest"),
                json={"symbol": symbol, "name": name},
                headers={"Authorization": f"Bearer {settings.STOCK_COIN_TRADE_API_KEY}"},
            )
            resp.raise_for_status()
            result = resp.json()
    except Exception as exc:
        logger.warning("OHLCV 적재 실패 symbol=%s: %s", symbol, exc)
        return None
    if result.get("status") == "ingested":
        logger.info("OHLCV 적재 완료 %s %s행", symbol, result.get("rows"))
    return result
