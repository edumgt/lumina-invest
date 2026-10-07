"""통합 대시보드 「KIS 모의투자 시작」 원클릭.

버튼 한 번으로 종목 선정 화면의 설정(증권사 KIS · 실행 모드 live · AI 추천 종목 · Testbed 권장 한도)을
저장하고 자동매매를 켠다. 자격증명은 서버(Secrets Manager)가 관리하므로 사용자 입력이 없다.

안전장치
  - 실주문이 KIS **모의투자(Testbed)** 로만 나가는 경우에만 시작한다. 경로가 실전(real)이면 409.
  - 게이트웨이(stock-coin-trade)도, 서버 관리 KIS 자격증명도 없으면 409 (연동 안 됨).
  - 비상 정지(kill switch) 상태면 409.
Testbed 권장값은 todo.md 7절 L1·L22 (1회 50만 원 · 종목 비중 20% · 일손실 3%; 쿨다운·일 주문 수는 공격 모드가 env 값으로 덮어쓴다).
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import BrokerSettings
from app.services import aggressive_mode, auto_trade, kis_credentials
from app.services.audit import audit
from app.services.brokers import stock_coin_trade_gateway as gateway

logger = logging.getLogger(__name__)

TESTBED_DEFAULTS = {
    "quant_mode": "live",             # live 여야 가상 체결 외에 실제 KIS(Testbed) 주문이 나간다
    "paper": False,
    "broker": "kis",
    "quant_symbol_source": "ai",      # AI 추천 종목
    "quant_ai_top_n": 3,
    "quant_per_trade_budget": 500_000.0,   # 2026-10-07, 30만 → 50만
    "quant_buy_ratio": 1.0,
    "quant_sell_ratio": 0.5,
    "risk_daily_loss_limit_pct": 3.0,
    "risk_max_position_pct": 20.0,
    "risk_max_orders_per_day": 10,       # 공격 모드(QUANT_AGGRESSIVE_MODE)면 런타임에 QUANT_AGGRESSIVE_* 값으로 덮어써진다
    "risk_cooldown_min": 30,
}


def interval_min() -> int:
    """사이클 주기(분). celery-beat 스케줄(QUANT_CYCLE_SEC)과 같은 값."""
    return max(1, int(settings.QUANT_CYCLE_SEC) // 60)


def effective_defaults() -> dict:
    """화면에 보여 줄 실제 적용값. 공격 모드면 쿨다운·일 주문 수는 aggressive_mode.apply_limits 와 같은 env 값."""
    d = {k: v for k, v in TESTBED_DEFAULTS.items() if k not in ("paper",)}
    if aggressive_mode.is_enabled():
        d["risk_cooldown_min"] = max(0, int(settings.QUANT_AGGRESSIVE_COOLDOWN_MIN))
        d["risk_max_orders_per_day"] = max(0, int(settings.QUANT_AGGRESSIVE_MAX_ORDERS_PER_DAY))
    return d


class QuickstartBlocked(Exception):
    """시작할 수 없는 상태. message 는 사용자에게 그대로 보여 준다."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class Route:
    via: str            # "stock-coin-trade" | "kis-direct" | None
    environment: str    # "paper" | "real"
    configured: bool
    detail: str


async def resolve_route() -> Route:
    """live 주문이 어디로, 어느 환경으로 나가는지. auto_trade._place_live_order 와 같은 우선순위."""
    if gateway.is_configured():
        env = gateway.environment()
        return Route("stock-coin-trade", env, True, f"stock-coin-trade 게이트웨이 → KIS {'모의(Testbed)' if env == 'paper' else '실전'}")
    if kis_credentials.is_configured():
        creds = await kis_credentials.get_credentials()
        if creds is not None:
            return Route("kis-direct", creds.environment, True,
                         f"KIS 직접 호출 ({creds.source}) → {'모의(Testbed)' if creds.paper else '실전'}")
        st = kis_credentials.status(None)
        return Route(None, "paper", False, f"KIS 자격증명 조회 실패: {st.get('error') or '미연동'}")
    return Route(None, "paper", False, "stock-coin-trade 게이트웨이와 KIS 자격증명(Secrets Manager)이 모두 설정되지 않았습니다")


async def _row(db: AsyncSession, uid: uuid.UUID) -> BrokerSettings | None:
    return (await db.execute(select(BrokerSettings).where(BrokerSettings.user_id == uid))).scalar_one_or_none()


BATCH_EXCLUSIVE_MESSAGE = ("KIS 모의투자 배치가 단독 실행 모드(KIS_PAPER_BATCH_EXCLUSIVE=true)로 돌고 있어 사용자 계정의 KIS 자동매매는 "
                           "다음 사이클에 자동으로 꺼집니다. 배치 결과는 「KIS 모의투자결과」 화면에서 보고, 직접 돌리려면 서버 설정에서 단독 실행을 끄세요.")


async def _batch_exclusive_running(db: AsyncSession) -> bool:
    """배치가 단독 실행 모드로 돌고 있으면 사용자 kis·live 세션은 다음 사이클에 꺼지므로 시작 자체를 막는다."""
    if not settings.KIS_PAPER_BATCH_EXCLUSIVE:
        return False
    from app.services import kis_batch  # 지연 import (순환 방지)
    try:
        return bool((await kis_batch.system_status(db)).get("running"))
    except Exception:
        return False


async def readiness(db: AsyncSession, uid: uuid.UUID) -> dict:
    """대시보드 패널 표시용: 시작 가능 여부와 현재 상태."""
    route = await resolve_route()
    row = await _row(db, uid)
    running = bool(row and row.quant_auto_enabled)
    kill = bool(row and row.risk_kill_switch)
    mode = row.quant_mode if row and row.quant_mode in ("paper", "live") else "paper"
    broker = (row.broker if row and row.broker else "mock")
    if not route.configured:
        ready, reason = False, "not_connected"
    elif route.environment != "paper":
        ready, reason = False, "real_environment"
    elif kill:
        ready, reason = False, "kill_switch"
    else:
        ready, reason = True, ""
    already = running and mode == "live" and broker == "kis"
    from app.services import kis_batch  # 지연 import (순환 방지)
    system_batch = await kis_batch.system_status(db)
    if ready and settings.KIS_PAPER_BATCH_EXCLUSIVE and system_batch.get("running"):
        ready, reason = False, "batch_exclusive"
    return {
        "system_batch": system_batch,   # 계정·로그인 무관 백그라운드 배치(KIS_PAPER_BATCH_ENABLED) 상태
        "ready": ready, "reason": reason, "route": route.via, "environment": route.environment,
        "route_detail": route.detail, "connected": route.configured,
        "running": running, "already_started": already, "mode": mode, "broker": broker,
        "kill_switch": kill, "kill_reason": (row.risk_halt_reason if row else "") or "",
        "defaults": effective_defaults(),
        "interval_min": interval_min(),
        "aggressive": aggressive_mode.is_enabled(),
    }


async def start(db: AsyncSession, user_id: str) -> dict:
    uid = uuid.UUID(user_id)
    route = await resolve_route()
    if not route.configured:
        raise QuickstartBlocked("not_connected", route.detail)
    if route.environment != "paper":
        raise QuickstartBlocked("real_environment", "현재 KIS 경로가 실전(real)으로 설정되어 있어 원클릭 모의투자를 시작하지 않습니다. 종목 선정 화면에서 직접 설정하세요.")

    if await _batch_exclusive_running(db):
        raise QuickstartBlocked("batch_exclusive", BATCH_EXCLUSIVE_MESSAGE)

    row = await _row(db, uid)
    if row is None:
        row = BrokerSettings(user_id=uid)
        db.add(row)
    if row.risk_kill_switch:
        raise QuickstartBlocked("kill_switch", f"비상 정지 상태입니다. 해제 후 시작하세요. (사유: {row.risk_halt_reason or '수동 정지'})")

    for key, value in TESTBED_DEFAULTS.items():
        setattr(row, key, value)
    row.quant_selected_symbols = []
    row.app_key = row.app_secret = row.account_no = ""   # KIS 자격증명은 서버 관리 — DB 에 두지 않는다
    await db.commit()

    started = await auto_trade.start_auto_trade(db, user_id)
    eff = effective_defaults()
    await audit(user_id, "", "quant.kis_quickstart", {"route": route.via, "environment": route.environment, "started": started})
    logger.info("KIS 모의투자 원클릭 시작 user=%s route=%s started=%s", user_id, route.via, started)
    return {
        "ok": True, "started": started, "already_running": not started,
        "route": route.via, "environment": route.environment, "route_detail": route.detail,
        "settings": {
            "mode": "live", "broker": "kis", "symbol_source": "ai", "ai_top_n": TESTBED_DEFAULTS["quant_ai_top_n"],
            "per_trade_budget": TESTBED_DEFAULTS["quant_per_trade_budget"],
            "risk": {"daily_loss_limit_pct": eff["risk_daily_loss_limit_pct"], "max_position_pct": eff["risk_max_position_pct"],
                     "max_orders_per_day": eff["risk_max_orders_per_day"], "cooldown_min": eff["risk_cooldown_min"]},
        },
        "interval_min": interval_min(),
    }
