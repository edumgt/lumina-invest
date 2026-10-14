"""기업 대시보드 — /api/stocks/search?q= · /api/stocks/fundamentals?symbol= (DART 보강 포함)"""
import streamlit as st

from streamlit_app import api
from streamlit_app.ui import call, kpis, num, table

DEFAULT = [("005930.KS", "삼성전자"), ("000660.KS", "SK하이닉스"), ("035420.KS", "NAVER"), ("005380.KS", "현대자동차"), ("105560.KS", "KB금융"), ("042660.KS", "한화오션"), ("052710.KQ", "아모텍")]


def render() -> None:
    st.title("기업 대시보드")
    c1, c2 = st.columns([2, 3])
    choice = c1.selectbox("종목 선택", DEFAULT, format_func=lambda x: f"{x[1]} ({x[0]})")
    q = c2.text_input("종목 검색 (이름/코드)")
    symbol = choice[0]
    if q.strip():
        hits = call(api.get, "/api/stocks/search", q=q.strip()) or {}
        items = hits.get("items") or hits.get("results") or (hits if isinstance(hits, list) else [])
        if items:
            pick = st.selectbox("검색 결과", items, format_func=lambda x: f"{x.get('name')} ({x.get('symbol') or x.get('code')})")
            symbol = pick.get("symbol") or pick.get("code")
        else:
            st.caption("검색 결과 없음")
    d = call(api.get, "/api/stocks/fundamentals", symbol=symbol) or {}
    if not d:
        return
    pu, au = d.get("priceUnit") or "", d.get("amountUnit") or ""
    kpis([("현재가", num(d.get("price"), pu), f"{d.get('chg', 0):+.2f}%" if d.get("chg") is not None else None),
          ("시가총액", num(d.get("cap"), d.get("capUnit") or ""), d.get("name")),
          ("EPS" + (" · DART" if str(d.get("valuationSource") or "").startswith("DART") else ""), num(d.get("eps"), pu), f"PER {num(d.get('per'), 'x', 2)}"),
          ("BPS", num(d.get("bps"), pu), f"PBR {num(d.get('pbr'), 'x', 2)}")])
    if d.get("warning"):
        st.warning(d["warning"])
    l, r = st.columns(2)
    with l:
        st.subheader("밸류에이션")
        table([{"항목": "PER", "값": num(d.get("per"), "x", 2)}, {"항목": "PBR", "값": num(d.get("pbr"), "x", 2)},
               {"항목": "배당수익률", "값": num(d.get("divYield"), "%", 2)}, {"항목": "주당배당금", "값": num(d.get("div"), pu)}])
        if d.get("valuationSource"):
            st.caption(f"근거: {d['valuationSource']}")
        for n in d.get("valuationNotes") or []:
            st.caption(f"· {n}")
        if d.get("dividendSource"):
            st.caption(f"배당 근거: {d['dividendSource']}")
    with r:
        st.subheader("수익성")
        basis = d.get("profitabilityBasis") or {}
        tag = lambda k: " (DART)" if str(basis.get(k, "")).startswith("DART") else ""
        table([{"항목": "ROE" + tag("roe"), "값": num(d.get("roe"), "%", 2)}, {"항목": "ROA" + tag("roa"), "값": num(d.get("roa"), "%", 2)},
               {"항목": "영업이익률" + tag("opMargin"), "값": num(d.get("opMargin"), "%", 2)}, {"항목": "부채비율" + tag("debt"), "값": num(d.get("debt"), "%" if tag("debt") else "", 1)}])
        if d.get("profitabilitySource"):
            st.caption(f"근거: {d['profitabilitySource']}")
    st.subheader("분기 실적")
    qs = d.get("quarters") or []
    if qs:
        table([{"구분": "매출액", **{q: num(v) for q, v in zip(qs, d.get("revenue") or [])}},
               {"구분": "영업이익", **{q: num(v) for q, v in zip(qs, d.get("op") or [])}},
               {"구분": "순이익", **{q: num(v) for q, v in zip(qs, d.get("net") or [])}}])
        st.caption(f"단위 {au} · 출처 {d.get('source', '')}")
    if d.get("dartOpSeries"):
        st.caption("영업이익 (DART 전자공시 · 분기 3개월)")
        table(d["dartOpSeries"], columns=["label", "operatingIncome", "basis", "fsDiv"], rename={"label": "분기", "operatingIncome": f"영업이익({au})", "basis": "기준", "fsDiv": "구분"})
    if d.get("assets") is not None:
        st.subheader("재무상태표")
        table([{"항목": "총자산", "금액": num(d.get("assets"))}, {"항목": "자기자본", "금액": num(d.get("equity"))}, {"항목": "부채총계", "금액": num(d.get("liabilities"))}, {"항목": "현금", "금액": num(d.get("cash"))}])
        st.caption(f"단위 {au} · {d.get('balanceSource', '')}")
    with st.expander("원본 JSON"):
        st.json(d, expanded=False)
