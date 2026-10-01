"""인증 라우터.

세션 쿠키 방식(기존)과 JWT Bearer 방식(신규)을 모두 지원합니다.

쿠키 방식 (브라우저):
  POST /api/auth/register  – 회원가입 + 세션 쿠키 발급
  POST /api/auth/login     – 로그인 + 세션 쿠키 발급
  POST /api/auth/logout    – 로그아웃 + 쿠키 삭제 (세션이 이미 만료돼 있어도 쿠키는 지운다)
  세션은 슬라이딩 만료: 인증된 요청이 있을 때마다 서버 TTL 과 브라우저 쿠키 만료가 함께 연장된다
  (app/lib/session.py 의 touch_session + SessionCookieRefreshMiddleware).

JWT 방식 (API 클라이언트 / 모바일):
  POST /api/auth/token         – 로그인 → access_token + refresh_token 반환
  POST /api/auth/token/refresh – refresh_token → 새 access_token + 새 refresh_token 발급(슬라이딩)
  POST /api/auth/token/revoke  – 토큰 폐기 (블랙리스트 등록)

공용:
  GET  /api/me       – 현재 사용자 정보 (쿠키 또는 Bearer 모두 허용)
  GET  /api/sessions – 내 활성 세션 목록
  DELETE /api/sessions/{sid} – 특정 세션 강제 만료
"""
import uuid

import bcrypt
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.database.postgres import get_session_factory
from app.models import User
from app.lib.jwt_auth import (
    create_token_pair,
    decode_token,
    get_current_user_any,
    is_revoked,
    require_roles,
    revoke_token,
)
from app.lib.session import (
    COOKIE_NAME,
    clear_session_cookie,
    create_session,
    delete_all_user_sessions,
    delete_session,
    get_session,
    list_user_sessions,
    set_session_cookie,
)
from app.lib.user_state import clear_user_state, mark_offline, mark_online

router = APIRouter(prefix="/api")


# ── 요청/응답 스키마 ───────────────────────────────────────────────────────────

class RegisterBody(BaseModel):
    name: str
    email: EmailStr
    password: str


class LoginBody(BaseModel):
    email: str
    password: str


class TokenRefreshBody(BaseModel):
    refresh_token: str


class TokenRevokeBody(BaseModel):
    access_token: str
    refresh_token: str | None = None


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────

def _build_session_data(user_id: str, user: dict) -> dict:
    return {
        "id": user_id,
        "name": user["name"],
        "email": user["email"],
        "client_id": user.get("client_id", ""),
        "roles": user.get("roles", ["user"]),
    }


async def _find_user_by_email(db, email: str) -> User | None:
    try:
        result = await db.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()
    except Exception as e:
        raise HTTPException(503, f"데이터베이스 오류: {e}")


def _user_to_dict(user: User) -> dict:
    return {
        "name": user.name,
        "email": user.email,
        "client_id": user.client_id,
        "roles": list(user.roles),
    }


# ── 쿠키 기반 인증 ─────────────────────────────────────────────────────────────

@router.post("/auth/register")
async def register(body: RegisterBody, response: Response):
    try:
        session_factory = get_session_factory()
    except RuntimeError:
        raise HTTPException(503, "인증 서버(PostgreSQL)에 연결할 수 없습니다.")

    pw_hash = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt()).decode()
    client_id = str(uuid.uuid4()).replace("-", "")[:16].upper()
    roles = ["admin"] if body.email in settings.admin_email_list else ["user"]

    async with session_factory() as db:
        user = User(
            name=body.name, email=body.email, password_hash=pw_hash,
            client_id=client_id, roles=roles,
        )
        db.add(user)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(400, "이미 사용 중인 이메일입니다.")
        user_id = str(user.id)

    session_data = _build_session_data(user_id, {
        "name": body.name, "email": body.email, "client_id": client_id, "roles": roles,
    })
    sid = await create_session(session_data)
    await mark_online(user_id)

    set_session_cookie(response, sid)
    return {"ok": True, "user": {"name": body.name, "email": body.email,
                                  "clientId": client_id, "roles": roles}}


@router.post("/auth/login")
async def login(body: LoginBody, response: Response):
    try:
        session_factory = get_session_factory()
    except RuntimeError:
        raise HTTPException(503, "인증 서버(PostgreSQL)에 연결할 수 없습니다.")

    async with session_factory() as db:
        user = await _find_user_by_email(db, body.email)
        if not user or not bcrypt.checkpw(body.password.encode(), user.password_hash.encode()):
            raise HTTPException(401, "이메일 또는 비밀번호가 올바르지 않습니다.")
        user_id = str(user.id)
        user_dict = _user_to_dict(user)

    session_data = _build_session_data(user_id, user_dict)
    sid = await create_session(session_data)
    await mark_online(user_id)

    set_session_cookie(response, sid)
    return {"ok": True, "user": {"name": user_dict["name"], "email": user_dict["email"],
                                  "clientId": user_dict["client_id"],
                                  "roles": user_dict["roles"]}}


@router.post("/auth/logout")
async def logout(
    response: Response,
    fin_session: str | None = Cookie(default=None, alias=COOKIE_NAME),
):
    """세션을 삭제하고 쿠키를 지웁니다.

    세션이 이미 만료됐거나 Redis 에 없어도 401 을 내지 않고 쿠키만 정리합니다
    (만료된 쿠키가 브라우저에 남아 로그아웃이 실패하는 상황 방지).
    """
    if fin_session:
        session = await get_session(fin_session)
        if session:
            await delete_session(fin_session)
            await mark_offline(session["id"])
    clear_session_cookie(response)
    return {"ok": True}


# ── JWT 기반 인증 ──────────────────────────────────────────────────────────────

@router.post("/auth/token")
async def issue_token(body: LoginBody):
    """JWT 액세스/리프레시 토큰을 발급합니다 (API 클라이언트용)."""
    try:
        session_factory = get_session_factory()
    except RuntimeError:
        raise HTTPException(503, "인증 서버(PostgreSQL)에 연결할 수 없습니다.")

    async with session_factory() as db:
        user = await _find_user_by_email(db, body.email)
        if not user or not bcrypt.checkpw(body.password.encode(), user.password_hash.encode()):
            raise HTTPException(401, "이메일 또는 비밀번호가 올바르지 않습니다.")
        user_id = str(user.id)
        user_dict = _user_to_dict(user)

    payload = {
        "sub": user_id,
        "id": user_id,
        "name": user_dict["name"],
        "email": user_dict["email"],
        "client_id": user_dict["client_id"],
        "roles": user_dict["roles"],
    }
    await mark_online(user_id)
    return create_token_pair(payload)


@router.post("/auth/token/refresh")
async def refresh_token(body: TokenRefreshBody):
    """리프레시 토큰으로 새 액세스 토큰을 발급합니다."""
    if await is_revoked(body.refresh_token):
        raise HTTPException(401, "만료(폐기)된 리프레시 토큰입니다.")

    payload = decode_token(body.refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(401, "리프레시 토큰이 아닙니다.")

    # 슬라이딩 만료: 새 액세스 토큰과 함께 만료가 연장된 새 리프레시 토큰을 발급한다.
    # 활동 중인 클라이언트가 JWT_REFRESH_TTL(기본 7일) 마다 재로그인하지 않도록 하기 위함.
    # 기존 리프레시 토큰은 폐기하지 않고 원래 만료 시각까지 유효하다 (새 토큰을 저장하지 않는
    # 구형 클라이언트와의 호환). 즉시 무효화가 필요하면 /auth/token/revoke 를 호출한다.
    user_payload = {k: v for k, v in payload.items() if k not in ("type", "iat", "exp")}
    return create_token_pair(user_payload)


@router.post("/auth/token/revoke")
async def revoke_tokens(body: TokenRevokeBody):
    """토큰을 블랙리스트에 등록합니다 (JWT 로그아웃)."""
    await revoke_token(body.access_token)
    if body.refresh_token:
        payload = decode_token(body.refresh_token)
        await mark_offline(payload.get("id", ""))
        await revoke_token(body.refresh_token)
    return {"ok": True}


# ── 공용 엔드포인트 ────────────────────────────────────────────────────────────

@router.get("/me")
async def me(user=Depends(get_current_user_any)):
    """현재 사용자 정보를 반환합니다 (쿠키 or Bearer 모두 허용)."""
    from app.lib.user_state import get_user_state
    state = await get_user_state(user["id"])
    return {
        "user": {
            "name": user["name"],
            "email": user["email"],
            "clientId": user.get("client_id", ""),
            "roles": user.get("roles", ["user"]),
        },
        "state": {
            "online": state.get("online", False),
            "last_seen": state.get("last_seen"),
            "active_conversation_id": state.get("active_conversation_id"),
        },
    }


@router.get("/sessions")
async def list_sessions(user=Depends(get_current_user_any)):
    """내 활성 세션 목록을 반환합니다."""
    sids = await list_user_sessions(user["id"])
    return {"sessions": sids, "count": len(sids)}


@router.delete("/sessions/{sid}")
async def revoke_session(
    sid: str,
    user=Depends(get_current_user_any),
):
    """특정 세션을 강제 만료시킵니다."""
    session = await get_session(sid)
    if not session or session.get("id") != user["id"]:
        raise HTTPException(404, "세션을 찾을 수 없습니다.")
    await delete_session(sid)
    return {"ok": True}


@router.delete("/sessions")
async def revoke_all_sessions(user=Depends(require_roles("admin", "user"))):
    """내 모든 세션을 일괄 만료시킵니다 (전체 로그아웃)."""
    count = await delete_all_user_sessions(user["id"])
    await clear_user_state(user["id"])
    return {"ok": True, "revoked": count}
