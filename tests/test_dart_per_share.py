"""DART 계산 EPS·BPS·PER·PBR (6-52): 우선주→보통주 코드, 발행주식수 파싱, TTM 순이익, 주당 지표, Yahoo 폴백 우선순위.

실측 근거(2026-10-13 아모레퍼시픽 2026 반기보고서 CFS): 반기순이익 thstrm_amount=3개월, thstrm_add_amount=당기 누적,
frmtrm_add_amount=전기 동기간 누적. stockTotqySttus 는 se=보통주/종류주/합계, istc_totqy=발행주식 총수.
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from app.services import dart, stock


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setattr(settings, "DART_API_KEY", "a" * 40, raising=False)


# ── 코드 매핑 ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("code,expected", [("090435", ("090430", True)), ("005935", ("005930", True)), ("005937", ("005930", True)),
                                           ("005930", ("005930", False)), ("AAPL", ("AAPL", False))])
def test_base_stock_code(code, expected):
    assert dart.base_stock_code(code) == expected


def test_resolve_corp_maps_preferred_to_company():
    with patch.object(dart, "corp_code_map", AsyncMock(return_value={"090430": "00583424"})):
        assert asyncio.run(dart.resolve_corp("090435.KS")) == ("00583424", "090430", True)
        assert asyncio.run(dart.resolve_corp("090430.KS")) == ("00583424", "090430", False)
        assert asyncio.run(dart.resolve_corp("999990.KS")) is None
        assert asyncio.run(dart.resolve_corp("AAPL")) is None


# ── 파싱 ─────────────────────────────────────────────────────────────────────
def test_parse_statements_extracts_net_cumulative_and_owners_equity():
    rows = [
        {"sj_div": "BS", "account_nm": "자본총계", "thstrm_amount": "5,631,695,212,278"},
        {"sj_div": "BS", "account_nm": "지배기업의 소유주에게 귀속되는 자본", "thstrm_amount": "5,573,067,675,747"},
        {"sj_div": "CIS", "account_nm": "반기순이익", "thstrm_amount": "93,363,202,728", "thstrm_add_amount": "206,360,950,558",
         "frmtrm_add_amount": "156,583,504,005"},
    ]
    out = dart._parse_statements(rows, "11012")
    assert out["netIncome"] == 93_363_202_728 and out["netCumulative"] == 206_360_950_558 and out["netPrevCumulative"] == 156_583_504_005
    assert out["equity"] == 5_631_695_212_278 and out["equityOwners"] == 5_573_067_675_747


def test_parse_statements_annual_uses_yearly_as_cumulative():
    rows = [{"sj_div": "IS", "account_nm": "당기순이익", "thstrm_amount": "1,000", "thstrm_add_amount": "", "frmtrm_amount": "800"}]
    out = dart._parse_statements(rows, "11011")
    assert out["netIncome"] == 1000 and out["netCumulative"] == 1000 and out["netPrevCumulative"] == 800


def test_parse_shares_prefers_total_and_sums_when_missing():
    rows = [{"se": "보통주", "istc_totqy": "58,492,759"}, {"se": "종류주", "istc_totqy": "10,557,830"},
            {"se": "합계", "istc_totqy": "69,050,589"}, {"se": "비고", "istc_totqy": "-"}]
    assert dart._parse_shares(rows) == {"common": 58_492_759, "preferred": 10_557_830, "total": 69_050_589}
    assert dart._parse_shares(rows[:2]) == {"common": 58_492_759, "preferred": 10_557_830, "total": 69_050_589}
    assert dart._parse_shares([{"se": "비고", "istc_totqy": "-"}]) is None


# ── TTM ──────────────────────────────────────────────────────────────────────
def test_ttm_net_income_half_year_report():
    latest = {"quarter": 2, "netCumulative": 206, "netPrevCumulative": 156}
    assert dart.ttm_net_income(latest, {"netIncome": 300}) == (206 + 300 - 156, "최근 4개 분기 합산(TTM)")
    assert dart.ttm_net_income(latest, None) == (None, "전기 사업보고서 또는 전기 동기간 누적 없음")
    assert dart.ttm_net_income({"quarter": 2, "netCumulative": None}, {"netIncome": 300})[0] is None


def test_ttm_net_income_annual_report_is_yearly():
    assert dart.ttm_net_income({"quarter": 4, "netIncome": 900}, None) == (900, "연간")


# ── 주당 지표 ─────────────────────────────────────────────────────────────────
def _latest(**over):
    base = {"year": 2026, "quarter": 2, "reportName": "반기보고서", "fsDiv": "연결", "stockCode": "090430", "isPreferred": False,
            "netIncome": 93_363_202_728, "netCumulative": 206_360_950_558, "netPrevCumulative": 156_583_504_005,
            "equity": 5_631_695_212_278, "equityOwners": 5_573_067_675_747}
    return {**base, **over}


@pytest.fixture
def dart_mocks(monkeypatch):
    store: dict = {}
    async def cget(key, max_age_hours=24): return store.get(key)
    async def cset(key, data): store[key] = data
    monkeypatch.setattr(dart, "cache_get", cget)
    monkeypatch.setattr(dart, "cache_set", cset)
    monkeypatch.setattr(dart, "latest_financials", AsyncMock(return_value=_latest()))
    monkeypatch.setattr(dart, "fetch_financials", AsyncMock(return_value={"netIncome": 300_000_000_000, "quarter": 4}))
    monkeypatch.setattr(dart, "shares_outstanding", AsyncMock(return_value={"common": 58_492_759, "preferred": 10_557_830, "total": 69_050_589, "reportName": "반기보고서"}))
    return store


def test_per_share_metrics_common_and_preferred(dart_mocks):
    ps = asyncio.run(dart.per_share_metrics("090430.KS"))
    ttm = 206_360_950_558 + 300_000_000_000 - 156_583_504_005
    assert ps["eps"] == round(ttm / 69_050_589, 2) and ps["epsBasis"] == "최근 4개 분기 합산(TTM)"
    assert ps["bps"] == round(5_573_067_675_747 / 69_050_589, 2) and ps["bpsBasis"] == "지배기업 소유주 귀속 자본"
    assert ps["isPreferred"] is False and "69,050,589" in ps["sharesBasis"] and ps["notes"] == []
    pref = asyncio.run(dart.per_share_metrics("090435.KS"))         # 같은 회사 캐시 공유, 우선주 표시만 다르다
    assert pref["eps"] == ps["eps"] and pref["isPreferred"] is True
    assert dart.latest_financials.await_count == 1                 # 두 번째 호출은 캐시


def test_per_share_metrics_without_prior_annual_skips_eps_but_keeps_bps(dart_mocks):
    dart.fetch_financials.return_value = None
    ps = asyncio.run(dart.per_share_metrics("090430.KS"))
    assert ps["eps"] is None and ps["bps"] is not None and any("EPS 계산 불가" in n for n in ps["notes"])


def test_per_share_metrics_falls_back_to_total_equity(dart_mocks):
    dart.latest_financials.return_value = _latest(equityOwners=None)
    ps = asyncio.run(dart.per_share_metrics("090430.KS"))
    assert ps["bps"] == round(5_631_695_212_278 / 69_050_589, 2) and ps["bpsBasis"].startswith("자본총계")


def test_per_share_metrics_none_without_shares_or_report(dart_mocks):
    dart.shares_outstanding.return_value = None
    assert asyncio.run(dart.per_share_metrics("090430.KS")) is None
    dart_mocks.clear()
    dart.latest_financials.return_value = None
    assert asyncio.run(dart.per_share_metrics("005930.KS")) is None
    assert asyncio.run(dart.per_share_metrics("AAPL")) is None


def test_shares_outstanding_caches_empty_on_013(monkeypatch):
    store: dict = {}
    async def cget(key, max_age_hours=24): return store.get(key)
    async def cset(key, data): store[key] = data
    monkeypatch.setattr(dart, "cache_get", cget); monkeypatch.setattr(dart, "cache_set", cset)
    monkeypatch.setattr(dart, "resolve_corp", AsyncMock(return_value=("00583424", "090430", True)))
    monkeypatch.setattr(dart, "_get_json", AsyncMock(side_effect=dart.DartError("013", "조회된 데이타가 없습니다.")))
    assert asyncio.run(dart.shares_outstanding("090435.KS", 2026, 2)) is None
    assert store["dart:shares:v1:090430:2026Q2"] == {}
    dart._get_json.return_value = {"list": [{"se": "합계", "istc_totqy": "10"}]}; dart._get_json.side_effect = None
    out = asyncio.run(dart.shares_outstanding("090435.KS", 2026, 3))
    assert out["total"] == 10 and out["reportName"] == "3분기보고서"


# ── fundamentals 우선순위: DART 우선, 항목별 Yahoo 폴백 ───────────────────────────
def _fund(**over):
    return {"price": 37_500.0, "eps": 1.0, "bps": 2.0, "per": 3.0, "pbr": 4.0, "perBasis": "forward", **over}


def test_apply_dart_valuation_prefers_dart_and_recomputes_ratios():
    f = _fund()
    stock._apply_dart_valuation(f, {"eps": 5_000.0, "bps": 80_000.0, "epsBasis": "최근 4개 분기 합산(TTM)", "bpsBasis": "지배기업 소유주 귀속 자본",
                                    "period": "2026 반기보고서", "fsDiv": "연결", "sharesBasis": "발행주식 총수 69,050,589주", "notes": [], "isPreferred": True, "stockCode": "090430"})
    assert (f["eps"], f["bps"], f["per"], f["pbr"]) == (5_000.0, 80_000.0, 7.5, 0.47)
    assert f["perBasis"] == "dart_ttm" and f["valuationSource"].startswith("DART 계산 · 2026 반기보고서 연결")
    assert f["yahooValuation"] == {"eps": 1.0, "bps": 2.0, "per": 3.0, "pbr": 4.0, "perBasis": "forward"}
    assert f["isPreferred"] and any("우선주" in n for n in f["valuationNotes"])


def test_apply_dart_valuation_item_level_fallback_and_loss():
    f = _fund()
    stock._apply_dart_valuation(f, {"eps": None, "bps": 80_000.0, "epsBasis": "", "bpsBasis": "자본총계", "period": "p", "fsDiv": "연결",
                                    "sharesBasis": "", "notes": ["EPS 계산 불가: 전기 사업보고서 없음"], "isPreferred": False})
    assert f["eps"] == 1.0 and f["per"] == 3.0 and f["perBasis"] == "forward"      # EPS·PER 은 Yahoo 폴백
    assert f["bps"] == 80_000.0 and f["pbr"] == 0.47                               # BPS·PBR 은 DART
    g = _fund()
    stock._apply_dart_valuation(g, {"eps": -10.0, "bps": 80_000.0, "epsBasis": "연간", "bpsBasis": "자본총계", "period": "p", "fsDiv": "별도", "sharesBasis": "", "notes": [], "isPreferred": False})
    assert g["eps"] == -10.0 and g["per"] is None and any("적자" in n for n in g["valuationNotes"])


def test_apply_dart_valuation_keeps_yahoo_when_dart_missing():
    f = _fund()
    stock._apply_dart_valuation(f, None)
    assert (f["eps"], f["per"], f["pbr"]) == (1.0, 3.0, 4.0) and f["valuationSource"] == "Yahoo Finance quoteSummary"
    g = _fund(eps=None, bps=None, per=None, pbr=None)
    stock._apply_dart_valuation(g, None)
    assert g["valuationSource"] is None
