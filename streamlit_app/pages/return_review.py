"""수익률 검증 — GET /api/quant/return-review (rows·summary·live·plans·guide·applied_plans) · POST /snapshot · POST /plans/{id}/apply"""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from streamlit_app import api
from streamlit_app.api import ApiError
from streamlit_app.ui import call, kpis, pct, table, won


def render() -> None:
    st.title("수익률 검증 · 개선 가이드")
    env = st.selectbox("환경", ["paper", "real"], format_func=lambda v: "모의투자(paper)" if v == "paper" else "실전(real)")
    d = call(api.get, "/api/quant/return-review", environment=env) or {}
    if not d:
        return
    live, s, t = d.get("live") or {}, d.get("summary") or {}, d.get("trade_stats") or {}
    items = []
    if live.get("ok"):
        items.append(("지금 평가액 (실시간)", won(live.get("equity")), f"{live.get('last_snap_date')} 대비 {won(live.get('change_pnl'))} ({pct(live.get('change_pct'))}p) · {'장중' if live.get('market') == 'open' else '장외'}"))
    elif live:
        items.append(("지금 평가액 (실시간)", "-", live.get("error") or "조회 실패"))
    if s.get("days"):
        items += [("누적 수익률", pct(s.get("cum_return_pct")), f"{won(s.get('cum_pnl'))} · 원금 {won(s.get('principal'))}"),
                  ("최대 낙폭", pct(s.get("max_drawdown_pct")), f"최고 {won(s.get('peak_equity'))}"),
                  ("상승/하락일", f"{s.get('up_days')} / {s.get('down_days')}", f"{s.get('days')}관측일 · 평균 {pct(s.get('avg_daily_change_pct'))}p"),
                  ("승률 · 손익비", f"{t.get('win_rate_pct', '-')}% · {t.get('payoff_ratio', '-')}" if t.get("round_trips") else "-", f"왕복 {t.get('round_trips', 0)}건"),
                  ("거래당 기대값", won(t.get("expectancy")) if t.get("round_trips") else "-", f"체결 {t.get('fills', 0)}/주문 {t.get('orders', 0)}건")]
    kpis(items, cols=3)
    rows = d.get("rows") or []
    if rows:
        df = pd.DataFrame(rows)
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=df["snap_date"], y=df["equity"], mode="lines+markers", name="평가액"))
        fig.add_trace(go.Bar(x=df["snap_date"], y=df["daily_change_pct"], name="일변화 %p", yaxis="y2", opacity=0.4))
        fig.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10), yaxis=dict(title="원"), yaxis2=dict(title="%p", overlaying="y", side="right"), legend=dict(orientation="h"))
        st.plotly_chart(fig, use_container_width=True)
        show = df[["snap_date", "equity", "cum_return_pct", "cum_pnl", "daily_change_pct", "daily_pnl", "drawdown_pct", "source"]].copy()
        if "trading" in df.columns:
            show["체결/주문"] = df["trading"].apply(lambda x: f"{(x or {}).get('fills', 0)}/{(x or {}).get('orders', 0)}")
            show["실현손익"] = df["trading"].apply(lambda x: (x or {}).get("realized", 0))
        st.dataframe(show.rename(columns={"snap_date": "날짜", "equity": "평가액", "cum_return_pct": "누적%", "cum_pnl": "누적손익", "daily_change_pct": "일변화%p", "daily_pnl": "일손익", "drawdown_pct": "낙폭%", "source": "출처"}),
                     use_container_width=True, hide_index=True)
    c1, c2 = st.columns(2)
    if c1.button("오늘 스냅샷 기록 (게이트웨이 잔고)"):
        r = call(api.post, "/api/quant/return-review/snapshot", json={"environment": env})
        if r:
            st.success(f"기록됨 — {r['row']['snap_date']} {won(r['row']['equity'])}"); st.rerun()
    st.subheader("수익률 증대 방안")
    plans = d.get("plans") or {}
    if plans.get("reason"):
        st.caption(plans["reason"])
    for p in plans.get("plans") or []:
        with st.expander(f"방안 {p['id']} · {p.get('title', '')}" + (" · 권장" if plans.get("recommended") == p["id"] else ""), expanded=plans.get("recommended") == p["id"]):
            table(p.get("changes") or [], columns=["label", "current", "proposed", "why"], rename={"label": "항목", "current": "현재", "proposed": "제안", "why": "사유"})
            st.markdown(f"**기대**: {p.get('expected', '')}  \n**리스크**: {p.get('risk', '')}")
            if not p.get("applicable"):
                st.warning(p.get("blocker") or "적용 불가")
            elif st.button(f"방안 {p['id']} 승인 → 전략설정 적용", key=f"apply_{p['id']}"):
                try:
                    r = api.post(f"/api/quant/return-review/plans/{p['id']}/apply", json={"note": "streamlit"})
                    st.success("적용됨"); st.json(r, expanded=False); st.rerun()
                except ApiError as exc:
                    st.error(exc.detail)
    if d.get("applied_plans"):
        st.subheader("승인된 방안 · 진행")
        table(d["applied_plans"])
    st.subheader("진단 가이드")
    for g in d.get("guide") or []:
        icon = {"bad": "🔴", "warn": "🟠", "good": "🟢"}.get(g.get("level"), "ℹ️")
        with st.expander(f"{icon} {g.get('title', '')}"):
            for e in g.get("evidence") or []:
                st.markdown(f"- {e}")
            if g.get("expected"):
                st.caption(f"기대: {g['expected']}")
    with st.expander("붙여넣기 적재 (KIS 화면 표)"):
        text = st.text_area("날짜 · 수익률 · 손익 · 원금 · 평가액 · 건수 (탭/공백 구분)", height=120)
        note = st.text_input("메모", "")
        if st.button("적재") and text.strip():
            r = call(api.post, "/api/quant/return-review/rows", json={"text": text, "environment": env, "note": note})
            if r:
                st.success(f"신규 {r.get('inserted')} · 갱신 {r.get('updated')}"); st.rerun()
