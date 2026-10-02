"""LEAN 백테스트를 domain-rag-lab(`POST /backtests/run`)으로 위임한다 — LEAN 정본 일원화(2026-10-02 결정 L5/R1).

`DOMAIN_RAG_LAB_BASE_URL` 이 설정되면 원격이 1순위, 연결 실패·5xx 면 로컬 `lean_backtest.service.run` 으로 폴백한다.
원격 응답은 lumina 화면·LeanBacktestRun 저장이 기대하는 키(start_date, end_date, lean_ok, lean_mode)를 보강한다.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

import httpx

from app.config import settings
from app.services.lean_backtest import LeanBacktestError

logger = logging.getLogger(__name__)
_transport: httpx.AsyncBaseTransport | None = None


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    global _transport
    _transport = transport


def is_enabled() -> bool:
    return bool(getattr(settings, "DOMAIN_RAG_LAB_BASE_URL", ""))


def remote_status() -> dict[str, Any]:
    return {"enabled": is_enabled(), "base_url": settings.DOMAIN_RAG_LAB_BASE_URL or None, "api_key": bool(settings.DOMAIN_RAG_LAB_API_KEY)}


def _headers() -> dict[str, str]:
    h = {"Accept": "application/json"}
    if settings.DOMAIN_RAG_LAB_API_KEY:
        h["X-API-Key"] = settings.DOMAIN_RAG_LAB_API_KEY
    return h


def normalize_remote_result(payload: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    engine = str(body.get("engine") or "")
    return {
        **body,
        "start_date": str(payload.get("start_date")), "end_date": str(payload.get("end_date")),
        "lean_ok": bool(body.get("lean_ok", "LEAN" in engine.upper())),
        "lean_mode": "remote:domain-rag-lab",
        "source": "domain-rag-lab",
    }


async def run_remote(payload: dict[str, Any]) -> dict[str, Any]:
    """domain-rag-lab 로 백테스트 실행. 422(검증 실패)는 LeanBacktestError, 연결·5xx 는 httpx.HTTPError 로 올린다."""
    body = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in payload.items()}
    async with httpx.AsyncClient(base_url=settings.DOMAIN_RAG_LAB_BASE_URL.rstrip("/"), headers=_headers(),
                                 timeout=float(getattr(settings, "LEAN_TIMEOUT_SECONDS", 300) or 300) + 30, transport=_transport) as cli:
        response = await cli.post("/backtests/run", json=body)
    if response.status_code == 422:
        detail = response.json().get("detail") if response.headers.get("content-type", "").startswith("application/json") else response.text
        raise LeanBacktestError(str(detail))
    response.raise_for_status()
    return normalize_remote_result(payload, response.json())


async def run_or_fallback(payload: dict[str, Any], local_run: Callable[..., Awaitable[dict[str, Any]]]) -> dict[str, Any]:
    if not is_enabled():
        return await local_run(**payload)
    try:
        return await run_remote(payload)
    except LeanBacktestError:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("domain-rag-lab 백테스트 실패 → 로컬 LEAN 폴백: %s", exc)
        result = await local_run(**payload)
        return {**result, "fallback_reason": f"remote: {exc}"}
