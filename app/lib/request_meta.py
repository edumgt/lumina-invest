"""요청 메타(클라이언트 IP·User-Agent) — 사람이 낸 주문(수동·리밸런싱)의 추적용 (6-49).

Caddy 프록시 뒤라 X-Forwarded-For 의 첫 값을 쓴다. 라우트에서 bind(request) 로 ContextVar 에 넣어 두면
서비스 계층(rebalance.execute 등)이 current() 로 꺼내 쓴다 — 시그니처를 바꾸지 않고 전달하기 위해. 배치·Celery 에서는 빈 값.
"""
from __future__ import annotations

from contextvars import ContextVar

from fastapi import Request

_EMPTY = {"client_ip": "", "user_agent": ""}
_meta_var: ContextVar[dict[str, str]] = ContextVar("request_meta", default=_EMPTY)


def client_meta(request: Request | None) -> dict[str, str]:
    if request is None:
        return dict(_EMPTY)
    fwd = request.headers.get("x-forwarded-for", "")
    ip = fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "")
    return {"client_ip": (ip or "")[:64], "user_agent": (request.headers.get("user-agent", "") or "")[:200]}


def bind(request: Request | None) -> dict[str, str]:
    meta = client_meta(request)
    _meta_var.set(meta)
    return meta


def current() -> dict[str, str]:
    return dict(_meta_var.get())
