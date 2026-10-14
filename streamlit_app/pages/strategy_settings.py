"""전략설정 — GET/POST /api/quant/strategy/settings(필드 메타 + 현재값), /reset, /spec, /revalidate, /performance"""
import streamlit as st

from streamlit_app import api
from streamlit_app.api import ApiError
from streamlit_app.ui import call, table


def _widget(f: dict, current):
    key, label, t = f["key"], f.get("label") or f["key"], (f.get("type") or "str").lower()
    help_ = " · ".join(x for x in (f.get("description"), f.get("note")) if x) or None
    if f.get("choices"):
        choices = [str(c) for c in f["choices"]]
        idx = choices.index(str(current)) if str(current) in choices else 0
        return st.selectbox(label, choices, index=idx, key=f"ss_{key}", help=help_)
    if t in ("bool", "boolean"):
        return st.checkbox(label, value=bool(current in (True, "true", "True", "1", 1)), key=f"ss_{key}", help=help_)
    if t in ("int", "integer"):
        lo, hi = f.get("min"), f.get("max")
        return st.number_input(label, value=int(float(current or 0)), min_value=int(lo) if lo is not None else None, max_value=int(hi) if hi is not None else None, step=1, key=f"ss_{key}", help=help_)
    if t in ("float", "number", "pct"):
        lo, hi = f.get("min"), f.get("max")
        return st.number_input(label, value=float(current or 0), min_value=float(lo) if lo is not None else None, max_value=float(hi) if hi is not None else None, key=f"ss_{key}", help=help_)
    return st.text_input(label, value="" if current is None else str(current), key=f"ss_{key}", help=help_)


def render() -> None:
    st.title("봇 전략 · 파라미터 설정")
    data = call(api.get, "/api/quant/strategy/settings") or {}
    if not data:
        return
    values = data.get("values") or {}
    mode = data.get("signal_mode")
    modes = data.get("signal_modes") or []
    st.info(f"현재 운영 모드: **{next((m['label'] for m in modes if m['value'] == mode), mode)}** — "
            + next((m["description"] for m in modes if m["value"] == mode), ""))
    fields = [f for f in (data.get("fields") or []) if not f.get("hidden")]
    groups = data.get("groups") or []
    new_values: dict = {}
    with st.form("strategy_settings"):
        for g in groups:
            gf = [f for f in fields if f.get("group") == g["name"]]
            if not gf:
                continue
            with st.expander(f"{g['name']}  ·  {g.get('help', '')}", expanded=(g["name"] == groups[0]["name"])):
                cols = st.columns(2)
                for i, f in enumerate(gf):
                    with cols[i % 2]:
                        new_values[f["key"]] = _widget(f, values.get(f["key"], f.get("default")))
        label = st.text_input("프로필 메모(선택)", placeholder="예: 보수형 테스트")
        saved = st.form_submit_button("저장", type="primary")
    if saved:
        changed = {k: v for k, v in new_values.items() if str(values.get(k)) != str(v)}
        if not changed:
            st.warning("바뀐 값이 없습니다.")
        else:
            try:
                r = api.post("/api/quant/strategy/settings", json={"values": changed, "label": label})
                st.success(f"저장됨 — 변경 {len(changed)}개"); st.json(r, expanded=False); st.cache_data.clear()
            except ApiError as exc:
                st.error(exc.detail)
    c1, c2 = st.columns(2)
    if c1.button("기본값으로 초기화"):
        if call(api.post, "/api/quant/strategy/settings/reset") is not None:
            st.success("초기화됨"); st.rerun()
    with c2:
        with st.expander("전략 스펙(백테스트 기반 규칙)"):
            st.json({"spec": data.get("spec"), "versions": data.get("spec_versions"), "health": data.get("spec_health")}, expanded=False)
            if data.get("strategy_public_url"):
                st.link_button("pr 전략 화면 열기", data["strategy_public_url"])
    with st.expander("하이퍼파라미터 설명"):
        for k, v in (data.get("hp_help") or {}).items():
            st.markdown(f"**{k}** — {v}")


def render_performance() -> None:
    st.title("설정별 성과")
    p = call(api.get, "/api/quant/strategy/performance") or {}
    rows = p.get("rows") or p.get("profiles") or p.get("items") or []
    table(rows)
    with st.expander("원본 JSON"):
        st.json(p, expanded=False)
