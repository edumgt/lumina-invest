"""자유 산식 커스텀 지표 API — /api/formula-indicators

- GET  /reference                : DSL 함수 목록·템플릿
- POST /validate                 : 문법·이름 검사 (+ symbol 주면 시험 계산)
- POST /compute                  : 저장 없이 즉시 계산 (에드혹)
- CRUD /, /{id}                  : 정의 저장 — 산식이 바뀌면 버전 +1 (이력 보존)
- GET  /{id}/versions, POST /{id}/versions/{v}/restore
- POST /{id}/compute             : 계산 후 결과 저장(같은 날·같은 버전·같은 종목·기간은 재사용), GET /{id}/results
- GET  /{id}/export?format=pine|python
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.postgres import get_pg_session
from app.lib.jwt_auth import get_current_user_any
from app.models import FormulaIndicator, FormulaIndicatorVersion, FormulaIndicatorResult
from app.services import formula as fx
from app.services.audit import audit
from app.services.stock import get_candles

router = APIRouter(prefix="/api/formula-indicators", tags=["formula-indicators"])


def _uid(user: dict) -> uuid.UUID:
    return uuid.UUID(str(user["id"]))


def _oid(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except Exception:
        raise HTTPException(400, "유효하지 않은 ID입니다.")


class DefinitionBody(BaseModel):
    indicator_expr: str = Field(..., min_length=1, max_length=2000)
    buy_expr: str = Field("", max_length=2000)
    sell_expr: str = Field("", max_length=2000)
    params: dict[str, float] = Field(default_factory=dict)


class SaveBody(DefinitionBody):
    name: str = Field(..., min_length=1, max_length=60)
    description: str = Field("", max_length=300)
    note: str = Field("", max_length=200, description="버전 메모")


class ComputeBody(BaseModel):
    symbol: str = "005930.KS"
    period: str = Field("2y", description="1y | 2y | 5y | 10y")
    commission_bps: float = Field(0.0, ge=0, le=500)
    slippage_bps: float = Field(0.0, ge=0, le=500)
    stop_loss_pct: float | None = Field(None, ge=0.1, le=90)
    take_profit_pct: float | None = Field(None, ge=0.1, le=500)
    use_cache: bool = True


class AdhocComputeBody(DefinitionBody, ComputeBody):
    pass


class ValidateBody(DefinitionBody):
    symbol: str | None = None


def _ind_dict(r: FormulaIndicator) -> dict:
    return {"id": str(r.id), "name": r.name, "description": r.description, "indicator_expr": r.indicator_expr,
            "buy_expr": r.buy_expr, "sell_expr": r.sell_expr, "params": r.params or {}, "current_version": r.current_version,
            "checksum": r.checksum, "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None}


def _ver_dict(v: FormulaIndicatorVersion) -> dict:
    return {"id": str(v.id), "version": v.version, "indicator_expr": v.indicator_expr, "buy_expr": v.buy_expr,
            "sell_expr": v.sell_expr, "params": v.params or {}, "checksum": v.checksum, "note": v.note,
            "created_at": v.created_at.isoformat() if v.created_at else None}


def _res_dict(r: FormulaIndicatorResult, with_payload: bool = False) -> dict:
    d = {"id": str(r.id), "indicator_id": str(r.indicator_id), "version": r.version, "checksum": r.checksum, "symbol": r.symbol,
         "period": r.period, "as_of": r.as_of, "rows": r.rows, "latest_value": r.latest_value, "latest_signal": r.latest_signal,
         "costs": r.costs, "metrics": r.metrics, "created_at": r.created_at.isoformat() if r.created_at else None}
    if with_payload:
        d["payload"] = r.payload
    return d


async def _get_owned(db: AsyncSession, user: dict, ind_id: str) -> FormulaIndicator:
    row = (await db.execute(select(FormulaIndicator).where(FormulaIndicator.id == _oid(ind_id), FormulaIndicator.user_id == _uid(user)))).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "지표를 찾을 수 없습니다.")
    return row


async def _run(defn: dict, body: ComputeBody) -> dict:
    candles = (await get_candles(body.symbol, period=body.period, interval="1d")).get("candles", [])
    if not candles:
        raise HTTPException(404, f"종목 데이터 없음: {body.symbol}")
    try:
        return fx.compute(candles, defn["indicator_expr"], defn.get("buy_expr") or None, defn.get("sell_expr") or None, defn.get("params") or {},
                          commission_bps=body.commission_bps, slippage_bps=body.slippage_bps,
                          stop_loss_pct=body.stop_loss_pct, take_profit_pct=body.take_profit_pct)
    except fx.FormulaError as exc:
        raise HTTPException(422, str(exc))
    except Exception as exc:
        raise HTTPException(422, f"산식 계산 오류: {str(exc)[:200]}")


@router.get("/reference")
async def reference(_user=Depends(get_current_user_any)):
    return {"functions": [{"signature": s, "description": d, "example": e} for s, d, e in fx.FUNCTION_DOCS],
            "templates": fx.TEMPLATES,
            "rules": ["모든 함수는 과거 봉만 참조합니다. shift(x, n) 은 n ≥ 1 만 허용되어 미래 값을 볼 수 없습니다.",
                      "buy/sell 산식은 참/거짓 시리즈여야 하며, buy 가 참인 날 진입, sell 이 참인 날 청산(다음 거래일 수익률부터 반영).",
                      "params 에 정의한 이름은 산식 안에서 숫자 변수로 쓰이며, 값만 바꿔도 새 버전이 생성됩니다."]}


@router.post("/validate")
async def validate(body: ValidateBody, _user=Depends(get_current_user_any)):
    try:
        info = fx.validate_definition(body.indicator_expr, body.buy_expr or None, body.sell_expr or None, body.params)
    except fx.FormulaError as exc:
        raise HTTPException(422, str(exc))
    if body.symbol:
        try:
            trial = await _run(body.model_dump(), ComputeBody(symbol=body.symbol, period="1y"))
            info["trial"] = {k: trial[k] for k in ("rows", "as_of", "latest_value", "latest_signal", "indicator_stats", "signal_counts")}
        except HTTPException as exc:
            info["trial_error"] = exc.detail
    return info


@router.post("/compute")
async def compute_adhoc(body: AdhocComputeBody, _user=Depends(get_current_user_any)):
    return await _run(body.model_dump(), body)


@router.get("")
async def list_indicators(user=Depends(get_current_user_any), db: AsyncSession = Depends(get_pg_session)):
    rows = (await db.execute(select(FormulaIndicator).where(FormulaIndicator.user_id == _uid(user)).order_by(FormulaIndicator.updated_at.desc()))).scalars().all()
    return {"indicators": [_ind_dict(r) for r in rows]}


@router.post("", status_code=201)
async def create_indicator(body: SaveBody, user=Depends(get_current_user_any), db: AsyncSession = Depends(get_pg_session)):
    try:
        info = fx.validate_definition(body.indicator_expr, body.buy_expr or None, body.sell_expr or None, body.params)
    except fx.FormulaError as exc:
        raise HTTPException(422, str(exc))
    dup = (await db.execute(select(FormulaIndicator.id).where(FormulaIndicator.user_id == _uid(user), FormulaIndicator.name == body.name.strip()))).scalar_one_or_none()
    if dup:
        raise HTTPException(409, "같은 이름의 지표가 이미 있습니다.")
    row = FormulaIndicator(user_id=_uid(user), name=body.name.strip(), description=body.description, indicator_expr=body.indicator_expr.strip(),
                           buy_expr=body.buy_expr.strip(), sell_expr=body.sell_expr.strip(), params=body.params, current_version=1, checksum=info["checksum"])
    db.add(row); await db.flush()
    db.add(FormulaIndicatorVersion(indicator_id=row.id, version=1, indicator_expr=row.indicator_expr, buy_expr=row.buy_expr,
                                   sell_expr=row.sell_expr, params=row.params, checksum=row.checksum, note=body.note or "최초 저장"))
    await db.commit(); await db.refresh(row)
    await audit(user["id"], user.get("client_id", ""), "formula.create", {"name": row.name, "checksum": row.checksum})
    return _ind_dict(row)


@router.get("/{ind_id}")
async def get_indicator(ind_id: str, user=Depends(get_current_user_any), db: AsyncSession = Depends(get_pg_session)):
    return _ind_dict(await _get_owned(db, user, ind_id))


@router.put("/{ind_id}")
async def update_indicator(ind_id: str, body: SaveBody, user=Depends(get_current_user_any), db: AsyncSession = Depends(get_pg_session)):
    """이름·설명은 그대로 갱신, 산식/파라미터가 바뀌면 버전을 올리고 이전 버전은 이력에 남긴다."""
    row = await _get_owned(db, user, ind_id)
    try:
        info = fx.validate_definition(body.indicator_expr, body.buy_expr or None, body.sell_expr or None, body.params)
    except fx.FormulaError as exc:
        raise HTTPException(422, str(exc))
    if body.name.strip() != row.name:
        dup = (await db.execute(select(FormulaIndicator.id).where(FormulaIndicator.user_id == _uid(user), FormulaIndicator.name == body.name.strip(), FormulaIndicator.id != row.id))).scalar_one_or_none()
        if dup: raise HTTPException(409, "같은 이름의 지표가 이미 있습니다.")
    row.name, row.description = body.name.strip(), body.description
    bumped = False
    if info["checksum"] != row.checksum:
        row.current_version += 1
        row.indicator_expr, row.buy_expr, row.sell_expr, row.params, row.checksum = body.indicator_expr.strip(), body.buy_expr.strip(), body.sell_expr.strip(), body.params, info["checksum"]
        db.add(FormulaIndicatorVersion(indicator_id=row.id, version=row.current_version, indicator_expr=row.indicator_expr, buy_expr=row.buy_expr,
                                       sell_expr=row.sell_expr, params=row.params, checksum=row.checksum, note=body.note or f"v{row.current_version} 수정"))
        bumped = True
    await db.commit(); await db.refresh(row)
    await audit(user["id"], user.get("client_id", ""), "formula.update", {"name": row.name, "version": row.current_version, "bumped": bumped})
    return {**_ind_dict(row), "version_bumped": bumped}


@router.delete("/{ind_id}")
async def delete_indicator(ind_id: str, user=Depends(get_current_user_any), db: AsyncSession = Depends(get_pg_session)):
    row = await _get_owned(db, user, ind_id)
    await db.execute(delete(FormulaIndicatorResult).where(FormulaIndicatorResult.indicator_id == row.id))
    await db.execute(delete(FormulaIndicatorVersion).where(FormulaIndicatorVersion.indicator_id == row.id))
    await db.delete(row); await db.commit()
    return {"ok": True}


@router.get("/{ind_id}/versions")
async def list_versions(ind_id: str, user=Depends(get_current_user_any), db: AsyncSession = Depends(get_pg_session)):
    row = await _get_owned(db, user, ind_id)
    vs = (await db.execute(select(FormulaIndicatorVersion).where(FormulaIndicatorVersion.indicator_id == row.id).order_by(FormulaIndicatorVersion.version.desc()))).scalars().all()
    return {"current_version": row.current_version, "versions": [_ver_dict(v) for v in vs]}


@router.post("/{ind_id}/versions/{version}/restore")
async def restore_version(ind_id: str, version: int, user=Depends(get_current_user_any), db: AsyncSession = Depends(get_pg_session)):
    """과거 버전 산식을 새 버전으로 복원(이력은 지우지 않고 앞으로 나아간다)."""
    row = await _get_owned(db, user, ind_id)
    v = (await db.execute(select(FormulaIndicatorVersion).where(FormulaIndicatorVersion.indicator_id == row.id, FormulaIndicatorVersion.version == version))).scalar_one_or_none()
    if v is None: raise HTTPException(404, "해당 버전이 없습니다.")
    if v.checksum == row.checksum:
        return {**_ind_dict(row), "restored": False, "message": "이미 현재 버전과 같은 산식입니다."}
    row.current_version += 1
    row.indicator_expr, row.buy_expr, row.sell_expr, row.params, row.checksum = v.indicator_expr, v.buy_expr, v.sell_expr, v.params, v.checksum
    db.add(FormulaIndicatorVersion(indicator_id=row.id, version=row.current_version, indicator_expr=v.indicator_expr, buy_expr=v.buy_expr,
                                   sell_expr=v.sell_expr, params=v.params, checksum=v.checksum, note=f"v{version} 복원"))
    await db.commit(); await db.refresh(row)
    return {**_ind_dict(row), "restored": True}


@router.post("/{ind_id}/compute")
async def compute_saved(ind_id: str, body: ComputeBody, user=Depends(get_current_user_any), db: AsyncSession = Depends(get_pg_session)):
    row = await _get_owned(db, user, ind_id)
    costs = {"commission_bps": body.commission_bps, "slippage_bps": body.slippage_bps, "stop_loss_pct": body.stop_loss_pct, "take_profit_pct": body.take_profit_pct}
    if body.use_cache:
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        cached = (await db.execute(select(FormulaIndicatorResult).where(
            FormulaIndicatorResult.indicator_id == row.id, FormulaIndicatorResult.version == row.current_version,
            FormulaIndicatorResult.symbol == body.symbol.upper(), FormulaIndicatorResult.period == body.period,
            FormulaIndicatorResult.costs == costs).order_by(FormulaIndicatorResult.created_at.desc()).limit(1))).scalar_one_or_none()
        if cached and cached.created_at.strftime("%Y-%m-%d") == today:
            return {**_res_dict(cached, with_payload=True), "from_cache": True}
    result = await _run(_ind_dict(row), body)
    bt = result.get("backtest") or {}
    metrics = {k: bt.get(k) for k in ("total_return_pct", "gross_return_pct", "cost_pct", "buy_hold_return_pct", "sharpe_ratio", "mdd_pct",
                                      "trade_count", "win_rate_pct", "stop_loss_exits", "take_profit_exits")} if bt else {}
    res = FormulaIndicatorResult(indicator_id=row.id, user_id=_uid(user), version=row.current_version, checksum=row.checksum,
                                 symbol=body.symbol.upper(), period=body.period, as_of=result["as_of"], rows=result["rows"],
                                 latest_value=result["latest_value"], latest_signal=result["latest_signal"], costs=costs, metrics=metrics,
                                 payload={k: result[k] for k in ("series", "indicator_stats", "signal_counts", "backtest")})
    db.add(res); await db.commit(); await db.refresh(res)
    await audit(user["id"], user.get("client_id", ""), "formula.compute", {"name": row.name, "version": row.current_version, "symbol": res.symbol})
    return {**_res_dict(res, with_payload=True), "from_cache": False}


@router.get("/{ind_id}/results")
async def list_results(ind_id: str, limit: int = Query(30, ge=1, le=200), user=Depends(get_current_user_any), db: AsyncSession = Depends(get_pg_session)):
    row = await _get_owned(db, user, ind_id)
    rs = (await db.execute(select(FormulaIndicatorResult).where(FormulaIndicatorResult.indicator_id == row.id)
                           .order_by(FormulaIndicatorResult.created_at.desc()).limit(limit))).scalars().all()
    return {"results": [_res_dict(r) for r in rs]}


@router.get("/{ind_id}/export", response_class=PlainTextResponse)
async def export_code(ind_id: str, format: str = Query("pine", pattern="^(pine|python)$"), user=Depends(get_current_user_any), db: AsyncSession = Depends(get_pg_session)):
    row = await _get_owned(db, user, ind_id)
    gen = fx.to_pine if format == "pine" else fx.to_python
    try:
        # to_pine 은 Pine 으로 옮길 수 없는 함수·구문을 만나면 깨진 코드를 내보내지 않고 FormulaError 를 낸다.
        return gen(row.name, row.indicator_expr, row.buy_expr or None, row.sell_expr or None, row.params)
    except fx.FormulaError as exc:
        raise HTTPException(422, str(exc))
