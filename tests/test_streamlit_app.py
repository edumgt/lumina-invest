"""Streamlit UI(streamlit_app/, 6-60) — AppTest 로 헤드리스 실행. API 는 패치해 서버 없이 검증한다."""
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from streamlit_app import api

APP = str(Path(__file__).resolve().parents[1] / "streamlit_app" / "app.py")


def _at(**state) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=30)
    for k, v in state.items():
        at.session_state[k] = v
    return at


def test_login_page_shown_when_logged_out():
    at = _at().run()
    assert not at.exception
    assert any("로그인" in t.value for t in at.title)
    assert len(at.text_input) == 2


def test_login_failure_shows_api_error():
    with patch.object(api, "login", side_effect=api.ApiError(401, "이메일 또는 비밀번호가 올바르지 않습니다.")):
        at = AppTest.from_string("from streamlit_app.pages import login\nlogin.render()\n", default_timeout=30)
        at.run()
        at.text_input[0].input("x@y.z"); at.text_input[1].input("pw")
        at.button[0].click().run()
    assert not at.exception and any("올바르지 않습니다" in e.value for e in at.error)


def test_navigation_lists_gnb_sections_when_logged_in():
    fake = {"/api/dashboard/accounts": {"tabs": [{"key": "kis", "label": "KIS 모의투자", "connected": True, "currency": "KRW", "total": 87459230, "cash": -31826005, "invested": 119000000, "pnl": -12540770, "pnl_pct": -12.54, "positions": [{"symbol": "000040", "quantity": 1}]},
                                                  {"key": "us", "label": "미국", "connected": False, "error": "미연동", "link": "#settings"}]},
            "/api/quant/kis/quickstart": {"configured": True}, "/api/quant/auto/status": {"running": False, "signals": [1, 2]}, "/api/rebalance/status": {"ok": True}}
    with patch.object(api, "get", side_effect=lambda path, **p: fake.get(path, {})):
        at = _at(sid="s", user={"name": "테스터"}).run()
    assert not at.exception
    assert any("통합 대시보드" in t.value for t in at.title)
    values = [m.value for m in at.metric]
    assert "87,459,230원" in values and "중지됨" in values and "2개" in values
    assert any("테스터" in m.value for m in at.sidebar.markdown)


def test_company_page_renders_dart_fundamentals():
    fund = {"symbol": "052710.KQ", "name": "Amotech", "price": 20250, "chg": -1.46, "cap": 3782, "capUnit": "억원", "priceUnit": "원", "amountUnit": "억원",
            "eps": -237.13, "per": None, "bps": 10972.06, "pbr": 1.85, "roe": -2.16, "roa": -0.94, "opMargin": 0.14, "debt": 128.96,
            "valuationSource": "DART 계산 · 2026 반기보고서", "profitabilityBasis": {"roe": "DART 계산 · x", "debt": "DART 계산 · y"}, "profitabilitySource": "DART 계산",
            "quarters": ["25Q3", "25Q4"], "revenue": [567, 536], "op": [None, None], "net": [None, None], "source": "Yahoo",
            "dartOpSeries": [{"label": "25Q3", "operatingIncome": 8.0, "basis": "3개월", "fsDiv": "연결"}], "assets": 3671, "equity": 1603, "liabilities": 2068, "cash": 330, "balanceSource": "DART 연결"}
    import json
    script = f"""
import sys; sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})
from unittest.mock import patch
from streamlit_app import api
from streamlit_app.pages import company
FUND = json.loads({json.dumps(json.dumps(fund, ensure_ascii=False))})
with patch.object(api, "get", side_effect=lambda path, **p: FUND if path == "/api/stocks/fundamentals" else {{}}):
    company.render()
"""
    at = AppTest.from_string("import json\n" + script, default_timeout=30)   # 페이지 함수 단독 실행 (st.navigation 선택은 AppTest 로 못 바꾼다)
    at.run()
    assert not at.exception
    text = " ".join(m.value for m in at.markdown) + " ".join(m.value for m in at.metric) + " ".join(c.value for c in at.caption)
    assert "20,250원" in " ".join(m.value for m in at.metric)
    assert "DART" in text


def test_api_client_cookie_and_errors(monkeypatch):
    import httpx
    import streamlit as st
    st.session_state["sid"] = "abc"
    seen = {}
    def handler(request: httpx.Request):
        seen["cookie"] = request.headers.get("cookie"); seen["url"] = str(request.url)
        if request.url.path == "/api/boom":
            return httpx.Response(422, json={"detail": "값이 잘못됐습니다"})
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json={"ok": True, "user": {"name": "n"}}, headers={"set-cookie": "fin_session=xyz; Path=/"})
        return httpx.Response(200, json={"ok": True})
    transport = httpx.MockTransport(handler)
    real_client = httpx.Client
    monkeypatch.setattr(api.httpx, "Client", lambda **kw: real_client(transport=transport, **{k: v for k, v in kw.items() if k != "transport"}))
    assert api.get("/api/ping", environment="paper") == {"ok": True}
    assert seen["cookie"] == "fin_session=abc" and seen["url"].endswith("/api/ping?environment=paper")
    with pytest.raises(api.ApiError) as ei:
        api.post("/api/boom", json={})
    assert ei.value.status == 422 and "잘못" in ei.value.detail
    user = api.login("a@b.c", "pw")
    assert user == {"name": "n"} and st.session_state["sid"] == "xyz"
