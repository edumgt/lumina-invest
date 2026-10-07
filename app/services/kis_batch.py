"""KIS 모의투자(Testbed) 자동매매 — 계정·로그인과 무관한 백그라운드 배치.

celery-beat 의 ``quant.auto_trade_cycle``(QUANT_CYCLE_SEC, 기본 3분) 이 매 사이클 :func:`ensure_system_batch` 를 먼저 호출한다.
``KIS_PAPER_BATCH_ENABLED=true`` 이면 시스템 사용자(``SYSTEM_USER_ID``) 의 ``BrokerSettings`` 행을
Testbed 권장값(:data:`kis_quickstart.TESTBED_DEFAULTS`)으로 만들고 ``quant_auto_enabled=True`` 로 켠다.
그 뒤의 매수·매도·위험관리·게이트웨이 실주문은 사용자 계정과 똑같이 :mod:`auto_trade` 가 처리한다.

안전장치
  - 실주문 경로가 KIS **paper(Testbed)** 일 때만 켠다. real 이면 켜지 않고, 켜져 있으면 끈다.
  - 게이트웨이(stock-coin-trade)·서버 관리 KIS 자격증명이 모두 없으면 켜지 않는다.
  - 비상 정지(``risk_kill_switch``) 된 행은 자동으로 재가동하지 않는다. 사람이 해제해야 한다.
  - **env 가 배치 행의 정본**(2026-10-07): 1회 투자금(``KIS_PAPER_BATCH_PER_TRADE_BUDGET``)·AI 종목 수·수동 종목은 매 사이클
    env 값으로 맞춘다. 시스템 행은 화면에서 고칠 수 없으므로 env 를 바꾸면 재기동 후 다음 사이클에 반영된다.
    위험 한도(종목 비중·일손실·쿨다운·일 주문 수)는 덮어쓰지 않는다(쿨다운·일 주문 수는 공격 모드가 런타임에 덮어쓴다).
    ``broker=kis``, ``quant_mode=live`` 는 항상 보장.
  - ``KIS_PAPER_BATCH_ENABLED=false`` 로 바꾸면 다음 사이클에 시스템 행(kis·live)을 끈다. 사용자 계정 행은 건드리지 않는다.
  - ``KIS_PAPER_BATCH_EXCLUSIVE=true``(기본 false, 2026-10-07) 면 배치가 켜져 있는 동안 **다른 사용자 계정의 kis·live 자동매매 행을 끈다**.
    같은 KIS Testbed 계좌로 두 사이클이 주문을 내는 것을 막기 위함. 사용자 계정의 paper/mock 행은 건드리지 않는다.
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
    return bool(row and getattr(row, "broker", "") == "kis" and getattr(row, "quant_mode", "") == "live")


async def _other_live_kis_rows(db: AsyncSession) -> list[BrokerSettings]:
    """시스템 행이 아닌데 kis·live 자동매매가 켜진 사용자 행."""
    stmt = select(BrokerSettings).where(
        BrokerSettings.user_id != SYSTEM_USER_ID,
        BrokerSettings.quant_auto_enabled.is_(True),
        BrokerSettings.broker == "kis",
        BrokerSettings.quant_mode == "live",
    )
    return list((await db.execute(stmt)).scalars().all())


async def _enforce_exclusive(db: AsyncSession) -> list[str]:
    """배치 단독 실행: 다른 사용자 계정의 kis·live 자동매매를 끈다. 끈 user_id 목록 반환."""
    if not settings.KIS_PAPER_BATCH_EXCLUSIVE:
        return []
    rows = await _other_live_kis_rows(db)
    if not rows:
        return []
    disabled = []
    for row in rows:
        row.quant_auto_enabled = False
        disabled.append(str(row.user_id))
    await db.commit()
    await audit(BATCH_USER, "", "quant.kis_batch.exclusive", {"disabled_users": disabled})
    logger.warning("KIS 모의투자 배치 단독 실행 — 사용자 계정 kis·live 자동매매 %d건 해제: %s", len(disabled), disabled)
    return disabled


async def system_status(db: AsyncSession) -> dict:
    """대시보드·상태 표시용(읽기 전용). 조회 실패는 '미실행' 으로 취급해 호출 화면을 막지 않는다."""
    try:
        row = await _row(db)
    except Exception as exc:  # 화면 보조 정보라 실패해도 본 응답은 살린다
        logger.warning("kis_batch.system_status 조회 실패: %s", exc)
        row = None
    # 시스템 사용자 행만 배치 행이다 (가짜 세션이 다른 사용자 행을 돌려줄 수 있으므로 user_id 도 확인)
    if row is not None and getattr(row, "user_id", SYSTEM_USER_ID) != SYSTEM_USER_ID:
        row = None
    return {
        "enabled": is_configured(),
        "running": bool(row and getattr(row, "quant_auto_enabled", False) and _is_batch_row(row)),
        "kill_switch": bool(row and getattr(row, "risk_kill_switch", False)),
        "kill_reason": (getattr(row, "risk_halt_reason", "") if row else "") or "",
        "symbol_source": (getattr(row, "quant_symbol_source", "ai") if row else ("manual" if configured_symbols() else "ai")),
        "user_id": str(SYSTEM_USER_ID),
    }


def _sync_env(row: BrokerSettings) -> list[str]:
    """배치 행의 투자금·종목 설정을 env 와 맞춘다. 바뀐 필드명을 돌려준다(빈 리스트면 변경 없음)."""
    changed: list[str] = []
    budget = float(settings.KIS_PAPER_BATCH_PER_TRADE_BUDGET)
    if float(row.quant_per_trade_budget or 0) != budget:
        row.quant_per_trade_budget = budget
        changed.append("quant_per_trade_budget")
    top_n = int(settings.KIS_PAPER_BATCH_AI_TOP_N)
    if int(row.quant_ai_top_n or 0) != top_n:
        row.quant_ai_top_n = top_n
        changed.append("quant_ai_top_n")
    symbols = configured_symbols()
    source = "manual" if symbols else "ai"
    if (row.quant_symbol_source or "") != source:
        row.quant_symbol_source = source
        changed.append("quant_symbol_source")
    if list(row.quant_selected_symbols or []) != symbols:
        row.quant_selected_symbols = symbols
        changed.append("quant_selected_symbols")
    return changed


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
    synced = [] if created else _sync_env(row)
    if synced:
        changed = True
        logger.info("KIS 모의투자 배치 설정을 env 에 맞춤: %s (1회 투자금 %.0f원, AI %d종목)",
                    synced, float(row.quant_per_trade_budget), int(row.quant_ai_top_n))
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
    disabled_users = await _enforce_exclusive(db)
    if started:
        await audit(BATCH_USER, "", "quant.kis_batch.start",
                    {"route": route.via, "environment": route.environment, "created": created,
                     "symbol_source": row.quant_symbol_source, "symbols": list(row.quant_selected_symbols or [])})
        logger.info("KIS 모의투자 배치 ON — route=%s env=%s created=%s", route.via, route.environment, created)
    return {"enabled": True, "running": True, "created": created, "started": started, "synced": synced,
            "route": route.via, "environment": route.environment, "symbol_source": row.quant_symbol_source,
            "exclusive_disabled_users": disabled_users}
