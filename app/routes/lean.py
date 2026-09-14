"""QuantConnect LEAN 백테스트 API — domain-rag-lab의 /backtests/run 이식.

접두어: /api/backtests/lean
"""
from __future__ import annotations

import re
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.postgres import get_pg_session
from app.lib.jwt_auth import get_current_user_any
from app.models import LeanBacktestRun
from app.services.audit import audit
from app.services.lean_backtest import STRATEGY_LABELS, LeanBacktestError, lean_status, service

router = APIRouter(prefix="/api/backtests/lean", tags=["lean-backtest"])


class BacktestRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=12, examples=["005930.KS"])
    start_date: date
    end_date: date
    compare_start_date: date
    compare_end_date: date
    initial_cash: float = Field(default=10_000, ge=1_000, le=1_000_000_000)
    strategy: str = Field(default="buy_hold", description="buy_hold | ma_cross | dca | momentum")
    short_window: int = Field(default=20, ge=2, le=120)
    long_window: int = Field(default=60, ge=5, le=300)
    dca_interval_days: int = Field(default=21, ge=1, le=120)
    breakout_window: int = Field(default=20, ge=5, le=120)

    @field_validator("ticker")
    @classmethod
    def ticker_is_safe(cls, value: str) -> str:
        ticker = value.strip().upper()
        if not re.fullmatch(r"[A-Z0-9.^=-]{1,12}", ticker):
            raise ValueError("티커는 영문·숫자와 . ^ = - 만 사용할 수 있습니다.")
        # 6자리 국내 코드만 들어오면 KOSPI 티커로 보정
        if re.fullmatch(r"\d{6}", ticker):
            ticker = f"{ticker}.KS"
        return ticker

    @field_validator("strategy")
    @classmethod
    def strategy_known(cls, value: str) -> str:
        if value not in STRATEGY_LABELS:
            raise ValueError(f"strategy는 {', '.join(STRATEGY_LABELS)} 중 하나여야 합니다.")
        return value

    @model_validator(mode="after")
    def windows_are_consistent(self) -> "BacktestRequest":
        if self.strategy == "ma_cross" and self.long_window <= self.short_window:
            raise ValueError("장기 이동평균 기간은 단기 이동평균 기간보다 길어야 합니다.")
        return self


@router.get("/status")
async def status():
    return lean_status()


@router.post("/run")
async def run_backtest(payload: BacktestRequest, user=Depends(get_current_user_any),
                       db: AsyncSession = Depends(get_pg_session)):
    try:
        result = await service.run(**payload.model_dump())
    except LeanBacktestError as exc:
        raise HTTPException(422, str(exc))
    except Exception as exc:
        raise HTTPException(502, f"백테스트 실행 중 외부 데이터 또는 LEAN 실행 오류가 발생했습니다: {exc}")

    try:
        db.add(LeanBacktestRun(
            user_id=uuid.UUID(str(user["id"])), ticker=result["ticker"], strategy=result["strategy"],
            engine=result["engine"][:60], start_date=result["start_date"], end_date=result["end_date"],
            strategy_return_pct=result["strategy_return_pct"], max_drawdown_pct=result["max_drawdown_pct"],
            sharpe_ratio=result["sharpe_ratio"], lean_ok=result["lean_ok"],
        ))
        await db.commit()
    except Exception:
        await db.rollback()
    await audit(user["id"], user.get("client_id", ""), "lean.backtest.run",
                {"ticker": result["ticker"], "strategy": result["strategy"], "mode": result["lean_mode"],
                 "return_pct": result["strategy_return_pct"], "lean_ok": result["lean_ok"]})
    return result


@router.get("/history")
async def history(limit: int = Query(30, ge=1, le=200), user=Depends(get_current_user_any),
                  db: AsyncSession = Depends(get_pg_session)):
    rows = (await db.execute(
        select(LeanBacktestRun).where(LeanBacktestRun.user_id == uuid.UUID(str(user["id"])))
        .order_by(LeanBacktestRun.created_at.desc()).limit(limit)
    )).scalars().all()
    return {"runs": [{
        "id": str(r.id), "ticker": r.ticker, "strategy": r.strategy, "strategy_label": STRATEGY_LABELS.get(r.strategy, r.strategy),
        "engine": r.engine, "start_date": r.start_date, "end_date": r.end_date,
        "strategy_return_pct": r.strategy_return_pct, "max_drawdown_pct": r.max_drawdown_pct,
        "sharpe_ratio": r.sharpe_ratio, "lean_ok": r.lean_ok, "created_at": r.created_at.isoformat(),
    } for r in rows]}
