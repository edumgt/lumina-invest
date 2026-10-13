"""전략설정 화면 API (GNB 「전략설정」 — data-view="strategy-settings" / "strategy-performance").

GET  /api/quant/strategy/settings     설정 레지스트리(설명·범위) + 유효값 + 서버 기본값 + 배치 행 값 + 전략 목록 + 현재 프로필
POST /api/quant/strategy/settings     저장: env 덮어쓰기(data_cache) + 배치 행(broker_settings) 갱신 → 즉시 settings 반영, 프로필 기록
POST /api/quant/strategy/settings/reset  env 덮어쓰기 해제(서버 기본값). 배치 행 값은 유지
GET  /api/quant/strategy/performance  프로필별 성과(가상 QUANT 주문·KIS 실주문 FILLED 실현손익, 승률, 평가액 변화, 최근 사이클 수)

인증 필수. 대상은 항상 배치(시스템 사용자) 행이다 — 로그인 사용자 개인 설정은 「증권사 API 설정」(/api/quant/settings) 그대로.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.postgres import get_pg_session
from app.lib.session import get_current_user
from app.models import BrokerSettings, LiveOrder, Order
from app.models.base import SYSTEM_USER_ID
from app.services import strategy_loader, strategy_settings as ss
from app.services.audit import audit
from app.services.data_cache import cache_get

router = APIRouter(prefix="/api/quant/strategy", tags=["strategy-settings"])


class SaveBody(BaseModel):
    values: dict[str, Any] = {}      # env 덮어쓰기 + 배치 행 값을 한 dict 로 (키로 구분)
    label: str = ""                  # 프로필 메모(선택) — 성과 표에 표시


async def _system_row(db: AsyncSession, create: bool = False) -> BrokerSettings | None:
    row = (await db.execute(select(BrokerSettings).where(BrokerSettings.user_id == SYSTEM_USER_ID))).scalar_one_or_none()
    if row is None and create:
        from app.services import kis_quickstart as qs
        row = BrokerSettings(user_id=SYSTEM_USER_ID)
        for key, value in qs.TESTBED_DEFAULTS.items():
            setattr(row, key, value)
        row.app_key = row.app_secret = row.account_no = ""
        row.quant_auto_enabled = False
        db.add(row)
        await db.flush()
    return row


def _row_values(row: BrokerSettings | None) -> dict[str, Any]:
    if row is None:
        return {k: None for k in ss.ROW_KEYS}
    out = {k: getattr(row, k, None) for k in ss.ROW_KEYS if k != "strategy_id"}
    out["strategy_id"] = row.quant_strategy_id or ""
    out["strategy_version"] = row.quant_strategy_version or 0
    return out


async def _latest_cycle() -> dict | None:
    log = await cache_get(f"quant:cycle_log:{SYSTEM_USER_ID}", max_age_hours=24 * 30) or {}
    cycles = log.get("cycles") or []
    return cycles[-1] if cycles else None


@router.get("/settings")
async def get_settings(user=Depends(get_current_user), db: AsyncSession = Depends(get_pg_session)):
    doc = await ss.load_and_apply()
    row = await _system_row(db)
    try:
        strategies = await strategy_loader.list_strategies()
    except Exception:
        strategies = []
    prof = ss.current_profile(doc)
    return {
        "groups": [{"name": g, "help": ss.GROUP_HELP[g]} for g in ss.GROUP_ORDER],
        "fields": ss.describe_fields(),
        "effective": ss.effective_env(),
        "overrides": doc.get("values") or {},
        "defaults": ss.ENV_DEFAULTS,
        "row": _row_values(row),
        "row_exists": row is not None,
        "batch_running": bool(row and row.quant_auto_enabled),
        "kill_switch": bool(row and row.risk_kill_switch),
        "strategies": strategies,
        "strategy_api_configured": strategy_loader.is_configured(),
        "profile": prof, "profile_count": len(doc.get("profiles") or []),
        "updated_at": doc.get("updated_at"), "updated_by": doc.get("updated_by"), "last_run_at": doc.get("last_run_at"),
        "beat_cycle_sec": ss.ENV_DEFAULTS.get("QUANT_CYCLE_SEC"),
    }


@router.post("/settings")
async def save_settings(body: SaveBody, user=Depends(get_current_user), db: AsyncSession = Depends(get_pg_session)):
    raw = body.values or {}
    try:
        env_values = ss.validate_env_values(raw)
        row_values = ss.validate_row_values(raw)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    # 보내지 않은 env 키는 "덮어쓰기 없음"이 아니라 현재 덮어쓰기 유지 — 화면은 항상 전체를 보내지만 부분 저장도 안전하게
    doc = await ss.load_doc()
    merged_env = {**(doc.get("values") or {}), **env_values}
    # 덮어쓰기 값이 서버 기본값과 같으면 굳이 저장하지 않는다(초기화와 동일하게 취급)
    merged_env = {k: v for k, v in merged_env.items() if v != ss.ENV_DEFAULTS.get(k)}

    row = await _system_row(db, create=True)
    strategy_version = 0
    if "strategy_id" in row_values:
        sid = row_values["strategy_id"]
        if sid:
            spec = await strategy_loader.get_strategy(sid, None)
            if spec is None:
                raise HTTPException(422, f"domain-rag-lab 에 합격한 전략이 없습니다: {sid}")
            strategy_version = int(spec.get("version") or 0)
        row.quant_strategy_id = sid
        row.quant_strategy_version = strategy_version
    for key, value in row_values.items():
        if key != "strategy_id":
            setattr(row, key, value)
    await db.commit()

    full_row = _row_values(row)
    equity_start = ss.equity_from_cycle(await _latest_cycle())
    result = await ss.save(merged_env, full_row, actor=str(user["id"]), label=(body.label or "").strip()[:80], equity_start=equity_start)
    await audit(str(user["id"]), "", "quant.strategy_settings.save",
                {"overrides": merged_env, "row": {k: full_row.get(k) for k in ss.ROW_KEYS}, "profile": (result["created"] or {}).get("id")})
    return {"ok": True, "effective": ss.effective_env(), "overrides": merged_env, "row": full_row,
            "profile": ss.current_profile(result["doc"]), "created": bool(result["created"]),
            "note": "다음 사이클부터 적용됩니다(워커가 사이클 시작 시 설정을 읽습니다)."}


@router.post("/settings/reset")
async def reset_settings(user=Depends(get_current_user), db: AsyncSession = Depends(get_pg_session)):
    row = await _system_row(db)
    equity_start = ss.equity_from_cycle(await _latest_cycle())
    result = await ss.reset(str(user["id"]), _row_values(row), equity_start=equity_start)
    await audit(str(user["id"]), "", "quant.strategy_settings.reset", {"profile": (result["created"] or {}).get("id")})
    return {"ok": True, "effective": ss.effective_env(), "overrides": {}, "profile": ss.current_profile(result["doc"])}


@router.get("/performance")
async def performance(user=Depends(get_current_user), db: AsyncSession = Depends(get_pg_session)):
    doc = await ss.load_doc()
    v_rows = (await db.execute(select(Order).where(Order.user_id == SYSTEM_USER_ID, Order.source == "QUANT").order_by(Order.created_at))).scalars().all()
    l_rows = (await db.execute(select(LiveOrder).where(LiveOrder.user_id == SYSTEM_USER_ID, LiveOrder.status == "FILLED").order_by(LiveOrder.created_at))).scalars().all()
    virtual = [{"time": r.created_at, "symbol": r.symbol, "side": r.order_type, "qty": r.quantity, "price": r.price}
               for r in v_rows if r.status == "filled" and r.created_at]
    live = [{"time": r.created_at, "symbol": r.symbol, "side": r.side, "qty": r.filled_quantity, "price": r.avg_filled_price}
            for r in l_rows if r.created_at and r.filled_quantity and r.avg_filled_price]
    log = await cache_get(f"quant:cycle_log:{SYSTEM_USER_ID}", max_age_hours=24 * 30) or {}
    cycles = log.get("cycles") or []
    latest = ss.equity_from_cycle(cycles[-1] if cycles else None)
    rows = ss.build_performance(doc.get("profiles") or [], virtual, live, cycles, latest)
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "profiles": rows, "fields": ss.describe_fields(),
            "virtual_orders": len(virtual), "live_fills": len(live), "cycles_in_cache": len(cycles), "latest_equity": latest,
            "aggressive_now": bool(settings.QUANT_AGGRESSIVE_MODE)}
