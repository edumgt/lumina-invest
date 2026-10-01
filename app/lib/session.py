"""Redis 기반 세션 관리.

연결 관리는 app/lib/redis_cache.py 의 connect_redis/close_redis 에서 담당합니다.
이 모듈은 하위 호환을 위해 connect_redis/close_redis 를 재내보냅니다.

세션 키 형식: fin_session:{sid}
슬라이딩 만료: 인증된 요청마다 TTL을 갱신해 마지막 활동으로부터 SESSION_TTL 후 만료됩니다.
  - Redis 에는 EXPIRE 만 보내고(데이터 재기록 없음), SESSION_REFRESH_INTERVAL 마다 1회만 갱신합니다.
  - 갱신이 일어난 요청은 request.state 에 표시해 두고, SessionCookieRefreshMiddleware 가 응답에
    같은 sid 의 Set-Cookie 를 다시 실어 브라우저 쪽 쿠키 만료(max_age)도 함께 연장합니다.
    (쿠키 max_age 를 로그인 시점에만 설정하면 서버 세션이 살아 있어도 브라우저가 쿠키를 먼저 버립니다.)

멀티 디바이스: 동일 user_id 로 여러 세션(sid)이 공존할 수 있습니다.
  사용자별 세션 목록 키: fin_session_list:{user_id} → Set of sid
"""
import uuid
from typing import Optional

from fastapi import Cookie, HTTPException, Request, Response, status
from redis.exceptions import RedisError

from app.config import settings
from app.lib.redis_cache import (
    connect_redis,   # 재내보내기 – main.py 가 여기서 import
    close_redis,     # 재내보내기
    get_redis,
    session_cache,
)

__all__ = [
    "connect_redis",
    "close_redis",
    "COOKIE_NAME",
    "create_session",
    "get_session",
    "refresh_session",
    "touch_session",
    "delete_session",
    "delete_all_user_sessions",
    "list_user_sessions",
    "set_session_cookie",
    "clear_session_cookie",
    "mark_cookie_refresh",
    "get_current_user",
    "get_optional_user",
    "SessionCookieRefreshMiddleware",
]

COOKIE_NAME = settings.SESSION_COOKIE_NAME
_STATE_KEY = "refresh_session_cookie"


def _list_key(user_id: str) -> str:
    """사용자의 모든 세션 ID를 보관하는 Redis Set 키."""
    return f"fin_session_list:{user_id}"


def _redis_unavailable(e: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=f"세션 저장소(Redis)에 연결할 수 없습니다: {e}",
    )


# ── 세션 CRUD ──────────────────────────────────────────────────────────────────

async def create_session(user_data: dict) -> str:
    """새 세션을 생성하고 세션 ID(sid)를 반환합니다."""
    sid = str(uuid.uuid4())
    await session_cache.set(sid, user_data, ttl=settings.SESSION_TTL)

    # 사용자별 세션 목록에 등록
    r = get_redis()
    list_key = _list_key(user_data["id"])
    await r.sadd(list_key, sid)
    await r.expire(list_key, settings.SESSION_TTL)

    return sid


async def get_session(sid: str) -> Optional[dict]:
    """세션 ID로 사용자 데이터를 조회합니다."""
    return await session_cache.get(sid)


async def refresh_session(sid: str, *, force: bool = False) -> bool:
    """슬라이딩 만료: 세션 TTL을 현재 시각 기준으로 재설정합니다.

    세션이 존재하지 않으면 False 를 반환합니다.
    마지막 갱신 후 SESSION_REFRESH_INTERVAL 이 지나지 않았으면 Redis 쓰기를 생략하고
    False 를 반환합니다 (force=True 면 항상 갱신).
    갱신을 실제로 수행했으면 True 를 반환합니다.
    """
    remaining = await session_cache.ttl(sid)
    if remaining < 0:
        # -2: 키 없음, -1: TTL 없음(비정상) → 갱신 대상 아님
        return False
    if not force and settings.SESSION_TTL - remaining < settings.SESSION_REFRESH_INTERVAL:
        return False

    await session_cache.expire(sid, settings.SESSION_TTL)
    data = await session_cache.get(sid)
    if data and data.get("id"):
        await get_redis().expire(_list_key(data["id"]), settings.SESSION_TTL)
    return True


async def touch_session(request: Optional[Request], sid: str) -> None:
    """인증 성공 시 호출: TTL 을 슬라이딩 갱신하고, 갱신됐으면 쿠키 재발급을 예약합니다."""
    try:
        refreshed = await refresh_session(sid)
    except RedisError:
        # 갱신 실패는 인증 자체를 막지 않는다 (다음 요청에서 재시도)
        return
    if refreshed and request is not None:
        mark_cookie_refresh(request, sid)


async def delete_session(sid: str) -> None:
    """단일 세션을 삭제합니다."""
    data = await session_cache.get(sid)
    if data:
        r = get_redis()
        await r.srem(_list_key(data["id"]), sid)
    await session_cache.delete(sid)


async def delete_all_user_sessions(user_id: str) -> int:
    """사용자의 모든 세션을 삭제합니다 (강제 로그아웃)."""
    r = get_redis()
    list_key = _list_key(user_id)
    sids = await r.smembers(list_key)
    for sid in sids:
        await session_cache.delete(sid)
    await r.delete(list_key)
    return len(sids)


async def list_user_sessions(user_id: str) -> list[str]:
    """사용자의 활성 세션 ID 목록을 반환합니다."""
    r = get_redis()
    return list(await r.smembers(_list_key(user_id)))


# ── 쿠키 헬퍼 ─────────────────────────────────────────────────────────────────

def set_session_cookie(response: Response, sid: str) -> None:
    """세션 쿠키를 발급합니다. 로그인/회원가입/슬라이딩 갱신에서 동일한 속성을 사용합니다."""
    response.set_cookie(
        COOKIE_NAME, sid,
        max_age=settings.SESSION_TTL,
        path="/",
        httponly=True,
        samesite=settings.COOKIE_SAMESITE,
        secure=settings.COOKIE_SECURE,
    )


def clear_session_cookie(response: Response) -> None:
    """세션 쿠키를 삭제합니다. 발급 때와 같은 속성이어야 브라우저가 같은 쿠키로 인식합니다."""
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        httponly=True,
        samesite=settings.COOKIE_SAMESITE,
        secure=settings.COOKIE_SECURE,
    )


def mark_cookie_refresh(request: Request, sid: str) -> None:
    """이 요청의 응답에 세션 쿠키를 다시 실어 보내도록 표시합니다 (미들웨어가 처리)."""
    request.state.refresh_session_cookie = sid


def _session_cookie_header(sid: str) -> str:
    """set_session_cookie 와 동일한 속성의 Set-Cookie 헤더 값을 만듭니다."""
    tmp = Response()
    set_session_cookie(tmp, sid)
    return tmp.headers["set-cookie"]


class SessionCookieRefreshMiddleware:
    """슬라이딩 만료로 서버 세션 TTL 이 연장된 요청의 응답에 Set-Cookie 를 추가하는 순수 ASGI 미들웨어.

    request.state 는 scope["state"] 를 그대로 사용하므로, 의존성(get_current_user 등)에서
    mark_cookie_refresh() 로 기록한 sid 를 응답 시작 시점에 읽어 헤더를 덧붙입니다.
    BaseHTTPMiddleware 를 쓰지 않아 스트리밍 응답·백그라운드 태스크에 영향을 주지 않습니다.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        state = scope.setdefault("state", {})

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                sid = state.get(_STATE_KEY)
                if sid:
                    headers = list(message.get("headers", []))
                    headers.append((b"set-cookie", _session_cookie_header(sid).encode("latin-1")))
                    message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


# ── FastAPI Depends ────────────────────────────────────────────────────────────

async def get_current_user(
    request: Request,
    fin_session: Optional[str] = Cookie(default=None, alias=COOKIE_NAME),
) -> dict:
    """세션 쿠키로 현재 사용자를 반환합니다 (슬라이딩 만료 포함)."""
    if not fin_session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="로그인이 필요합니다.",
        )
    try:
        user = await get_session(fin_session)
    except RedisError as e:
        raise _redis_unavailable(e)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="세션이 만료되었습니다.",
        )
    # 슬라이딩 만료: 유효한 요청마다 TTL(및 필요 시 쿠키) 갱신
    await touch_session(request, fin_session)
    return user


async def get_optional_user(
    request: Request,
    fin_session: Optional[str] = Cookie(default=None, alias=COOKIE_NAME),
) -> Optional[dict]:
    """인증이 선택적인 엔드포인트용 – 미인증 시 None 반환."""
    if not fin_session:
        return None
    try:
        user = await get_session(fin_session)
    except RedisError:
        return None
    if user:
        await touch_session(request, fin_session)
    return user
