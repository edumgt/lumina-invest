"""아직 Streamlit 으로 옮기지 않은 메뉴: 관련 GET API 를 표/JSON 으로 보여주고 기존 HTML 화면 링크를 준다."""
import streamlit as st

from streamlit_app import api
from streamlit_app.ui import call, table

# 메뉴 key → (제목, [(라벨, 경로, params)])
ENDPOINTS: dict[str, tuple[str, list[tuple[str, str, dict]]]] = {
    "quant-auto": ("자동매매 현황", [("상태", "/api/quant/auto/status", {}), ("KIS 배치", "/api/quant/kis/batch/status", {})]),
    "robo-rebalance": ("리밸런싱 엔진", [("상태", "/api/rebalance/status", {}), ("계획", "/api/rebalance/plan", {}), ("실행 이력", "/api/rebalance/runs", {"limit": 20}), ("현금흐름", "/api/rebalance/cashflows", {"limit": 20})]),
    "paper-dashboard": ("모의계좌 현황", [("계좌", "/api/paper/account", {}), ("주식 포지션", "/api/paper/stocks/positions", {}), ("주식 주문 이력", "/api/paper/stocks/orders/history", {})]),
    "paper-crypto": ("코인 모의매매", [("보유", "/api/paper/trade/hold", {}), ("주문 이력", "/api/paper/trade/order/history", {}), ("시장 목록", "/api/paper/crypto/market-list", {})]),
    "paper-alternative": ("대체자산", [("시장", "/api/paper/alternatives/markets", {}), ("포지션", "/api/paper/alternatives/positions", {}), ("주문 이력", "/api/paper/alternatives/orders/history", {})]),
    "paper-openapi": ("Open API · 외부 연동", [("API 키", "/api/paper/api-keys", {})]),
    "crawl-auto": ("자동 크롤링", [("수집 목록", "/api/ingest/crawl/list", {})]),
    "robo-screening": ("패턴 인식·종목 스크리닝", [("퀀트 종목", "/api/stocks/quant/list", {"limit": 50}), ("시그널", "/api/stocks/signals", {})]),
    "trading-portfolio": ("포트폴리오", [("포트폴리오", "/api/portfolio", {})]),
    "settings": ("증권사 API 설정", [("브로커 설정", "/api/broker/settings", {})]),
}


def render(key: str, label: str) -> None:
    title, endpoints = ENDPOINTS.get(key, (label, []))
    st.title(title)
    st.caption("이 메뉴는 조회 화면만 Streamlit 으로 옮겼습니다. 입력·실행은 기존 화면에서 하세요.")
    st.link_button(f"기존 화면 열기 (#{key})", api.public_url(key))
    for name, path, params in endpoints:
        st.subheader(name)
        data = call(api.get, path, **params)
        if data is None:
            continue
        rows = None
        if isinstance(data, list):
            rows = data
        elif isinstance(data, dict):
            for k in ("rows", "items", "orders", "positions", "runs", "results", "signals", "list"):
                if isinstance(data.get(k), list):
                    rows = data[k]; break
        if rows is not None and rows and isinstance(rows[0], dict):
            table(rows)
        st.json(data, expanded=False)
