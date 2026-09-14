"""모의투자(Paper Trading) 모델.

stock-coin-trade의 Member.asset(공유 현금) + StockPosition/StockOrder + HoldCrypto/CryptoOrder +
AlternativePosition/AlternativeOrder + ApiKey 를 lumina-invest(PostgreSQL, UUID 유저)로 옮긴 것.

- 주식 포지션/주문은 기존 Portfolio / Order 테이블을 그대로 재사용한다(직접매매 화면과 잔고 공유).
- 현금 잔고는 PaperAccount 한 행(유저당 1행)에서 주식·코인·대체자산이 함께 사용한다.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UpdatedAtMixin, UUIDPkMixin

PAPER_INITIAL_CASH = 100_000_000  # 가입 시 지급되는 모의투자 초기 현금(원) — stock-coin-trade와 동일


class PaperAccount(Base, UUIDPkMixin, CreatedAtMixin, UpdatedAtMixin):
    """모의투자 현금 계좌 (주식·코인·대체자산 공용)."""

    __tablename__ = "paper_accounts"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), unique=True, nullable=False
    )
    cash: Mapped[float] = mapped_column(Float, nullable=False, default=PAPER_INITIAL_CASH)
    initial_cash: Mapped[float] = mapped_column(Float, nullable=False, default=PAPER_INITIAL_CASH)


class CryptoHolding(Base, UUIDPkMixin, UpdatedAtMixin):
    """코인 보유 (Upbit KRW 마켓 기준)."""

    __tablename__ = "crypto_holdings"
    __table_args__ = (UniqueConstraint("user_id", "market_code", name="uq_crypto_holdings_user_market"),)

    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    market_code: Mapped[str] = mapped_column(String(30), nullable=False)  # KRW-BTC
    korean_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    quantity: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    avg_price: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    total_krw: Mapped[float] = mapped_column(Float, nullable=False, default=0)  # 매수 원금 누계


class CryptoOrder(Base, UUIDPkMixin, CreatedAtMixin):
    __tablename__ = "crypto_orders"
    __table_args__ = (Index("ix_crypto_orders_user_created", "user_id", "created_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    market_code: Mapped[str] = mapped_column(String(30), nullable=False)
    korean_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    order_type: Mapped[str] = mapped_column(String(4), nullable=False)  # BUY | SELL
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="WEB")


class AlternativePosition(Base, UUIDPkMixin, UpdatedAtMixin):
    """선물·옵션·파생·금속·부동산 지분 모의 포지션."""

    __tablename__ = "alternative_positions"
    __table_args__ = (UniqueConstraint("user_id", "symbol", name="uq_alternative_positions_user_symbol"),)

    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_price: Mapped[float] = mapped_column(Float, nullable=False, default=0)


class AlternativeOrder(Base, UUIDPkMixin, CreatedAtMixin):
    __tablename__ = "alternative_orders"
    __table_args__ = (Index("ix_alternative_orders_user_created", "user_id", "created_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    order_type: Mapped[str] = mapped_column(String(4), nullable=False)  # BUY | SELL
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    multiplier: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="WEB")


class ApiKey(Base, UUIDPkMixin, CreatedAtMixin):
    """외부 시스템용 Open API 키 (원문은 저장하지 않고 SHA-256 해시만 보관)."""

    __tablename__ = "api_keys"

    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False, default="My API Key")
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    call_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class LeanBacktestRun(Base, UUIDPkMixin, CreatedAtMixin):
    """QuantConnect LEAN 백테스트 실행 이력 (domain-rag-lab의 /backtests/run 결과 보관)."""

    __tablename__ = "lean_backtest_runs"
    __table_args__ = (Index("ix_lean_backtest_runs_user_created", "user_id", "created_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False)
    strategy: Mapped[str] = mapped_column(String(20), nullable=False)
    engine: Mapped[str] = mapped_column(String(60), nullable=False)
    start_date: Mapped[str] = mapped_column(String(10), nullable=False)
    end_date: Mapped[str] = mapped_column(String(10), nullable=False)
    strategy_return_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    max_drawdown_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    sharpe_ratio: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    lean_ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
