"""DART 계산 수익성 지표·배당 (6-57): 계정명 보강(반기순이익(손실)), 손익 항목별 누적, 범용 TTM, ROE·ROA·이익률·부채비율,
alotMatter 배당 파싱, Yahoo 항목 단위 폴백, Yahoo 의 '순이익 0·영업이익 없음' 조합을 N/A 로.

실측 근거(2026-10-14 아모텍 052710 OFS·별도): 반기보고서 "반기순이익(손실)" thstrm=-1,300,177,715 / add=-1,024,782,692 / frm_add=1,780,616,496,
2025 사업보고서 "당기순이익(손실)" 7,920,105,732. alotMatter 는 se/stock_knd/thstrm 이고 배당 없으면 '-'.
"""
import asyncio
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.services import dart, stock


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setattr(settings, "DART_API_KEY", "a" * 40, raising=False)


# ── 파싱 ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("name", ["반기순이익(손실)", "분기순이익(손실)", "당기순이익(손실)", "반기순손실", "당기순이익"])
def test_parse_statements_recognises_loss_variants(name):
    rows = [{"sj_div": "CIS", "account_nm": name, "thstrm_amount": "-1,300,177,715", "thstrm_add_amount": "-1,024,782,692",
             "frmtrm_add_amount": "1,780,616,496"}]
    out = dart._parse_statements(rows, "11012")
    assert out["netIncome"] == -1_300_177_715 and out["netCumulative"] == -1_024_782_692 and out["netPrevCumulative"] == 1_780_616_496


def test_parse_statements_keeps_cumulative_for_revenue_and_operating_income():
    rows = [
        {"sj_div": "CIS", "account_nm": "매출액", "thstrm_amount": "54,437,586,562", "thstrm_add_amount": "106,655,639,536", "frmtrm_add_amount": "125,693,747,537"},
        {"sj_div": "CIS", "account_nm": "영업이익(손실)", "thstrm_amount": "-575,237,185", "thstrm_add_amount": "-3,071,077", "frmtrm_add_amount": "-10,482,889,733"},
    ]
    out = dart._parse_statements(rows, "11012")
    assert out["revenue"] == 54_437_586_562 and out["revenueCumulative"] == 106_655_639_536 and out["revenuePrevCumulative"] == 125_693_747_537
    assert out["operatingIncome"] == -575_237_185 and out["opCumulative"] == -3_071_077 and out["opPrevCumulative"] == -10_482_889_733
    assert out["opBasis"] == "3개월"
    annual = dart._parse_statements([{"sj_div": "CIS", "account_nm": "매출액", "thstrm_amount": "253,765,089,587", "frmtrm_amount": "229,444,552,451"}], "11011")
    assert annual["revenueCumulative"] == 253_765_089_587 and annual["revenuePrevCumulative"] == 229_444_552_451


def test_ttm_amount_generic_and_net_income_alias():
    latest = {"quarter": 2, "netCumulative": -1_024_782_692, "netPrevCumulative": 1_780_616_496,
              "revenueCumulative": 106_655_639_536, "revenuePrevCumulative": 125_693_747_537, "opCumulative": None}
    prev = {"netIncome": 7_920_105_732, "revenue": 253_765_089_587, "operatingIncome": 5_505_372_842}
    assert dart.ttm_amount(latest, prev, "netIncome") == (-1_024_782_692 + 7_920_105_732 - 1_780_616_496, "최근 4개 분기 합산(TTM)")
    assert dart.ttm_net_income(latest, prev) == dart.ttm_amount(latest, prev, "netIncome")
    assert dart.ttm_amount(latest, prev, "revenue")[0] == 106_655_639_536 + 253_765_089_587 - 125_693_747_537
    assert dart.ttm_amount(latest, prev, "operatingIncome") == (None, "당기 누적 영업이익 없음")
    assert dart.ttm_amount({"quarter": 4, "revenue": 10}, None, "revenue") == (10, "연간")
    assert dart.ttm_amount({"quarter": 4}, None, "operatingIncome") == (None, "영업이익 없음")


# ── 수익성 지표 ───────────────────────────────────────────────────────────────
def _latest(**over):
    base = {"year": 2026, "quarter": 2, "reportName": "반기보고서", "fsDiv": "연결", "stockCode": "052710",
            "assets": 367_100_000_000, "liabilities": 206_800_000_000, "equity": 160_329_767_963, "equityOwners": 160_329_767_963,
            "netCumulative": -1_024_782_692, "netPrevCumulative": 1_780_616_496,
            "opCumulative": -3_071_077, "opPrevCumulative": -10_482_889_733,
            "revenueCumulative": 106_655_639_536, "revenuePrevCumulative": 125_693_747_537}
    return {**base, **over}


_PREV = {"quarter": 4, "netIncome": 7_920_105_732, "operatingIncome": 5_505_372_842, "revenue": 253_765_089_587}


@pytest.fixture
def dart_mocks(monkeypatch):
    store: dict = {}
    async def cget(key, max_age_hours=24): return store.get(key)
    async def cset(key, data): store[key] = data
    monkeypatch.setattr(dart, "cache_get", cget)
    monkeypatch.setattr(dart, "cache_set", cset)
    monkeypatch.setattr(dart, "latest_financials", AsyncMock(return_value=_latest()))
    monkeypatch.setattr(dart, "fetch_financials", AsyncMock(return_value=dict(_PREV)))
    return store


def test_profitability_metrics_computes_ratios_with_bases(dart_mocks):
    pm = asyncio.run(dart.profitability_metrics("052710.KQ"))
    ni = -1_024_782_692 + 7_920_105_732 - 1_780_616_496
    op = -3_071_077 + 5_505_372_842 - (-10_482_889_733)
    rev = 106_655_639_536 + 253_765_089_587 - 125_693_747_537
    assert pm["roe"] == round(ni / 160_329_767_963 * 100, 2) and pm["roa"] == round(ni / 367_100_000_000 * 100, 2)
    assert pm["opMargin"] == round(op / rev * 100, 2) and pm["netMargin"] == round(ni / rev * 100, 2)
    assert pm["debtRatio"] == round(206_800_000_000 / 160_329_767_963 * 100, 2)
    assert pm["bases"]["roe"].endswith("지배기업 소유주 귀속 자본") and "TTM" in pm["bases"]["opMargin"]
    assert pm["notes"] == [] and pm["period"] == "2026 반기보고서"
    assert asyncio.run(dart.profitability_metrics("052710.KQ")) == pm and dart.latest_financials.await_count == 1   # 캐시


def test_profitability_metrics_missing_inputs_leave_none_with_reason(dart_mocks):
    dart.fetch_financials.return_value = None            # 전기 사업보고서 없음 → TTM 불가
    pm = asyncio.run(dart.profitability_metrics("052710.KQ"))
    assert pm["roe"] is None and pm["roa"] is None and pm["opMargin"] is None
    assert pm["debtRatio"] is not None                   # 부채비율은 기말 잔액만 있으면 된다
    assert any("ROE·ROA" in n for n in pm["notes"]) and any("영업이익률" in n for n in pm["notes"])
    dart_mocks.clear()
    dart.latest_financials.return_value = _latest(equityOwners=None)
    dart.fetch_financials.return_value = dict(_PREV)
    assert "자본총계" in asyncio.run(dart.profitability_metrics("052710.KQ"))["bases"]["roe"]


def test_profitability_metrics_none_when_disabled_or_no_report(dart_mocks, monkeypatch):
    assert asyncio.run(dart.profitability_metrics("AAPL")) is None
    dart.latest_financials.return_value = None
    assert asyncio.run(dart.profitability_metrics("052710.KQ")) is None
    monkeypatch.setattr(settings, "DART_API_KEY", "", raising=False)
    assert asyncio.run(dart.profitability_metrics("005930.KS")) is None


# ── 배당 ─────────────────────────────────────────────────────────────────────
_SAMSUNG_ROWS = [
    {"se": "주당액면가액(원)", "stock_knd": "-", "thstrm": "100"},
    {"se": "현금배당금총액(백만원)", "stock_knd": "-", "thstrm": "11,107,906"},
    {"se": "(연결)현금배당성향(%)", "stock_knd": "-", "thstrm": "25.10"},
    {"se": "현금배당수익률(%)", "stock_knd": "보통주", "thstrm": "1.50"},
    {"se": "현금배당수익률(%)", "stock_knd": "우선주", "thstrm": "1.90"},
    {"se": "주당 현금배당금(원)", "stock_knd": "보통주", "thstrm": "1,668"},
    {"se": "주당 현금배당금(원)", "stock_knd": "우선주", "thstrm": "1,669"},
]


def test_parse_dividends_common_preferred_and_none():
    common = dart._parse_dividends(_SAMSUNG_ROWS)
    assert common["dps"] == 1668 and common["yieldReported"] == 1.5 and common["payoutRatio"] == 25.1 and common["noDividend"] is False
    assert common["totalCashDividendMillion"] == 11_107_906 and common["stockKind"] == "보통주"
    pref = dart._parse_dividends(_SAMSUNG_ROWS, preferred=True)
    assert pref["dps"] == 1669 and pref["yieldReported"] == 1.9 and pref["stockKind"] == "우선주"
    amotech = dart._parse_dividends([{"se": "주당 현금배당금(원)", "stock_knd": "보통주", "thstrm": "-"},
                                     {"se": "현금배당수익률(%)", "stock_knd": "보통주", "thstrm": "-"}])
    assert amotech["dps"] == 0.0 and amotech["noDividend"] is True and amotech["yieldReported"] is None
    assert dart._parse_dividends([{"se": "비고", "thstrm": "x"}]) is None
    assert dart._parse_dividends([{"se": "주당액면가액(원)", "stock_knd": "-", "thstrm": "500"}])["dps"] is None   # 배당 행 자체가 없으면 모른다


def test_dividend_info_walks_back_a_year_and_caches(monkeypatch):
    store: dict = {}
    async def cget(key, max_age_hours=24): return store.get(key)
    async def cset(key, data): store[key] = data
    monkeypatch.setattr(dart, "cache_get", cget)
    monkeypatch.setattr(dart, "cache_set", cset)
    monkeypatch.setattr(dart, "resolve_corp", AsyncMock(return_value=("00126380", "005930", False)))
    calls: list[dict] = []
    async def get_json(path, params):
        calls.append(params)
        if len(calls) == 1:
            raise dart.DartError("013", "조회된 데이타가 없습니다.")
        return {"status": "000", "list": _SAMSUNG_ROWS}
    monkeypatch.setattr(dart, "_get_json", get_json)
    dv = asyncio.run(dart.dividend_info("005930.KS"))
    assert dv["dps"] == 1668 and dv["reportName"] == "사업보고서" and dv["year"] == int(calls[1]["bsns_year"])
    assert all(p["reprt_code"] == "11011" for p in calls) and int(calls[0]["bsns_year"]) - int(calls[1]["bsns_year"]) == 1
    assert asyncio.run(dart.dividend_info("005930.KS")) == dv and len(calls) == 2


# ── Yahoo 항목 단위 폴백 ──────────────────────────────────────────────────────
def test_apply_dart_profitability_fills_only_missing_items():
    f = {"roe": 12.3, "roa": None, "opMargin": None, "netMargin": None, "debt": None}
    pm = {"roe": 3.2, "roa": 1.4, "opMargin": 2.1, "netMargin": 1.9, "debtRatio": 129.0, "period": "2026 반기보고서", "fsDiv": "연결",
          "bases": {"roe": "x", "roa": "TTM 순이익 ÷ 기말 자산총계", "opMargin": "o", "netMargin": "n", "debtRatio": "기말 부채총계 ÷ 자본총계"}, "notes": []}
    stock._apply_dart_profitability(f, pm)
    assert f["roe"] == 12.3 and f["roa"] == 1.4 and f["opMargin"] == 2.1 and f["debt"] == 129.0
    assert f["profitabilityBasis"]["roe"].startswith("Yahoo") and f["profitabilityBasis"]["debt"] == "DART 계산 · 기말 부채총계 ÷ 자본총계"
    assert f["profitabilitySource"].startswith("DART 계산 · 2026 반기보고서") and f["profitabilityFilled"] == ["roa", "opMargin", "netMargin", "debt"]
    assert f["yahooProfitability"]["roe"] == 12.3 and f["yahooProfitability"]["roa"] is None


def test_apply_dart_profitability_without_dart_keeps_yahoo_or_none():
    f = {"roe": None, "roa": None, "opMargin": None, "netMargin": None, "debt": None}
    stock._apply_dart_profitability(f, None)
    assert f["profitabilitySource"] is None and f["profitabilityFilled"] == [] and f["roe"] is None
    g = {"roe": 5.0, "roa": 2.0, "opMargin": 8.0, "netMargin": 4.0, "debt": 40.0}
    stock._apply_dart_profitability(g, None)
    assert g["profitabilitySource"] == "Yahoo Finance financialData" and g["debt"] == 40.0


def test_apply_dart_dividend_computes_yield_from_price_and_respects_yahoo():
    f = {"div": None, "divYield": None, "payoutRatio": None, "price": 83_400.0}
    stock._apply_dart_dividend(f, {"dps": 1668.0, "yieldReported": 1.5, "payoutRatio": 25.1, "year": 2025, "reportName": "사업보고서", "stockKind": "보통주", "noDividend": False})
    assert f["div"] == 1668.0 and f["divYield"] == 2.0 and f["payoutRatio"] == 25.1 and f["divYieldReported"] == 1.5
    assert f["dividendSource"].startswith("DART 2025 사업보고서") and "DPS ÷ 현재가" in f["dividendSource"]
    none = {"div": None, "divYield": None, "price": 20_250.0}
    stock._apply_dart_dividend(none, {"dps": 0.0, "noDividend": True, "year": 2025, "reportName": "사업보고서", "stockKind": "보통주"})
    assert none["div"] == 0.0 and none["divYield"] == 0.0 and "현금배당 없음" in none["dividendSource"]
    yahoo = {"div": 1444.0, "divYield": 2.7, "price": 50_000.0}
    stock._apply_dart_dividend(yahoo, {"dps": 1668.0, "year": 2025, "reportName": "사업보고서"})
    assert yahoo["div"] == 1444.0 and yahoo["dividendSource"] == "Yahoo Finance summaryDetail"
    missing = {"div": None, "divYield": None, "price": 1.0}
    stock._apply_dart_dividend(missing, None)
    assert missing["div"] is None and missing["dividendSource"] is None
