"""수익율검증 — KIS 계좌(모의/실전) 일별 평가액 스냅샷. GNB 「수익율검증」(app/routes/return_review.py) 이 읽고 쓴다.

원천: KIS 화면의 일별 손익 표(날짜 · 누적수익률 · 누적손익 · 원금 · 평가액 · 건수)를 붙여넣기로 적재하거나,
매 영업일 15:40 KST 비트 태스크(quant.equity_snapshot)가 게이트웨이 잔고(총평가액)로 자동 기록한다.
누적 수익률은 원금 대비이며, 일별 변화율은 화면/서비스에서 앞 행과의 차이로 계산한다.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import Date, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPkMixin


class KisEquitySnapshot(Base, UUIDPkMixin, CreatedAtMixin):
    __tablename__ = "kis_equity_snapshots"
    __table_args__ = (UniqueConstraint("environment", "snap_date", name="uq_kis_equity_env_date"),)

    environment: Mapped[str] = mapped_column(String(10), nullable=False, default="paper")   # paper | real
    snap_date: Mapped[date] = mapped_column(Date, nullable=False)
    principal: Mapped[float] = mapped_column(Float, nullable=False)        # 원금(투자 원금)
    equity: Mapped[float] = mapped_column(Float, nullable=False)           # 평가액(총자산)
    cum_return_pct: Mapped[float] = mapped_column(Float, nullable=False)   # 원금 대비 누적 수익률(%)
    cum_pnl: Mapped[float] = mapped_column(Float, nullable=False)          # 누적 손익(원)
    raw_count: Mapped[int | None] = mapped_column(Integer, nullable=True)  # KIS 화면 6번째 열(건수). 의미 확인 전까지 원본 보존
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")   # paste | manual | gateway
    note: Mapped[str] = mapped_column(String(200), nullable=False, default="")
