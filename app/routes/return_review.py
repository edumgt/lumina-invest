"""수익율검증 화면 API (GNB 「수익율검증」 — data-view="return-review", js/return_review.js).

GET    /api/quant/return-review                 일별 평가액(누적·일변화·낙폭) + 날짜별 매매·운영 모드 + 왕복 통계 + 가이드
POST   /api/quant/return-review/rows            KIS 화면 표 붙여넣기(text) 또는 rows[] 적재(upsert)
POST   /api/quant/return-review/snapshot        게이트웨이 잔고로 오늘 행 기록 (비트 태스크 quant.equity_snapshot 과 같은 함수)
DELETE /api/quant/return-review/rows/{date}     행 삭제
GET    /api/quant/return-review                 (추가) plans(수익률 증대 방안 A/B/C, 권장 1개) · applied_plans(승인된 방안의 사후 평가·진행) · progress_log
POST   /api/quant/return-review/plans/{id}/apply 「승인」: 서버가 방안을 다시 계산해 전략설정 저장 경로(strategy_settings.save_settings)로 적용
                                                → env 덮어쓰기(전 사이클 공통) + 배치 행 + 프로필 기록. 기준 수치·진행 로그 저장(data_cache + audit_events)

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
from app.routes import strategy_settings as ss_route
from app.services import return_review as rr, signal_mode, strategy_loader, strategy_settings as ss
from app.services.data_cache import cache_set
from app.services.stock import QUANT_STOCKS
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


class ApplyBody(BaseModel):
    note: str = ""
    environment: str = "paper"


PROGRESS_LOG_KEY = "quant:return_review_log"
LOG_MAX = 500


async def _load_applied() -> list[dict[str, Any]]:
    return list((await cache_get(rr.APPLIED_PLANS_KEY, max_age_hours=24 * 365 * 5) or {}).get("items") or [])


async def _save_applied(items: list[dict[str, Any]]) -> None:
    await cache_set(rr.APPLIED_PLANS_KEY, {"items": items[-50:]})


async def _progress_log(event: str, actor: str, detail: dict[str, Any]) -> dict[str, Any]:
    """진행 로그 1건: data_cache 타임라인(화면) + audit_events(영구). event: approve | apply | evaluate | error"""
    entry = {"time": datetime.now(timezone.utc).isoformat(), "event": event, "by": actor, **detail}
    doc = await cache_get(PROGRESS_LOG_KEY, max_age_hours=24 * 365 * 5) or {"items": []}
    items = list(doc.get("items") or []) + [entry]
    await cache_set(PROGRESS_LOG_KEY, {"items": items[-LOG_MAX:]})
    await audit(actor, "", f"quant.return_review.{event}", detail)
    return entry


def _progress_from_cycles(cycles: list[dict], profile_id: str) -> dict[str, Any]:
    """해당 프로필로 돈 사이클 수·체결·위험관리 생략·마지막 사이클 시각 (캐시된 최근 사이클 안에서)."""
    mine = [c for c in cycles if ((c.get("settings") or {}).get("profile_id") or "") == profile_id]
    trades = sum(1 for c in mine for t in (c.get("trades") or []) if t.get("status") == "filled")
    skipped = sum(len((c.get("risk") or {}).get("skipped") or []) for c in mine)
    halted = sum(1 for c in mine if (c.get("risk") or {}).get("halted"))
    return {"cycles": len(mine), "virtual_fills": trades, "risk_skipped": skipped, "halted_cycles": halted,
            "first_cycle": mine[0].get("time") if mine else None, "last_cycle": mine[-1].get("time") if mine else None,
            "last_note": (mine[-1].get("note") or "") if mine else ""}


async def _compute(db: AsyncSession, environment: str) -> dict[str, Any]:
    """GET 과 승인 엔드포인트가 공유하는 전체 계산."""
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
    row_vals = ss_route._row_values(broker)
    limits = {"daily_loss_limit_pct": float(row_vals.get("risk_daily_loss_limit_pct") or 0), "max_position_pct": float(row_vals.get("risk_max_position_pct") or 0)}
    eff = ss.effective_env()
    guide = rr.build_guide(rows, by_day, tstats, modes, limits, eff)
    try:
        strategies = await strategy_loader.list_strategies()
    except Exception:
        strategies = []
    summary = rr.summary(rows)
    plans = rr.build_plans(summary, tstats, {f["id"] for f in guide if f["level"] in ("bad", "warn")}, eff, row_vals, strategies,
                           [x["symbol"] for x in QUANT_STOCKS], signal_mode.resolve(row_vals.get("strategy_id") or ""))
    for r in rows:
        r["trading"] = {k: v for k, v in by_day.get(r["snap_date"], {k: 0 for k in ("fills", "buys", "sells", "realized", "orders", "open_or_cancelled", "virtual_buys", "virtual_sells")}).items() if k != "round_trips"}
        r["mode"] = modes.get(r["snap_date"], {})
    return {"doc": doc, "rows": rows, "by_day": by_day, "tstats": tstats, "summary": summary, "guide": guide, "plans": plans,
            "limits": limits, "eff": eff, "row_vals": row_vals, "l_all": l_all, "live_filled": live_filled, "virtual": virtual}


def _snap_dict(r: KisEquitySnapshot) -> dict[str, Any]:
    return {"snap_date": r.snap_date, "principal": r.principal, "equity": r.equity, "cum_return_pct": r.cum_return_pct,
            "cum_pnl": r.cum_pnl, "raw_count": r.raw_count, "source": r.source, "note": r.note}


@router.get("")
async def get_review(environment: str = "paper", user=Depends(get_current_user), db: AsyncSession = Depends(get_pg_session)):
    c = await _compute(db, environment)
    cycles = (await cache_get(f"quant:cycle_log:{SYSTEM_USER_ID}", max_age_hours=24 * 30) or {}).get("cycles") or []
    applied = rr.evaluate_applied(await _load_applied(), c["rows"], c["by_day"])
    changed = False
    for ap in applied:
        ap["progress"] = _progress_from_cycles(cycles, ap.get("profile_id") or "")
        if ap.get("verdict") != ap.get("last_verdict"):      # 판정이 바뀐 순간만 로그 (관찰 중 → 개선/미개선)
            await _progress_log("evaluate", "system", {"plan_id": ap.get("plan_id"), "profile_id": ap.get("profile_id"), "verdict": ap.get("verdict"),
                                                        "verdict_text": ap.get("verdict_text"), "observed_days": ap.get("observed_days"),
                                                        "avg_daily_change_after_pct": ap.get("avg_daily_change_after_pct"), "next_plan": ap.get("next_plan")})
            ap["last_verdict"] = ap["verdict"]; changed = True
    if changed:
        await _save_applied([{k: v for k, v in ap.items() if k not in ("progress",)} for ap in applied])
    log = (await cache_get(PROGRESS_LOG_KEY, max_age_hours=24 * 365 * 5) or {}).get("items") or []
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(), "environment": environment,
        "rows": c["rows"], "summary": c["summary"], "trade_stats": c["tstats"], "guide": c["guide"],
        "plans": c["plans"], "applied_plans": applied, "progress_log": log[-100:][::-1],
        "limits": c["limits"], "signal_mode": c["eff"].get("QUANT_SIGNAL_MODE") or "", "aggressive": bool(c["eff"].get("QUANT_AGGRESSIVE_MODE")),
        "profiles": len(c["doc"].get("profiles") or []), "live_orders": len(c["l_all"]), "live_fills": len(c["live_filled"]), "virtual_orders": len(c["virtual"]),
        "strategy_public_url": strategy_loader.public_url(),
    }


@router.post("/plans/{plan_id}/apply")
async def apply_plan(plan_id: str, body: ApplyBody, user=Depends(get_current_user), db: AsyncSession = Depends(get_pg_session)):
    """「승인」: 방안의 변경을 전략설정에 적용하고(다음 사이클부터 전 시스템 반영) 기준 수치·진행 로그를 남긴다."""
    actor = str(user["id"])
    c = await _compute(db, body.environment)
    plan = next((p for p in c["plans"]["plans"] if p["id"] == plan_id.upper()), None)
    if plan is None:
        raise HTTPException(404, f"방안 {plan_id} 가 없습니다.")
    if not plan["applicable"]:
        await _progress_log("error", actor, {"plan_id": plan["id"], "reason": plan["blocker"]})
        raise HTTPException(422, plan["blocker"] or "지금은 적용할 수 없는 방안입니다.")
    if not plan["changes"]:
        raise HTTPException(422, "현재 설정과 같아 바꿀 값이 없습니다.")
    values = {ch["key"]: ch["to"] for ch in plan["changes"]}
    label = f"수익율검증 방안 {plan['id']} 승인" + (f" · {body.note.strip()[:40]}" if body.note.strip() else "")
    await _progress_log("approve", actor, {"plan_id": plan["id"], "title": plan["title"], "changes": plan["changes"], "note": body.note.strip()[:200],
                                            "baseline": rr.baseline_from(c["summary"], c["tstats"])})
    try:
        result = await ss_route.save_settings(ss_route.SaveBody(values=values, label=label), user, db)
    except HTTPException as exc:
        await _progress_log("error", actor, {"plan_id": plan["id"], "reason": str(exc.detail)})
        raise
    profile = result.get("profile") or {}
    entry = {"plan_id": plan["id"], "title": plan["title"], "applied_at": datetime.now(timezone.utc).isoformat(), "by": actor,
             "profile_id": profile.get("id") or "", "changes": plan["changes"], "note": body.note.strip()[:200],
             "baseline": rr.baseline_from(c["summary"], c["tstats"]), "last_verdict": "observing",
             "effective_mode": result.get("effective", {}).get("QUANT_SIGNAL_MODE"), "strategy_id": (result.get("row") or {}).get("strategy_id") or ""}
    applied = await _load_applied(); applied.append(entry); await _save_applied(applied)
    await _progress_log("apply", actor, {"plan_id": plan["id"], "profile_id": entry["profile_id"], "applied_keys": list(values), "effective_mode": entry["effective_mode"],
                                          "strategy_id": entry["strategy_id"], "note": result.get("note", "")})
    return {"ok": True, "plan_id": plan["id"], "profile": profile, "applied": entry, "effective": result.get("effective"), "row": result.get("row"),
            "note": f"방안 {plan['id']} 적용 — 프로필 {profile.get('id') or '-'} 기록. {result.get('note', '')} 적용 후 {rr.OBSERVE_DAYS}관측일부터 이 화면에서 자동 평가합니다."}


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
