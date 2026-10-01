"""로그인 세션 유지(슬라이딩 만료) · JWT 블랙리스트 검증.

실제 Redis 없이, 사용되는 명령만 흉내 내는 FakeRedis 로 app.lib.redis_cache._redis 를 대체한다.
TTL 은 가짜 시계(FakeRedis.now)로 제어해 "시간이 흐른 뒤" 동작을 검증한다.
"""
from __future__ import annotations

import asyncio
import time

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.lib import redis_cache
from app.lib import session as session_lib
from app.lib.jwt_auth import (
    create_access_token,
    get_current_user_any,
    is_revoked,
    revoke_token,
)
from app.lib.session import (
    COOKIE_NAME,
    SessionCookieRefreshMiddleware,
    create_session,
    get_current_user,
    get_optional_user,
    refresh_session,
)


class FakeRedis:
    """세션/블랙리스트 코드가 쓰는 명령만 구현한 인메모리 Redis."""

    def __init__(self):
        self.kv: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}
        self.exp: dict[str, float] = {}       # key → 만료 시각(가짜 시계 기준)
        self.now = 1_000_000.0
        self.expire_calls: list[tuple[str, int]] = []

    def _alive(self, key: str) -> bool:
        if key in self.exp and self.exp[key] <= self.now:
            self.kv.pop(key, None)
            self.sets.pop(key, None)
            self.exp.pop(key, None)
            return False
        return key in self.kv or key in self.sets

    async def ping(self):
        return True

    async def get(self, key):
        return self.kv.get(key) if self._alive(key) else None

    async def set(self, key, value):
        self.kv[key] = value
        self.exp.pop(key, None)

    async def setex(self, key, ttl, value):
        self.kv[key] = value
        self.exp[key] = self.now + ttl

    async def delete(self, *keys):
        n = 0
        for k in keys:
            n += int(k in self.kv or k in self.sets)
            self.kv.pop(k, None)
            self.sets.pop(k, None)
            self.exp.pop(k, None)
        return n

    async def exists(self, key):
        return int(self._alive(key))

    async def expire(self, key, ttl):
        if not self._alive(key):
            return False
        self.exp[key] = self.now + ttl
        self.expire_calls.append((key, ttl))
        return True

    async def ttl(self, key):
        if not self._alive(key):
            return -2
        if key not in self.exp:
            return -1
        return int(self.exp[key] - self.now)

    async def sadd(self, key, *members):
        self.sets.setdefault(key, set()).update(members)

    async def srem(self, key, *members):
        self.sets.get(key, set()).difference_update(members)

    async def smembers(self, key):
        return set(self.sets.get(key, set())) if self._alive(key) else set()


@pytest.fixture
def fake_redis(monkeypatch):
    r = FakeRedis()
    monkeypatch.setattr(redis_cache, "_redis", r)
    return r


USER = {"id": "u1", "name": "홍길동", "email": "hong@example.com", "client_id": "C1", "roles": ["user"]}


def _run(coro):
    return asyncio.run(coro)


# ── JWT 블랙리스트 ─────────────────────────────────────────────────────────────

def test_revoking_one_token_does_not_revoke_others(fake_redis):
    """토큰 앞 32자(=공통 헤더)를 키로 쓰던 버그: 하나를 폐기하면 전체가 폐기됐다."""
    a = create_access_token({"sub": "u1", "id": "u1"})
    b = create_access_token({"sub": "u2", "id": "u2"})
    assert a[:32] == b[:32]  # 전제: JWT 헤더는 모든 토큰에서 동일

    _run(revoke_token(a))
    assert _run(is_revoked(a)) is True
    assert _run(is_revoked(b)) is False


# ── 슬라이딩 만료 (Redis TTL) ──────────────────────────────────────────────────

def test_refresh_session_is_throttled_then_extends_ttl(fake_redis):
    sid = _run(create_session(USER))
    key = f"fin_session:{sid}"
    assert _run(fake_redis.ttl(key)) == settings.SESSION_TTL
    fake_redis.expire_calls.clear()  # create_session 이 보낸 EXPIRE 는 제외

    # 방금 만든 세션: 갱신 간격이 지나지 않았으므로 Redis 쓰기 없이 False
    assert _run(refresh_session(sid)) is False
    assert fake_redis.expire_calls == []

    # 갱신 간격이 지난 뒤: EXPIRE 로 TTL 을 다시 SESSION_TTL 로 되돌린다
    fake_redis.now += settings.SESSION_REFRESH_INTERVAL + 1
    assert _run(refresh_session(sid)) is True
    assert _run(fake_redis.ttl(key)) == settings.SESSION_TTL
    assert (key, settings.SESSION_TTL) in fake_redis.expire_calls
    assert (f"fin_session_list:{USER['id']}", settings.SESSION_TTL) in fake_redis.expire_calls

    # force=True 면 간격과 무관하게 갱신
    assert _run(refresh_session(sid, force=True)) is True
    # 없는 세션은 False
    assert _run(refresh_session("nope")) is False


def test_session_expires_without_activity(fake_redis):
    sid = _run(create_session(USER))
    fake_redis.now += settings.SESSION_TTL + 1
    assert _run(session_lib.get_session(sid)) is None
    assert _run(refresh_session(sid)) is False


# ── 의존성 + 미들웨어 (브라우저 쿠키 만료 연장) ─────────────────────────────────

def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(SessionCookieRefreshMiddleware)

    @app.get("/cookie-only")
    async def cookie_only(user=Depends(get_current_user)):
        return {"id": user["id"]}

    @app.get("/any")
    async def any_auth(user=Depends(get_current_user_any)):
        return {"id": user["id"]}

    @app.get("/optional")
    async def optional(user=Depends(get_optional_user)):
        return {"id": user["id"] if user else None}

    return app


def _cookie_attrs(header: str) -> dict[str, str]:
    parts = [p.strip() for p in header.split(";")]
    attrs = {}
    for p in parts:
        k, _, v = p.partition("=")
        attrs[k.lower()] = v
    return attrs


@pytest.mark.parametrize("path", ["/cookie-only", "/any", "/optional"])
def test_cookie_is_reissued_only_when_ttl_was_refreshed(fake_redis, path):
    sid = _run(create_session(USER))
    client = TestClient(_make_app(), cookies={COOKIE_NAME: sid})

    # 1) 방금 로그인: 세션 TTL 갱신 없음 → Set-Cookie 도 없음
    res = client.get(path)
    assert res.status_code == 200
    assert res.json()["id"] == "u1"
    assert "set-cookie" not in res.headers

    # 2) 갱신 간격 경과: TTL 연장 + 같은 sid 로 쿠키 재발급(max_age=SESSION_TTL)
    fake_redis.now += settings.SESSION_REFRESH_INTERVAL + 1
    res = client.get(path)
    assert res.status_code == 200
    header = res.headers["set-cookie"]
    attrs = _cookie_attrs(header)
    assert attrs[COOKIE_NAME] == sid
    assert attrs["max-age"] == str(settings.SESSION_TTL)
    assert attrs["path"] == "/"
    assert "httponly" in attrs
    assert attrs["samesite"].lower() == settings.COOKIE_SAMESITE.lower()
    assert _run(fake_redis.ttl(f"fin_session:{sid}")) == settings.SESSION_TTL

    # 3) 바로 다음 요청: 다시 갱신 간격 안이므로 쿠키 없음
    res = client.get(path)
    assert res.status_code == 200
    assert "set-cookie" not in res.headers


def test_unknown_or_missing_cookie_is_unauthorized(fake_redis):
    client = TestClient(_make_app())
    assert client.get("/cookie-only").status_code == 401
    assert client.get("/any").status_code == 401
    assert client.get("/optional").json() == {"id": None}

    client = TestClient(_make_app(), cookies={COOKIE_NAME: "does-not-exist"})
    res = client.get("/cookie-only")
    assert res.status_code == 401
    assert "set-cookie" not in res.headers
    assert client.get("/any").status_code == 401
    assert client.get("/optional").json() == {"id": None}


def test_session_outlives_browser_cookie_lifetime_when_active(fake_redis):
    """로그인 후 SESSION_TTL 을 넘기더라도 중간에 활동이 있었다면 세션과 쿠키가 모두 살아 있어야 한다."""
    sid = _run(create_session(USER))
    client = TestClient(_make_app(), cookies={COOKIE_NAME: sid})

    step = settings.SESSION_TTL // 2
    for _ in range(4):  # 총 2×SESSION_TTL 경과, 매번 SESSION_TTL/2 간격으로 활동
        fake_redis.now += step
        res = client.get("/any")
        assert res.status_code == 200
        assert _cookie_attrs(res.headers["set-cookie"])[COOKIE_NAME] == sid


def test_redis_down_returns_503_not_500(monkeypatch):
    from redis.exceptions import ConnectionError as RedisConnectionError

    class DownRedis(FakeRedis):
        async def get(self, key):
            raise RedisConnectionError("connection refused")

    monkeypatch.setattr(redis_cache, "_redis", DownRedis())
    client = TestClient(_make_app(), cookies={COOKIE_NAME: "sid"})
    assert client.get("/cookie-only").status_code == 503
    assert client.get("/any").status_code == 503
    assert client.get("/optional").json() == {"id": None}


# ── 인증 라우터: 로그아웃 · 리프레시 토큰 슬라이딩 ──────────────────────────────

def _auth_app() -> FastAPI:
    from app.routes.auth import router

    app = FastAPI()
    app.add_middleware(SessionCookieRefreshMiddleware)
    app.include_router(router)
    return app


def test_logout_clears_cookie_even_if_session_already_expired(fake_redis):
    client = TestClient(_auth_app(), cookies={COOKIE_NAME: "expired-sid"})
    res = client.post("/api/auth/logout")
    assert res.status_code == 200
    attrs = _cookie_attrs(res.headers["set-cookie"])
    assert attrs[COOKIE_NAME] in ("", '""')
    assert attrs.get("max-age") == "0"


def test_logout_deletes_live_session(fake_redis):
    sid = _run(create_session(USER))
    client = TestClient(_auth_app(), cookies={COOKIE_NAME: sid})
    assert client.post("/api/auth/logout").status_code == 200
    assert _run(session_lib.get_session(sid)) is None
    assert _run(session_lib.list_user_sessions(USER["id"])) == []


def test_token_refresh_issues_new_sliding_refresh_token(fake_redis):
    from app.lib.jwt_auth import create_refresh_token, decode_token

    payload = {"sub": "u1", "id": "u1", "name": "홍길동", "email": "hong@example.com",
               "client_id": "C1", "roles": ["user"]}
    old_refresh = create_refresh_token(payload)
    time.sleep(1.1)  # exp 는 초 단위 → 새 토큰의 만료가 뒤로 밀렸는지 보려면 1초 이상 필요

    client = TestClient(_auth_app())
    res = client.post("/api/auth/token/refresh", json={"refresh_token": old_refresh})
    assert res.status_code == 200
    body = res.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == settings.JWT_ACCESS_TTL
    assert decode_token(body["access_token"])["type"] == "access"

    new_refresh = body["refresh_token"]
    assert new_refresh != old_refresh
    assert decode_token(new_refresh)["type"] == "refresh"
    assert decode_token(new_refresh)["exp"] > decode_token(old_refresh)["exp"]
    assert decode_token(new_refresh)["id"] == "u1"

    # 액세스 토큰으로 리프레시 시도는 거부
    res = client.post("/api/auth/token/refresh", json={"refresh_token": body["access_token"]})
    assert res.status_code == 401

    # 폐기된 리프레시 토큰은 거부되지만, 다른 토큰은 영향받지 않는다
    _run(revoke_token(old_refresh))
    assert client.post("/api/auth/token/refresh", json={"refresh_token": old_refresh}).status_code == 401
    assert client.post("/api/auth/token/refresh", json={"refresh_token": new_refresh}).status_code == 200
