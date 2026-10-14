"""FastAPI 호출 클라이언트. 세션 쿠키(fin_session)를 st.session_state 에 보관한다 — 서버의 모든 화면 라우트가 쿠키 세션만 받는다(app/lib/session.py).

LUMINA_API_BASE: 컨테이너 안에서는 http://app:8000, 로컬 개발은 http://127.0.0.1:8000.
"""
from __future__ import annotations

import os
from typing import Any

import httpx
import streamlit as st

API_BASE = os.environ.get("LUMINA_API_BASE", "http://127.0.0.1:8000").rstrip("/")
PUBLIC_BASE = os.environ.get("LUMINA_PUBLIC_BASE", "https://fd.edumgt.co.kr").rstrip("/")
COOKIE_NAME = os.environ.get("LUMINA_SESSION_COOKIE", "fin_session")
TIMEOUT = float(os.environ.get("LUMINA_API_TIMEOUT", "60"))


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _client() -> httpx.Client:
    cookies = {COOKIE_NAME: st.session_state["sid"]} if st.session_state.get("sid") else {}
    return httpx.Client(base_url=API_BASE, cookies=cookies, timeout=TIMEOUT, headers={"Accept": "application/json"})


def _detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:200] or f"HTTP {resp.status_code}"
    if isinstance(body, dict):
        d = body.get("detail") or body.get("error") or body.get("message")
        if isinstance(d, dict):
            d = d.get("message") or d.get("detail") or str(d)
        if d:
            return str(d)
    return f"HTTP {resp.status_code}"


def request(method: str, path: str, *, params: dict | None = None, json: dict | None = None) -> Any:
    try:
        with _client() as c:
            resp = c.request(method, path, params=params, json=json)
    except httpx.HTTPError as exc:
        raise ApiError(0, f"API 서버({API_BASE})에 연결하지 못했습니다: {exc.__class__.__name__}")
    if resp.status_code == 401:
        st.session_state.pop("sid", None); st.session_state.pop("user", None)
        raise ApiError(401, _detail(resp))
    if resp.status_code >= 400:
        raise ApiError(resp.status_code, _detail(resp))
    if not resp.content:
        return None
    try:
        return resp.json()
    except ValueError:
        return resp.text


def get(path: str, **params) -> Any:
    return request("GET", path, params={k: v for k, v in params.items() if v is not None} or None)


def post(path: str, json: dict | None = None, **params) -> Any:
    return request("POST", path, params=params or None, json=json or {})


def delete(path: str, **params) -> Any:
    return request("DELETE", path, params=params or None)


def login(email: str, password: str) -> dict:
    """POST /api/auth/login → Set-Cookie fin_session. 쿠키 값을 세션 상태에 저장한다."""
    try:
        with httpx.Client(base_url=API_BASE, timeout=TIMEOUT) as c:
            resp = c.post("/api/auth/login", json={"email": email, "password": password})
    except httpx.HTTPError as exc:
        raise ApiError(0, f"API 서버({API_BASE})에 연결하지 못했습니다: {exc.__class__.__name__}")
    if resp.status_code >= 400:
        raise ApiError(resp.status_code, _detail(resp))
    sid = resp.cookies.get(COOKIE_NAME)
    if not sid:
        raise ApiError(502, "로그인 응답에 세션 쿠키가 없습니다.")
    body = resp.json() if resp.content else {}
    st.session_state["sid"] = sid
    st.session_state["user"] = (body.get("user") or {}) if isinstance(body, dict) else {}
    return st.session_state["user"]


def logout() -> None:
    try:
        post("/api/auth/logout")
    except ApiError:
        pass
    finally:
        st.session_state.pop("sid", None); st.session_state.pop("user", None)


def is_logged_in() -> bool:
    return bool(st.session_state.get("sid"))


def public_url(view: str) -> str:
    """기존 HTML 화면 링크(아직 Streamlit 으로 옮기지 않은 메뉴)."""
    return f"{PUBLIC_BASE}/app.html#{view}"
