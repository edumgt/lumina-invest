"""시세 수집 — 표준 라이브러리(urllib)만 쓰는 로컬 시세 공급자.

  * yahoo : Yahoo Finance chart API. 일봉·분봉 모두 가능. 국내 분봉은 약 20분 지연이다.
  * kis   : 한국투자증권 Open API 직접 호출(일봉 + 현재가). 분봉은 1분봉 30개만 주므로
            공격 모드(5분봉 MA20 필요)에는 부족해 Yahoo 로 폴백한다.
  * mock  : 네트워크 없이 종목코드로 시드를 고정한 랜덤워크. 오프라인 점검·드라이런용.

캔들은 app/services/stock.py 와 같은 모양으로 돌려준다:
    {"symbol", "interval", "period", "candles": [{"time"(epoch), "open","high","low","close","volume"}], "source"}
반복 실행 시 호출을 줄이려고 data/cache/ 에 JSON 파일 캐시를 둔다(나이 제한은 호출자가 준다).
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Settings
from .universe import code_of, is_krx

logger = logging.getLogger(__name__)

YAHOO_CHART = "https://query2.finance.yahoo.com/v8/finance/chart"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; LuminaLocal/1.0)"}
KST = timezone(timedelta(hours=9))


class MarketDataError(Exception):
    pass


# ── HTTP ────────────────────────────────────────────────────────────────
def http_json(url: str, params: dict | None = None, headers: dict | None = None,
              timeout: float = 15.0, method: str = "GET", body: dict | None = None) -> dict:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={**HEADERS, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise MarketDataError(f"HTTP {exc.code} {url} — {detail}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise MarketDataError(f"{type(exc).__name__}: {exc} ({url})") from exc


# ── 파일 캐시 ───────────────────────────────────────────────────────────
def _cache_path(cache_dir: Path, key: str) -> Path:
    return cache_dir / f"{hashlib.sha1(key.encode()).hexdigest()[:16]}.json"


def cache_get(cache_dir: Path, key: str, max_age_hours: float) -> dict | None:
    if max_age_hours <= 0:
        return None
    path = _cache_path(cache_dir, key)
    if not path.exists():
        return None
    if (time.time() - path.stat().st_mtime) > max_age_hours * 3600:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def cache_set(cache_dir: Path, key: str, value: dict) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(cache_dir, key)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# ── Yahoo ───────────────────────────────────────────────────────────────
def yahoo_candles(symbol: str, period: str, interval: str, timeout: float) -> dict:
    data = http_json(f"{YAHOO_CHART}/{urllib.parse.quote(symbol)}",
                     {"interval": interval, "range": period}, timeout=timeout)
    result = (data.get("chart") or {}).get("result") or []
    if not result:
        return {"symbol": symbol, "interval": interval, "period": period, "candles": [], "source": "yahoo"}
    chart = result[0]
    quote = ((chart.get("indicators") or {}).get("quote") or [{}])[0]
    opens, highs = quote.get("open") or [], quote.get("high") or []
    lows, closes = quote.get("low") or [], quote.get("close") or []
    volumes = quote.get("volume") or []
    candles = []
    for i, ts in enumerate(chart.get("timestamp") or []):
        if i >= len(closes) or closes[i] is None:
            continue
        candles.append({
            "time": ts,
            "open": opens[i] if i < len(opens) else None,
            "high": highs[i] if i < len(highs) else None,
            "low": lows[i] if i < len(lows) else None,
            "close": closes[i],
            "volume": volumes[i] if i < len(volumes) else None,
        })
    return {"symbol": symbol, "interval": interval, "period": period, "candles": candles, "source": "yahoo"}


def yahoo_quote(symbol: str, timeout: float) -> dict:
    data = http_json(f"{YAHOO_CHART}/{urllib.parse.quote(symbol)}",
                     {"interval": "1d", "range": "1d"}, timeout=timeout)
    result = (data.get("chart") or {}).get("result") or []
    meta = (result[0].get("meta") if result else {}) or {}
    return {"symbol": symbol, "name": meta.get("longName") or meta.get("shortName") or symbol,
            "price": meta.get("regularMarketPrice"),
            "prev_close": meta.get("previousClose") or meta.get("chartPreviousClose"),
            "currency": meta.get("currency", "KRW"), "source": "yahoo"}


# ── Mock(오프라인) ──────────────────────────────────────────────────────
_PERIOD_DAYS = {"1mo": 30, "3mo": 90, "6mo": 180, "1y": 365, "2y": 730, "5y": 1825, "10y": 3650}


def mock_candles(symbol: str, period: str, interval: str) -> dict:
    """종목코드로 시드를 고정한 랜덤워크. 같은 종목은 항상 같은 시계열이 나온다."""
    rng = random.Random(int(hashlib.sha1(code_of(symbol).encode()).hexdigest()[:8], 16))
    minutes = interval.endswith("m")
    bars = 240 if minutes else max(120, min(_PERIOD_DAYS.get(period, 365), 900))
    step = 60 * int(interval.rstrip("m") or 5) if minutes else 86_400
    price = rng.uniform(20_000, 200_000)
    now = int(time.time())
    candles = []
    for i in range(bars):
        drift = rng.gauss(0, 0.012 if not minutes else 0.003)
        close = max(1_000.0, price * (1 + drift))
        high = max(price, close) * (1 + abs(rng.gauss(0, 0.004)))
        low = min(price, close) * (1 - abs(rng.gauss(0, 0.004)))
        candles.append({"time": now - (bars - i) * step, "open": round(price), "high": round(high),
                        "low": round(low), "close": round(close), "volume": rng.randint(10_000, 5_000_000)})
        price = close
    return {"symbol": symbol, "interval": interval, "period": period, "candles": candles, "source": "mock"}


# ── 공급자 ──────────────────────────────────────────────────────────────
class MarketData:
    """설정에 맞는 공급자를 골라 캔들·현재가를 돌려주는 얇은 래퍼."""

    def __init__(self, settings: Settings, kis_client=None):
        self.settings = settings
        self.kis = kis_client          # broker.KISClient | None (kis 소스일 때만 쓴다)

    # 일봉 ----------------------------------------------------------------
    def daily_candles(self, symbol: str, period: str | None = None,
                      max_age_hours: float | None = None) -> dict:
        s = self.settings
        period = period or s.candle_period
        max_age = s.candle_cache_hours if max_age_hours is None else max_age_hours
        source = s.market_data_source
        key = f"candles:{source}:{symbol}:{period}:1d"
        cached = cache_get(s.cache_dir, key, max_age)
        if cached is not None:
            return cached

        result: dict | None = None
        if source == "mock":
            result = mock_candles(symbol, period, "1d")
        elif source == "kis" and self.kis and is_krx(symbol):
            try:
                result = self.kis.daily_candles(symbol, period)
            except Exception as exc:
                logger.warning("KIS 일봉 조회 실패 %s: %s — Yahoo 폴백", symbol, exc)
        if not result or not result.get("candles"):
            result = yahoo_candles(symbol, period, "1d", s.http_timeout)
        if result.get("candles"):
            cache_set(s.cache_dir, key, result)
        return result

    # 분봉(공격 모드) ------------------------------------------------------
    def intraday_candles(self, symbol: str) -> dict:
        s = self.settings
        interval = s.aggressive_candle_interval
        max_age = max(1, s.aggressive_cache_min) / 60
        key = f"candles:intraday:{s.market_data_source}:{symbol}:{interval}"
        cached = cache_get(s.cache_dir, key, max_age)
        if cached is not None:
            return cached
        if s.market_data_source == "mock":
            result = mock_candles(symbol, s.aggressive_candle_range, interval)
        else:
            # KIS 분봉은 1분봉 30개뿐이라 MA20(5분봉) 계산에 못 쓴다 — 분봉은 Yahoo 를 쓴다.
            result = yahoo_candles(symbol, s.aggressive_candle_range, interval, s.http_timeout)
        if result.get("candles"):
            cache_set(s.cache_dir, key, result)
        return result

    # 현재가 --------------------------------------------------------------
    def quote(self, symbol: str) -> dict:
        s = self.settings
        if s.market_data_source == "mock":
            # 일봉과 같은 기간으로 만들어 캔들·현재가가 같은 시계열을 보게 한다.
            candles = mock_candles(symbol, s.candle_period, "1d")["candles"]
            return {"symbol": symbol, "price": candles[-1]["close"], "source": "mock"}
        if s.market_data_source == "kis" and self.kis and is_krx(symbol):
            try:
                return self.kis.quote(symbol)
            except Exception as exc:
                logger.warning("KIS 현재가 조회 실패 %s: %s — Yahoo 폴백", symbol, exc)
        return yahoo_quote(symbol, s.http_timeout)


# ── 장 운영시간 ─────────────────────────────────────────────────────────
def is_krx_market_open(now: datetime | None = None) -> bool:
    """KRX 정규장(평일 09:00~15:30 KST). 휴장일은 보지 않는다 — 실주문은 브로커가 거부한다."""
    n = (now or datetime.now(KST)).astimezone(KST)
    if n.weekday() >= 5:
        return False
    return "0900" <= n.strftime("%H%M") <= "1530"
