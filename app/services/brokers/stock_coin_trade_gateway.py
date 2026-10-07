"""stock-coin-trade KIS 자동매매 Open API 클라이언트 (서버 간 호출).

계약: docs/contracts/kis-autotrade-api.md (세 저장소 공통). 주문은 2단계다.
  ① POST /openapi/v1/kis/order-approval  → 60초 1회용 승인 토큰 (주문 의도 해시에 묶임)
  ② POST /openapi/v1/kis/orders          → 승인 토큰 + 동일 본문 + clientOrderId(멱등키)

규칙
  - 주문(②)은 재시도하지 않는다. 연결 오류면 GatewayError(code="GATEWAY_UNREACHABLE")를 올리고 호출자가
    체결 조회로 확인한다. 승인 토큰 발급·조회·잔고는 1회 재시도한다.
  - ①과 ② 사이에 수량·가격을 바꾸지 않는다 (바꾸면 403 APPROVAL_INVALID).
  - 이 모듈은 DB·Celery에 의존하지 않는다. 테스트는 ``set_transport()``로 httpx MockTransport를 꽂는다.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
_transport: httpx.AsyncBaseTransport | None = None  # 테스트용 주입 지점


class GatewayError(Exception):
    def __init__(self, message: str, code: str = "GATEWAY_ERROR", status_code: int = 502, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.details = details or {}


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    global _transport
    _transport = transport


def is_configured() -> bool:
    return bool(settings.STOCK_COIN_TRADE_BASE_URL and settings.STOCK_COIN_TRADE_API_KEY)


def environment() -> str:
    env = (settings.STOCK_COIN_TRADE_KIS_ENVIRONMENT or "paper").strip().lower()
    return env if env in ("paper", "real") else "paper"


def default_order_type() -> str:
    ot = (settings.STOCK_COIN_TRADE_ORDER_TYPE or "LIMIT").strip().upper()
    return ot if ot in ("LIMIT", "MARKET") else "LIMIT"


KRX_OPEN = (9, 0)     # 정규장 시작 09:00 KST
KRX_CLOSE = (15, 30)  # 정규장 종료 15:30 KST (15:20~15:30 종가 단일가 포함)


# KRX 2026 휴장일 17일. 2026-10-02 웹 공개 일정(jangjeon.kr/events/holidays, glasswallet 2026 휴장일 안내)과 대조함.
# 대조 결과: 제헌절(7/17) 추가, 추석 대체공휴일로 넣었던 9/28 은 출처 목록에 없어 제외. KRX 공식 공시와 다르면 KRX_EXTRA_HOLIDAYS 로 보완한다.
KRX_HOLIDAYS_2026: frozenset[str] = frozenset({
    "2026-01-01",                                # 신정
    "2026-02-16", "2026-02-17", "2026-02-18",    # 설 연휴
    "2026-03-02",                                # 삼일절 대체공휴일 (3/1 일요일)
    "2026-05-01",                                # 근로자의 날 (KRX 휴장)
    "2026-05-05",                                # 어린이날
    "2026-05-25",                                # 부처님오신날 대체공휴일 (5/24 일요일)
    "2026-06-03",                                # 전국동시지방선거
    "2026-07-17",                                # 제헌절 (2026 공휴일 재지정)
    "2026-08-17",                                # 광복절 대체공휴일 (8/15 토요일)
    "2026-09-24", "2026-09-25",                  # 추석 연휴 (9/26 토요일)
    "2026-10-05",                                # 개천절 대체공휴일 (10/3 토요일)
    "2026-10-09",                                # 한글날
    "2026-12-25",                                # 성탄절
    "2026-12-31",                                # 연말 휴장
})


def krx_holidays() -> set[str]:
    extra = {d.strip() for d in (getattr(settings, "KRX_EXTRA_HOLIDAYS", "") or "").split(",") if d.strip()}
    return set(KRX_HOLIDAYS_2026) | extra


def is_krx_market_open(now: datetime | None = None) -> bool:
    """KRX 정규장 시간(평일 09:00~15:30 KST, 휴장일 제외) 여부."""
    kst = (now or datetime.now(KST)).astimezone(KST)
    if kst.weekday() >= 5 or kst.strftime("%Y-%m-%d") in krx_holidays():
        return False
    minutes = kst.hour * 60 + kst.minute
    return KRX_OPEN[0] * 60 + KRX_OPEN[1] <= minutes < KRX_CLOSE[0] * 60 + KRX_CLOSE[1]


def enforce_market_hours() -> bool:
    return bool(getattr(settings, "STOCK_COIN_TRADE_ENFORCE_MARKET_HOURS", True))


def normalize_symbol(symbol: str) -> str:
    """lumina 내부 표기(005930.KS / 005930.KQ)를 KRX 6자리 코드로."""
    code = str(symbol or "").strip().upper()
    for suffix in (".KS", ".KQ"):
        if code.endswith(suffix):
            code = code[: -len(suffix)]
    if len(code) != 6 or not code.isdigit():
        raise GatewayError(f"KRX 6자리 종목코드가 아닙니다: {symbol}", "INVALID_SYMBOL", 400)
    return code


def normalize_side(side: str) -> str:
    s = str(side or "").strip().upper()
    if s not in ("BUY", "SELL"):
        raise GatewayError(f"side는 buy/sell 이어야 합니다: {side}", "INVALID_SIDE", 400)
    return s


def tick_size(price: int) -> int:
    """KRX 호가 단위 (2023-01-25 개편, 전 시장 공통). stock-coin-trade kis._kis_tick_size 와 동일."""
    if price < 2_000:
        return 1
    if price < 5_000:
        return 5
    if price < 20_000:
        return 10
    if price < 50_000:
        return 50
    if price < 200_000:
        return 100
    if price < 500_000:
        return 500
    return 1_000


def align_price_to_tick(price: float, side: str) -> int:
    """지정가를 호가 단위에 맞춘다. 매수는 올림(체결 확률↑), 매도는 내림."""
    base = int(price)
    tick = tick_size(base)
    down = (base // tick) * tick
    if normalize_side(side) == "BUY" and down < price:
        return down + tick
    return down


def make_client_order_id(user_id: str, symbol: str, side: str, now: datetime | None = None) -> str:
    """멱등키. 사이클(기본 3분) 1회 + 쿨다운 전제에서 (사용자, 종목, 방향, 분) 이 유일하다. 최대 64자."""
    stamp = (now or datetime.now(KST)).astimezone(KST).strftime("%Y%m%d%H%M")
    uid = str(user_id).replace("-", "")[:12]
    return f"{uid}:{normalize_symbol(symbol)}:{normalize_side(side)[0]}:{stamp}"[:64]


def _client(timeout: float | None = None) -> httpx.AsyncClient:
    if not is_configured():
        raise GatewayError("STOCK_COIN_TRADE_BASE_URL / STOCK_COIN_TRADE_API_KEY 가 설정되지 않았습니다.", "GATEWAY_NOT_CONFIGURED", 503)
    return httpx.AsyncClient(
        base_url=settings.STOCK_COIN_TRADE_BASE_URL.rstrip("/"),
        headers={"Authorization": f"Bearer {settings.STOCK_COIN_TRADE_API_KEY}", "Accept": "application/json"},
        timeout=timeout or settings.STOCK_COIN_TRADE_TIMEOUT,
        transport=_transport,
    )


def _raise_for_body(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as exc:
        raise GatewayError(f"게이트웨이가 JSON을 반환하지 않았습니다 (HTTP {response.status_code})", "GATEWAY_INVALID_RESPONSE", 502) from exc
    if response.status_code >= 400 or body.get("ok") is False:
        code = str(body.get("error") or f"HTTP_{response.status_code}")
        details = {k: v for k, v in body.items() if k not in ("ok", "error", "message")}
        raise GatewayError(str(body.get("message") or code), code, response.status_code, details)
    return body


async def _call(method: str, path: str, *, json: dict | None = None, params: dict | None = None, retry: bool,
                timeout: float | None = None) -> dict[str, Any]:
    attempts = 2 if retry else 1
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            async with _client(timeout) as cli:
                response = await cli.request(method, path, json=json, params=params)
            return _raise_for_body(response)
        except httpx.HTTPError as exc:
            last_exc = exc
            logger.warning("stock-coin-trade 게이트웨이 연결 실패 (%s %s, 시도 %d): %s", method, path, attempt + 1, exc)
    raise GatewayError(f"게이트웨이에 연결할 수 없습니다: {last_exc}", "GATEWAY_UNREACHABLE", 503)


def build_intent(symbol: str, side: str, quantity: int, price: float, *, client_order_id: str,
                 order_type: str | None = None, env: str | None = None) -> dict[str, Any]:
    ot = (order_type or default_order_type()).upper()
    side_u = normalize_side(side)
    if int(quantity) < 1:
        raise GatewayError("quantity는 1 이상이어야 합니다.", "INVALID_QUANTITY", 400)
    return {
        "environment": env or environment(),
        "symbol": normalize_symbol(symbol),
        "side": side_u,
        "orderType": ot,
        "quantity": int(quantity),
        "price": align_price_to_tick(price, side_u) if ot == "LIMIT" else 0,
        "clientOrderId": client_order_id,
    }


async def place_order(symbol: str, side: str, quantity: int, price: float, *, client_order_id: str,
                      order_type: str | None = None, env: str | None = None) -> dict[str, Any]:
    """승인 토큰 → 주문. 반환: {"order": {...계약서 order...}, "duplicate": bool, "intent": {...}}"""
    intent = build_intent(symbol, side, quantity, price, client_order_id=client_order_id, order_type=order_type, env=env)
    order_timeout = float(getattr(settings, "STOCK_COIN_TRADE_ORDER_TIMEOUT", 0) or 0) or None
    approval = await _call("POST", "/openapi/v1/kis/order-approval", json=intent, retry=True, timeout=order_timeout)
    token = approval.get("approvalToken")
    if not token:
        raise GatewayError("승인 토큰을 받지 못했습니다.", "APPROVAL_MISSING", 502)
    body = await _call("POST", "/openapi/v1/kis/orders", json={**intent, "approvalToken": token}, retry=False, timeout=order_timeout)
    return {"order": body.get("order") or {}, "duplicate": bool(body.get("duplicate")), "intent": intent}


async def get_order_status(order_no: str, env: str | None = None) -> dict[str, Any]:
    body = await _call("GET", f"/openapi/v1/kis/orders/{order_no}", params={"environment": env or environment()}, retry=True)
    return body.get("order") or {}


async def list_today_orders(env: str | None = None, open_only: bool = False) -> list[dict[str, Any]]:
    body = await _call("GET", "/openapi/v1/kis/orders", params={"environment": env or environment(), "status": "OPEN" if open_only else "ALL"}, retry=True)
    return list(body.get("orders") or [])


async def cancel_order(order_no: str, env: str | None = None) -> dict[str, Any]:
    body = await _call("DELETE", f"/openapi/v1/kis/orders/{order_no}", params={"environment": env or environment()}, retry=False)
    return body.get("order") or {}


async def get_balance(env: str | None = None) -> dict[str, Any]:
    body = await _call("GET", "/openapi/v1/kis/balance", params={"environment": env or environment()}, retry=True)
    return body.get("balance") or {}
