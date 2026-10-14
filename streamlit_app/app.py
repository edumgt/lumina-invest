"""lumina-invest Streamlit UI 진입점 (6-60).

실행: streamlit run streamlit_app/app.py --server.port 8501 --server.baseUrlPath ui   (LUMINA_API_BASE 로 FastAPI 지정)
GNB 6묶음(core.js GNB_MENUS)을 그대로 사이드바 섹션으로 옮겼다. 옮긴 화면은 전용 페이지, 나머지는 generic(조회 + 기존 화면 링크).
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # `streamlit run streamlit_app/app.py` 로 띄워도 패키지 import 되게

from streamlit_app import api  # noqa: E402
from streamlit_app.pages import agent_chat, company, dashboard, generic, kis_monitor, login, return_review, strategy_settings  # noqa: E402

st.set_page_config(page_title="Lumina Invest", page_icon="📈", layout="wide")

# GNB 와 같은 순서·라벨. (key, label, renderer)
MENUS: dict[str, list[tuple[str, str]]] = {
    "로보 어드바이저": [("dashboard", "통합 대시보드"), ("agent-chat", "AI 투자 상담"), ("robo-portfolio", "자산배분·최적화"), ("robo-rebalance", "리밸런싱 엔진"),
                  ("robo-screening", "패턴 인식·종목 스크리닝"), ("robo-patterns", "차트 패턴·지지/저항"), ("robo-decision", "모의 투자 의사결정"), ("kis-monitor", "KIS 모의투자결과")],
    "전략설정": [("strategy-settings", "봇 전략 · 파라미터 설정"), ("strategy-performance", "설정별 성과")],
    "수익율검증": [("return-review", "수익률 검증 · 개선 가이드")],
    "투자 인디케이터": [("company-dashboard", "기업 대시보드")],
    "크롤링": [("crawl-auto", "자동 크롤링"), ("crawl-manual", "수동 크롤링"), ("crawl-ingest", "데이터 인제스트")],
    "직접매매": [("trading-chart", "주가 차트"), ("trading-portfolio", "포트폴리오"), ("trading-order", "주문/매매")],
    "모의투자": [("paper-dashboard", "모의계좌 현황"), ("paper-stock", "국내주식 모의주문"), ("paper-crypto", "코인 모의매매"), ("paper-alternative", "대체자산"), ("paper-openapi", "Open API · 외부 연동")],
    "퀀트자동매매": [("quant-dashboard", "퀀트 대시보드"), ("quant-auto", "자동매매 현황"), ("quant-backtest", "전략 분석"), ("quant-lean", "LEAN 백테스트"), ("settings", "증권사 API 설정")],
}
DEDICATED = {
    "dashboard": dashboard.render, "agent-chat": agent_chat.render, "kis-monitor": kis_monitor.render,
    "strategy-settings": strategy_settings.render, "strategy-performance": strategy_settings.render_performance,
    "return-review": return_review.render, "company-dashboard": company.render,
}


def _page(key: str, label: str):
    fn = DEDICATED.get(key)
    if fn is None:
        return st.Page(lambda k=key, l=label: generic.render(k, l), title=label, url_path=key)
    return st.Page(fn, title=label, url_path=key)


def main() -> None:
    if not api.is_logged_in():
        st.navigation([st.Page(login.render, title="로그인", url_path="login", default=True)]).run()
        return
    user = st.session_state.get("user") or {}
    with st.sidebar:
        st.markdown(f"**{user.get('name') or user.get('email') or '사용자'}** · {api.API_BASE}")
        if st.button("로그아웃", use_container_width=True):
            api.logout(); st.rerun()
    pages = {section: [_page(k, l) for k, l in items] for section, items in MENUS.items()}
    st.navigation(pages).run()


main()
