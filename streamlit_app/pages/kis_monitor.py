"""KIS 모의투자결과 — /api/quant/kis/monitor?cycles=48 · /api/quant/kis/orders"""
import streamlit as st

from streamlit_app import api
from streamlit_app.ui import call, kpis, num, pct, table, won


def render() -> None:
    st.title("KIS 모의투자결과")
    m = call(api.get, "/api/quant/kis/monitor", cycles=48) or {}
    if not m:
        return
    st.caption(f"조회 {m.get('checked_at', '')} · 환경 {m.get('environment', '')} · 게이트웨이 {'연결' if m.get('gateway_configured') else '미설정'}")
    live = m.get("live") or {}
    if live:
        kpis([("평가액", won(live.get("equity") or live.get("eval_amount")), None), ("현금(예수금)", won(live.get("cash")), None),
              ("투자금", won(live.get("invested")), None), ("평가손익", won(live.get("profit_loss")), pct(live.get("profit_loss_rate")))])
        if live.get("error"):
            st.warning(live["error"])
    agg = m.get("aggressive") or {k: m.get(k) for k in ("enabled", "interval", "take_profit_pct", "stop_loss_pct", "order_type") if k in m}
    halted = m.get("halted") or (m.get("batch") or {}).get("halted")
    if halted:
        st.error(f"비상 정지: {m.get('halt_reason') or (m.get('batch') or {}).get('halt_reason') or ''}")
    today = m.get("today_counts") or {}
    if today:
        kpis([(k, str(v), None) for k, v in list(today.items())[:4]])
    t1, t2, t3, t4 = st.tabs(["보유 종목", "최근 사이클", "주문 목록", "원본 JSON"])
    with t1:
        table((live.get("holdings") or live.get("positions") or m.get("positions") or []),
              rename={"symbol": "종목", "name": "이름", "quantity": "수량", "avg_price": "평균단가", "current_price": "현재가", "eval_amount": "평가액", "profit_loss": "손익", "profit_loss_rate": "수익률%"})
    with t2:
        table(m.get("recent") or m.get("cycles") or [])
    with t3:
        c1, c2, c3 = st.columns(3)
        origin = c1.selectbox("주체", ["", "bot", "human", "manual", "batch_auto", "user_auto", "rebalance"], format_func=lambda v: v or "전체")
        status = c2.text_input("상태(쉼표 구분)", "")
        limit = c3.number_input("최대 건수", 50, 1000, 200, 50)
        o = call(api.get, "/api/quant/kis/orders", origin=origin or None, status=status or None, limit=int(limit)) or {}
        if o:
            st.caption(f"총 {o.get('total', 0)}건 · 상태별 {o.get('counts_by_status')} · 주체별 {o.get('counts_by_origin')}")
            table(o.get("rows") or [], columns=["created_at", "symbol", "name", "side", "quantity", "price", "status", "origin_label", "owner_label", "order_no"],
                  rename={"created_at": "시각", "symbol": "종목", "name": "이름", "side": "방향", "quantity": "수량", "price": "가격", "status": "상태", "origin_label": "주체", "owner_label": "계정", "order_no": "주문번호"})
    with t4:
        st.json(m, expanded=False)
