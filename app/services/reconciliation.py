"""정합성 점검 — 이 시스템의 로그(가상 QUANT 장부·live_orders·사이클 로그)와 실거래(KIS 계좌 잔고)가 맞는지 확인한다.

점검 항목(issue.type)
  kis_position_mismatch   : KIS 실제 보유수량 ≠ 기준선(baseline) + 봇 실주문 체결 순수량. 봇이 모르는 체결·취소·수동 거래가 있다는 뜻
  virtual_vs_live_mismatch: 가상 장부 수량 ≠ 봇 실주문 체결 순수량. 가상은 체결됐는데 실주문이 안 나갔거나(phantom) 실패한 경우
  phantom_trade           : 사이클 로그에 가상 체결(filled)은 있는데 실주문 결과가 없거나 skipped/error 인 거래
  stale_open_order        : ACCEPTED/PENDING/UNKNOWN 등 열린 실주문이 RECONCILE_OPEN_ORDER_MAX_MIN 을 넘김
  unresolved_order        : LOST/ERROR/UNKNOWN 상태 실주문(당일)
  slippage                : 가상 체결가 대비 실체결가 괴리가 RECONCILE_SLIPPAGE_ALERT_PCT 초과

기준선(baseline): Testbed 계좌에는 봇과 무관한 기존 보유가 있으므로, 첫 점검 시 KIS 보유를 기준선으로 저장하고
그 뒤로는 "기준선 + 봇 체결 순수량 = 실제 보유" 를 검사한다. 기준선은 data_cache `quant:reconcile:baseline:<uid>:<env>`.
수동 거래로 기준선을 다시 잡으려면 그 키를 지우면 된다(다음 점검에서 재수집).
결과는 `quant:reconcile:latest:<uid>` 에 저장되고 /api/quant/auto/status 의 `reconcile` 로 화면에 나온다.
불일치가 새로 생기거나 바뀌면 알림(dispatch) 1회. celery beat `quant.reconcile`(RECONCILE_INTERVAL_SEC).
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import LiveOrder, Portfolio, PORTFOLIO_BOOK_QUANT, LIVE_ORDER_OPEN_STATUSES
from app.models.base import SYSTEM_USER_ID
from app.services import notification
from app.services.brokers import stock_coin_trade_gateway as gateway
from app.services.data_cache import cache_get, cache_set

logger = logging.getLogger(__name__)

FILLED_STATUSES = ("FILLED", "PARTIALLY_FILLED")
UNRESOLVED_STATUSES = ("LOST", "ERROR", "UNKNOWN")


def _baseline_key(uid, env: str) -> str:
    return f"quant:reconcile:baseline:{uid}:{env}"


def latest_key(uid) -> str:
    return f"quant:reconcile:latest:{uid}"


def _code(symbol: str) -> str:
    return gateway.normalize_symbol(symbol)


async def kis_holdings() -> dict[str, dict]:
    """KIS 잔고 → {6자리코드: {quantity, avgPrice, name}}"""
    balance = await gateway.get_balance()
    out: dict[str, dict] = {}
    for h in balance.get("holdings") or []:
        code = _code(str(h.get("symbol") or ""))
        qty = int(h.get("quantity") or 0)
        if code and qty > 0:
            out[code] = {"quantity": qty, "avgPrice": h.get("avgPrice"), "name": h.get("name")}
    return out


def net_filled_by_symbol(rows: list) -> dict[str, int]:
    net: dict[str, int] = {}
    for r in rows:
        if r.status not in FILLED_STATUSES:
            continue
        qty = int(r.filled_quantity or 0)
        if qty <= 0:
            continue
        code = _code(r.symbol)
        net[code] = net.get(code, 0) + (qty if str(r.side).upper() == "BUY" else -qty)
    return net


def compare_positions(baseline: dict[str, int], net_live: dict[str, int], actual: dict[str, int]) -> list[dict]:
    issues = []
    for code in sorted(set(baseline) | set(net_live) | set(actual)):
        expected = int(baseline.get(code, 0)) + int(net_live.get(code, 0))
        got = int(actual.get(code, 0))
        if expected != got:
            issues.append({"type": "kis_position_mismatch", "symbol": code, "expected": expected, "actual": got,
                           "baseline": int(baseline.get(code, 0)), "bot_net_filled": int(net_live.get(code, 0)),
                           "detail": "KIS 실제 보유가 기준선+봇 체결과 다름 (봇이 모르는 체결·취소·수동 거래 가능)"})
    return issues


def compare_virtual(virtual: dict[str, int], net_live: dict[str, int]) -> list[dict]:
    issues = []
    for code in sorted(set(virtual) | set(net_live)):
        v, l = int(virtual.get(code, 0)), int(net_live.get(code, 0))
        if v != l:
            issues.append({"type": "virtual_vs_live_mismatch", "symbol": code, "virtual": v, "live_filled": l,
                           "detail": "가상 체결이 실주문보다 많음(실주문 미발행/실패)" if v > l else "실주문 체결이 가상 장부보다 많음"})
    return issues


def phantom_trades(cycles: list[dict], max_cycles: int = 24) -> list[dict]:
    out = []
    for c in cycles[-max_cycles:]:
        if (c.get("settings") or {}).get("mode") not in (None, "live"):
            continue
        for t in c.get("trades") or []:
            if t.get("type") != "auto" or t.get("status") != "filled":
                continue
            lo = t.get("live_order")
            st = (lo or {}).get("status")
            if lo is None or st in ("skipped", "error"):
                out.append({"type": "phantom_trade", "time": t.get("time") or c.get("time"), "symbol": t.get("symbol"),
                            "action": t.get("action"), "quantity": t.get("quantity"),
                            "live_status": st or "none", "live_reason": (lo or {}).get("reason") or (lo or {}).get("error") or ""})
    return out


def order_issues(rows: list, now: datetime, max_open_min: int, slip_alert_pct: float) -> list[dict]:
    issues = []
    for r in rows:
        created = r.created_at.astimezone(timezone.utc) if getattr(r, "created_at", None) else None
        age_min = (now - created).total_seconds() / 60 if created else None
        if r.status in LIVE_ORDER_OPEN_STATUSES and age_min is not None and age_min > max_open_min:
            issues.append({"type": "stale_open_order", "symbol": r.symbol, "side": r.side, "status": r.status,
                           "age_min": round(age_min), "client_order_id": r.client_order_id})
        if r.status in UNRESOLVED_STATUSES:
            issues.append({"type": "unresolved_order", "symbol": r.symbol, "side": r.side, "status": r.status,
                           "quantity": r.quantity, "client_order_id": r.client_order_id, "message": (r.message or "")[:120]})
        if r.status == "FILLED" and r.price and r.avg_filled_price:
            slip = (float(r.avg_filled_price) / float(r.price) - 1) * 100
            if abs(slip) > slip_alert_pct:
                issues.append({"type": "slippage", "symbol": r.symbol, "side": r.side, "virtual_price": r.price,
                               "filled_price": r.avg_filled_price, "slippage_pct": round(slip, 3)})
    return issues


def _signature(issues: list[dict]) -> str:
    key = sorted(json.dumps({k: v for k, v in i.items() if k not in ("age_min", "time")}, sort_keys=True, ensure_ascii=False) for i in issues)
    return hashlib.sha1("|".join(key).encode()).hexdigest()[:12]


async def run_reconciliation(db: AsyncSession, uid=SYSTEM_USER_ID) -> dict:
    now = datetime.now(timezone.utc)
    env = gateway.environment()
    report: dict = {"checked_at": now.isoformat(), "user_id": str(uid), "environment": env, "ok": None, "issues": [], "summary": {}}
    if not settings.RECONCILE_ENABLED:
        report["summary"]["note"] = "RECONCILE_ENABLED=false"
        return report
    if not gateway.is_configured():
        report["summary"]["note"] = "게이트웨이 미설정 — KIS 잔고 대조 불가"
        return report

    # 실거래: KIS 잔고
    try:
        actual = await kis_holdings()
    except gateway.GatewayError as e:
        report["summary"]["note"] = f"KIS 잔고 조회 실패: [{e.code}] {e}"
        await cache_set(latest_key(uid), report)
        return report
    actual_qty = {k: v["quantity"] for k, v in actual.items()}

    # 로그: 가상 장부 · 실주문 · 사이클
    virtual = {}
    for p in (await db.execute(select(Portfolio).where(Portfolio.user_id == uid, Portfolio.book == PORTFOLIO_BOOK_QUANT))).scalars().all():
        if p.quantity > 0:
            virtual[_code(p.symbol)] = int(p.quantity)
    day_start = (now.astimezone(timezone(timedelta(hours=9)))).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    # KIS Testbed 계좌는 배치와 로그인 사용자들이 **공용**으로 쓴다(KIS_PAPER_BATCH_EXCLUSIVE=false). 실제 보유와 대조할 때는
    # 같은 환경(env)의 모든 사용자 실주문 체결을 합산하고, 가상 장부·주문 상태 점검은 이 uid 의 주문만 본다.
    rows_account = list((await db.execute(select(LiveOrder).where(LiveOrder.environment == env).order_by(LiveOrder.created_at.desc()).limit(1000))).scalars().all())
    rows = [r for r in rows_account if getattr(r, "user_id", uid) == uid]
    rows_today = [r for r in rows if getattr(r, "created_at", None) and r.created_at.astimezone(timezone.utc) >= day_start]
    net_live = net_filled_by_symbol(rows)                 # 이 uid 의 봇 체결 순수량 (가상 장부 대조용)
    net_account = net_filled_by_symbol(rows_account)      # 계좌 전체 체결 순수량 (KIS 보유 대조용)
    cycles = ((await cache_get(f"quant:cycle_log:{uid}", max_age_hours=24 * 30)) or {}).get("cycles") or []

    # 기준선: 봇이 거래를 시작하기 전의 계좌 보유. 첫 점검이 봇 체결 뒤에 돌 수 있으므로 "현재 보유 − 봇 체결 순수량" 으로 잡는다.
    bkey = _baseline_key(uid, env)
    baseline = await cache_get(bkey, max_age_hours=24 * 365)
    if not baseline:
        pre_bot = {}
        for code in set(actual_qty) | set(net_account):
            q = int(actual_qty.get(code, 0)) - int(net_account.get(code, 0))
            if q > 0:
                pre_bot[code] = q
        baseline = {"captured_at": now.isoformat(), "holdings": pre_bot, "bot_net_filled_at_capture": net_account}
        await cache_set(bkey, baseline)
        report["summary"]["baseline"] = "지금 수집(첫 점검)"
    else:
        report["summary"]["baseline"] = baseline.get("captured_at")

    issues: list[dict] = []
    issues += compare_positions({k: int(v) for k, v in (baseline.get("holdings") or {}).items()}, net_account, actual_qty)
    issues += compare_virtual(virtual, net_live)
    issues += phantom_trades(cycles)
    issues += order_issues(rows_today, now, int(settings.RECONCILE_OPEN_ORDER_MAX_MIN), float(settings.RECONCILE_SLIPPAGE_ALERT_PCT))

    counts: dict[str, int] = {}
    for r in rows_today:
        counts[r.status] = counts.get(r.status, 0) + 1
    report["summary"].update({
        "kis_holdings": actual_qty, "virtual": virtual, "bot_net_filled": net_live, "account_net_filled": net_account,
        "account_users": len({str(getattr(r, "user_id", uid)) for r in rows_account}),
        "live_orders_today": counts, "cycles_checked": min(len(cycles), 24),
    })
    report["issues"] = issues
    report["ok"] = not issues
    report["issue_types"] = sorted({i["type"] for i in issues})
    sig = _signature(issues)
    report["signature"] = sig

    prev = await cache_get(latest_key(uid), max_age_hours=24 * 30) or {}
    await cache_set(latest_key(uid), report)
    if issues and settings.RECONCILE_NOTIFY and prev.get("signature") != sig:
        lines = [f"- {i['type']} {i.get('symbol', '')}: " + ", ".join(f"{k}={v}" for k, v in i.items() if k not in ("type", "symbol", "detail")) for i in issues[:8]]
        plain = f"[정합성] 로그와 실거래 불일치 {len(issues)}건 ({env})\n" + "\n".join(lines)
        html = f"⚠️ <b>[정합성] 로그와 실거래 불일치 {len(issues)}건</b> ({env})\n\n" + "\n".join(lines)
        try:
            await notification.dispatch(plain, html_message=html, subject="[정합성] 로그·실거래 불일치", user_id=str(uid))
        except Exception as exc:  # 알림 실패가 점검을 막으면 안 된다
            logger.warning("정합성 알림 실패: %s", exc)
    logger.info("정합성 점검 uid=%s env=%s ok=%s issues=%d", uid, env, report["ok"], len(issues))
    return report


async def latest(uid=SYSTEM_USER_ID) -> dict | None:
    return await cache_get(latest_key(uid), max_age_hours=24 * 30)
