"""수익율검증 화면 API (GNB 「수익율검증」 — data-view="return-review", js/return_review.js).

GET    /api/quant/return-review                 일별 평가액(누적·일변화·낙폭) + 날짜별 매매·운영 모드 + 왕복 통계 + 가이드
POST   /api/quant/return-review/rows            KIS 화면 표 붙여넣기(text) 또는 rows[] 적재(upsert)
POST   /api/quant/return-review/snapshot        게이트웨이 잔고로 오늘 행 기록 (비트 태스크 quant.equity_snapshot 과 같은 함수)
DELETE /api/quant/return-review/rows/{date}     행 삭제

인증 필수. 대상은 배치(시스템 사용자)의 KIS 계좌(environment 기본 paper).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.postgres import get_pg_session
from app.lib.session import get_current_user
from app.models import BrokerSettings, LiveOrder, Order
from app.models.base import SYSTEM_USER_ID
from app.models.review import KisEquitySnapshot
from app.services import return_review as rr, strategy_settings as ss
from app.services.audit import audit
from app.services.data_cache import cache_get

router = APIRouter(prefix="/api/quant/return-review", tags=["return-review"])


class RowsBody(BaseModel):
    text: str = ""                       # KIS 화면 표 붙여넣기
    rows: list[dict[str, Any]] = []      # 또는 구조화된 행
    environment: str = "paper"
    note: str = ""


class SnapshotBody(BaseModel):
    principal: float | None = None
    environment: str | None = None


def _snap_dict(r: KisEquitySnapshot) -> dict[str, Any]:
    return {"snap_date": r.snap_date, "principal": r.principal, "equity": r.equity, "cum_return_pct": r.cum_return_pct,
            "cum_pnl": r.cum_pnl, "raw_count": r.raw_count, "source": r.source, "note": r.note}


@router.get("")
async def get_review(environment: str = "paper", user=Depends(get_current_user), db: AsyncSession = Depends(get_pg_session)):
    doc = await ss.load_and_apply()
    snaps = (await db.execute(select(KisEquitySnapshot).where(KisEquitySnapshot.environment == environment).order_by(KisEquitySnapshot.snap_date))).scalars().all()
    rows = rr.enrich([_snap_dict(r) for r in snaps])
    l_all = (await db.execute(select(LiveOrder).where(LiveOrder.user_id == SYSTEM_USER_ID).order_by(LiveOrder.created_at))).scalars().all()
    v_rows = (await db.execute(select(Order).where(Order.user_id == SYSTEM_USER_ID, Order.source == "QUANT").order_by(Order.created_at))).scalars().all()
    live_filled = [{"time": r.created_at, "symbol": r.symbol, "side": r.side, "qty": r.filled_quantity, "price": r.avg_filled_price}
                   for r in l_all if r.status == "FILLED" and r.created_at and r.filled_quantity and r.avg_filled_price]
    live_all = [{"time": r.created_at, "status": r.status} for r in l_all if r.created_at]
    virtual = [{"time": r.created_at, "side": r.order_type} for r in v_rows if r.status == "filled" and r.created_at]
    by_day = rr.trading_by_day(live_filled, live_all, virtual)
    tstats = rr.trade_stats(by_day)
    modes = rr.mode_by_day(doc.get("profiles") or [], [r["snap_date"] for r in rows])
    broker = (await db.execute(select(BrokerSettings).where(BrokerSettings.user_id == SYSTEM_USER_ID))).scalar_one_or_none()
    limits = {"daily_loss_limit_pct": float(getattr(broker, "risk_daily_loss_limit_pct", 0) or 0) if broker else 0.0,
              "max_position_pct": float(getattr(broker, "risk_max_position_pct", 0) or 0) if broker else 0.0}
    eff = ss.effective_env()
    for r in rows:
        r["trading"] = by_day.get(r["snap_date"], {k: 0 for k in ("fills", "buys", "sells", "realized", "orders", "open_or_cancelled", "virtual_buys", "virtual_sells")})
        r["trading"] = {k: v for k, v in r["trading"].items() if k != "round_trips"}
        r["mode"] = modes.get(r["snap_date"], {})
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(), "environment": environment,
        "rows": rows, "summary": rr.summary(rows), "trade_stats": tstats,
        "guide": rr.build_guide(rows, by_day, tstats, modes, limits, eff),
        "limits": limits, "signal_mode": eff.get("QUANT_SIGNAL_MODE") or "", "aggressive": bool(eff.get("QUANT_AGGRESSIVE_MODE")),
        "profiles": len(doc.get("profiles") or []), "live_orders": len(l_all), "live_fills": len(live_filled), "virtual_orders": len(virtual),
        "strategy_public_url": (getattr(__import__("app.config", fromlist=["settings"]).settings, "DOMAIN_RAG_LAB_PUBLIC_URL", "") or "").rstrip("/"),
    }


@router.post("/rows")
async def post_rows(body: RowsBody, user=Depends(get_current_user), db: AsyncSession = Depends(get_pg_session)):
    try:
        rows = rr.parse_pasted(body.text) if body.text.strip() else []
        for r in body.rows:
            rows.append({"snap_date": date.fromisoformat(str(r["snap_date"])[:10]), "principal": float(r["principal"]), "equity": float(r["equity"]),
                         "cum_pnl": float(r.get("cum_pnl", float(r["equity"]) - float(r["principal"]))),
                         "cum_return_pct": float(r.get("cum_return_pct", (float(r["equity"]) / float(r["principal"]) - 1) * 100)),
                         "raw_count": r.get("raw_count")})
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, str(exc))
    if not rows:
        raise HTTPException(422, "적재할 행이 없습니다.")
    result = await rr.upsert_rows(db, rows, environment=body.environment, source="paste" if body.text.strip() else "manual", note=body.note)
    await audit(str(user["id"]), "", "quant.return_review.import", {"environment": body.environment, **result})
    return {"ok": True, **result, "dates": [rr._d(r["snap_date"]).isoformat() for r in rows]}


@router.post("/snapshot")
async def post_snapshot(body: SnapshotBody, user=Depends(get_current_user), db: AsyncSession = Depends(get_pg_session)):
    result = await rr.record_gateway_snapshot(db, principal=body.principal, environment=body.environment)
    if not result.get("ok"):
        raise HTTPException(502, result.get("error") or "스냅샷 실패")
    await audit(str(user["id"]), "", "quant.return_review.snapshot", {k: v for k, v in result.items() if k != "row"})
    return result


@router.delete("/rows/{snap_date}")
async def delete_row(snap_date: str, environment: str = "paper", user=Depends(get_current_user), db: AsyncSession = Depends(get_pg_session)):
    try:
        d = date.fromisoformat(snap_date)
    except ValueError:
        raise HTTPException(422, "날짜 형식은 YYYY-MM-DD")
    row = (await db.execute(select(KisEquitySnapshot).where(KisEquitySnapshot.environment == environment, KisEquitySnapshot.snap_date == d))).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "해당 날짜 행이 없습니다.")
    await db.delete(row)
    await db.commit()
    await audit(str(user["id"]), "", "quant.return_review.delete", {"environment": environment, "snap_date": snap_date})
    return {"ok": True}
