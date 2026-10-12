"""DART(금융감독원 전자공시) 재무제표 — 영업이익·재무상태표 전용 모듈.

왜 별도 모듈인가
  Yahoo quoteSummary 는 국내·해외 모두 분기 손익계산서의 `operatingIncome` 을 **빈 객체**로,
  `balanceSheetHistory` 는 **결산일만** 채워 보낸다(2026-10-12 원본 확인:
  `operatingIncome={}`, `ebit/grossProfit` 은 raw=0 placeholder, 재무상태표 항목은 키 자체가 없음).
  그래서 #company-dashboard 의 영업이익 행과 재무상태표가 항상 N/A 였다. 국내 상장사는 DART 원문이
  정본이므로 그 두 블록만 DART 에서 받아 채운다.

범위와 한계
  - DART 는 **국내 상장사만** 제공한다. 해외 종목(AAPL·7203.T 등)은 `None` 을 돌려주고 화면은
    기존처럼 "출처가 제공하지 않음" 을 표시한다 — 다른 데이터로 메우거나 추정하지 않는다.
  - 인증키가 없으면 조용히 비활성(`is_enabled() == False`)이고 기존 동작을 바꾸지 않는다.

API 규약(실측)
  - 모든 응답이 **HTTP 200** 이고 성공·실패는 본문 `status` 로 온다 → HTTP 코드만 보면 안 된다.
    `000` 정상 · `010` 등록되지 않은 인증키 · `011` 사용할 수 없는 키 · `013` 조회된 데이터 없음 ·
    `020` 요청 제한 초과 · `100` 필드 부적절 · `800` 시스템 점검.
  - 고유번호(`corp_code`)는 `corpCode.xml` 이 **ZIP** 으로 내려준다 → 압축을 풀어 종목코드 매핑을 만든다.
  - 재무제표는 `fnlttSinglAcntAll.json` 의 계정 단위 행. `sj_div` 가 `BS`(재무상태표)·`IS`/`CIS`(손익계산서).
    분기보고서의 손익 행은 `thstrm_amount`(당기 3개월)와 `thstrm_add_amount`(당기 누적)를 함께 준다.
    영업이익은 **3개월 금액을 우선** 쓰고, 없으면 누적을 쓰되 어느 쪽인지 `opBasis` 로 알려 준다.
"""
from __future__ import annotations

import asyncio
import io
import logging
import re
import zipfile
from xml.etree import ElementTree

import httpx

from app.config import settings
from app.services.data_cache import cache_get, cache_set

logger = logging.getLogger(__name__)

BASE = "https://opendart.fss.or.kr/api"
CORP_CODE_CACHE_KEY = "dart:corp_codes:v1"
CORP_CODE_TTL_HOURS = 24 * 30          # 고유번호는 자주 안 바뀐다
FIN_TTL_HOURS = 24                     # 분기 공시는 하루 한 번이면 충분하다
TIMEOUT = 20.0

# 분기 → 보고서 코드. 1분기·반기·3분기·사업보고서
REPRT_CODES = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}
_REPRT_NAME = {"11013": "1분기보고서", "11012": "반기보고서", "11014": "3분기보고서", "11011": "사업보고서"}

# 계정명은 회사마다 표기가 조금씩 다르다 — 정규화해서 맞춘다.
_BS_TARGETS = {
    "assets": ("자산총계",),
    "liabilities": ("부채총계",),
    "equity": ("자본총계",),
    "cash": ("현금및현금성자산", "현금및현금성자산등"),
}
_IS_TARGETS = {
    "operatingIncome": ("영업이익", "영업이익(손실)", "영업손익"),
    "revenue": ("매출액", "수익(매출액)", "영업수익"),
    "netIncome": ("당기순이익", "당기순이익(손실)", "분기순이익", "반기순이익"),
}

_lock = asyncio.Lock()


def api_key() -> str:
    """인증키. 여러 이름을 허용한다 — 배포마다 변수명이 달랐다."""
    for name in ("DART_API_KEY", "OPEN_DART_API_KEY", "OPENDART_API_KEY", "DART_KEY"):
        value = str(getattr(settings, name, "") or "").strip()
        if value:
            return value
    return ""


def is_enabled() -> bool:
    return bool(api_key())


def is_krx(symbol: str) -> bool:
    s = str(symbol or "").upper()
    return s.endswith(".KS") or s.endswith(".KQ") or bool(re.fullmatch(r"\d{6}", s))


def stock_code_of(symbol: str) -> str | None:
    s = str(symbol or "").upper()
    for suffix in (".KS", ".KQ"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
            break
    return s if re.fullmatch(r"\d{6}", s) else None


def _norm(name: str) -> str:
    """계정명 비교용 — 공백·괄호 안 설명을 떼고 본문만 남긴다."""
    return re.sub(r"\s+", "", str(name or ""))


def _amount(row: dict, *keys: str) -> int | None:
    """DART 금액은 문자열이고 쉼표·음수 괄호·'-' 가 섞여 온다."""
    for key in keys:
        raw = str(row.get(key) or "").strip()
        if not raw or raw in ("-", "–"):
            continue
        neg = raw.startswith("(") and raw.endswith(")")
        cleaned = re.sub(r"[^0-9\-]", "", raw)
        if not cleaned or cleaned == "-":
            continue
        try:
            value = int(cleaned)
        except ValueError:
            continue
        return -value if neg and value > 0 else value
    return None


class DartError(Exception):
    def __init__(self, status: str, message: str):
        super().__init__(f"[{status}] {message}")
        self.status = status
        self.message = message


async def _get(path: str, params: dict) -> httpx.Response:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.get(f"{BASE}/{path}", params={**params, "crtfc_key": api_key()})
    resp.raise_for_status()
    return resp


async def _get_json(path: str, params: dict) -> dict:
    resp = await _get(path, params)
    body = resp.json()
    status = str(body.get("status") or "")
    if status and status != "000":
        raise DartError(status, str(body.get("message") or "알 수 없는 오류"))
    return body


async def corp_code_map() -> dict[str, str]:
    """종목코드(6자리) → DART 고유번호(8자리). ZIP 을 풀어 만들고 장기 캐시한다."""
    cached = await cache_get(CORP_CODE_CACHE_KEY, max_age_hours=CORP_CODE_TTL_HOURS)
    if cached:
        return cached.get("map", {}) if isinstance(cached, dict) else {}
    async with _lock:
        cached = await cache_get(CORP_CODE_CACHE_KEY, max_age_hours=CORP_CODE_TTL_HOURS)
        if cached:
            return cached.get("map", {}) if isinstance(cached, dict) else {}
        resp = await _get("corpCode.xml", {})
        head = resp.content[:200].decode("utf-8", "replace")
        if "<status>" in head and "<status>000</status>" not in head:
            status = re.search(r"<status>(\d+)</status>", head)
            message = re.search(r"<message>([^<]*)</message>", head)
            raise DartError(status.group(1) if status else "900",
                            message.group(1) if message else "고유번호 조회 실패")
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            xml = zf.read(zf.namelist()[0])
        mapping: dict[str, str] = {}
        for item in ElementTree.fromstring(xml).iter("list"):
            stock = (item.findtext("stock_code") or "").strip()
            corp = (item.findtext("corp_code") or "").strip()
            if stock and corp and re.fullmatch(r"\d{6}", stock):
                mapping[stock] = corp
        await cache_set(CORP_CODE_CACHE_KEY, {"map": mapping, "count": len(mapping)})
        logger.info("DART 고유번호 %d개 적재", len(mapping))
        return mapping


def _parse_statements(rows: list[dict]) -> dict:
    """계정 행 → 재무상태표·손익 항목. 같은 계정이 여러 번 나오면 첫 값을 쓴다."""
    out: dict[str, int | None] = {k: None for k in (*_BS_TARGETS, *_IS_TARGETS)}
    op_basis = None
    for row in rows:
        div = str(row.get("sj_div") or "").upper()
        name = _norm(row.get("account_nm"))
        if div == "BS":
            for key, names in _BS_TARGETS.items():
                if out[key] is None and name in {_norm(n) for n in names}:
                    out[key] = _amount(row, "thstrm_amount")
        elif div in ("IS", "CIS"):
            for key, names in _IS_TARGETS.items():
                if out[key] is None and name in {_norm(n) for n in names}:
                    # 분기 3개월 금액 우선, 없으면 누적
                    value = _amount(row, "thstrm_amount")
                    basis = "3개월"
                    if value is None:
                        value = _amount(row, "thstrm_add_amount")
                        basis = "누적"
                    out[key] = value
                    if key == "operatingIncome" and value is not None:
                        op_basis = basis
    return {**out, "opBasis": op_basis}


async def fetch_financials(symbol: str, year: int, quarter: int) -> dict | None:
    """한 보고서의 재무상태표·영업이익. 연결(CFS)을 먼저 보고 없으면 별도(OFS)."""
    code = stock_code_of(symbol)
    if not code or not is_enabled():
        return None
    corp = (await corp_code_map()).get(code)
    if not corp:
        return None
    reprt = REPRT_CODES[quarter]
    for fs_div in ("CFS", "OFS"):
        try:
            body = await _get_json("fnlttSinglAcntAll.json", {
                "corp_code": corp, "bsns_year": str(year), "reprt_code": reprt, "fs_div": fs_div,
            })
        except DartError as exc:
            if exc.status == "013":      # 이 보고서·구분에는 자료가 없다 → 다음 구분
                continue
            raise
        parsed = _parse_statements(body.get("list") or [])
        if parsed.get("assets") is None and parsed.get("operatingIncome") is None:
            continue
        return {
            **parsed,
            "corpCode": corp, "stockCode": code,
            "year": year, "quarter": quarter,
            "reportName": _REPRT_NAME.get(reprt, reprt),
            "fsDiv": "연결" if fs_div == "CFS" else "별도",
            "source": "금융감독원 전자공시(DART) fnlttSinglAcntAll",
        }
    return None


async def latest_financials(symbol: str, lookback: int = 5) -> dict | None:
    """가장 최근 공시된 보고서를 뒤에서부터 찾는다(분기 공시는 두 달 이상 늦게 올라온다).

    결과는 symbol 단위로 캐시한다 — 화면을 다시 열 때마다 DART 를 두드리지 않는다.
    """
    if not is_enabled() or not is_krx(symbol):
        return None
    code = stock_code_of(symbol)
    if not code:
        return None
    cache_key = f"dart:fin:v1:{code}"
    cached = await cache_get(cache_key, max_age_hours=FIN_TTL_HOURS)
    if cached is not None:
        return cached or None

    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone(timedelta(hours=9)))
    candidates: list[tuple[int, int]] = []
    year, quarter = now.year, (now.month - 1) // 3 + 1
    for _ in range(lookback):
        quarter -= 1
        if quarter == 0:
            year, quarter = year - 1, 4
        candidates.append((year, quarter))

    for cand_year, cand_quarter in candidates:
        try:
            found = await fetch_financials(symbol, cand_year, cand_quarter)
        except DartError as exc:
            logger.warning("DART 조회 실패 %s %dY%dQ: %s", symbol, cand_year, cand_quarter, exc)
            if exc.status in ("010", "011", "020", "800"):   # 키·한도·점검이면 더 시도해도 같다
                return None
            continue
        except (httpx.HTTPError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
            logger.warning("DART 통신/파싱 실패 %s: %s", symbol, exc)
            return None
        if found:
            await cache_set(cache_key, found)
            return found
    await cache_set(cache_key, {})      # 없음도 캐시해 반복 조회를 막는다
    return None
