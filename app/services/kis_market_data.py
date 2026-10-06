"""KIS 연계 시세 — stock-coin-trade 의 KIS 차트 API(`/api/kis-chart/minutes|candles`)로 국내 분봉·일봉·현재가를 받는다.

왜: Yahoo 국내 분봉은 ~20분 지연이고 EC2 에서 31종목×5분 조회는 요청 제한에 걸린다. KIS 는 실시간이며
st 가 KIS 토큰·호출 제한을 관리한다. 응답 형태(2026-10-06 확인):
  minutes: {"ok", "symbol", "summary": {"name","price","prevClose",...}, "candles": [{"time":"YYYY-MM-DD HH:MM","timestamp",open,high,low,close,volume}]}
  candles: {"ok", "summary": {...}, "candles": [{"time":"YYYY-MM-DD",open,high,low,close,volume}]}   (count ≤ 300)
분봉은 `time=HHMMSS`(정규장 090000~153000) 기준으로 그 시각까지의 1분봉을 돌려준다. 장 전에는 전 영업일 막판 봉이
오늘 날짜로 라벨링되므로(st 측 동작) 장중에만 신뢰한다. 테스트는 set_transport() 로 MockTransport 주입.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
_transport: httpx.AsyncBaseTransport | None = None


class KisMarketDataError(Exception):
    pass


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    global _transport
    _transport = transport


def is_enabled() -> bool:
    return (settings.MARKET_DATA_SOURCE or "").strip().lower() == "kis" and bool(settings.STOCK_COIN_TRADE_BASE_URL)


def is_krx(symbol: str) -> bool:
    s = str(symbol or "").upper()
    return s.endswith(".KS") or s.endswith(".KQ") or (len(s) == 6 and s.isdigit())


def code_of(symbol: str) -> str:
    s = str(symbol).upper()
    for suf in (".KS", ".KQ"):
        if s.endswith(suf):
            return s[: -len(suf)]
    return s


def kst_now() -> datetime:
    return datetime.now(KST)


def clamp_end_time(now: datetime | None = None) -> str:
    """st 분봉 API 의 time 파라미터(정규장 090000~153000)."""
    n = (now or kst_now()).astimezone(KST)
    hhmmss = n.strftime("%H%M%S")
    if hhmmss < "090000" or hhmmss > "153000":
        return "153000"
    return hhmmss


def _client() -> httpx.AsyncClient:
    headers = {"Accept": "application/json"}
    if settings.STOCK_COIN_TRADE_API_KEY:
        headers["Authorization"] = f"Bearer {settings.STOCK_COIN_TRADE_API_KEY}"
    return httpx.AsyncClient(base_url=settings.STOCK_COIN_TRADE_BASE_URL.rstrip("/"), headers=headers,
                             timeout=float(settings.KIS_CHART_TIMEOUT or 10), transport=_transport)


async def _get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        async with _client() as cli:
            resp = await cli.get(path, params=params)
    except httpx.HTTPError as exc:
        raise KisMarketDataError(f"KIS 차트 API 연결 실패: {exc}") from exc
    try:
        body = resp.json()
    except ValueError as exc:
        raise KisMarketDataError(f"KIS 차트 API 응답이 JSON 이 아님 (HTTP {resp.status_code})") from exc
    if resp.status_code >= 400 or body.get("ok") is False:
        raise KisMarketDataError(str(body.get("message") or body.get("error") or f"HTTP {resp.status_code}"))
    return body


def _ts_of(time_str: str) -> int:
    t = str(time_str)
    fmt = "%Y-%m-%d %H:%M" if len(t) > 10 else "%Y-%m-%d"
    return int(datetime.strptime(t, fmt).replace(tzinfo=KST).timestamp())


def _norm(c: dict) -> dict | None:
    if c.get("close") in (None, 0):
        return None
    ts = c.get("timestamp") or _ts_of(c.get("time", ""))
    return {"time": int(ts), "open": c.get("open"), "high": c.get("high"), "low": c.get("low"),
            "close": float(c["close"]), "volume": c.get("volume")}


async def fetch_minutes(symbol: str, count: int | None = None, end_time: str | None = None) -> dict[str, Any]:
    """1분봉 (오래된 → 최신). 반환 {"candles": [...], "summary": {...}}"""
    n = max(30, min(240, int(count or settings.KIS_CHART_MINUTES or 240)))
    body = await _get("/api/kis-chart/minutes", {"symbol": code_of(symbol), "count": n, "time": end_time or clamp_end_time()})
    candles = [x for x in (_norm(c) for c in body.get("candles") or []) if x]
    candles.sort(key=lambda c: c["time"])
    return {"candles": candles, "summary": body.get("summary") or {}}


async def fetch_daily(symbol: str, count: int = 300) -> dict[str, Any]:
    n = max(20, min(300, int(count)))
    body = await _get("/api/kis-chart/candles", {"symbol": code_of(symbol), "period": "D", "count": n})
    candles = [x for x in (_norm(c) for c in body.get("candles") or []) if x]
    candles.sort(key=lambda c: c["time"])
    return {"candles": candles, "summary": body.get("summary") or {}}


def aggregate(candles_1m: list[dict], minutes: int = 5) -> list[dict]:
    """1분봉 → N분봉. 버킷은 timestamp // (N*60)."""
    if minutes <= 1:
        return list(candles_1m)
    out: list[dict] = []
    width = minutes * 60
    for c in candles_1m:
        bucket = (int(c["time"]) // width) * width
        if out and out[-1]["time"] == bucket:
            o = out[-1]
            o["high"] = max(v for v in (o["high"], c["high"]) if v is not None) if (o["high"] is not None or c["high"] is not None) else None
            o["low"] = min(v for v in (o["low"], c["low"]) if v is not None) if (o["low"] is not None or c["low"] is not None) else None
            o["close"] = c["close"]
            o["volume"] = (o["volume"] or 0) + (c["volume"] or 0)
        else:
            out.append({"time": bucket, "open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"], "volume": c["volume"] or 0})
    return out


async def get_intraday_candles(symbol: str, interval_min: int = 5) -> dict[str, Any]:
    """공격 모드용 N분봉 + 현재가. get_candles 와 같은 모양에 price/source 추가."""
    raw = await fetch_minutes(symbol)
    bars = aggregate(raw["candles"], interval_min)
    price = raw["summary"].get("price")
    return {"symbol": symbol, "interval": f"{interval_min}m", "candles": bars,
            "price": float(price) if price else (bars[-1]["close"] if bars else None),
            "name": raw["summary"].get("name"), "source": "kis"}


def daily_count_for_period(period: str) -> int:
    p = (period or "1y").lower()
    table = {"1mo": 30, "3mo": 70, "6mo": 130, "1y": 260, "2y": 300, "5y": 300, "10y": 300, "max": 300}
    return table.get(p, 300)


async def get_daily_candles(symbol: str, period: str = "1y") -> dict[str, Any]:
    raw = await fetch_daily(symbol, daily_count_for_period(period))
    return {"symbol": symbol, "interval": "1d", "period": period, "candles": raw["candles"], "source": "kis",
            "price": raw["summary"].get("price"), "name": raw["summary"].get("name")}


async def get_quote(symbol: str) -> dict[str, Any]:
    """stock.get_quote 와 같은 키. KIS summary 기반(실시간)."""
    raw = await fetch_daily(symbol, 20)
    s = raw["summary"]
    price = s.get("price"); prev = s.get("prevClose")
    change = s.get("change") if s.get("change") is not None else ((price - prev) if price and prev else None)
    pct = s.get("changeRate") if s.get("changeRate") is not None else ((change / prev * 100) if change is not None and prev else None)
    return {"symbol": symbol, "name": s.get("name") or symbol, "price": price, "prev_close": prev,
            "change": change, "change_pct": pct, "currency": "KRW", "market": "KRX", "source": "kis",
            "open": s.get("open"), "high": s.get("high"), "low": s.get("low"), "volume": s.get("volume")}
