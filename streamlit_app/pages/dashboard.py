"""통합 대시보드 — /api/dashboard/accounts(사이트별 계좌) · /api/quant/kis/quickstart · /api/quant/auto/status · /api/rebalance/status"""
import streamlit as st

from streamlit_app import api
from streamlit_app.ui import call, kpis, pct, table, won


def render() -> None:
    st.title("통합 대시보드")
    c1, c2 = st.columns([3, 1])
    c2.button("새로고침", on_click=st.cache_data.clear, use_container_width=True)
    accounts = call(api.get, "/api/dashboard/accounts") or {}
    tabs = accounts.get("tabs") or []
    if tabs:
        names = [t.get("label") or t.get("site") or t.get("key") for t in tabs]
        for tab, box in zip(tabs, st.tabs(names)):
            with box:
                if not tab.get("connected"):
                    st.info(f"미연동 — {tab.get('error') or tab.get('note') or ''}")
                    if tab.get("link"):
                        st.link_button("설정으로 이동", api.public_url(str(tab["link"]).lstrip("#")))
                    continue
                cur = tab.get("currency") or "KRW"
                fmt = (lambda v: won(v)) if cur == "KRW" else (lambda v: f"{float(v or 0):,.0f} {cur}")
                kpis([("총자산", fmt(tab.get("total")), None), ("현금", fmt(tab.get("cash")), None),
                      ("투자금", fmt(tab.get("invested")), None), ("손익", fmt(tab.get("pnl")), pct(tab.get("pnl_pct")))])
                if tab.get("note"):
                    st.caption(tab["note"])
                positions = tab.get("positions") or tab.get("stocks") or []
                if positions:
                    st.subheader("보유 종목")
                    table(positions)
    st.divider()
    left, right = st.columns(2)
    with left:
        st.subheader("KIS 모의투자 연동")
        q = call(api.get, "/api/quant/kis/quickstart") or {}
        st.json(q, expanded=False)
        auto = call(api.get, "/api/quant/auto/status") or {}
        kpis([("자동매매", "실행 중" if auto.get("running") else "중지됨", None),
              ("현재 신호", f"{len(auto.get('signals') or [])}개", None)], cols=2)
    with right:
        st.subheader("리밸런싱")
        st.json(call(api.get, "/api/rebalance/status") or {}, expanded=False)
