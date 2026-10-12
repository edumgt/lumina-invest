"""DART 재무제표 모듈 — 금액 파싱·계정 매핑·비활성 동작.

이 모듈이 틀리면 화면에 **조용히 잘못된 재무제표**가 나온다(출처가 DART 라서 더 신뢰받는다).
그래서 숫자 파싱과 계정명 매칭을 고정해 둔다. 실제 DART 응답 스키마 기준:
`fnlttSinglAcntAll.json` → {"status","message","list":[{sj_div, account_nm, thstrm_amount, thstrm_add_amount, ...}]}
"""
import asyncio

import pytest

from app.config import settings
from app.services import dart


# ── 금액 파싱 ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("1,234,567", 1234567),
    ("-1,234", -1234),
    ("(1,234)", -1234),          # 괄호 음수 표기
    ("0", 0),
    ("", None),
    ("-", None),
    ("   ", None),
    ("12 345", 12345),           # 공백 섞임
])
def test_amount_parsing(raw, expected):
    assert dart._amount({"thstrm_amount": raw}, "thstrm_amount") == expected


def test_amount_falls_back_to_next_key():
    row = {"thstrm_amount": "", "thstrm_add_amount": "9,900"}
    assert dart._amount(row, "thstrm_amount", "thstrm_add_amount") == 9900


# ── 종목코드 ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("symbol,code", [
    ("005930.KS", "005930"), ("035420.KQ", "035420"), ("005930", "005930"),
    ("AAPL", None), ("7203.T", None), ("", None),
])
def test_stock_code_of(symbol, code):
    assert dart.stock_code_of(symbol) == code


@pytest.mark.parametrize("symbol,krx", [
    ("005930.KS", True), ("035420.KQ", True), ("005930", True),
    ("AAPL", False), ("7203.T", False),
])
def test_is_krx(symbol, krx):
    assert dart.is_krx(symbol) is krx


# ── 계정 매핑 ────────────────────────────────────────────────────────────────
def _rows():
    return [
        {"sj_div": "BS", "account_nm": "자산총계", "thstrm_amount": "514,531,948,000,000"},
        {"sj_div": "BS", "account_nm": "부채총계", "thstrm_amount": "112,339,878,000,000"},
        {"sj_div": "BS", "account_nm": "자본총계", "thstrm_amount": "402,192,070,000,000"},
        {"sj_div": "BS", "account_nm": "현금및현금성자산", "thstrm_amount": "69,080,893,000,000"},
        {"sj_div": "IS", "account_nm": "매출액", "thstrm_amount": "79,100,000,000,000",
         "thstrm_add_amount": "158,000,000,000,000"},
        {"sj_div": "IS", "account_nm": "영업이익", "thstrm_amount": "10,440,000,000,000",
         "thstrm_add_amount": "17,100,000,000,000"},
        {"sj_div": "IS", "account_nm": "당기순이익", "thstrm_amount": "9,800,000,000,000"},
    ]


def test_parse_statements_maps_all_targets():
    out = dart._parse_statements(_rows())
    assert out["assets"] == 514_531_948_000_000
    assert out["liabilities"] == 112_339_878_000_000
    assert out["equity"] == 402_192_070_000_000
    assert out["cash"] == 69_080_893_000_000
    assert out["revenue"] == 79_100_000_000_000
    assert out["netIncome"] == 9_800_000_000_000


def test_operating_income_prefers_three_month_amount():
    """분기 금액(3개월)을 누적보다 우선한다 — 누적을 쓰면 분기 추이가 부풀려진다."""
    out = dart._parse_statements(_rows())
    assert out["operatingIncome"] == 10_440_000_000_000
    assert out["opBasis"] == "3개월"


def test_operating_income_falls_back_to_cumulative_and_says_so():
    rows = [{"sj_div": "IS", "account_nm": "영업이익", "thstrm_amount": "",
             "thstrm_add_amount": "17,100,000,000,000"}]
    out = dart._parse_statements(rows)
    assert out["operatingIncome"] == 17_100_000_000_000
    assert out["opBasis"] == "누적"      # 어느 기준인지 화면에 알려 줘야 한다


def test_parse_handles_alternative_account_names():
    rows = [
        {"sj_div": "CIS", "account_nm": "영업이익(손실)", "thstrm_amount": "-500,000"},
        {"sj_div": "IS", "account_nm": "수익(매출액)", "thstrm_amount": "1,000,000"},
        {"sj_div": "BS", "account_nm": "현금및현금성자산등", "thstrm_amount": "7,000"},
    ]
    out = dart._parse_statements(rows)
    assert out["operatingIncome"] == -500_000      # 적자도 그대로
    assert out["revenue"] == 1_000_000
    assert out["cash"] == 7_000


def test_missing_accounts_stay_none():
    out = dart._parse_statements([{"sj_div": "BS", "account_nm": "기타자산", "thstrm_amount": "1"}])
    assert out["assets"] is None and out["operatingIncome"] is None


# ── 활성/비활성 ──────────────────────────────────────────────────────────────
def test_disabled_without_key(monkeypatch):
    for name in ("DART_API_KEY", "OPEN_DART_API_KEY", "OPENDART_API_KEY", "DART_KEY"):
        monkeypatch.setattr(settings, name, "", raising=False)
    assert dart.is_enabled() is False
    assert asyncio.run(dart.latest_financials("005930.KS")) is None


@pytest.mark.parametrize("name", ["DART_API_KEY", "OPEN_DART_API_KEY", "OPENDART_API_KEY", "DART_KEY"])
def test_any_key_name_enables(monkeypatch, name):
    for other in ("DART_API_KEY", "OPEN_DART_API_KEY", "OPENDART_API_KEY", "DART_KEY"):
        monkeypatch.setattr(settings, other, "", raising=False)
    monkeypatch.setattr(settings, name, "a" * 40, raising=False)
    assert dart.is_enabled() is True
    assert dart.api_key() == "a" * 40


def test_overseas_symbol_is_skipped(monkeypatch):
    monkeypatch.setattr(settings, "DART_API_KEY", "a" * 40, raising=False)
    called = False

    async def boom(*a, **k):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(dart, "corp_code_map", boom)
    assert asyncio.run(dart.latest_financials("AAPL")) is None
    assert called is False, "해외 종목인데 DART 를 호출했다"


# ── 보고서 탐색 ──────────────────────────────────────────────────────────────
def test_latest_financials_walks_back_to_a_published_report(monkeypatch):
    monkeypatch.setattr(settings, "DART_API_KEY", "a" * 40, raising=False)
    tried: list[tuple[int, int]] = []

    async def fake_fetch(symbol, year, quarter):
        tried.append((year, quarter))
        return {"assets": 1, "equity": 1, "liabilities": 1, "cash": 1, "operatingIncome": 2,
                "opBasis": "3개월", "corpCode": "00126380", "stockCode": "005930",
                "year": year, "quarter": quarter, "reportName": "반기보고서",
                "fsDiv": "연결", "source": "DART"} if len(tried) == 3 else None

    async def no_cache(*a, **k):
        return None

    monkeypatch.setattr(dart, "fetch_financials", fake_fetch)
    monkeypatch.setattr(dart, "cache_get", no_cache)
    monkeypatch.setattr(dart, "cache_set", lambda *a, **k: asyncio.sleep(0))
    out = asyncio.run(dart.latest_financials("005930.KS"))
    assert out is not None and out["operatingIncome"] == 2
    assert len(tried) == 3, f"직전 분기부터 거슬러 올라가야 한다: {tried}"


def test_key_error_stops_retrying(monkeypatch):
    """키가 잘못됐으면 분기를 더 뒤져도 같은 실패다 — 즉시 멈춰 외부 호출을 낭비하지 않는다."""
    monkeypatch.setattr(settings, "DART_API_KEY", "a" * 40, raising=False)
    calls = 0

    async def fake_fetch(symbol, year, quarter):
        nonlocal calls
        calls += 1
        raise dart.DartError("010", "등록되지 않은 인증키입니다.")

    async def no_cache(*a, **k):
        return None

    monkeypatch.setattr(dart, "fetch_financials", fake_fetch)
    monkeypatch.setattr(dart, "cache_get", no_cache)
    monkeypatch.setattr(dart, "cache_set", lambda *a, **k: asyncio.sleep(0))
    assert asyncio.run(dart.latest_financials("005930.KS")) is None
    assert calls == 1
