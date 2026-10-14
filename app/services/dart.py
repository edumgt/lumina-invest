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
  - 재무제표는 `fnlttSinglAcntAll.json` 의 계정 단위 행. `sj_div` 는 `BS`·`IS`·`CIS`·`CF`·`SCE`.
    `자본총계` 는 자본변동표(`SCE`)에도 7번 나오고 금액이 전부 다르므로 **반드시 `BS` 로 한정**해야 한다.

  금액 기준(2026-10-12 삼성전자 2025년 보고서로 실측 — 추정 아님)
  - 1분기·반기·3분기 보고서: `thstrm_amount` = **당기 3개월**, `thstrm_add_amount` = **당기 누적**.
    검산: 1Q 66,853억 + 2Q 46,761억 = 반기 누적 113,613억, + 3Q 121,661억 = 3분기 누적 235,274억.
  - **사업보고서(11011): `thstrm_amount` 가 연간**이고 `thstrm_add_amount` 는 빈 값이다.
    그래서 4분기 3개월 금액은 **연간 − 3분기 누적**으로 계산한다(436,011 − 235,274 = 200,737억).
    이 차감 외의 금액은 만들어내지 않는다.
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
    # BPS 는 지배기업 소유주 귀속 자본으로 계산한다(비지배지분 제외). 없으면 자본총계로 폴백하고 라벨에 밝힌다.
    # 실측 표기(2026 반기, CFS): 아모레 "지배기업의 소유주에게 귀속되는 자본", SK하이닉스 "지배기업의 소유지분",
    # NAVER "지배기업 소유주지분", 현대차 "지배기업소유주지분". _norm 이 공백을 떼므로 공백 없는 형태로 적는다.
    "equityOwners": ("지배기업의소유주에게귀속되는자본", "지배기업소유주지분", "지배기업의소유주지분", "지배기업의소유지분", "지배기업소유지분",
                     "지배기업소유주귀속자본", "지배주주지분", "지배기업의소유주에게귀속되는자본합계", "지배기업소유주에게귀속되는자본"),
    "cash": ("현금및현금성자산", "현금및현금성자산등"),
}
_IS_TARGETS = {
    "operatingIncome": ("영업이익", "영업이익(손실)", "영업손익"),
    "revenue": ("매출액", "수익(매출액)", "영업수익"),
    # 실측(2026-10-14 아모텍 052710 CFS): 반기보고서는 "반기순이익(손실)", 1·3분기는 "당기순이익(손실)" 로 온다.
    "netIncome": ("당기순이익", "당기순이익(손실)", "분기순이익", "반기순이익", "분기순이익(손실)", "반기순이익(손실)",
                  "당기순손실", "분기순손실", "반기순손실"),
}
# 누적 칸 이름: _parse_statements 가 손익 항목마다 `<prefix>Cumulative`·`<prefix>PrevCumulative` 를 함께 담는다(TTM 계산용).
_IS_CUM_PREFIX = {"operatingIncome": "op", "revenue": "revenue", "netIncome": "net"}

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


def base_stock_code(code: str) -> tuple[str, bool]:
    """우선주 코드 → 보통주 코드. KRX 보통주는 끝자리 0, 우선주는 5·7·9 등(005935 삼성전자우, 005937 삼성전자2우B).
    DART 고유번호는 회사(보통주) 단위라 우선주 코드는 매핑에 없다. (보통주 코드, 우선주 여부) 를 돌려준다."""
    c = str(code or "")
    if re.fullmatch(r"\d{6}", c) and c[-1] != "0":
        return c[:5] + "0", True
    return c, False


async def resolve_corp(symbol: str) -> tuple[str, str, bool] | None:
    """symbol → (DART 고유번호, 조회에 쓴 종목코드, 우선주 여부). 우선주면 보통주 코드로 회사를 찾는다. 없으면 None."""
    code = stock_code_of(symbol)
    if not code or not is_enabled():
        return None
    mapping = await corp_code_map()
    corp = mapping.get(code)
    if corp:
        return corp, code, False
    base, preferred = base_stock_code(code)
    if preferred and mapping.get(base):
        return mapping[base], base, True
    return None


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


def _parse_statements(rows: list[dict], reprt_code: str = "") -> dict:
    """계정 행 → 재무상태표·손익 항목. 같은 계정이 여러 번 나오면 첫 값을 쓴다.

    `reprt_code` 가 사업보고서(11011)면 손익 `thstrm_amount` 는 **연간** 금액이다(실측).
    그 경우 3개월 금액으로 오해하지 않도록 기준을 `연간` 으로 표시하고 누적도 함께 담는다.
    """
    annual = reprt_code == "11011"
    out: dict[str, int | None] = {k: None for k in (*_BS_TARGETS, *_IS_TARGETS)}
    extra: dict[str, object] = {"opBasis": None, "periodName": None}
    for prefix in _IS_CUM_PREFIX.values():          # TTM 계산용 — 당기 누적 · 전기 동기간 누적(사업보고서는 연간 = 누적)
        extra[f"{prefix}Cumulative"] = extra[f"{prefix}PrevCumulative"] = None
    for row in rows:
        div = str(row.get("sj_div") or "").upper()
        name = _norm(row.get("account_nm"))
        if div == "BS":          # 자본총계는 SCE 에도 있으므로 BS 로 한정한다
            for key, names in _BS_TARGETS.items():
                if out[key] is None and name in {_norm(n) for n in names}:
                    out[key] = _amount(row, "thstrm_amount")
        elif div in ("IS", "CIS"):
            for key, names in _IS_TARGETS.items():
                if out[key] is None and name in {_norm(n) for n in names}:
                    value = _amount(row, "thstrm_amount")
                    basis = "연간" if annual else "3개월"
                    if value is None:                       # 드물게 당기 칸이 비는 경우
                        value = _amount(row, "thstrm_add_amount")
                        basis = "누적"
                    out[key] = value
                    if value is None:
                        continue
                    prefix = _IS_CUM_PREFIX[key]
                    # 사업보고서는 thstrm_amount 가 연간이라 누적 칸이 비어 있다 → 연간 금액을 누적으로 쓴다
                    extra[f"{prefix}Cumulative"] = value if annual else _amount(row, "thstrm_add_amount")
                    extra[f"{prefix}PrevCumulative"] = _amount(row, "frmtrm_amount") if annual else _amount(row, "frmtrm_add_amount")
                    if key == "operatingIncome":
                        extra["opBasis"] = basis
                        extra["periodName"] = str(row.get("thstrm_nm") or "").strip() or None
    return {**out, **extra}


async def fetch_financials(symbol: str, year: int, quarter: int) -> dict | None:
    """한 보고서의 재무상태표·영업이익. 연결(CFS)을 먼저 보고 없으면 별도(OFS)."""
    resolved = await resolve_corp(symbol)
    if not resolved:
        return None
    corp, code, preferred = resolved
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
        parsed = _parse_statements(body.get("list") or [], reprt)
        if parsed.get("assets") is None and parsed.get("operatingIncome") is None:
            continue
        return {
            **parsed,
            "corpCode": corp, "stockCode": code, "isPreferred": preferred,
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
    code = base_stock_code(code)[0]          # 우선주는 보통주(회사) 캐시를 공유
    cache_key = f"dart:fin:v3:{code}"   # v3: revenue/op 누적 추가(6-57)
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


async def quarterly_operating_income(symbol: str, periods: int = 4) -> list[dict] | None:
    """분기 영업이익 시계열(최근 → 과거 순서를 뒤집어 과거 → 최근).

    각 분기의 **3개월 금액**을 쓴다. 1~3분기는 보고서의 `thstrm_amount` 가 바로 3개월 금액이고,
    4분기는 DART 가 따로 주지 않으므로 **사업보고서 연간 − 3분기 누적**으로 계산한다(실측 근거는 모듈 docstring).
    계산에 필요한 값이 없으면 그 분기는 건너뛴다 — 추정치로 채우지 않는다.
    """
    if not is_enabled() or not is_krx(symbol):
        return None
    code = stock_code_of(symbol)
    if not code:
        return None
    code = base_stock_code(code)[0]
    cache_key = f"dart:opseries:v1:{code}:{periods}"
    cached = await cache_get(cache_key, max_age_hours=FIN_TTL_HOURS)
    if cached is not None:
        return (cached.get("series") or None) if isinstance(cached, dict) else None

    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone(timedelta(hours=9)))
    year, quarter = now.year, (now.month - 1) // 3 + 1
    wanted: list[tuple[int, int]] = []
    for _ in range(periods + 1):            # 공시 지연을 감안해 한 분기 더 뒤에서 시작
        quarter -= 1
        if quarter == 0:
            year, quarter = year - 1, 4
        wanted.append((year, quarter))

    series: list[dict] = []
    for cand_year, cand_quarter in wanted:
        try:
            report = await fetch_financials(symbol, cand_year, cand_quarter)
        except DartError as exc:
            if exc.status in ("010", "011", "020", "800"):
                return None
            continue
        except Exception as exc:
            logger.warning("DART 분기 영업이익 조회 실패 %s %dY%dQ: %s", symbol, cand_year, cand_quarter, exc)
            return None
        if not report or report.get("operatingIncome") is None:
            continue
        amount, basis = report["operatingIncome"], report.get("opBasis")
        if cand_quarter == 4 and basis == "연간":
            # 4분기 3개월 = 연간 − 3분기 누적. 3분기 보고서에서 누적을 받아 와야 한다.
            try:
                third = await fetch_financials(symbol, cand_year, 3)
            except Exception:
                third = None
            cumulative = (third or {}).get("opCumulative")
            if cumulative is None:
                continue                     # 누적을 못 받으면 4분기 금액을 만들지 않는다
            amount, basis = amount - cumulative, "연간−3분기누적"
        elif basis not in ("3개월", "연간−3분기누적"):
            continue                         # 누적만 있는 값을 분기로 섞지 않는다
        series.append({
            "label": f"{str(cand_year)[2:]}Q{cand_quarter}",
            "periodName": report.get("periodName"),
            "operatingIncome": amount,
            "basis": basis,
            "fsDiv": report.get("fsDiv"),
        })
        if len(series) >= periods:
            break

    series.reverse()                         # 과거 → 최근
    await cache_set(cache_key, {"series": series})
    return series or None


# ── 발행주식수 · 주당 지표 (EPS·BPS, 6-52) ─────────────────────────────────────
# Yahoo quoteSummary 는 국내 종목(특히 우선주)의 trailingEps/bookValue/priceToBook 을 비워 보낸다(2026-10-13 아모레퍼시픽·우 실측).
# DART 원문으로 직접 계산한다: EPS = 최근 4개 분기 순이익 합(TTM) ÷ 발행주식 총수(보통주+종류주),
# BPS = 지배기업 소유주 귀속 자본(없으면 자본총계) ÷ 발행주식 총수. PER·PBR 은 호출 시점 현재가로 나눈다.
# 우선주는 회사(보통주) 실적을 그대로 쓰므로 라벨에 "보통주 기준 회사 실적 ÷ 전체 주식수" 를 밝힌다.
_SE_KIND = {"보통주": "common", "종류주": "preferred", "우선주": "preferred", "합계": "total"}


def _parse_shares(rows: list[dict]) -> dict | None:
    """stockTotqySttus 행 → {common, preferred, total}. 발행주식 총수(istc_totqy) 기준. 합계가 없으면 보통+종류주."""
    out: dict[str, int] = {}
    for row in rows:
        kind = _SE_KIND.get(_norm(row.get("se")))
        if not kind:
            continue
        value = _amount(row, "istc_totqy")
        if value is not None and kind not in out:
            out[kind] = value
    if "total" not in out and ("common" in out or "preferred" in out):
        out["total"] = out.get("common", 0) + out.get("preferred", 0)
    return out if out.get("total") else None


async def shares_outstanding(symbol: str, year: int, quarter: int) -> dict | None:
    """보고서 기준 발행주식수(DART stockTotqySttus). 같은 보고서가 없으면 None — 다른 기간 값으로 메우지 않는다."""
    resolved = await resolve_corp(symbol)
    if not resolved:
        return None
    corp, code, _ = resolved
    cache_key = f"dart:shares:v1:{code}:{year}Q{quarter}"
    cached = await cache_get(cache_key, max_age_hours=FIN_TTL_HOURS * 30)
    if cached is not None:
        return cached or None
    try:
        body = await _get_json("stockTotqySttus.json", {"corp_code": corp, "bsns_year": str(year), "reprt_code": REPRT_CODES[quarter]})
    except DartError as exc:
        if exc.status == "013":
            await cache_set(cache_key, {})
            return None
        raise
    parsed = _parse_shares(body.get("list") or [])
    result = {**parsed, "year": year, "quarter": quarter, "reportName": _REPRT_NAME[REPRT_CODES[quarter]],
              "source": "금융감독원 전자공시(DART) stockTotqySttus"} if parsed else {}
    await cache_set(cache_key, result)
    return result or None


_TTM_LABEL = {"netIncome": "순이익", "operatingIncome": "영업이익", "revenue": "매출액"}


def ttm_amount(latest: dict, prev_annual: dict | None, key: str = "netIncome") -> tuple[int | None, str]:
    """최근 4개 분기 합(TTM). 사업보고서면 연간 그대로, 분·반기면 당기 누적 + 전기 연간 − 전기 동기간 누적.
    셋 중 하나라도 없으면 (None, 사유) — 연환산 같은 추정은 하지 않는다. key: netIncome·operatingIncome·revenue."""
    label = _TTM_LABEL.get(key, key)
    if latest.get("quarter") == 4:
        value = latest.get(key)
        return (value, "연간") if value is not None else (None, f"{label} 없음")
    prefix = _IS_CUM_PREFIX[key]
    cum, prev_cum = latest.get(f"{prefix}Cumulative"), latest.get(f"{prefix}PrevCumulative")
    prev_year = (prev_annual or {}).get(key)
    if cum is None:
        return None, f"당기 누적 {label} 없음"
    if prev_year is None or prev_cum is None:
        return None, "전기 사업보고서 또는 전기 동기간 누적 없음"
    return cum + prev_year - prev_cum, "최근 4개 분기 합산(TTM)"


def ttm_net_income(latest: dict, prev_annual: dict | None) -> tuple[int | None, str]:
    return ttm_amount(latest, prev_annual, "netIncome")


async def per_share_metrics(symbol: str) -> dict | None:
    """EPS·BPS 와 근거 라벨(가격 무관 → 캐시). 호출자가 현재가로 PER·PBR 을 만든다.
    반환 {eps, bps, epsBasis, bpsBasis, shares, sharesBasis, period, fsDiv, isPreferred, source, notes[]} 또는 None."""
    if not is_enabled() or not is_krx(symbol):
        return None
    code = stock_code_of(symbol)
    if not code:
        return None
    base, preferred = base_stock_code(code)
    cache_key = f"dart:pershare:v2:{base}"   # v2: 반기순이익(손실) 등 계정명 보강(6-57)
    cached = await cache_get(cache_key, max_age_hours=FIN_TTL_HOURS)
    if cached is not None:
        return ({**cached, "isPreferred": preferred} if cached else None)
    try:
        latest = await latest_financials(symbol)
        if not latest:
            await cache_set(cache_key, {})
            return None
        year, quarter = int(latest["year"]), int(latest["quarter"])
        prev_annual = None if quarter == 4 else await fetch_financials(symbol, year - 1, 4)
        shares = await shares_outstanding(symbol, year, quarter)
    except (DartError, httpx.HTTPError) as exc:
        logger.warning("DART 주당 지표 조회 실패 %s: %s", symbol, exc)
        return None
    notes: list[str] = []
    if not shares:
        await cache_set(cache_key, {})
        return None
    total = int(shares["total"])
    ni, ni_basis = ttm_net_income(latest, prev_annual)
    eps = round(ni / total, 2) if ni is not None and total > 0 else None
    if eps is None:
        notes.append(f"EPS 계산 불가: {ni_basis}")
    equity = latest.get("equityOwners")
    bps_basis = "지배기업 소유주 귀속 자본"
    if equity is None:
        equity, bps_basis = latest.get("equity"), "자본총계(비지배지분 포함)"
    bps = round(equity / total, 2) if equity is not None and total > 0 else None
    if bps is None:
        notes.append("BPS 계산 불가: 자본 항목 없음")
    period = f"{latest['year']} {latest['reportName']}"
    shares_basis = f"발행주식 총수 {total:,}주 (보통주 {shares.get('common', 0):,} + 종류주 {shares.get('preferred', 0):,}, {shares['reportName']} 기준)"
    result = {
        "eps": eps, "bps": bps, "epsBasis": ni_basis, "bpsBasis": bps_basis, "shares": total, "sharesBasis": shares_basis,
        "period": period, "fsDiv": latest.get("fsDiv"), "stockCode": latest.get("stockCode"), "notes": notes,
        "source": "DART 계산 · fnlttSinglAcntAll + stockTotqySttus",
    }
    await cache_set(cache_key, result)
    return {**result, "isPreferred": preferred}


# ── 수익성 지표 · 배당 (6-57) ─────────────────────────────────────────────────
# Yahoo quoteSummary 는 코스닥 소형주(2026-10-14 아모텍 실측)의 financialData(ROE·ROA·영업이익률·부채비율)와
# summaryDetail(배당)을 전부 비워 보낸다. 이미 받고 있는 DART 재무제표로 직접 계산한다 — 비율 정의는 아래에 밝힌다.
#   ROE = TTM 순이익 ÷ 지배기업 소유주 귀속 자본(없으면 자본총계)   ROA = TTM 순이익 ÷ 자산총계
#   영업이익률 = TTM 영업이익 ÷ TTM 매출액   순이익률 = TTM 순이익 ÷ TTM 매출액   부채비율 = 부채총계 ÷ 자본총계
# 분모가 기말 잔액(평균 아님)이라는 점을 라벨에 적는다. 값이 하나라도 없으면 그 지표는 None — 추정하지 않는다.
def _ratio(numer: int | None, denom: int | None) -> float | None:
    if numer is None or denom is None or denom == 0:
        return None
    return round(numer / denom * 100, 2)


async def profitability_metrics(symbol: str) -> dict | None:
    """ROE·ROA·영업이익률·순이익률·부채비율(%) 과 근거. 가격 무관 → 24h 캐시.
    반환 {roe, roa, opMargin, netMargin, debtRatio, bases{...}, notes[], period, fsDiv, source} 또는 None."""
    if not is_enabled() or not is_krx(symbol):
        return None
    code = stock_code_of(symbol)
    if not code:
        return None
    base = base_stock_code(code)[0]
    cache_key = f"dart:profit:v1:{base}"
    cached = await cache_get(cache_key, max_age_hours=FIN_TTL_HOURS)
    if cached is not None:
        return cached or None
    try:
        latest = await latest_financials(symbol)
        if not latest:
            await cache_set(cache_key, {})
            return None
        year, quarter = int(latest["year"]), int(latest["quarter"])
        prev_annual = None if quarter == 4 else await fetch_financials(symbol, year - 1, 4)
    except (DartError, httpx.HTTPError) as exc:
        logger.warning("DART 수익성 지표 조회 실패 %s: %s", symbol, exc)
        return None
    ni, ni_basis = ttm_amount(latest, prev_annual, "netIncome")
    op, op_basis = ttm_amount(latest, prev_annual, "operatingIncome")
    rev, rev_basis = ttm_amount(latest, prev_annual, "revenue")
    equity_owners, equity = latest.get("equityOwners"), latest.get("equity")
    roe_denom = equity_owners if equity_owners is not None else equity
    roe_denom_label = "지배기업 소유주 귀속 자본" if equity_owners is not None else "자본총계(비지배지분 포함)"
    notes: list[str] = []
    if ni is None:
        notes.append(f"ROE·ROA·순이익률 계산 불가: {ni_basis}")
    if op is None or rev is None:
        notes.append(f"영업이익률 계산 불가: {op_basis if op is None else rev_basis}")
    result = {
        "roe": _ratio(ni, roe_denom), "roa": _ratio(ni, latest.get("assets")),
        "opMargin": _ratio(op, rev), "netMargin": _ratio(ni, rev),
        "debtRatio": _ratio(latest.get("liabilities"), equity),
        "bases": {"roe": f"{ni_basis} 순이익 ÷ 기말 {roe_denom_label}", "roa": f"{ni_basis} 순이익 ÷ 기말 자산총계",
                  "opMargin": f"{op_basis} 영업이익 ÷ {rev_basis} 매출액", "netMargin": f"{ni_basis} 순이익 ÷ {rev_basis} 매출액",
                  "debtRatio": "기말 부채총계 ÷ 자본총계"},
        "notes": notes, "period": f"{latest['year']} {latest['reportName']}", "fsDiv": latest.get("fsDiv"),
        "stockCode": latest.get("stockCode"), "source": "DART 계산 · fnlttSinglAcntAll",
    }
    await cache_set(cache_key, result)
    return result


# alotMatter(배당에 관한 사항) 실측(2026-10-14 삼성전자·아모텍): se=구분, stock_knd=보통주/우선주/-, thstrm=당기, frmtrm=전기.
# 사업보고서(11011) 의 당기 값이 그 해 연간 배당이다. 분·반기보고서의 당기는 기중 누계라 쓰지 않는다. 배당이 없으면 '-' 로 온다.
_DIV_FIELDS = {"주당현금배당금(원)": "dps", "현금배당수익률(%)": "yieldReported", "(연결)현금배당성향(%)": "payoutRatio",
               "현금배당금총액(백만원)": "totalCashDividendMillion", "주당액면가액(원)": "parValue"}


def _parse_dividends(rows: list[dict], preferred: bool = False) -> dict | None:
    """alotMatter 행 → {dps, yieldReported, payoutRatio, totalCashDividendMillion, parValue, stockKind, noDividend}.
    주식 종류 행은 보통주(우선주 심볼이면 우선주)만 본다. 행 자체가 없으면 None."""
    want_kind = "우선주" if preferred else "보통주"
    out: dict = {}
    seen_dps_row = False
    for row in rows:
        field = _DIV_FIELDS.get(_norm(row.get("se")))
        if not field:
            continue
        kind = _norm(row.get("stock_knd")) or "-"
        if kind not in ("-", "") and kind != want_kind:
            continue
        if field in out:
            continue
        raw = str(row.get("thstrm") or "").strip()
        if field == "dps":
            seen_dps_row = True
        if not raw or raw in ("-", "–"):
            out[field] = None
            continue
        try:
            out[field] = float(raw.replace(",", ""))
        except ValueError:
            out[field] = None
    if not out:
        return None
    for field in _DIV_FIELDS.values():
        out.setdefault(field, None)
    # '주당 현금배당금' 행이 있는데 '-' 면 그 해 현금배당이 없다는 뜻 → 0 으로 확정한다(행이 없으면 모른다).
    if seen_dps_row and out.get("dps") is None:
        out["dps"], out["noDividend"] = 0.0, True
    else:
        out["noDividend"] = False
    out["stockKind"] = want_kind
    return out


async def dividend_info(symbol: str, lookback_years: int = 2) -> dict | None:
    """최근 사업보고서의 연간 배당(DART alotMatter). 반환 {dps, yieldReported, payoutRatio, ..., year, reportName, source} 또는 None.
    배당수익률은 호출자가 현재가로 다시 계산한다(공시 수익률은 결산일 주가 기준)."""
    if not is_enabled() or not is_krx(symbol):
        return None
    resolved = await resolve_corp(symbol)
    if not resolved:
        return None
    corp, code, preferred = resolved
    cache_key = f"dart:dividend:v1:{code}:{'p' if preferred else 'c'}"
    cached = await cache_get(cache_key, max_age_hours=FIN_TTL_HOURS)
    if cached is not None:
        return cached or None
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone(timedelta(hours=9)))
    for year in range(now.year - 1, now.year - 1 - lookback_years, -1):
        try:
            body = await _get_json("alotMatter.json", {"corp_code": corp, "bsns_year": str(year), "reprt_code": "11011"})
        except DartError as exc:
            if exc.status == "013":
                continue
            logger.warning("DART 배당 조회 실패 %s %d: %s", symbol, year, exc)
            return None
        except httpx.HTTPError as exc:
            logger.warning("DART 배당 통신 실패 %s: %s", symbol, exc)
            return None
        parsed = _parse_dividends(body.get("list") or [], preferred)
        if parsed:
            result = {**parsed, "year": year, "reportName": "사업보고서", "stockCode": code,
                      "source": "금융감독원 전자공시(DART) alotMatter"}
            await cache_set(cache_key, result)
            return result
    await cache_set(cache_key, {})
    return None
