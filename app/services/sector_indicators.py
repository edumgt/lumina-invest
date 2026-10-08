"""섹터별 투자 인디케이터 (#company-sector).

QUANT_STOCKS 유니버스(실제 자동매매가 쓰는 종목)를 섹터로 묶어, 투자 판단에 필요한
밸류에이션·수익성·성장·재무안정성·가격모멘텀·시장폭 지표를 계산한다.

원칙
  - 모든 수치는 실제 시장 데이터(Yahoo quoteSummary 펀더멘털 + 일봉)에서만 나온다.
    값이 없으면 None 으로 두고 coverage/missing 에 몇 종목이 빠졌는지 적는다. 지어내지 않는다.
  - 집계 방식을 숨기지 않는다: 배수(PER/PBR/PSR/EV·EBITDA)는 시가총액 가중 조화평균
    (= 섹터를 하나의 기업으로 합친 것과 같은 의미)과 중위값을 함께 낸다. 비율(ROE·마진 등)은
    시가총액 가중 산술평균. 가격 지표는 시총 가중 섹터 인덱스를 만들어 그 시계열에서 계산한다.
  - 점수·판정은 예측이 아니라 '3개 섹터 내 상대 순위'다. 가중치를 SCORE_WEIGHTS 로 공개한다.
"""
from __future__ import annotations

import asyncio
import logging
import statistics
from datetime import datetime, timezone

from app.services.data_cache import cache_get, cache_set
from app.services.stock import (
    QUANT_SECTORS, QUANT_STOCKS, _calc_rsi, _calc_sma, get_candles, get_fundamentals,
)

logger = logging.getLogger(__name__)

CACHE_KEY = "sector_indicators:v1"
CACHE_HOURS = 1.0
BENCHMARK = {"symbol": "^KS11", "name": "KOSPI"}
_CONCURRENCY = 6
_TRADING_DAYS = {"1m": 21, "3m": 63, "6m": 126, "12m": 252}

# 상대 점수 가중치(합 100). 모멘텀을 가장 크게 보되 수익성·성장으로 균형을 잡는다.
SCORE_WEIGHTS = {"momentum": 30, "profitability": 25, "growth": 20, "valuation": 15, "stability": 10}


# ── 집계 도구 ────────────────────────────────────────────────────────────
def _pairs(rows: list[dict], key: str) -> list[tuple[float, float]]:
    """(값, 시가총액) 쌍. 값이나 시총이 없는 종목은 제외한다."""
    out = []
    for r in rows:
        v, cap = r.get(key), r.get("cap")
        if v is None or cap is None or cap <= 0:
            continue
        out.append((float(v), float(cap)))
    return out


def _w_mean(rows: list[dict], key: str) -> float | None:
    """시가총액 가중 산술평균 — 비율 지표(ROE·마진·성장률 등)에 쓴다."""
    p = _pairs(rows, key)
    if not p:
        return None
    tw = sum(c for _, c in p)
    return round(sum(v * c for v, c in p) / tw, 2) if tw else None


def _w_harmonic(rows: list[dict], key: str) -> float | None:
    """시가총액 가중 조화평균 — PER 등 배수에 쓴다(Σcap / Σ(cap/배수) = 합산 기업의 배수).

    0 이하 배수(적자 PER 등)는 의미가 없어 제외한다.
    """
    p = [(v, c) for v, c in _pairs(rows, key) if v > 0]
    if not p:
        return None
    denom = sum(c / v for v, c in p)
    return round(sum(c for _, c in p) / denom, 2) if denom else None


def _median(rows: list[dict], key: str) -> float | None:
    vals = [float(r[key]) for r in rows if r.get(key) is not None]
    return round(statistics.median(vals), 2) if vals else None


def _coverage(rows: list[dict], key: str) -> int:
    return sum(1 for r in rows if r.get(key) is not None)


# ── 가격 시계열 ───────────────────────────────────────────────────────────
def _closes_by_date(candles: list[dict]) -> dict[str, float]:
    out = {}
    for c in candles or []:
        ts, close = c.get("time"), c.get("close")
        if ts is None or close is None:
            continue
        out[datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")] = float(close)
    return out


def _cap_weighted_index(members: list[dict]) -> tuple[list[str], list[float]]:
    """시총 가중 섹터 인덱스. 각 종목을 첫 거래일 100 으로 정규화해 현재 시총 비중으로 합친다.

    비중을 '현재' 시총으로 고정한 근사다(과거 시총 시계열이 없다). 수익률 비교용으로는 충분하지만
    실제 지수 산출과는 다르다 — 화면 각주에 같은 설명을 적는다.
    """
    series = [(m["cap"], m["_closes"]) for m in members if m.get("cap") and m.get("_closes")]
    if not series:
        return [], []
    dates = sorted(set.intersection(*[set(s.keys()) for _, s in series]))
    if len(dates) < 2:
        return [], []
    total = sum(cap for cap, _ in series)
    index = []
    for d in dates:
        index.append(sum((cap / total) * (s[d] / s[dates[0]]) * 100 for cap, s in series))
    return dates, index


def _ret(index: list[float], days: int) -> float | None:
    if len(index) <= days or not index[-1 - days]:
        return None
    return round((index[-1] / index[-1 - days] - 1) * 100, 2)


def _price_metrics(index: list[float]) -> dict:
    if len(index) < 2:
        return {}
    rets = {f"ret_{k}": _ret(index, d) for k, d in _TRADING_DAYS.items()}
    daily = [index[i] / index[i - 1] - 1 for i in range(1, len(index)) if index[i - 1]]
    vol = None
    if len(daily) >= 20:
        vol = round(statistics.pstdev(daily[-20:]) * (252 ** 0.5) * 100, 2)
    lo, hi = min(index[-252:]), max(index[-252:])
    rsi = _calc_rsi(index)
    ma20, ma60 = _calc_sma(index, 20), _calc_sma(index, 60)
    return {
        **rets,
        "vol_20d_annual": vol,
        "pos_52w": round((index[-1] - lo) / (hi - lo) * 100, 1) if hi > lo else None,
        "rsi14": round(rsi[-1], 1) if rsi and rsi[-1] is not None else None,
        "vs_ma20_pct": round((index[-1] / ma20[-1] - 1) * 100, 2) if ma20 and ma20[-1] else None,
        "vs_ma60_pct": round((index[-1] / ma60[-1] - 1) * 100, 2) if ma60 and ma60[-1] else None,
    }


def _breadth(members: list[dict]) -> dict:
    """구성종목 중 추세가 살아 있는 비중 — 섹터 지수 한 줄로는 안 보이는 쏠림을 드러낸다."""
    above20 = [m for m in members if m.get("above_ma20") is not None]
    above60 = [m for m in members if m.get("above_ma60") is not None]
    up1m = [m for m in members if m.get("ret_1m") is not None]
    pct = lambda sel, all_: round(len(sel) / len(all_) * 100, 1) if all_ else None
    return {
        "above_ma20_pct": pct([m for m in above20 if m["above_ma20"]], above20),
        "above_ma60_pct": pct([m for m in above60 if m["above_ma60"]], above60),
        "up_1m_pct": pct([m for m in up1m if m["ret_1m"] > 0], up1m),
        "counted": len(above20),
    }


# ── 종목 1개 수집 ─────────────────────────────────────────────────────────
async def _load_member(stock: dict, sem: asyncio.Semaphore) -> dict:
    sym = stock["symbol"]
    async with sem:
        fund, candles = await asyncio.gather(
            get_fundamentals(sym), get_candles(sym, period="2y"), return_exceptions=True,
        )
    m: dict = {"symbol": sym, "name": stock["name"], "sector": stock["sector"], "errors": []}
    if isinstance(fund, Exception) or not isinstance(fund, dict) or fund.get("error"):
        m["errors"].append(f"펀더멘털: {fund.get('error') if isinstance(fund, dict) else fund}")
    else:
        m.update({k: fund.get(k) for k in (
            "cap", "price", "chg", "per", "perBasis", "forwardPer", "pbr", "psr", "evEbitda", "divYield",
            "roe", "roa", "opMargin", "netMargin", "revenueGrowth", "earningsGrowth",
            "debt", "currentRatio", "freeCashflow", "totalRevenue")})
        m["name"] = stock["name"]   # Yahoo 는 영문명을 주므로 유니버스의 한글명을 쓴다

    closes = _closes_by_date(candles.get("candles") if isinstance(candles, dict) else None)
    if isinstance(candles, Exception) or len(closes) < 2:
        m["errors"].append("일봉 없음")
        return m
    m["_closes"] = closes
    dates = sorted(closes)
    series = [closes[d] for d in dates]
    m["price_as_of"] = dates[-1]
    m["candle_source"] = candles.get("source") if isinstance(candles, dict) else None
    for k, d in _TRADING_DAYS.items():
        m[f"ret_{k}"] = _ret(series, d)
    ma20, ma60 = _calc_sma(series, 20), _calc_sma(series, 60)
    m["above_ma20"] = bool(series[-1] > ma20[-1]) if ma20 and ma20[-1] else None
    m["above_ma60"] = bool(series[-1] > ma60[-1]) if ma60 and ma60[-1] else None
    rsi = _calc_rsi(series)
    m["rsi14"] = round(rsi[-1], 1) if rsi and rsi[-1] is not None else None
    return m


# ── 점수 ─────────────────────────────────────────────────────────────────
def _percentile_rank(value: float | None, pool: list[float], *, lower_is_better: bool = False) -> float | None:
    """같은 지표를 가진 섹터들 안에서의 백분위(0~100). 섹터가 1개면 50 으로 둔다."""
    vals = [v for v in pool if v is not None]
    if value is None or len(vals) < 2:
        return 50.0 if value is not None else None
    below = sum(1 for v in vals if (v > value if lower_is_better else v < value))
    ties = sum(1 for v in vals if v == value)
    return round((below + 0.5 * ties) / len(vals) * 100, 1)


_FACTORS = {
    # 팩터: [(지표 경로, 낮을수록 좋은가)]
    "momentum":      [("price.ret_3m", False), ("price.ret_12m", False), ("relative.excess_3m", False)],
    "profitability": [("profitability.roe", False), ("profitability.op_margin", False)],
    "growth":        [("growth.revenue", False), ("growth.earnings", False)],
    # Yahoo 가 국내 상장사 PBR 을 대부분 주지 않으므로 PSR·EV/EBITDA 까지 넣어 저평가를 본다.
    "valuation":     [("valuation.per", True), ("valuation.pbr", True),
                      ("valuation.psr", True), ("valuation.ev_ebitda", True)],
    "stability":     [("stability.debt_to_equity", True), ("stability.current_ratio", False)],
}


def _dig(d: dict, path: str):
    cur = d
    for part in path.split("."):
        cur = (cur or {}).get(part)
    return cur


def _score_sectors(sectors: list[dict]) -> None:
    for factor, metrics in _FACTORS.items():
        for path, lower in metrics:
            pool = [_dig(s, path) for s in sectors]
            for s in sectors:
                s.setdefault("_ranks", {}).setdefault(factor, []).append(
                    _percentile_rank(_dig(s, path), pool, lower_is_better=lower))
    for s in sectors:
        parts, used = {}, 0.0
        for factor, weight in SCORE_WEIGHTS.items():
            vals = [v for v in s.get("_ranks", {}).get(factor, []) if v is not None]
            if not vals:
                parts[factor] = None
                continue
            parts[factor] = round(sum(vals) / len(vals), 1)
            used += weight
        total = None
        if used:
            total = round(sum(parts[f] * w for f, w in SCORE_WEIGHTS.items() if parts.get(f) is not None) / used, 1)
        s["score"] = {**parts, "total": total, "weights": SCORE_WEIGHTS, "covered_weight": used}
        s["verdict"] = ("비중확대" if total is not None and total >= 60 else
                        "비중축소" if total is not None and total <= 40 else
                        "중립" if total is not None else "판단 불가")
        s.pop("_ranks", None)


# ── 근거 문구 ────────────────────────────────────────────────────────────
def _rationale(s: dict, sectors: list[dict], bench: dict) -> list[str]:
    """모두 계산된 수치를 인용한다 — 수치가 없으면 그 문장을 만들지 않는다."""
    out: list[str] = []
    n = len(sectors)
    rank_of = lambda path, lower=False: (
        sorted([x for x in (_dig(t, path) for t in sectors) if x is not None], reverse=not lower).index(_dig(s, path)) + 1
        if _dig(s, path) is not None else None)

    r3, b3 = _dig(s, "price.ret_3m"), bench.get("ret_3m")
    if r3 is not None and b3 is not None:
        out.append(f"3개월 수익률 {r3:+.2f}% vs KOSPI {b3:+.2f}% → 초과 {r3 - b3:+.2f}%p")
    elif r3 is not None:
        out.append(f"3개월 수익률 {r3:+.2f}%")

    roe = _dig(s, "profitability.roe")
    if roe is not None:
        rk = rank_of("profitability.roe")
        out.append(f"ROE {roe:.2f}% — {n}개 섹터 중 {rk}위" if rk else f"ROE {roe:.2f}%")

    per, per_med = _dig(s, "valuation.per"), _dig(s, "valuation.per_median")
    if per is not None:
        tail = f" (중위 {per_med:.2f}x)" if per_med is not None else ""
        rk = rank_of("valuation.per", lower=True)
        out.append(f"PER {per:.2f}x{tail} — 저평가 {rk}위/{n}" if rk else f"PER {per:.2f}x{tail}")

    g = _dig(s, "growth.revenue")
    if g is not None:
        out.append(f"매출 성장률 {g:+.2f}% (최근 분기, 전년 동기 대비)")

    b = s.get("breadth") or {}
    if b.get("above_ma20_pct") is not None and b.get("counted"):
        cnt = round(b["above_ma20_pct"] / 100 * b["counted"])
        out.append(f"구성 {b['counted']}종목 중 {cnt}종목이 20일선 위 ({b['above_ma20_pct']:.0f}%)")

    pos, vol = _dig(s, "price.pos_52w"), _dig(s, "price.vol_20d_annual")
    if pos is not None:
        out.append(f"52주 레인지 내 위치 {pos:.0f}% (0=최저, 100=최고)")
    if vol is not None:
        out.append(f"20일 변동성(연율) {vol:.1f}%")

    rsi = _dig(s, "price.rsi14")
    if rsi is not None:
        zone = "과매수 구간" if rsi > 70 else "과매도 구간" if rsi < 30 else "중립"
        out.append(f"섹터 지수 RSI(14) {rsi:.1f} — {zone}")

    d2e = _dig(s, "stability.debt_to_equity")
    if d2e is not None:
        out.append(f"부채비율(D/E) {d2e:.1f}%")
    return out


def _per_basis(rows: list[dict]) -> dict:
    """PER 집계에 들어간 종목들이 실적 PER 인지 전망 PER 인지."""
    basis = [r.get("perBasis") for r in rows if r.get("per") is not None]
    return {"trailing": basis.count("trailing"), "forward": basis.count("forward")}


def _holding(m: dict, cap_total: float) -> dict:
    """구성종목 표 한 줄. 키는 프런트에서 쓰는 snake_case 로 맞춘다."""
    return {
        "symbol": m.get("symbol"), "name": m.get("name"),
        "cap": m.get("cap"), "price": m.get("price"), "chg": m.get("chg"),
        "per": m.get("per"), "pbr": m.get("pbr"), "roe": m.get("roe"),
        "op_margin": m.get("opMargin"), "revenue_growth": m.get("revenueGrowth"),
        "debt_to_equity": m.get("debt"),
        "ret_1m": m.get("ret_1m"), "ret_3m": m.get("ret_3m"), "ret_12m": m.get("ret_12m"),
        "rsi14": m.get("rsi14"), "above_ma20": m.get("above_ma20"), "above_ma60": m.get("above_ma60"),
        "weight_pct": round(float(m["cap"]) / cap_total * 100, 2) if m.get("cap") and cap_total else None,
        "errors": m.get("errors") or None,
    }


# ── 메인 ─────────────────────────────────────────────────────────────────
async def sector_overview(*, force: bool = False) -> dict:
    if not force:
        cached = await cache_get(CACHE_KEY, max_age_hours=CACHE_HOURS)
        if cached is not None:
            return {**cached, "from_cache": True}

    sem = asyncio.Semaphore(_CONCURRENCY)
    members = await asyncio.gather(*[_load_member(s, sem) for s in QUANT_STOCKS])

    bench_candles = await get_candles(BENCHMARK["symbol"], period="2y")
    bench_closes = _closes_by_date(bench_candles.get("candles"))
    bench_series = [bench_closes[d] for d in sorted(bench_closes)]
    bench = {**BENCHMARK, **{f"ret_{k}": _ret(bench_series, d) for k, d in _TRADING_DAYS.items()},
             "as_of": sorted(bench_closes)[-1] if bench_closes else None}

    sectors = []
    for name in QUANT_SECTORS:
        rows = [m for m in members if m["sector"] == name]
        priced = [m for m in rows if m.get("_closes")]
        dates, index = _cap_weighted_index(priced)
        price = _price_metrics(index)
        cap_total = sum(float(m["cap"]) for m in rows if m.get("cap"))
        s = {
            "sector": name,
            "count": len(rows),
            "covered": len([m for m in rows if m.get("cap") is not None]),
            "market_cap": round(cap_total, 0) or None,
            "valuation": {
                "per": _w_harmonic(rows, "per"), "per_median": _median(rows, "per"),
                "forward_per": _w_harmonic(rows, "forwardPer"),
                "pbr": _w_harmonic(rows, "pbr"), "pbr_median": _median(rows, "pbr"),
                "psr": _w_harmonic(rows, "psr"), "ev_ebitda": _w_harmonic(rows, "evEbitda"),
                "div_yield": _w_mean(rows, "divYield"),
                # PER 의 근거가 실적인지 전망인지 — 섞여 있으면 수를 함께 낸다.
                "per_basis": _per_basis(rows),
            },
            "profitability": {"roe": _w_mean(rows, "roe"), "roa": _w_mean(rows, "roa"),
                              "op_margin": _w_mean(rows, "opMargin"), "net_margin": _w_mean(rows, "netMargin")},
            "growth": {"revenue": _w_mean(rows, "revenueGrowth"), "earnings": _w_mean(rows, "earningsGrowth")},
            "stability": {"debt_to_equity": _w_mean(rows, "debt"), "current_ratio": _w_mean(rows, "currentRatio")},
            "price": price,
            "breadth": _breadth(rows),
            "index_series": {"dates": dates[-252:], "values": [round(v, 2) for v in index[-252:]]},
            "basis": {
                "source": "yahoo",
                "members": len(rows), "priced": len(priced),
                "price_as_of": max((m["price_as_of"] for m in priced if m.get("price_as_of")), default=None),
                "index_days": len(dates),
                "coverage": {k: _coverage(rows, v) for k, v in (
                    ("per", "per"), ("pbr", "pbr"), ("roe", "roe"), ("op_margin", "opMargin"),
                    ("revenue_growth", "revenueGrowth"), ("debt_to_equity", "debt"),
                    ("ev_ebitda", "evEbitda"), ("div_yield", "divYield"))},
                "errors": [f"{m['name']}: {'; '.join(m['errors'])}" for m in rows if m.get("errors")],
                "method": "배수=시총가중 조화평균·중위값 동시 표기 / 비율=시총가중 산술평균 / 가격=현재 시총 비중 고정 섹터 인덱스",
            },
            "holdings": sorted([_holding(m, cap_total) for m in rows],
                               key=lambda h: h["weight_pct"] or 0, reverse=True),
        }
        s["relative"] = {f"excess_{k}": (round(s["price"][f"ret_{k}"] - bench[f"ret_{k}"], 2)
                                         if s["price"].get(f"ret_{k}") is not None and bench.get(f"ret_{k}") is not None else None)
                         for k in _TRADING_DAYS}
        sectors.append(s)

    _score_sectors(sectors)
    ranked = sorted([s for s in sectors if s["price"].get("ret_3m") is not None],
                    key=lambda x: x["price"]["ret_3m"], reverse=True)
    for i, s in enumerate(ranked, 1):
        s["momentum_rank"] = i
    for s in sectors:
        s["rationale"] = _rationale(s, sectors, bench)
    sectors.sort(key=lambda s: (s["score"]["total"] is None, -(s["score"]["total"] or 0)))

    out = {
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "benchmark": bench,
        "universe": {"symbols": len(QUANT_STOCKS), "sectors": list(QUANT_SECTORS)},
        "score_weights": SCORE_WEIGHTS,
        "verdict_rule": "상대 점수 ≥60 비중확대 · 41~59 중립 · ≤40 비중축소 (3개 섹터 내 상대 순위이며 수익률 예측이 아님)",
        "sectors": sectors,
    }
    await cache_set(CACHE_KEY, out)
    return {**out, "from_cache": False}
