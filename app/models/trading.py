from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import ARRAY, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UpdatedAtMixin, UUIDPkMixin


class Portfolio(Base, UUIDPkMixin, UpdatedAtMixin):
    __tablename__ = "portfolio"
    __table_args__ = (UniqueConstraint("user_id", "symbol", name="uq_portfolio_user_symbol"),)

    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_price: Mapped[float] = mapped_column(Float, nullable=False, default=0)


class Order(Base, UUIDPkMixin, CreatedAtMixin):
    __tablename__ = "orders"
    __table_args__ = (Index("ix_orders_user_created", "user_id", "created_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    order_type: Mapped[str] = mapped_column(String(10), nullable=False)  # buy | sell
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="filled")
    broker: Mapped[str] = mapped_column(String(20), nullable=False, default="virtual")
    # WEB(직접매매 화면) | PAPER(모의투자 화면) | OPENAPI(외부 Open API) | PINE(파인스크립트 실습) | QUANT(자동매매)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="WEB")


class BrokerSettings(Base, UUIDPkMixin, UpdatedAtMixin):
    """증권사 자격증명 + 퀀트 자동매매 전략설정 (Mongo 문서처럼 1유저 1행 통합)."""

    __tablename__ = "broker_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), unique=True, nullable=False
    )
    broker: Mapped[str] = mapped_column(String(20), nullable=False, default="mock")
    app_key: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    app_secret: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    account_no: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    paper: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    quant_mode: Mapped[str] = mapped_column(String(10), nullable=False, default="paper")
    quant_symbol_source: Mapped[str] = mapped_column(String(10), nullable=False, default="ai")
    quant_selected_symbols: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    quant_ai_top_n: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    quant_per_trade_budget: Mapped[float] = mapped_column(Float, nullable=False, default=1_000_000)
    quant_buy_ratio: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    quant_sell_ratio: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)


class QuantVirtualAccount(Base, UUIDPkMixin, CreatedAtMixin, UpdatedAtMixin):
    __tablename__ = "quant_virtual_accounts"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), unique=True, nullable=False
    )
    initial_capital: Mapped[float] = mapped_column(Float, nullable=False, default=10_000_000)
    cash_balance: Mapped[float] = mapped_column(Float, nullable=False, default=10_000_000)


class CustomIndicator(Base, UUIDPkMixin, CreatedAtMixin):
    __tablename__ = "custom_indicators"
    __table_args__ = (Index("ix_custom_indicators_user_created", "user_id", "created_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    base: Mapped[str] = mapped_column(String(20), nullable=False, default="rsi_ma")
    short_window: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    mid_window: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    rsi_period: Mapped[int] = mapped_column(Integer, nullable=False, default=14)
    buy_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=35.0)
