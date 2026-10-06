"""통합 대시보드 — 투자 사이트별 현재 투자액 탭.

각 탭은 독립적으로 집계하며 하나가 실패해도 나머지는 돌려준다(connected=False + error).
탭 순서는 프런트가 그대로 그린다: KIS 모의투자가 가장 왼쪽.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.postgres import get_pg_session
from app.lib.session import get_current_user
from app.models.base import SYSTEM_USER_ID
from app.models import BrokerSettings, LiveOrder, Portfolio, QuantVirtualAccount, PORTFOLIO_BOOK_QUANT, LIVE_ORDER_OPEN_STATUSES
from app.services import kis_credentials, paper_trading
from app.services.brokers import stock_coin_trade_gateway as gateway
from app.services.brokers.factory import get_broker_client
from app.services.stock import get_quote

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

TAB_ORDER = ("kis", "quant", "paper", "us")


def _tab(key: str, label: str, site: str, **fields) -> dict:
    base = {"key": key, "label": label, "site": site, "connected": False, "invested": None, "cash": None,
            "total": None, "pnl": None, "pnl_pct": None, "positions": None, "currency": "KRW", "note": "", "error": "", "link": ""}
    base.update(fields)
    return base


def _f(v, default=0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


async def kis_tab(db: AsyncSession, uid: uuid.UUID) -> dict:
    """KIS 모의투자(Testbed) 계좌 — 게이트웨이(stock-coin-trade) 우선, 없으면 서버 관리 자격증명으로 직접 조회."""
    tab = _tab("kis", "KIS 모의투자", "한국투자증권 Testbed", link="#settings")
    row = (await db.execute(select(BrokerSettings).where(BrokerSettings.user_id == uid))).scalar_one_or_none()
    from app.services import kis_batch
    batch = await kis_batch.system_status(db)
    owners = [uid, SYSTEM_USER_ID] if batch.get("running") else [uid]
    open_orders = (await db.execute(select(LiveOrder).where(LiveOrder.user_id.in_(owners), LiveOrder.status.in_(LIVE_ORDER_OPEN_STATUSES)))).scalars().all()
    me_running = bool(row and row.quant_auto_enabled and row.quant_mode == "live" and (row.broker or "") == "kis")
    tab["auto_trade_running"] = me_running or bool(batch.get("running"))
    tab["batch_running"] = bool(batch.get("running"))
    tab["me_running"] = me_running
    tab["open_live_orders"] = len(open_orders)
    if gateway.is_configured():
        tab["route"] = "stock-coin-trade"
        tab["environment"] = gateway.environment()
        bal = await gateway.get_balance()
        holdings = bal.get("holdings") or []
        cash = _f(bal.get("cashBalance"))
        invested = sum(_f(h.get("evalAmount")) for h in holdings) if holdings else max(0.0, _f(bal.get("totalEvalAmount")) - cash)
        tab.update(connected=True, cash=cash, invested=invested, total=_f(bal.get("totalEvalAmount"), cash + invested),
                   pnl=_f(bal.get("totalProfitLoss")), positions=len(holdings),
                   note=f"게이트웨이 경유 · {'모의(Testbed)' if tab['environment'] == 'paper' else '실전'}")
        return tab
    creds = await kis_credentials.get_credentials() if kis_credentials.is_configured() else None
    if creds is None:
        tab.update(note="KIS 연동 없음 — 게이트웨이(STOCK_COIN_TRADE_*) 또는 Secrets Manager(KIS_SECRETS_NAME) 설정 필요")
        return tab
    tab["route"] = "kis-direct"
    tab["environment"] = creds.environment
    client = get_broker_client("kis", creds.app_key, creds.app_secret, paper=creds.paper)
    bal = await client.get_balance(creds.account_no)
    holdings = list(getattr(bal, "holdings", []) or [])
    total = _f(getattr(bal, "total_eval", None))
    invested = sum(_f(getattr(h, "eval_amount", None) or getattr(h, "quantity", 0) * getattr(h, "current_price", 0)) for h in holdings)
    tab.update(connected=True, total=total, invested=invested, cash=max(0.0, total - invested), pnl=_f(getattr(bal, "total_gain", None)),
               positions=len(holdings), note=f"직접 조회({creds.source}) · {'모의(Testbed)' if creds.paper else '실전'}")
    return tab


async def quant_tab(db: AsyncSession, uid: uuid.UUID) -> dict:
    """자동매매 가상 장부(QUANT book) — 자동매매가 사고판 수량만 담긴다."""
    tab = _tab("quant", "퀀트 가상계좌", "lumina 자동매매 장부", link="#quant-auto")
    acc = (await db.execute(select(QuantVirtualAccount).where(QuantVirtualAccount.user_id == uid))).scalar_one_or_none()
    rows = (await db.execute(select(Portfolio).where(Portfolio.user_id == uid, Portfolio.book == PORTFOLIO_BOOK_QUANT))).scalars().all()
    cash = _f(acc.cash_balance) if acc else 0.0
    initial = _f(acc.initial_capital) if acc else 0.0
    invested = 0.0
    positions = 0
    for p in rows:
        if p.quantity <= 0:
            continue
        positions += 1
        px = None
        try:
            px = (await get_quote(p.symbol)).get("price")
        except Exception:  # noqa: BLE001 — 시세 실패는 평균단가로 대체
            px = None
        invested += p.quantity * _f(px, _f(p.avg_price))
    total = cash + invested
    tab.update(connected=acc is not None, cash=cash, invested=invested, total=total, positions=positions,
               pnl=(total - initial) if initial else None, pnl_pct=round((total / initial - 1) * 100, 2) if initial else None,
               note="가상 체결 장부 · 초기 자본 " + (f"{initial:,.0f}원" if initial else "미생성"))
    return tab


async def paper_tab(db: AsyncSession, uid: uuid.UUID) -> dict:
    """모의투자 계좌(국내주식·코인·대체자산, stock-coin-trade 이식)."""
    tab = _tab("paper", "모의투자 계좌", "lumina 모의투자 (주식·코인·대체)", link="#paper-account")
    snap = await paper_trading.account_snapshot(db, uid)
    invested = _f(snap.get("stockEval")) + _f(snap.get("cryptoEval")) + _f(snap.get("alternativeEval"))
    counts = snap.get("counts") or {}
    tab.update(connected=True, cash=_f(snap.get("cash")), invested=invested, total=_f(snap.get("totalAsset")),
               pnl=_f(snap.get("totalPnl")), pnl_pct=_f(snap.get("totalPnlRate")),
               positions=sum(int(v or 0) for v in counts.values()),
               breakdown={"stocks": _f(snap.get("stockEval")), "crypto": _f(snap.get("cryptoEval")), "alternatives": _f(snap.get("alternativeEval"))},
               note="국내주식 · 코인(Upbit) · 대체자산 가상 체결")
    return tab


async def us_tab(db: AsyncSession, uid: uuid.UUID) -> dict:
    """미국주식 — Alpaca Paper Trading (서버 키가 있을 때만)."""
    tab = _tab("us", "미국주식", "Alpaca Paper Trading", currency="USD", link="#us-dashboard")
    if not (settings.ALPACA_API_KEY and settings.ALPACA_SECRET_KEY):
        tab.update(note="ALPACA_API_KEY / ALPACA_SECRET_KEY 미설정")
        return tab
    from app.routes.paper import _alpaca_get, _alpaca_headers  # 순환 import 방지용 지연 import
    acct = await _alpaca_get("/account", _alpaca_headers(None))
    cash = _f(acct.get("cash"))
    invested = _f(acct.get("long_market_value")) + abs(_f(acct.get("short_market_value")))
    equity = _f(acct.get("equity"), cash + invested)
    last_equity = _f(acct.get("last_equity"))
    tab.update(connected=True, cash=cash, invested=invested, total=equity, positions=None,
               pnl=(equity - last_equity) if last_equity else None, pnl_pct=round((equity / last_equity - 1) * 100, 2) if last_equity else None,
               note=f"상태 {acct.get('status', '-')} · 매수여력 {_f(acct.get('buying_power')):,.0f} USD")
    return tab


_BUILDERS = {"kis": kis_tab, "quant": quant_tab, "paper": paper_tab, "us": us_tab}


async def _safe(key: str, db: AsyncSession, uid: uuid.UUID) -> dict:
    try:
        return await _BUILDERS[key](db, uid)
    except Exception as e:  # noqa: BLE001 — 탭 하나의 실패가 전체를 막지 않는다
        logger.warning("dashboard accounts tab %s 실패: %s", key, e)
        label = {"kis": ("KIS 모의투자", "한국투자증권 Testbed"), "quant": ("퀀트 가상계좌", "lumina 자동매매 장부"),
                 "paper": ("모의투자 계좌", "lumina 모의투자"), "us": ("미국주식", "Alpaca Paper Trading")}[key]
        return _tab(key, *label, error=f"{type(e).__name__}: {str(e)[:160]}")


@router.get("/accounts")
async def accounts(user=Depends(get_current_user), db: AsyncSession = Depends(get_pg_session)):
    """투자 사이트별 현재 투자액. 같은 DB 세션을 공유하므로 순차 실행한다(탭 수가 적어 충분히 빠름)."""
    uid = uuid.UUID(user["id"])
    tabs = [await _safe(key, db, uid) for key in TAB_ORDER]
    return {"tabs": tabs, "order": list(TAB_ORDER), "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
