"""백테스트·분석용 일봉 OHLCV — 시스템 OHLCV 저장소를 먼저 보고, 없는 구간만 Yahoo 에서 받아 저장소에 적재한다.

우선순위
  1. 시스템 OHLCV 저장소: stock-coin-trade pg-stock (`GET /openapi/v1/ohlcv/{code}`, 공개). 국내 6자리 종목 전용.
  2. 저장소에 없는 구간 → `POST /openapi/v1/ohlcv/ingest` (st 가 Yahoo→네이버 순으로 받아 upsert, API 키 필요) → 저장소 재조회.
  3. 그래도 비거나(해외 종목·적재 실패·st 미설정) → Yahoo chart API 직접 조회 (적재 없음).

구간 판단은 domain-rag-lab `ohlcv_store` 와 같은 규칙: 저장소가 비었으면 전체, 첫 봉이 start 보다 10일 넘게 늦으면
앞 구간, 마지막 봉이 end 보다 3일 넘게 오래됐으면 뒤 구간을 적재한다. 상장 전처럼 외부에도 없는 앞 구간은
프로세스 메모리에 기억해 매번 다시 적재를 시도하지 않는다.

반환 봉은 `get_candles` 와 같은 {"time", "open", "high", "low", "close", "volume"} 이며 time 은 거래일 00:00 UTC 초.
메타 `price_source` 는 domain-rag-lab 과 같은 키(source · db_rows_before · fetched · stored_rows · rows · first_date · last_date)라
LEAN 원격 결과와 화면에서 같은 방식으로 보여 줄 수 있다.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

Candle = dict[str, Any]

HEAD_TOLERANCE_DAYS = 10
TAIL_TOLERANCE_DAYS = 3
PAGE_LIMIT = 1000                     # st `/ohlcv/{code}` 의 limit 상한
READ_TIMEOUT = 15.0
INGEST_TIMEOUT = 90.0                 # 10년치 일봉 적재를 기다린다
_TICKER_CODE = re.compile(r"^[0-9A-Z]{6}$")
_KR_SUFFIX = {"KS", "KQ"}
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; FinAgent/1.0)"}

# 외부에도 없는 구간(상장 전 등) — (종류, 코드, 경계일)
_known_empty: set[tuple[str, str, date]] = set()
_transport: httpx.AsyncBaseTransport | None = None     # 테스트 주입용


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    global _transport
    _transport = transport


def reset_known_empty() -> None:
    _known_empty.clear()


def configured() -> bool:
    return bool(settings.STOCK_COIN_TRADE_BASE_URL)


def can_ingest() -> bool:
    return configured() and bool(settings.STOCK_COIN_TRADE_API_KEY)


def krx_code(symbol: str) -> str | None:
    """`005930.KS` / `035720.KQ` / `005930` → `005930`. 국내 종목이 아니면 None (저장소가 국내 일봉 전용)."""
    raw = (symbol or "").strip().upper()
    code, _, suffix = raw.partition(".")
    if _TICKER_CODE.fullmatch(code) and suffix in _KR_SUFFIX | {""}:
        return code
    return None


def _url(path: str) -> str:
    return f"{settings.STOCK_COIN_TRADE_BASE_URL.rstrip('/')}{path}"


def _client(timeout: float, **kw) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, transport=_transport, **kw)


def _to_candle(row: dict) -> Candle | None:
    d = row.get("trade_date") or row.get("tradeDate")
    close = row.get("close")
    if not d or close is None:
        return None
    day = date.fromisoformat(str(d)[:10])
    ts = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())
    return {"time": ts, "open": row.get("open"), "high": row.get("high"), "low": row.get("low"),
            "close": float(close), "volume": int(row.get("volume") or 0)}


def _day(c: Candle) -> date:
    return datetime.fromtimestamp(int(c["time"]), tz=timezone.utc).date()


# ── 1. 시스템 저장소 읽기 ─────────────────────────────────────────────────

async def read_store(code: str, start: date, end: date) -> list[Candle] | None:
    """저장소 [start, end] 일봉. 종목이 저장소에 없으면 None, 장애면 예외."""
    out: list[Candle] = []
    offset = 0
    async with _client(READ_TIMEOUT) as cli:
        while True:
            resp = await cli.get(_url(f"/openapi/v1/ohlcv/{code}"),
                                 params={"date_from": start.isoformat(), "date_to": end.isoformat(),
                                         "limit": PAGE_LIMIT, "offset": offset, "order": "asc"})
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            body = resp.json()
            out.extend(c for c in (_to_candle(r) for r in body.get("rows", [])) if c)
            nxt = body.get("nextOffset")
            if nxt is None or nxt <= offset:
                break
            offset = int(nxt)
    return out


async def ingest_store(symbol: str, start: date, end: date) -> dict | None:
    """st 에 [start, end] 적재를 요청한다. 미설정·실패·skipped 는 None."""
    if not can_ingest():
        return None
    try:
        async with _client(INGEST_TIMEOUT) as cli:
            resp = await cli.post(_url("/openapi/v1/ohlcv/ingest"),
                                  json={"symbol": symbol, "start": start.isoformat(), "end": (end + timedelta(days=1)).isoformat()},
                                  headers={"Authorization": f"Bearer {settings.STOCK_COIN_TRADE_API_KEY}"})
            resp.raise_for_status()
            result = resp.json()
    except Exception as exc:
        logger.warning("OHLCV 적재 실패 %s %s~%s: %s", symbol, start, end, exc)
        return None
    return result if result.get("status") == "ingested" else None


# ── 2. Yahoo 직접 조회 (적재 불가 시 폴백) ───────────────────────────────

async def fetch_yahoo(symbol: str, start: date, end: date) -> list[Candle]:
    """Yahoo chart API period1/period2 로 [start, end] 일봉. 실패·없음은 빈 리스트."""
    p1 = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp())
    end1 = end + timedelta(days=1)
    p2 = int(datetime(end1.year, end1.month, end1.day, tzinfo=timezone.utc).timestamp())
    try:
        async with _client(READ_TIMEOUT, headers=_HEADERS) as cli:
            resp = await cli.get(f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}",
                                 params={"interval": "1d", "period1": p1, "period2": p2, "events": "div,splits"})
            resp.raise_for_status()
            result = resp.json().get("chart", {}).get("result") or []
    except Exception as exc:
        logger.warning("Yahoo 일봉 조회 실패 %s: %s", symbol, exc)
        return []
    if not result:
        return []
    data = result[0]
    q = (data.get("indicators", {}).get("quote") or [{}])[0]
    out: list[Candle] = []
    for i, ts in enumerate(data.get("timestamp") or []):
        close = (q.get("close") or [None])[i] if i < len(q.get("close") or []) else None
        if close is None:
            continue
        day = datetime.fromtimestamp(int(ts), tz=timezone.utc).date()
        pick = lambda k: (q.get(k) or [None] * (i + 1))[i]  # noqa: E731
        out.append({"time": int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp()),
                    "open": pick("open"), "high": pick("high"), "low": pick("low"), "close": float(close),
                    "volume": int(pick("volume") or 0)})
    return out


# ── 3. 공개 API ───────────────────────────────────────────────────────────

def _missing_ranges(candles: list[Candle], code: str, start: date, end: date) -> list[tuple[str, date, date]]:
    if not candles:
        return [("all", start, end)]
    gaps: list[tuple[str, date, date]] = []
    first, last = _day(candles[0]), _day(candles[-1])
    if first > start + timedelta(days=HEAD_TOLERANCE_DAYS) and ("head", code, start) not in _known_empty:
        gaps.append(("head", start, first - timedelta(days=1)))
    if last < end - timedelta(days=TAIL_TOLERANCE_DAYS) and ("tail", code, end) not in _known_empty:
        gaps.append(("tail", last + timedelta(days=1), end))
    return gaps


def _merge(*lists: list[Candle]) -> list[Candle]:
    by_day: dict[date, Candle] = {}
    for lst in lists:
        for c in lst:
            by_day[_day(c)] = c
    return [by_day[d] for d in sorted(by_day)]


async def daily_candles(symbol: str, start: date, end: date) -> tuple[list[Candle], dict]:
    """[start, end] 일봉과 출처 메타. 저장소 → 적재 → Yahoo 순."""
    code = krx_code(symbol)
    meta: dict[str, Any] = {"symbol": symbol, "ticker": code, "store": "stock-coin-trade" if configured() else None,
                            "db_rows_before": 0, "fetched": [], "stored_rows": 0, "source": "none"}
    candles: list[Candle] = []
    store_ok = configured() and code is not None
    if store_ok:
        try:
            stored = await read_store(code, start, end)
        except Exception as exc:
            logger.warning("OHLCV 저장소 조회 실패 %s: %s — Yahoo 로 진행", symbol, exc)
            meta["store_error"] = str(exc)[:160]
            store_ok = False
            stored = None
        if store_ok:
            candles = stored or []
            meta["db_rows_before"] = len(candles)
            for kind, a, b in _missing_ranges(candles, code, start, end):
                ingested = await ingest_store(symbol, a, b)
                if ingested:
                    meta["stored_rows"] += int(ingested.get("rows") or 0)
                    meta["fetched"].append({"range": kind, "from": a.isoformat(), "to": b.isoformat(),
                                            "source": "yahoo→store", "rows": int(ingested.get("rows") or 0)})
                    continue
                direct = await fetch_yahoo(symbol, a, b)
                if direct:
                    candles = _merge(candles, direct)
                    meta["fetched"].append({"range": kind, "from": a.isoformat(), "to": b.isoformat(), "source": "yahoo", "rows": len(direct)})
                else:
                    _known_empty.add((kind if kind != "all" else "head", code, start if kind != "tail" else end))
                    meta["fetched"].append({"range": kind, "from": a.isoformat(), "to": b.isoformat(), "source": None, "rows": 0})
            if meta["stored_rows"]:
                try:
                    candles = _merge(candles, await read_store(code, start, end) or [])
                except Exception as exc:
                    logger.warning("OHLCV 적재 후 재조회 실패 %s: %s", symbol, exc)
    elif code is None and configured():
        meta["note"] = "해외 종목은 시스템 OHLCV 저장소(국내 전용)에 적재하지 않습니다"
    if not store_ok:
        candles = await fetch_yahoo(symbol, start, end)
        meta["fetched"].append({"range": "all", "from": start.isoformat(), "to": end.isoformat(),
                                "source": "yahoo" if candles else None, "rows": len(candles)})

    sources = sorted({f["source"] for f in meta["fetched"] if f["source"]})
    parts = (["db"] if meta["db_rows_before"] else []) + sources
    meta["source"] = "+".join(parts) or "none"
    meta["rows"] = len(candles)
    meta["first_date"] = _day(candles[0]).isoformat() if candles else None
    meta["last_date"] = _day(candles[-1]).isoformat() if candles else None
    return candles, meta


PERIOD_DAYS = {"1d": 5, "5d": 10, "1mo": 31, "3mo": 92, "6mo": 183, "1y": 366, "2y": 731, "3y": 1096, "5y": 1827, "10y": 3653}


def period_start(period: str, today: date | None = None) -> date:
    """Yahoo range 문자열(1y·10y·max…) → 시작일. max 는 1990-01-01."""
    today = today or date.today()
    if period == "max":
        return date(1990, 1, 1)
    return today - timedelta(days=PERIOD_DAYS.get(period, 366))
