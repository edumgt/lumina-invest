"""KIS 모의투자(Testbed) 자동매매 — 계정·로그인과 무관한 백그라운드 배치.

celery-beat 의 ``quant.auto_trade_cycle``(5분) 이 매 사이클 :func:`ensure_system_batch` 를 먼저 호출한다.
``KIS_PAPER_BATCH_ENABLED=true`` 이면 시스템 사용자(``SYSTEM_USER_ID``) 의 ``BrokerSettings`` 행을
Testbed 권장값(:data:`kis_quickstart.TESTBED_DEFAULTS`)으로 만들고 ``quant_auto_enabled=True`` 로 켠다.
그 뒤의 매수·매도·위험관리·게이트웨이 실주문은 사용자 계정과 똑같이 :mod:`auto_trade` 가 처리한다.

안전장치
  - 실주문 경로가 KIS **paper(Testbed)** 일 때만 켠다. real 이면 켜지 않고, 켜져 있으면 끈다.
  - 게이트웨이(stock-coin-trade)·서버 관리 KIS 자격증명이 모두 없으면 켜지 않는다.
  - 비상 정지(``risk_kill_switch``) 된 행은 자동으로 재가동하지 않는다. 사람이 해제해야 한다.
  - 기존 행의 한도·종목은 덮어쓰지 않는다(최초 생성 시에만 시드). ``broker=kis``, ``quant_mode=live`` 만 보장.
  - ``KIS_PAPER_BATCH_ENABLED=false`` 로 바꾸면 다음 사이클에 시스템 행(kis·live)을 끈다. 사용자 계정 행은 건드리지 않는다.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import BrokerSettings
from app.models.base import SYSTEM_USER_ID
from app.services.audit import audit

logger = logging.getLogger(__name__)

BATCH_USER = "quant_system"   # auto_trade._resolve_user_id 가 SYSTEM_USER_ID 로 바꾼다


def is_configured() -> bool:
    return bool(settings.KIS_PAPER_BATCH_ENABLED)


def configured_symbols() -> list[str]:
    raw = (settings.KIS_PAPER_BATCH_SYMBOLS or "").strip()
    return [s.strip() for s in raw.split(",") if s.strip()] if raw else []


async def _row(db: AsyncSession) -> BrokerSettings | None:
    return (await db.execute(select(BrokerSettings).where(BrokerSettings.user_id == SYSTEM_USER_ID))).scalar_one_or_none()


def _is_batch_row(row: BrokerSettings | None) -> bool:
    return bool(row and row.broker == "kis" and row.quant_mode == "live")


async def system_status(db: AsyncSession) -> dict:
    """대시보드·헬스 표시용(읽기 전용)."""
    row = await _row(db)
    return {
        "enabled": is_configured(),
        "running": bool(row and row.quant_auto_enabled and _is_batch_row(row)),
        "kill_switch": bool(row and row.risk_kill_switch),
        "kill_reason": (row.risk_halt_reason if row else "") or "",
        "symbol_source": (row.quant_symbol_source if row else ("manual" if configured_symbols() else "ai")),
        "user_id": str(SYSTEM_USER_ID),
    }


async def ensure_system_batch(db: AsyncSession) -> dict:
    """배치 스위치(env)와 실주문 경로를 보고 시스템 행을 켜거나 끈다. 매 사이클 호출해도 안전(멱등)."""
    from app.services import kis_quickstart as qs  # 지연 import: kis_quickstart → auto_trade → (지연) kis_batch

    row = await _row(db)

    if not is_configured():
        if row is not None and row.quant_auto_enabled and _is_batch_row(row):
            row.quant_auto_enabled = False
            await db.commit()
            await audit(BATCH_USER, "", "quant.kis_batch.stop", {"reason": "KIS_PAPER_BATCH_ENABLED=false"})
            logger.info("KIS 모의투자 배치 OFF — 시스템 행 자동매매 해제")
            return {"enabled": False, "running": False, "action": "disabled"}
        return {"enabled": False, "running": bool(row and row.quant_auto_enabled), "action": "none"}

    route = await qs.resolve_route()
    if not route.configured or route.environment != "paper":
        reason = "not_connected" if not route.configured else "real_environment"
        action = "none"
        if row is not None and row.quant_auto_enabled and _is_batch_row(row):
            row.quant_auto_enabled = False
            await db.commit()
            action = "disabled"
            await audit(BATCH_USER, "", "quant.kis_batch.stop", {"reason": reason, "route": route.detail})
        logger.warning("KIS 모의투자 배치를 켤 수 없음(%s): %s", reason, route.detail)
        return {"enabled": True, "running": False, "reason": reason, "route_detail": route.detail, "action": action}

    created = False
    if row is None:
        row = BrokerSettings(user_id=SYSTEM_USER_ID)
        for key, value in qs.TESTBED_DEFAULTS.items():
            setattr(row, key, value)
        row.quant_ai_top_n = int(settings.KIS_PAPER_BATCH_AI_TOP_N)
        row.quant_per_trade_budget = float(settings.KIS_PAPER_BATCH_PER_TRADE_BUDGET)
        symbols = configured_symbols()
        row.quant_symbol_source = "manual" if symbols else "ai"
        row.quant_selected_symbols = symbols
        row.app_key = row.app_secret = row.account_no = ""   # KIS 자격증명은 서버 관리 — DB 에 두지 않는다
        row.quant_auto_enabled = False
        db.add(row)
        created = True

    if row.risk_kill_switch:
        if row.quant_auto_enabled:
            row.quant_auto_enabled = False
        if created or row.quant_auto_enabled is False:
            await db.commit()
        logger.warning("KIS 모의투자 배치: 비상 정지 상태라 재가동하지 않음 (사유: %s)", row.risk_halt_reason or "수동 정지")
        return {"enabled": True, "running": False, "reason": "kill_switch", "kill_reason": row.risk_halt_reason or "", "created": created}

    changed, started = created, False
    if row.broker != "kis":
        row.broker, changed = "kis", True
    if row.quant_mode != "live":
        row.quant_mode, changed = "live", True
    if row.paper:
        row.paper, changed = False, True
    if not row.quant_auto_enabled:
        row.quant_auto_enabled, changed, started = True, True, True
    if changed:
        await db.commit()
    if started:
        await audit(BATCH_USER, "", "quant.kis_batch.start",
                    {"route": route.via, "environment": route.environment, "created": created,
                     "symbol_source": row.quant_symbol_source, "symbols": list(row.quant_selected_symbols or [])})
        logger.info("KIS 모의투자 배치 ON — route=%s env=%s created=%s", route.via, route.environment, created)
    return {"enabled": True, "running": True, "created": created, "started": started,
            "route": route.via, "environment": route.environment, "symbol_source": row.quant_symbol_source}
