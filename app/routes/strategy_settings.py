"""전략설정 화면 API (GNB 「전략설정」 — data-view="strategy-settings" / "strategy-performance").

GET  /api/quant/strategy/settings     설정 레지스트리(설명·범위) + 유효값 + 서버 기본값 + 배치 행 값 + 전략 목록 + 현재 프로필
POST /api/quant/strategy/settings     저장: env 덮어쓰기(data_cache) + 배치 행(broker_settings) 갱신 → 즉시 settings 반영, 프로필 기록
POST /api/quant/strategy/settings/reset  env 덮어쓰기 해제(서버 기본값). 배치 행 값은 유지
GET  /api/quant/strategy/performance  프로필별 성과(가상 QUANT 주문·KIS 실주문 FILLED 실현손익, 승률, 평가액 변화, 최근 사이클 수)
GET  /api/quant/strategy/spec         선택한 전략 스펙 상세 + 전체 버전 + 상태 점검(백테스트 기반 규칙 카드) — ?strategy_id=&version=
POST /api/quant/strategy/revalidate   pr 에 재검증(최근 기간 백테스트 → 새 버전) 요청을 중계. 201 합격 / 422 불합격(failed[])

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
from app.services import signal_mode, strategy_loader, strategy_settings as ss
from app.services.audit import audit
from app.services.data_cache import cache_get
from app.services.stock import QUANT_STOCKS

router = APIRouter(prefix="/api/quant/strategy", tags=["strategy-settings"])


class SaveBody(BaseModel):
    values: dict[str, Any] = {}      # env 덮어쓰기 + 배치 행 값을 한 dict 로 (키로 구분)
    label: str = ""                  # 프로필 메모(선택) — 성과 표에 표시


class RevalidateBody(BaseModel):
    strategy_id: str
    ticker: str | None = None        # 비우면 스펙 universe 첫 종목
    start_date: str | None = None    # 비우면 스펙의 백테스트 시작일
    end_date: str | None = None      # 비우면 스펙의 백테스트 종료일 (화면은 오늘을 보낸다)


def _manual_symbols() -> list[str] | None:
    raw = (getattr(settings, "KIS_PAPER_BATCH_SYMBOLS", "") or "").strip()
    return [x.strip() for x in raw.split(",") if x.strip()] or None


async def _spec_bundle(strategy_id: str, version: int | None, strategies: list[dict] | None = None) -> dict[str, Any]:
    """스펙 상세 + 버전 목록 + 최신 합격 버전 + 상태 점검. 로더 실패는 None 으로 흡수한다."""
    spec = versions = None
    if strategy_id:
        try:
            spec = await strategy_loader.get_strategy(strategy_id, version or None)
            versions = await strategy_loader.list_versions(strategy_id)
        except Exception:   # pragma: no cover - 로더는 자체적으로 예외를 삼키지만 방어
            spec = versions = None
    if strategies is None:
        try:
            strategies = await strategy_loader.list_strategies()
        except Exception:
            strategies = []
    latest = next((int(x.get("version") or 0) for x in strategies if x.get("strategy_id") == strategy_id), None)
    if latest is None and versions:
        latest = versions.get("latest_passed_version")
    return {"spec": spec, "versions": versions, "latest_version": latest,
            "health": ss.spec_health(spec, latest, version or (spec or {}).get("version"), _manual_symbols()) if strategy_id else []}


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
    row_vals = _row_values(row)
    sid = row_vals.get("strategy_id") or ""
    applied_version = int(row_vals.get("strategy_version") or 0)
    bundle = await _spec_bundle(sid, applied_version, strategies)
    return {
        "groups": ss.describe_groups(),
        "fields": ss.describe_fields(),
        "signal_mode": signal_mode.resolve(sid),
        "signal_mode_explicit": signal_mode.explicit(),
        "signal_modes": [{"value": m, "label": signal_mode.LABELS[m], "description": signal_mode.DESCRIPTIONS[m]} for m in signal_mode.MODES],
        "hp_help": ss.HP_HELP,
        "spec": bundle["spec"], "spec_versions": bundle["versions"], "spec_latest_version": bundle["latest_version"], "spec_health": bundle["health"],
        "strategy_public_url": strategy_loader.public_url(),
        "stock_names": {str(x["symbol"])[:6]: x["name"] for x in QUANT_STOCKS},
        "effective": ss.effective_env(),
        "overrides": doc.get("values") or {},
        "defaults": ss.ENV_DEFAULTS,
        "row": row_vals,
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
    # 운영 모드 ② 백테스트 기반 규칙은 합격 전략 없이는 저장할 수 없다 (사이클이 매수를 보류하므로 미리 막는다)
    next_sid = row_values.get("strategy_id", row.quant_strategy_id or "") if "strategy_id" in row_values else (row.quant_strategy_id or "")
    next_mode = merged_env.get("QUANT_SIGNAL_MODE") or ss.ENV_DEFAULTS.get("QUANT_SIGNAL_MODE") or ""
    if next_mode == signal_mode.BACKTEST and not next_sid:
        raise HTTPException(422, "운영 모드 ② 백테스트 기반 규칙은 합격한 전략 스펙을 선택해야 저장할 수 있습니다.")
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


@router.get("/spec")
async def get_spec(strategy_id: str = "", version: int | None = None, user=Depends(get_current_user)):
    """백테스트 기반 규칙 카드: 드롭다운에서 전략을 바꿀 때 스펙 상세·버전·상태 점검을 다시 받는다."""
    await ss.load_and_apply()
    sid = (strategy_id or "").strip().lower()
    if not sid:
        return {"spec": None, "versions": None, "latest_version": None, "health": ss.spec_health(None)}
    bundle = await _spec_bundle(sid, version)
    if bundle["spec"] is None:
        raise HTTPException(404, f"domain-rag-lab 에 합격한 전략이 없습니다: {sid}")
    return bundle


@router.post("/revalidate")
async def revalidate(body: RevalidateBody, user=Depends(get_current_user)):
    """pr 에 재검증을 중계한다. 수십 초~수 분(LEAN 실행) 걸린다. 201 이면 새 합격 버전 — 「최신 버전 적용」 으로 바꾼다."""
    sid = (body.strategy_id or "").strip().lower()
    if not sid:
        raise HTTPException(422, "strategy_id 가 필요합니다.")
    if not strategy_loader.is_configured():
        raise HTTPException(503, "domain-rag-lab 이 설정되지 않았습니다(DOMAIN_RAG_LAB_BASE_URL).")
    status, data = await strategy_loader.revalidate(sid, ticker=body.ticker, start_date=body.start_date, end_date=body.end_date)
    await audit(str(user["id"]), "", "quant.strategy.revalidate", {"strategy_id": sid, "status": status, "end_date": body.end_date})
    if status == 201:
        return {"ok": True, "spec": data, "note": f"재검증 합격 — v{(data or {}).get('version')} 저장됨. 「최신 버전 적용」 을 누르면 다음 사이클부터 이 버전을 씁니다."}
    detail = (data or {}).get("detail") if isinstance(data, dict) else None
    if isinstance(detail, dict):   # pr 불합격 응답 {message, failed[], spec} → 화면 토스트용 한 줄
        detail = f"{detail.get('message') or '재검증 불합격'} · 미달: {'; '.join(detail.get('failed') or []) or '-'}"
    if status == 422:
        raise HTTPException(422, detail or "재검증 불합격")
    if status == 404:
        raise HTTPException(404, detail or "합격한 전략 스펙이 없습니다.")
    raise HTTPException(502, detail or f"pr 재검증 호출 실패 (status {status})")


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
