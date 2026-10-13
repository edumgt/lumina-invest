"""live_orders.origin / client_ip / user_agent — 주문 경로(배치 봇·사용자 자동매매·수동·리밸런싱)와 수동 주문의 요청 IP·UA (6-49)

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-13

기존 행 백필: 시스템 사용자 → batch_auto, audit_events(order.manual_kis) 와 (user_id, 종목코드, 방향, ±15초) 가 맞는 행 → manual,
나머지 사용자 행 → user_auto (사용자 계정의 자동매매 사이클이 낸 주문. 2026-10-13 기준 수동 주문 감사 기록은 1건뿐).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    op.add_column("live_orders", sa.Column("origin", sa.String(20), nullable=False, server_default=""))
    op.add_column("live_orders", sa.Column("client_ip", sa.String(64), nullable=False, server_default=""))
    op.add_column("live_orders", sa.Column("user_agent", sa.String(200), nullable=False, server_default=""))
    op.execute(f"UPDATE live_orders SET origin = 'batch_auto' WHERE origin = '' AND user_id = '{SYSTEM_USER_ID}'")
    op.execute("""
        UPDATE live_orders l SET origin = 'manual'
        FROM audit_events a
        WHERE l.origin = '' AND a.event_type = 'order.manual_kis' AND a.user_id = l.user_id
          AND upper(coalesce(a.payload->>'side', '')) = upper(l.side)
          AND left(regexp_replace(upper(coalesce(a.payload->>'symbol', '')), '\\.(KS|KQ)$', ''), 6)
              = left(regexp_replace(upper(l.symbol), '\\.(KS|KQ)$', ''), 6)
          AND abs(extract(epoch from (a.created_at - l.created_at))) <= 15
    """)
    op.execute("UPDATE live_orders SET origin = 'user_auto' WHERE origin = ''")


def downgrade() -> None:
    op.drop_column("live_orders", "user_agent")
    op.drop_column("live_orders", "client_ip")
    op.drop_column("live_orders", "origin")
