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

# Master lists mutate in place so existing module imports stay synchronized.
from app.services.korea_universe import STOCKS as QUANT_STOCKS, SECTORS as QUANT_SECTORS

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
    # 가격만 있는 폴백에서도 통화를 함께 준다 — 없으면 프런트가 '원' 으로 오표기한다.
    cur = str(meta.get("currency") or "").upper() or None
    return {"symbol": symbol, "name": name, "price": price,
            "currency": cur, "priceUnit": PRICE_UNIT.get(cur, cur or ""),
            "capUnit": "억원" if cur == "KRW" else (f"백만 {cur}" if cur else "단위 미확인"),
            "amountUnit": "억원" if cur == "KRW" else (f"백만 {cur}" if cur else "단위 미확인"),
            "quarters": [], "revenue": [], "op": [], "net": [],
            "source": "Yahoo Finance chart" if price is not None else None,
            "data_status": "partial" if price is not None else "unavailable",
            "warning": f"재무 데이터 조회 실패 ({reason}). " + ("가격만 제공하며 재무 지표는 N/A입니다." if price is not None else "현재 제공할 자료가 없습니다. 다시 조회하세요.")}


# 주당 금액·시세에 붙이는 통화 단위. 없는 통화는 ISO 코드를 그대로 쓴다.
PRICE_UNIT = {"KRW": "원", "USD": "달러", "JPY": "엔", "EUR": "유로", "CNY": "위안",
              "HKD": "홍콩달러", "GBP": "파운드", "TWD": "대만달러", "VND": "동"}
# 큰 단위 표기(시가총액 등)에 쓰는 '조' 환산 제수 — 억원은 1e4, 백만 단위는 1e6 를 곱해 조가 된다.


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
    # v4: 통화 필드(currency/priceUnit/...)와 DART 블록(assets/dartOpSeries/...)이 추가돼
    # 예전 캐시는 그 값이 없다. 키를 올려 구버전 캐시가 그대로 표시되는 일을 막는다.
    cache_key = f"fundamentals:v6:{symbol}"   # v6: DART 계산 ROE·ROA·이익률·부채비율·배당(profitabilitySource/dividendSource) 추가(6-57)
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

    # ── 통화 ────────────────────────────────────────────────────────────────
    # 금액을 통화와 무관하게 1e8 로 나눠 '억원' 으로 쓰면 해외 종목이 전부 틀어진다.
    # (2026-10-12 확인: AAPL totalRevenue 109,417,000,000 USD → 1,094 "억원" 으로 표시돼
    #  실제 152조원 규모가 1,094억원으로 보였다. 종목 검색은 해외 종목을 의도적으로 지원한다.)
    # 시세·주당 지표는 price.currency, 재무제표는 financialData.financialCurrency 를 따른다
    # (ADR 처럼 둘이 다를 수 있다). 환율로 원화 환산하지 않는다 — 환율 출처가 없고
    # 지어낸 환산은 이 화면의 원칙에 어긋난다. 대신 단위를 정확히 알려 준다.
    quote_cur = str(price_mod.get("currency") or "").upper() or None
    fin_cur = str(fin.get("financialCurrency") or price_mod.get("financialCurrency") or quote_cur or "").upper() or None

    def _scale(v: float | None, currency: str | None) -> float | None:
        """재무 금액을 통화에 맞는 단위로 — 원화는 억원, 그 외는 백만 단위."""
        if v is None:
            return None
        return round(v / 1e8, 0) if currency == "KRW" else round(v / 1e6, 2)

    def _unit(currency: str | None) -> str:
        return "억원" if currency == "KRW" else f"백만 {currency}" if currency else "단위 미확인"

    def _eok(v: float | None) -> float | None:
        """재무제표 금액 — financialCurrency 기준 단위."""
        return _scale(v, fin_cur)

    quarters, revenue, op, net = [], [], [], []
    inc_history = list(reversed(r.get("incomeStatementHistoryQuarterly", {}).get("incomeStatementHistory", [])))
    for q in inc_history:
        end = q.get("endDate", {}).get("fmt", "")
        quarters.append(f"{end[2:4]}Q{(int(end[5:7]) - 1) // 3 + 1}" if end else "")
        revenue.append(_eok(_raw(q, "totalRevenue")))
        op.append(_eok(_raw(q, "operatingIncome")))
        net.append(_eok(_raw(q, "netIncome")))

    # Yahoo 는 국내 소형주의 분기 순이익을 0 으로, 영업이익을 빈 값으로 보낸다(2026-10-14 아모텍 실측: net=[0,0,0,0], op=[null]*4).
    # 네 분기 모두 0 이면서 영업이익이 전부 없는 조합은 '자료 없음' 이지 '0원' 이 아니다 → N/A 로 돌린다.
    if net and all(v == 0 for v in net) and all(v is None for v in op):
        net = [None] * len(net)

    bs_list = r.get("balanceSheetHistory", {}).get("balanceSheetStatements", [])
    bs = bs_list[0] if bs_list else {}

    fundamentals = {
        "symbol": symbol,
        "data_status": "complete",
        "source": "Yahoo Finance quoteSummary",
        "name": price_mod.get("longName") or price_mod.get("shortName") or symbol,
        # 프런트가 라벨을 이 값으로 붙인다 — '원/억원' 을 하드코딩하면 해외 종목이 틀어진다.
        "currency": quote_cur,
        "financialCurrency": fin_cur,
        "priceUnit": PRICE_UNIT.get(quote_cur, quote_cur or ""),
        "capUnit": _unit(quote_cur),
        "amountUnit": _unit(fin_cur),
        "price": _raw(price_mod, "regularMarketPrice"),
        "chg": _pct(price_mod, "regularMarketChangePercent"),
        "cap": _scale(_raw(price_mod, "marketCap") or _raw(summary, "marketCap"), quote_cur),
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
    # ── DART 덮어쓰기 ───────────────────────────────────────────────────────
    # Yahoo 가 비워 보내는 두 블록(영업이익·재무상태표)만 국내 상장사에 한해 DART 원문으로 채운다.
    # 키가 없거나 해외 종목이면 아무것도 바뀌지 않는다. 금액 단위는 원이므로 _eok 로 맞춘다.
    await _overlay_dart(fundamentals, symbol, _eok, fin_cur)

    await cache_set(cache_key, fundamentals)
    return fundamentals


def _apply_dart_valuation(fundamentals: dict, ps: dict | None) -> None:
    """EPS·BPS·PER·PBR 은 DART 계산값을 **우선**하고 Yahoo 값은 폴백으로만 쓴다(6-52, 사용자 결정).
    Yahoo 값은 yahooValuation 에 보존해 화면이 '어느 값을 썼는지' 밝힐 수 있게 한다."""
    yahoo = {k: fundamentals.get(k) for k in ("eps", "bps", "per", "pbr")}
    fundamentals["yahooValuation"] = {**yahoo, "perBasis": fundamentals.get("perBasis")}
    price = fundamentals.get("price")
    if not ps or (ps.get("eps") is None and ps.get("bps") is None):
        fundamentals["valuationSource"] = ("Yahoo Finance quoteSummary" if any(v is not None for v in yahoo.values()) else None)
        fundamentals["valuationNotes"] = list((ps or {}).get("notes") or [])
        return
    eps, bps = ps.get("eps"), ps.get("bps")
    per = round(float(price) / eps, 2) if price and eps and eps > 0 else None
    pbr = round(float(price) / bps, 2) if price and bps and bps > 0 else None
    notes = list(ps.get("notes") or [])
    if eps is not None and eps <= 0:
        notes.append("순이익이 0 이하(적자)라 PER 을 계산하지 않습니다")
    if ps.get("isPreferred"):
        notes.append(f"우선주: 보통주({ps.get('stockCode')}) 기준 회사 실적 ÷ 전체 주식수로 계산한 값이라 보통주 PER·PBR 과 의미가 다릅니다")
    # 항목별로 DART 값이 있으면 DART(PER·PBR 도 DART 기준으로 다시 계산), 없으면 그 항목만 Yahoo 폴백
    fundamentals.update({
        "eps": eps if eps is not None else yahoo["eps"],
        "per": per if eps is not None else yahoo["per"],      # DART EPS 가 적자면 per=None(의도)
        "bps": bps if bps is not None else yahoo["bps"],
        "pbr": pbr if bps is not None else yahoo["pbr"],
        "perBasis": "dart_ttm" if eps is not None else fundamentals.get("perBasis"),
        "valuationSource": f"DART 계산 · {ps.get('period')} {ps.get('fsDiv') or ''} · EPS {ps.get('epsBasis')} ÷ 발행주식수 · BPS {ps.get('bpsBasis')} ÷ 발행주식수",
        "valuationShares": ps.get("sharesBasis"), "valuationNotes": notes, "isPreferred": bool(ps.get("isPreferred")),
    })


async def _overlay_dart(fundamentals: dict, symbol: str, eok, fin_cur: str | None) -> None:
    """DART 재무제표를 fundamentals 에 덮어쓴다. 실패는 조용히 무시한다(화면을 막지 않는다)."""
    from app.services import dart
    if not dart.is_enabled() or not dart.is_krx(symbol) or fin_cur not in (None, "KRW"):
        return
    try:
        d = await dart.latest_financials(symbol)
    except Exception as exc:                      # 외부 의존이므로 화면을 막지 않는다
        logger.warning("DART 보강 실패 %s: %s", symbol, exc)
        return
    # 주당 지표는 보고서 유무와 별개로 시도한다(실패해도 Yahoo 폴백)
    try:
        ps = await dart.per_share_metrics(symbol)
    except Exception as exc:
        logger.warning("DART 주당 지표 실패 %s: %s", symbol, exc)
        ps = None
    _apply_dart_valuation(fundamentals, ps)
    # 배당은 재무제표 유무와 별개(사업보고서 alotMatter)라 먼저 시도한다.
    try:
        dv = await dart.dividend_info(symbol)
    except Exception as exc:
        logger.warning("DART 배당 실패 %s: %s", symbol, exc)
        dv = None
    _apply_dart_dividend(fundamentals, dv)
    if not d:
        _apply_dart_profitability(fundamentals, None)
        return
    period = f"{d['year']} {d['reportName']}"
    pref = f" · 보통주({d.get('stockCode')}) 기준 회사 재무제표" if d.get("isPreferred") else ""
    if d.get("assets") is not None:
        fundamentals.update({
            "assets": eok(d.get("assets")), "equity": eok(d.get("equity")),
            "liabilities": eok(d.get("liabilities")), "cash": eok(d.get("cash")),
            "balanceSource": f"DART {d['fsDiv']} · {period}{pref}",
        })
    if d.get("operatingIncome") is not None:
        fundamentals.update({
            "dartOperatingIncome": eok(d.get("operatingIncome")),
            "opSource": f"DART {d['fsDiv']} · {period}"
                       + (f" · {d['opBasis']}" if d.get("opBasis") else "") + pref,
        })
    # 분기 영업이익 시계열 — Yahoo 가 비워 보내는 op 행을 DART 3개월 금액으로 채운다.
    try:
        series = await dart.quarterly_operating_income(symbol)
    except Exception as exc:
        logger.warning("DART 분기 영업이익 실패 %s: %s", symbol, exc)
        series = None
    if series:
        fundamentals["dartOpSeries"] = [
            {**row, "operatingIncome": eok(row.get("operatingIncome"))} for row in series
        ]
    fundamentals["dart"] = {
        "period": period, "fsDiv": d["fsDiv"], "corpCode": d["corpCode"],
        "source": d["source"], "opBasis": d.get("opBasis"),
    }
    # 수익성 지표·배당 — Yahoo 가 비운 항목만 DART 계산값으로 채운다(항목 단위 폴백).
    try:
        pm = await dart.profitability_metrics(symbol)
    except Exception as exc:
        logger.warning("DART 수익성 지표 실패 %s: %s", symbol, exc)
        pm = None
    _apply_dart_profitability(fundamentals, pm, pref)


_PROFIT_KEYS = {"roe": "roe", "roa": "roa", "opMargin": "opMargin", "netMargin": "netMargin", "debt": "debtRatio"}


def _apply_dart_profitability(fundamentals: dict, pm: dict | None, pref: str = "") -> None:
    """ROE·ROA·영업이익률·순이익률·부채비율: Yahoo 값이 없으면 DART 계산값. 어느 항목을 어디서 받았는지 profitabilityBasis 에 남긴다.
    Yahoo 의 debt(debtToEquity)는 '총차입금 ÷ 자본' 이고 DART 부채비율은 '부채총계 ÷ 자본총계' 라 정의가 다르다 → 기준에 밝힌다."""
    yahoo = {k: fundamentals.get(k) for k in _PROFIT_KEYS}
    fundamentals["yahooProfitability"] = yahoo
    basis: dict[str, str] = {k: "Yahoo Finance financialData" + (" · 총차입금 ÷ 자본(%)" if k == "debt" else "")
                             for k, v in yahoo.items() if v is not None}
    filled: list[str] = []
    if pm:
        for key, src in _PROFIT_KEYS.items():
            if yahoo.get(key) is None and pm.get(src) is not None:
                fundamentals[key] = pm[src]
                basis[key] = f"DART 계산 · {pm['bases'].get(src, '')}"
                filled.append(key)
    fundamentals["profitabilityBasis"] = basis
    fundamentals["profitabilityNotes"] = list((pm or {}).get("notes") or [])
    fundamentals["profitabilitySource"] = (f"DART 계산 · {pm.get('period')} {pm.get('fsDiv') or ''}{pref}" if filled else
                                           "Yahoo Finance financialData" if any(v is not None for v in yahoo.values()) else None)
    fundamentals["profitabilityFilled"] = filled


def _apply_dart_dividend(fundamentals: dict, dv: dict | None) -> None:
    """DPS·배당수익률: Yahoo 가 없으면 DART 사업보고서 연간 배당. 수익률은 DPS ÷ 현재가(공시 수익률은 결산일 주가 기준이라 참고만)."""
    fundamentals["yahooDividend"] = {"div": fundamentals.get("div"), "divYield": fundamentals.get("divYield")}
    if fundamentals.get("div") is not None or fundamentals.get("divYield") is not None:
        fundamentals["dividendSource"] = "Yahoo Finance summaryDetail"
        return
    if not dv or dv.get("dps") is None:
        fundamentals["dividendSource"] = None
        return
    price = fundamentals.get("price")
    dps = float(dv["dps"])
    fundamentals["div"] = dps
    fundamentals["divYield"] = round(dps / float(price) * 100, 2) if price and dps > 0 else (0.0 if dps == 0 else None)
    if fundamentals.get("payoutRatio") is None and dv.get("payoutRatio") is not None:
        fundamentals["payoutRatio"] = dv["payoutRatio"]
    fundamentals["divYieldReported"] = dv.get("yieldReported")
    fundamentals["dividendSource"] = (f"DART {dv['year']} {dv['reportName']} · {dv.get('stockKind') or '보통주'} 주당 현금배당금"
                                      + (" · 현금배당 없음" if dv.get("noDividend") else " · 수익률 = DPS ÷ 현재가"))


async def get_candles(symbol: str, period: str = "1y", interval: str = "1d", max_age_hours: float = 6) -> dict:
    """캔들 차트 데이터 (OHLCV). 반복 스캔 시 Yahoo 호출을 줄이기 위해 캐시를 우선 사용한다.
    max_age_hours: 캐시 허용 나이. 분봉(공격 모드)은 사이클보다 짧게 준다."""
    from app.services import kis_market_data as kmd
    use_kis = kmd.is_enabled() and kmd.is_krx(symbol) and interval == "1d"
    cache_key = f"candles:{'kis:' if use_kis else ''}{symbol}:{period}:{interval}"
    cached = await cache_get(cache_key, max_age_hours=max_age_hours) if max_age_hours > 0 else None
    if cached is not None:
        return cached

    if use_kis:
        try:
            result = await kmd.get_daily_candles(symbol, period, refresh=max_age_hours <= 0)
            if result.get("candles"):
                result.setdefault("source", "kis")
                await cache_set(cache_key, result)
                return result
            logger.warning("KIS 일봉 빈 응답 %s", symbol)
        except Exception as exc:
            logger.warning("KIS 일봉 조회 실패 %s: %s%s", symbol, exc, " — Yahoo 폴백" if settings.MARKET_DATA_FALLBACK_YAHOO else "")
        if not settings.MARKET_DATA_FALLBACK_YAHOO:
            return {"symbol": symbol, "candles": []}

    # 일봉: Yahoo 전에 시스템 OHLCV 저장소(stock-coin-trade pg-stock)를 먼저 보고, 없는 구간은 적재한 뒤 쓴다
    if interval == "1d" and settings.OHLCV_STORE_FIRST:
        from app.services import ohlcv_store
        candles, meta = await ohlcv_store.daily_candles(symbol, ohlcv_store.period_start(period), datetime.now(timezone.utc).date())
        if candles:
            result = {"symbol": symbol, "interval": interval, "period": period, "candles": candles,
                      "source": meta["source"], "price_source": meta}
            await cache_set(cache_key, result)
            return result
        logger.warning("OHLCV 저장소·Yahoo 모두 빈 응답 %s — Yahoo range 조회로 폴백", symbol)

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
        if avg_loss == 0:
            rsi[i] = 100.0 if avg_gain > 0 else 50.0
        else:
            rsi[i] = round(100 - (100 / (1 + avg_gain / avg_loss)), 2)
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
    return calculate_quant_indicators(symbol, data)


def calculate_quant_indicators(symbol: str, data: dict) -> dict:
    """Pure calculation shared by the all-market batch and on-demand analysis."""
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
        "signal_revision": 2,
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
    components = []
    score = 0  # + = 매수, - = 매도

    def add(reason: str, points: int) -> None:
        nonlocal score
        signals.append(reason)
        components.append({"reason": reason, "points": points})
        score += points

    # RSI 시그널
    if rsi[n] is not None:
        if rsi[n] < 30:
            add("RSI 과매도 (매수 신호)", 2)
        elif rsi[n] > 70:
            add("RSI 과매수 (매도 신호)", -2)
        else:
            add(f"RSI 중립 ({rsi[n]:.1f})", 0)

    # 이동평균 골든/데드크로스
    if ma5[n] and ma20[n] and ma5[n-1] and ma20[n-1]:
        if ma5[n] > ma20[n] and ma5[n-1] <= ma20[n-1]:
            add("골든크로스 (MA5가 MA20 상향 돌파)", 3)
        elif ma5[n] < ma20[n] and ma5[n-1] >= ma20[n-1]:
            add("데드크로스 (MA5가 MA20 하향 돌파)", -3)
        elif ma5[n] > ma20[n]:
            add("단기 이평 > 중기 이평 (매수 우위)", 1)
        elif ma5[n] < ma20[n]:
            add("단기 이평 < 중기 이평 (매도 우위)", -1)
        else:
            add("단기 이평 = 중기 이평 (중립)", 0)

    # 볼린저밴드
    if bb_upper[n] and bb_lower[n]:
        if closes[n] < bb_lower[n]:
            add("볼린저 하단 이탈 (반등 가능)", 1)
        elif closes[n] > bb_upper[n]:
            add("볼린저 상단 돌파 (조정 가능)", -1)

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

    return {"action": action, "color": color, "score": score, "reasons": signals,
            "components": components, "method": "daily_technical_rules", "strong_buy_threshold": 3}
