"""주가 데이터 서비스: Yahoo Finance API 기반."""
import time
import asyncio
import httpx

from app.config import settings
import logging
logger = logging.getLogger(__name__)
from datetime import datetime, timezone
from typing import Any

from app.services.data_cache import cache_get, cache_set, cache_info

YAHOO_CHART = "https://query2.finance.yahoo.com/v8/finance/chart"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; FinAgent/1.0)"}

# 국내 상장사 퀀트 스크리닝 대상 유니버스 — 3개 섹터(반도체 · IT · K뷰티) 31종목 (2026-10-06 조정).
# 실제 추천/자동매매/스크리닝은 이 유니버스 전체를 스캔해 데이터 기반으로 종목을 선정한다 —
# 화면에 노출되는 개별 종목은 매 요청마다 계산된 스코어에 따라 달라질 수 있다.
# 심볼은 Yahoo 표기(.KS=코스피, .KQ=코스닥). 브로커/게이트웨이는 접미사를 떼고 KRX 6자리 코드로 보낸다.
QUANT_SECTORS = ("반도체", "IT", "K뷰티")
QUANT_STOCKS = [
    # ── 반도체 (12) ──
    {"symbol": "005930.KS", "name": "삼성전자", "sector": "반도체"},
    {"symbol": "000660.KS", "name": "SK하이닉스", "sector": "반도체"},
    {"symbol": "042700.KS", "name": "한미반도체", "sector": "반도체"},
    {"symbol": "000990.KS", "name": "DB하이텍", "sector": "반도체"},
    {"symbol": "108320.KS", "name": "LX세미콘", "sector": "반도체"},
    {"symbol": "014680.KS", "name": "한솔케미칼", "sector": "반도체"},
    {"symbol": "058470.KQ", "name": "리노공업", "sector": "반도체"},
    {"symbol": "039030.KQ", "name": "이오테크닉스", "sector": "반도체"},
    {"symbol": "403870.KQ", "name": "HPSP", "sector": "반도체"},
    {"symbol": "240810.KQ", "name": "원익IPS", "sector": "반도체"},
    {"symbol": "036930.KQ", "name": "주성엔지니어링", "sector": "반도체"},
    {"symbol": "357780.KQ", "name": "솔브레인", "sector": "반도체"},
    # ── IT (10) ──
    {"symbol": "035420.KS", "name": "NAVER", "sector": "IT"},
    {"symbol": "035720.KS", "name": "카카오", "sector": "IT"},
    {"symbol": "018260.KS", "name": "삼성에스디에스", "sector": "IT"},
    {"symbol": "064400.KS", "name": "LG CNS", "sector": "IT"},
    {"symbol": "259960.KS", "name": "크래프톤", "sector": "IT"},
    {"symbol": "036570.KS", "name": "엔씨소프트", "sector": "IT"},
    {"symbol": "307950.KS", "name": "현대오토에버", "sector": "IT"},
    {"symbol": "012510.KQ", "name": "더존비즈온", "sector": "IT"},
    {"symbol": "293490.KQ", "name": "카카오게임즈", "sector": "IT"},
    {"symbol": "263750.KQ", "name": "펄어비스", "sector": "IT"},
    # ── K뷰티 (9) ──
    {"symbol": "090430.KS", "name": "아모레퍼시픽", "sector": "K뷰티"},
    {"symbol": "051900.KS", "name": "LG생활건강", "sector": "K뷰티"},
    {"symbol": "192820.KS", "name": "코스맥스", "sector": "K뷰티"},
    {"symbol": "161890.KS", "name": "한국콜마", "sector": "K뷰티"},
    {"symbol": "278470.KS", "name": "에이피알", "sector": "K뷰티"},
    {"symbol": "257720.KQ", "name": "실리콘투", "sector": "K뷰티"},
    {"symbol": "237880.KQ", "name": "클리오", "sector": "K뷰티"},
    {"symbol": "018290.KQ", "name": "브이티", "sector": "K뷰티"},
    {"symbol": "241710.KQ", "name": "코스메카코리아", "sector": "K뷰티"},
]

MARKET_INDICES = [
    {"symbol": "^KS11", "name": "KOSPI"},
    {"symbol": "^KQ11", "name": "KOSDAQ"},
    {"symbol": "KRW=X", "name": "USD/KRW"},
]


async def _yahoo_chart(symbol: str, interval: str, range_: str) -> dict | None:
    url = f"{YAHOO_CHART}/{symbol}"
    params = {"interval": interval, "range": range_}
    async with httpx.AsyncClient(timeout=15.0, headers=HEADERS) as client:
        try:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            result = data.get("chart", {}).get("result", [])
            return result[0] if result else None
        except Exception:
            return None


async def get_quote(symbol: str) -> dict:
    """현재 주가 정보. 국내 종목은 MARKET_DATA_SOURCE=kis 면 KIS(st 게이트웨이) 우선, 실패 시 Yahoo."""
    from app.services import kis_market_data as kmd
    if kmd.is_enabled() and kmd.is_krx(symbol):
        try:
            q = await kmd.get_quote(symbol)
            if q.get("price"):
                return q
        except Exception as exc:
            logger.warning("KIS 현재가 조회 실패 %s: %s%s", symbol, exc, " — Yahoo 폴백" if settings.MARKET_DATA_FALLBACK_YAHOO else "")
            if not settings.MARKET_DATA_FALLBACK_YAHOO:
                return {"symbol": symbol, "error": str(exc)}
    data = await _yahoo_chart(symbol, "1d", "1d")
    if not data:
        return {"symbol": symbol, "error": "데이터 없음"}

    meta = data.get("meta", {})
    return {
        "symbol": symbol,
        "name": meta.get("longName") or meta.get("shortName") or symbol,
        "price": meta.get("regularMarketPrice"),
        "prev_close": meta.get("previousClose") or meta.get("chartPreviousClose"),
        "change": None,
        "change_pct": None,
        "currency": meta.get("currency", "KRW"),
        "market": meta.get("exchangeName"),
    }


_yahoo_session: dict = {"cookies": None, "crumb": None, "ts": 0.0}
_CRUMB_TTL_SEC = 1800
_yahoo_session_lock = asyncio.Lock()


async def _get_yahoo_crumb(rejected_crumb: str | None = None) -> tuple[dict, str] | None:
    """Share a validated cookie/crumb pair. Refresh a rejected pair only once."""
    async with _yahoo_session_lock:
        now = time.time()
        if (_yahoo_session["crumb"] and now - _yahoo_session["ts"] < _CRUMB_TTL_SEC
                and _yahoo_session["crumb"] != rejected_crumb):
            return _yahoo_session["cookies"], _yahoo_session["crumb"]
        try:
            async with httpx.AsyncClient(timeout=10.0, headers=HEADERS) as client:
                # fc.yahoo.com can return 404 while still issuing the needed cookie.
                await client.get("https://fc.yahoo.com")
                resp = await client.get("https://query2.finance.yahoo.com/v1/test/getcrumb", cookies=client.cookies)
                resp.raise_for_status()
                crumb = resp.text.strip()
                cookies = dict(client.cookies)
            if not crumb or any(c.isspace() for c in crumb) or "<" in crumb or not cookies:
                return None
            _yahoo_session.update({"cookies": cookies, "crumb": crumb, "ts": now})
            return cookies, crumb
        except (httpx.HTTPError, ValueError):
            return None


async def _fundamentals_fallback(symbol: str, cache_key: str, reason: str) -> dict:
    """Clearly label older real data or quote-only results; never invent financials."""
    old = await cache_get(cache_key, max_age_hours=168)
    if old and not old.get("error"):
        info = await cache_info(cache_key)
        return {**old, "data_status": "stale", "cached_at": (info or {}).get("updated_at"),
                "warning": f"재무 데이터 갱신 실패 ({reason}). 최대 7일 이내의 이전 조회 자료입니다."}
    quote = await _yahoo_chart(symbol, "1d", "1d")
    meta = (quote or {}).get("meta", {})
    price = meta.get("regularMarketPrice")
    name = next((c["name"] for c in QUANT_STOCKS if c["symbol"] == symbol), symbol)
    return {"symbol": symbol, "name": name, "price": price,
            "quarters": [], "revenue": [], "op": [], "net": [],
            "source": "Yahoo Finance chart" if price is not None else None,
            "data_status": "partial" if price is not None else "unavailable",
            "warning": f"재무 데이터 조회 실패 ({reason}). " + ("가격만 제공하며 재무 지표는 N/A입니다." if price is not None else "현재 제공할 자료가 없습니다. 다시 조회하세요.")}


def _raw(mod: dict | None, key: str) -> Any:
    """Yahoo quoteSummary 필드는 대부분 {"raw": ..., "fmt": ...} 형태."""
    if not mod:
        return None
    v = mod.get(key)
    if isinstance(v, dict):
        return v.get("raw")
    return v


async def get_fundamentals(symbol: str) -> dict:
    """Yahoo Finance quoteSummary 기반 실제 기업 펀더멘털 (PER/PBR/ROE/분기실적 등).

    일부 필드(특히 재무상태표 총자산/부채 상세)는 Yahoo가 국내 상장사에 대해
    제공하지 않는 경우가 있어 해당 값은 null로 반환한다 — 프론트엔드가 '데이터 없음'
    으로 표시하며, 값을 지어내지 않는다.
    """
    cache_key = f"fundamentals:v2:{symbol}"
    cached = await cache_get(cache_key, max_age_hours=6)
    if cached is not None:
        return cached

    session = await _get_yahoo_crumb()
    modules = "price,summaryDetail,defaultKeyStatistics,financialData,incomeStatementHistoryQuarterly,balanceSheetHistory"
    reason = "인증 세션 발급 실패"
    if not session:
        return await _fundamentals_fallback(symbol, cache_key, reason)
    r = None
    for attempt in range(2):
        cookies, crumb = session
        try:
            async with httpx.AsyncClient(timeout=12.0, headers=HEADERS, cookies=cookies) as client:
                resp = await client.get(
                    f"https://query{1 if attempt == 0 else 2}.finance.yahoo.com/v10/finance/quoteSummary/{symbol}",
                    params={"modules": modules, "crumb": crumb},
                )
                resp.raise_for_status()
                data = resp.json()
            result = data.get("quoteSummary", {}).get("result")
            if not result:
                reason = "외부 제공 자료 없음"
                break
            r = result[0]
            break
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            reason = f"외부 HTTP {status}"
            if status in (401, 403) and attempt == 0:
                session = await _get_yahoo_crumb(rejected_crumb=crumb)
                if not session:
                    break
            elif status not in (429, 500, 502, 503, 504):
                break
        except (httpx.RequestError, ValueError):
            reason = "외부 연결 지연 또는 응답 오류"
    if r is None:
        # Do not forward upstream URLs or cookies/crumbs in client errors.
        return await _fundamentals_fallback(symbol, cache_key, reason)

    price_mod = r.get("price", {})
    summary = r.get("summaryDetail", {})
    stats = r.get("defaultKeyStatistics", {})
    fin = r.get("financialData", {})

    def _pct(mod: dict, key: str) -> float | None:
        v = _raw(mod, key)
        return round(v * 100, 2) if v is not None else None

    def _eok(v: float | None) -> float | None:
        """원 단위 -> 억원 단위 (기존 목업과 동일한 스케일)."""
        return round(v / 1e8, 0) if v is not None else None

    quarters, revenue, op, net = [], [], [], []
    inc_history = list(reversed(r.get("incomeStatementHistoryQuarterly", {}).get("incomeStatementHistory", [])))
    for q in inc_history:
        end = q.get("endDate", {}).get("fmt", "")
        quarters.append(f"{end[2:4]}Q{(int(end[5:7]) - 1) // 3 + 1}" if end else "")
        revenue.append(_eok(_raw(q, "totalRevenue")))
        op.append(_eok(_raw(q, "operatingIncome")))
        net.append(_eok(_raw(q, "netIncome")))

    bs_list = r.get("balanceSheetHistory", {}).get("balanceSheetStatements", [])
    bs = bs_list[0] if bs_list else {}

    fundamentals = {
        "symbol": symbol,
        "data_status": "complete",
        "source": "Yahoo Finance quoteSummary",
        "name": price_mod.get("longName") or price_mod.get("shortName") or symbol,
        "price": _raw(price_mod, "regularMarketPrice"),
        "chg": _pct(price_mod, "regularMarketChangePercent"),
        "cap": _eok(_raw(price_mod, "marketCap") or _raw(summary, "marketCap")),
        # PER 은 실적(trailing) 우선, 없으면 전망(forward). 어느 쪽인지 perBasis 로 알려 준다 —
        # 둘을 같은 'PER' 로 섞어 보여 주면 투자 판단에서 의미가 달라진다.
        "per": _raw(summary, "trailingPE") or _raw(stats, "forwardPE"),
        "perBasis": ("trailing" if _raw(summary, "trailingPE") is not None
                     else "forward" if _raw(stats, "forwardPE") is not None else None),
        "pbr": _raw(stats, "priceToBook"),
        "eps": _raw(stats, "trailingEps"),
        "bps": _raw(stats, "bookValue"),
        "roe": _pct(fin, "returnOnEquity"),
        "roa": _pct(fin, "returnOnAssets"),
        "debt": round(_raw(fin, "debtToEquity"), 1) if _raw(fin, "debtToEquity") is not None else None,
        "div": _raw(summary, "dividendRate"),
        "divYield": _pct(summary, "dividendYield"),
        "opMargin": _pct(fin, "operatingMargins"),
        "forwardPer": _raw(stats, "forwardPE"),
        "psr": _raw(summary, "priceToSalesTrailing12Months"),
        "evEbitda": _raw(stats, "enterpriseToEbitda"),
        "grossMargin": _pct(fin, "grossMargins"),
        "netMargin": _pct(fin, "profitMargins"),
        "revenueGrowth": _pct(fin, "revenueGrowth"),
        "earningsGrowth": _pct(fin, "earningsGrowth"),
        "payoutRatio": _pct(summary, "payoutRatio"),
        "currentRatio": _raw(fin, "currentRatio"),
        "quickRatio": _raw(fin, "quickRatio"),
        "totalRevenue": _eok(_raw(fin, "totalRevenue")),
        "totalCash": _eok(_raw(fin, "totalCash")),
        "totalDebt": _eok(_raw(fin, "totalDebt")),
        "operatingCashflow": _eok(_raw(fin, "operatingCashflow")),
        "freeCashflow": _eok(_raw(fin, "freeCashflow")),
        "revenue": revenue,
        "op": op,
        "net": net,
        "quarters": quarters,
        "assets": _eok(_raw(bs, "totalAssets")),
        "equity": _eok(_raw(bs, "totalStockholderEquity")),
        "liabilities": _eok(_raw(bs, "totalLiab")),
        "cash": _eok(_raw(bs, "cash")),
    }
    await cache_set(cache_key, fundamentals)
    return fundamentals


async def get_candles(symbol: str, period: str = "1y", interval: str = "1d", max_age_hours: float = 6) -> dict:
    """캔들 차트 데이터 (OHLCV). 반복 스캔 시 Yahoo 호출을 줄이기 위해 캐시를 우선 사용한다.
    max_age_hours: 캐시 허용 나이. 분봉(공격 모드)은 사이클보다 짧게 준다."""
    from app.services import kis_market_data as kmd
    use_kis = kmd.is_enabled() and kmd.is_krx(symbol) and interval == "1d"
    cache_key = f"candles:{'kis:' if use_kis else ''}{symbol}:{period}:{interval}"
    cached = await cache_get(cache_key, max_age_hours=max_age_hours)
    if cached is not None:
        return cached

    if use_kis:
        try:
            result = await kmd.get_daily_candles(symbol, period)
            if result.get("candles"):
                result.setdefault("source", "kis")
                await cache_set(cache_key, result)
                return result
            logger.warning("KIS 일봉 빈 응답 %s", symbol)
        except Exception as exc:
            logger.warning("KIS 일봉 조회 실패 %s: %s%s", symbol, exc, " — Yahoo 폴백" if settings.MARKET_DATA_FALLBACK_YAHOO else "")
        if not settings.MARKET_DATA_FALLBACK_YAHOO:
            return {"symbol": symbol, "candles": []}

    # 10년치는 10y range로 요청
    data = await _yahoo_chart(symbol, interval, period)
    if not data:
        return {"symbol": symbol, "candles": []}

    timestamps = data.get("timestamp", [])
    indicators = data.get("indicators", {}).get("quote", [{}])[0]
    opens = indicators.get("open", [])
    highs = indicators.get("high", [])
    lows = indicators.get("low", [])
    closes = indicators.get("close", [])
    volumes = indicators.get("volume", [])

    candles = []
    for i, ts in enumerate(timestamps):
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

    result = {"symbol": symbol, "interval": interval, "period": period, "candles": candles, "source": "yahoo"}
    if candles:
        await cache_set(cache_key, result)
    return result


async def get_market_summary() -> list[dict]:
    """시장 지수 요약."""
    results = []
    for idx in MARKET_INDICES:
        q = await get_quote(idx["symbol"])
        prev = q.get("prev_close")
        price = q.get("price")
        change_pct = None
        if prev and price and prev != 0:
            change_pct = round((price - prev) / prev * 100, 2)
        results.append({
            "symbol": idx["symbol"],
            "name": idx["name"],
            "price": price,
            "change_pct": change_pct,
        })
    return results


# ── 기술적 지표 계산 ──────────────────────────────────────────────────
def _calc_rsi(closes: list[float], period: int = 14) -> list[float | None]:
    rsi = [None] * len(closes)
    if len(closes) < period + 1:
        return rsi
    gains, losses = [], []
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    for i in range(period, len(closes)):
        if i > period:
            diff = closes[i] - closes[i - 1]
            avg_gain = (avg_gain * (period - 1) + max(diff, 0)) / period
            avg_loss = (avg_loss * (period - 1) + max(-diff, 0)) / period
        rs = avg_gain / avg_loss if avg_loss > 0 else 100
        rsi[i] = round(100 - (100 / (1 + rs)), 2)
    return rsi


def _calc_sma(closes: list[float], period: int) -> list[float | None]:
    result = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        result[i] = round(sum(closes[i - period + 1:i + 1]) / period, 2)
    return result


def _calc_bollinger(closes: list[float], period: int = 20, std_mult: float = 2.0):
    upper, lower, mid = [None] * len(closes), [None] * len(closes), [None] * len(closes)
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1:i + 1]
        m = sum(window) / period
        std = (sum((x - m) ** 2 for x in window) / period) ** 0.5
        mid[i] = round(m, 2)
        upper[i] = round(m + std_mult * std, 2)
        lower[i] = round(m - std_mult * std, 2)
    return upper, mid, lower


async def get_quant_indicators(symbol: str, period: str = "2y") -> dict:
    """기술적 지표 + AI 매매 시그널."""
    data = await get_candles(symbol, period=period)
    candles = data.get("candles", [])
    if len(candles) < 20:
        return {"symbol": symbol, "error": "데이터 부족"}

    closes = [c["close"] for c in candles if c["close"] is not None]
    times = [c["time"] for c in candles if c["close"] is not None]

    rsi = _calc_rsi(closes)
    ma5 = _calc_sma(closes, 5)
    ma20 = _calc_sma(closes, 20)
    ma60 = _calc_sma(closes, 60)
    bb_upper, bb_mid, bb_lower = _calc_bollinger(closes)

    # 매매 시그널 (규칙 기반 + AI 해석)
    signal = _generate_signal(closes, rsi, ma5, ma20, ma60, bb_upper, bb_lower)

    return {
        "symbol": symbol,
        "times": times[-100:],
        "closes": closes[-100:],
        "rsi": rsi[-100:],
        "ma5": ma5[-100:],
        "ma20": ma20[-100:],
        "ma60": ma60[-100:],
        "bb_upper": bb_upper[-100:],
        "bb_mid": bb_mid[-100:],
        "bb_lower": bb_lower[-100:],
        "signal": signal,
        "current_price": closes[-1],
        "current_rsi": rsi[-1],
        # 판단 근거의 데이터 출처 (의사결정 화면 「판단 근거」 카드에 그대로 표시된다)
        "interval": "1d",
        "bars": len(closes),
        "as_of": times[-1] if times else None,
        "source": data.get("source"),
    }


def _generate_signal(closes, rsi, ma5, ma20, ma60, bb_upper, bb_lower) -> dict:
    """규칙 기반 매매 시그널 생성."""
    n = len(closes) - 1
    signals = []
    score = 0  # + = 매수, - = 매도

    # RSI 시그널
    if rsi[n] is not None:
        if rsi[n] < 30:
            signals.append("RSI 과매도 (매수 신호)")
            score += 2
        elif rsi[n] > 70:
            signals.append("RSI 과매수 (매도 신호)")
            score -= 2
        else:
            signals.append(f"RSI 중립 ({rsi[n]:.1f})")

    # 이동평균 골든/데드크로스
    if ma5[n] and ma20[n] and ma5[n-1] and ma20[n-1]:
        if ma5[n] > ma20[n] and ma5[n-1] <= ma20[n-1]:
            signals.append("골든크로스 (강력 매수)")
            score += 3
        elif ma5[n] < ma20[n] and ma5[n-1] >= ma20[n-1]:
            signals.append("데드크로스 (강력 매도)")
            score -= 3
        elif ma5[n] > ma20[n]:
            signals.append("단기 이평 > 중기 이평 (매수 우위)")
            score += 1
        else:
            signals.append("단기 이평 < 중기 이평 (매도 우위)")
            score -= 1

    # 볼린저밴드
    if bb_upper[n] and bb_lower[n]:
        if closes[n] < bb_lower[n]:
            signals.append("볼린저 하단 이탈 (반등 가능)")
            score += 1
        elif closes[n] > bb_upper[n]:
            signals.append("볼린저 상단 돌파 (조정 가능)")
            score -= 1

    # 최종 판단
    if score >= 3:
        action, color = "강력 매수", "green"
    elif score >= 1:
        action, color = "매수", "lightgreen"
    elif score <= -3:
        action, color = "강력 매도", "red"
    elif score <= -1:
        action, color = "매도", "salmon"
    else:
        action, color = "관망", "gray"

    return {"action": action, "color": color, "score": score, "reasons": signals}
