"""kis_equity_snapshots — 수익율검증(GNB) 의 KIS 일별 평가액 스냅샷 + 2026-09-28~10-12 Testbed 실측 9행 시드

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-13

시드 행은 사용자가 KIS 화면에서 옮겨 적은 값(날짜 · 누적수익률 · 누적손익 · 원금 · 평가액 · 건수). 같은 (environment, snap_date) 가
이미 있으면 건너뛴다. 6번째 열(건수)은 의미 확인 전이라 raw_count 로 원본만 보존한다.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SEED = [
    ("2026-09-28", -0.07, -72_700, 100_000_000, 99_927_300, 2284),
    ("2026-09-29", -0.06, -59_730, 100_000_000, 99_940_270, 2246),
    ("2026-09-30", -0.15, -153_141, 100_000_000, 99_846_859, 2360),
    ("2026-10-01", 0.19, 187_460, 100_000_000, 100_187_460, 1598),
    ("2026-10-02", 0.19, 185_889, 100_000_000, 100_185_889, 1649),
    ("2026-10-06", 0.07, 74_154, 100_000_000, 100_074_154, 1902),
    ("2026-10-07", -0.50, -500_146, 100_000_000, 99_499_854, 2927),
    ("2026-10-08", -2.04, -2_044_366, 100_000_000, 97_955_634, 3443),
    ("2026-10-12", -6.66, -6_661_763, 100_000_000, 93_338_237, 4021),
]


def upgrade() -> None:
    op.create_table(
        "kis_equity_snapshots",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("environment", sa.String(10), nullable=False, server_default="paper"),
        sa.Column("snap_date", sa.Date(), nullable=False),
        sa.Column("principal", sa.Float(), nullable=False),
        sa.Column("equity", sa.Float(), nullable=False),
        sa.Column("cum_return_pct", sa.Float(), nullable=False),
        sa.Column("cum_pnl", sa.Float(), nullable=False),
        sa.Column("raw_count", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("note", sa.String(200), nullable=False, server_default=""),
        sa.UniqueConstraint("environment", "snap_date", name="uq_kis_equity_env_date"),
    )
    for d, pct, pnl, principal, equity, cnt in SEED:
        op.execute(
            f"INSERT INTO kis_equity_snapshots (environment, snap_date, principal, equity, cum_return_pct, cum_pnl, raw_count, source, note) "
            f"VALUES ('paper', '{d}', {principal}, {equity}, {pct}, {pnl}, {cnt}, 'paste', 'KIS Testbed 화면 2026-10-13 적재') "
            f"ON CONFLICT ON CONSTRAINT uq_kis_equity_env_date DO NOTHING"
        )


def downgrade() -> None:
    op.drop_table("kis_equity_snapshots")
