import streamlit as st

from streamlit_app import api
from streamlit_app.api import ApiError


def render() -> None:
    st.title("로그인")
    st.caption("fd.edumgt.co.kr 계정으로 로그인합니다. 서버 세션 쿠키(fin_session)를 이 Streamlit 세션에만 보관합니다.")
    with st.form("login"):
        email = st.text_input("이메일")
        password = st.text_input("비밀번호", type="password")
        ok = st.form_submit_button("로그인", type="primary")
    if ok:
        try:
            user = api.login(email.strip(), password)
            st.success(f"{user.get('name') or user.get('email') or '사용자'} 님, 로그인됐습니다.")
            st.rerun()
        except ApiError as exc:
            st.error(exc.detail)
