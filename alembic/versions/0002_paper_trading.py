"""paper trading (stock-coin-trade 이식) + LEAN backtest runs (domain-rag-lab 이식)

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _uuid_pk():
    return sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _user_fk(unique: bool = False):
    return sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False, unique=unique)


def _created_at():
    return sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def _updated_at():
    return sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def upgrade() -> None:
    # 주문 출처 구분 (직접매매/모의투자/OpenAPI/Pine/퀀트)
    op.add_column("orders", sa.Column("source", sa.String(20), nullable=False, server_default="WEB"))

    op.create_table(
        "paper_accounts",
        _uuid_pk(), _user_fk(unique=True),
        sa.Column("cash", sa.Float, nullable=False, server_default="100000000"),
        sa.Column("initial_cash", sa.Float, nullable=False, server_default="100000000"),
        _created_at(), _updated_at(),
    )

    op.create_table(
        "crypto_holdings",
        _uuid_pk(), _user_fk(),
        sa.Column("market_code", sa.String(30), nullable=False),
        sa.Column("korean_name", sa.String(100), nullable=False, server_default=""),
        sa.Column("quantity", sa.Float, nullable=False, server_default="0"),
        sa.Column("avg_price", sa.Float, nullable=False, server_default="0"),
        sa.Column("total_krw", sa.Float, nullable=False, server_default="0"),
        _updated_at(),
        sa.UniqueConstraint("user_id", "market_code", name="uq_crypto_holdings_user_market"),
    )

    op.create_table(
        "crypto_orders",
        _uuid_pk(), _user_fk(),
        sa.Column("market_code", sa.String(30), nullable=False),
        sa.Column("korean_name", sa.String(100), nullable=False, server_default=""),
        sa.Column("order_type", sa.String(4), nullable=False),
        sa.Column("quantity", sa.Float, nullable=False),
        sa.Column("price", sa.Float, nullable=False),
        sa.Column("amount", sa.Float, nullable=False),
        sa.Column("source", sa.String(20), nullable=False, server_default="WEB"),
        _created_at(),
    )
    op.create_index("ix_crypto_orders_user_created", "crypto_orders", ["user_id", "created_at"])

    op.create_table(
        "alternative_positions",
        _uuid_pk(), _user_fk(),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False, server_default="0"),
        sa.Column("avg_price", sa.Float, nullable=False, server_default="0"),
        _updated_at(),
        sa.UniqueConstraint("user_id", "symbol", name="uq_alternative_positions_user_symbol"),
    )

    op.create_table(
        "alternative_orders",
        _uuid_pk(), _user_fk(),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("order_type", sa.String(4), nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("price", sa.Float, nullable=False),
        sa.Column("multiplier", sa.Integer, nullable=False, server_default="1"),
        sa.Column("amount", sa.Float, nullable=False),
        sa.Column("source", sa.String(20), nullable=False, server_default="WEB"),
        _created_at(),
    )
    op.create_index("ix_alternative_orders_user_created", "alternative_orders", ["user_id", "created_at"])

    op.create_table(
        "api_keys",
        _uuid_pk(), _user_fk(),
        sa.Column("label", sa.String(100), nullable=False, server_default="My API Key"),
        sa.Column("key_prefix", sa.String(16), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("call_count", sa.BigInteger, nullable=False, server_default="0"),
        _created_at(),
    )
    op.create_index("ix_api_keys_user", "api_keys", ["user_id"])

    op.create_table(
        "lean_backtest_runs",
        _uuid_pk(), _user_fk(),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("strategy", sa.String(20), nullable=False),
        sa.Column("engine", sa.String(60), nullable=False),
        sa.Column("start_date", sa.String(10), nullable=False),
        sa.Column("end_date", sa.String(10), nullable=False),
        sa.Column("strategy_return_pct", sa.Float, nullable=False, server_default="0"),
        sa.Column("max_drawdown_pct", sa.Float, nullable=False, server_default="0"),
        sa.Column("sharpe_ratio", sa.Float, nullable=False, server_default="0"),
        sa.Column("lean_ok", sa.Boolean, nullable=False, server_default=sa.text("false")),
        _created_at(),
    )
    op.create_index("ix_lean_backtest_runs_user_created", "lean_backtest_runs", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_table("lean_backtest_runs")
    op.drop_table("api_keys")
    op.drop_table("alternative_orders")
    op.drop_table("alternative_positions")
    op.drop_table("crypto_orders")
    op.drop_table("crypto_holdings")
    op.drop_table("paper_accounts")
    op.drop_column("orders", "source")
