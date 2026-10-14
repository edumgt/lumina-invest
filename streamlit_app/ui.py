"""공통 렌더링 도우미 — 숫자 표기·KPI 타일·표·오류 표시."""
from __future__ import annotations

from typing import Any, Iterable

import pandas as pd
import streamlit as st

from streamlit_app.api import ApiError


def won(v: Any, digits: int = 0) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "-"
    return f"{f:,.{digits}f}원"


def pct(v: Any, digits: int = 2, signed: bool = True) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "-"
    return f"{f:+.{digits}f}%" if signed else f"{f:.{digits}f}%"


def num(v: Any, suffix: str = "", digits: int = 0) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "N/A"
    return f"{f:,.{digits}f}{suffix}"


def kpis(items: Iterable[tuple[str, str, str | None]], cols: int = 4) -> None:
    """[(label, value, delta/sub)] 를 st.metric 격자로."""
    items = list(items)
    for start in range(0, len(items), cols):
        row = items[start:start + cols]
        for col, (label, value, sub) in zip(st.columns(len(row)), row):
            col.metric(label, value, sub if sub else None)


def table(rows: list[dict] | None, columns: list[str] | None = None, rename: dict[str, str] | None = None, empty: str = "데이터 없음") -> None:
    if not rows:
        st.caption(empty); return
    df = pd.DataFrame(rows)
    if columns:
        df = df[[c for c in columns if c in df.columns]]
    if rename:
        df = df.rename(columns=rename)
    st.dataframe(df, use_container_width=True, hide_index=True)


def show_error(exc: ApiError) -> None:
    if exc.status == 401:
        st.warning("로그인이 필요합니다. 좌측에서 다시 로그인하세요.")
    else:
        st.error(f"API 오류 ({exc.status or '연결'}): {exc.detail}")


def call(fn, *args, **kwargs):
    """API 호출을 감싸 오류를 화면에 표시하고 None 을 돌려준다."""
    try:
        return fn(*args, **kwargs)
    except ApiError as exc:
        show_error(exc)
        return None
