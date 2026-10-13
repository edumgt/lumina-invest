"""domain-rag-lab 전략 스펙 API 클라이언트 (계약서 1절). 파일 공유 대신 HTTP로 받고 프로세스 내 TTL 캐시를 둔다.

종목 선정 화면의 전략 드롭다운(list_strategies)과 사이클의 규칙 평가(get_strategy)가 사용한다.
domain-rag-lab 이 설정되지 않았거나 응답이 없으면 None/[] 를 돌려주고 호출자는 기존 하드코딩 규칙으로 폴백한다.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_cache: dict[str, tuple[float, Any]] = {}
_transport: httpx.AsyncBaseTransport | None = None


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    global _transport
    _transport = transport


def is_configured() -> bool:
    return bool(settings.DOMAIN_RAG_LAB_BASE_URL)


def clear_cache() -> None:
    _cache.clear()


async def _get(path: str) -> Any | None:
    if not is_configured():
        return None
    now = time.time()
    hit = _cache.get(path)
    if hit and hit[0] > now:
        return hit[1]
    headers = {"Accept": "application/json"}
    if settings.DOMAIN_RAG_LAB_API_KEY:
        headers["X-API-Key"] = settings.DOMAIN_RAG_LAB_API_KEY
    try:
        async with httpx.AsyncClient(base_url=settings.DOMAIN_RAG_LAB_BASE_URL.rstrip("/"), headers=headers, timeout=10, transport=_transport) as cli:
            response = await cli.get(path)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("domain-rag-lab 전략 API 실패 (%s): %s", path, exc)
        return hit[1] if hit else None  # 만료된 캐시라도 있으면 사용
    _cache[path] = (now + max(0, settings.STRATEGY_SPEC_CACHE_TTL), data)
    return data


async def list_strategies() -> list[dict[str, Any]]:
    data = await _get("/backtests/strategies")
    return list((data or {}).get("strategies") or [])


async def get_strategy(strategy_id: str, version: int | None = None) -> dict[str, Any] | None:
    if not strategy_id:
        return None
    path = f"/backtests/strategies/{strategy_id}" + (f"/versions/{version}" if version else "")
    return await _get(path)


async def list_versions(strategy_id: str) -> dict[str, Any] | None:
    """스펙의 모든 버전(합격·불합격 포함). pr `GET /backtests/strategies/{id}/versions` (계약서 1절, v0.6)."""
    if not strategy_id:
        return None
    return await _get(f"/backtests/strategies/{strategy_id}/versions")


async def revalidate(strategy_id: str, *, ticker: str | None = None, start_date: str | None = None,
                     end_date: str | None = None) -> tuple[int, Any]:
    """pr 에서 최신 합격 스펙의 규칙으로 백테스트를 다시 돌려 새 버전을 만든다 (계약서 1절 revalidate).

    (status, body). 201 = 새 합격 버전, 422 = 불합격(body.detail.failed) 또는 검증 오류, 0 = 미설정/연결 실패.
    성공·실패와 무관하게 캐시를 비운다(새 버전·목록 반영).
    """
    if not is_configured():
        return 0, {"detail": "domain-rag-lab 이 설정되지 않았습니다(DOMAIN_RAG_LAB_BASE_URL)."}
    headers = {"Accept": "application/json"}
    if settings.DOMAIN_RAG_LAB_API_KEY:
        headers["X-API-Key"] = settings.DOMAIN_RAG_LAB_API_KEY
    body = {k: v for k, v in {"ticker": ticker, "start_date": start_date, "end_date": end_date}.items() if v}
    try:
        async with httpx.AsyncClient(base_url=settings.DOMAIN_RAG_LAB_BASE_URL.rstrip("/"), headers=headers,
                                     timeout=max(30, int(getattr(settings, "STRATEGY_REVALIDATE_TIMEOUT", 420))), transport=_transport) as cli:
            response = await cli.post(f"/backtests/strategies/{strategy_id}/revalidate", json=body)
        try:
            data = response.json()
        except ValueError:
            data = {"detail": response.text[:300]}
    except httpx.HTTPError as exc:
        logger.warning("domain-rag-lab 재검증 실패 (%s): %s", strategy_id, exc)
        return 0, {"detail": f"pr 연결 실패: {exc}"}
    finally:
        clear_cache()
    return response.status_code, data


def public_url() -> str:
    return (getattr(settings, "DOMAIN_RAG_LAB_PUBLIC_URL", "") or "").rstrip("/")
