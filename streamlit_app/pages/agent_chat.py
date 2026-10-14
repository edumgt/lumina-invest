"""AI 투자 상담 — POST /api/chat {question, history, use_rag, conversation_id, llm_mode}. 비동기(task_id/poll_url) 응답이면 폴링한다."""
import time

import streamlit as st

from streamlit_app import api
from streamlit_app.api import ApiError


def _ask(question: str) -> dict:
    body = {"question": question, "history": st.session_state.get("chat_history", [])[-10:],
            "use_rag": st.session_state.get("chat_use_rag", True), "conversation_id": st.session_state.get("conversation_id"),
            "llm_mode": "ollama"}
    data = api.post("/api/chat", json=body) or {}
    poll = data.get("poll_url")
    deadline = time.time() + 180
    while poll and not data.get("answer") and time.time() < deadline:
        time.sleep(2)
        data = api.get(poll) or {}
        if data.get("status") in ("done", "completed", "finished") and data.get("result"):
            data = data["result"]
    return data


def render() -> None:
    st.title("AI 투자 상담")
    st.caption("신용 리스크·금융상품·퀀트 전략을 RAG 와 LLM 으로 종합 분석합니다. 투자 권유가 아닌 교육·참고용입니다.")
    st.session_state.setdefault("chat_history", [])
    st.session_state["chat_use_rag"] = st.toggle("문서 검색(RAG) 사용", value=st.session_state.get("chat_use_rag", True))
    if st.button("새 대화"):
        st.session_state["chat_history"] = []; st.session_state.pop("conversation_id", None); st.rerun()
    for m in st.session_state["chat_history"]:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])
            for c in m.get("citations") or []:
                st.caption(f"출처: {c.get('title') or c.get('source') or c}")
    if q := st.chat_input("질문을 입력하세요"):
        st.session_state["chat_history"].append({"role": "user", "content": q})
        with st.chat_message("user"):
            st.markdown(q)
        with st.chat_message("assistant"):
            with st.spinner("분석 중..."):
                try:
                    data = _ask(q)
                except ApiError as exc:
                    st.error(exc.detail); return
            answer = data.get("answer") or "응답이 없습니다."
            st.markdown(answer)
            if data.get("conversation_id"):
                st.session_state["conversation_id"] = data["conversation_id"]
            cites = data.get("citations") or []
            for c in cites:
                st.caption(f"출처: {c.get('title') or c.get('source') or c}" if isinstance(c, dict) else f"출처: {c}")
            with st.expander("처리 단계", expanded=False):
                st.json({"steps": data.get("steps"), "mode": data.get("mode")})
        st.session_state["chat_history"].append({"role": "assistant", "content": answer, "citations": cites})
