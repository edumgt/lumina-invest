"""리밸런싱 엔진 — 모의투자 계좌(현금 + 주식 포지션)를 목표 비중으로 되돌린다.

계좌 소스(account_source, 사용자별, data_cache `rebalance:source:<uid>`)
  paper : lumina 내부 모의계좌(paper_trading) — 기본
  kis   : **KIS 모의투자(Testbed) 계좌** — 현금·보유는 stock-coin-trade 게이트웨이 잔고, 시세는 KIS(stock.get_quote),
          체결은 게이트웨이 실주문(live_orders 추적, quant.confirm_fills 가 체결 확정). 2026-10-06 추가.
          시장가/지정가는 auto_trade._live_order_type()(공격 모드면 MARKET). 장외에는 주문이 skipped 로 남는다.

트리거
  TIME     : plan.time_period(monthly/quarterly/yearly) 주기의 next_run_at 도래
  DRIFT    : |현재 비중 − 목표 비중| 최대값 ≥ plan.drift_threshold_pct (%p)
  CASHFLOW : 입금·출금·배당 이벤트 금액 ≥ plan.cashflow_min_amount
  MANUAL   : 사용자가 화면에서 직접 실행

흐름
  snapshot()  → 현재 비중·이탈률 계산
  propose()   → 목표 비중과의 차액을 주문(매도 먼저, 매수 나중)으로 변환
  execute()   → paper_trading.stock_order 로 모의 체결하고 RebalanceRun 기록
  check_due() → TIME/DRIFT 트리거 점검(스케줄러·API 공용)
"""
from __future__ import annotations

import calendar
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CashflowEvent, RebalancePlan, RebalanceRun
from app.models.rebalance import CASHFLOW_KINDS, TIME_PERIODS
from app.services import paper_trading as pt
from app.services.data_cache import cache_get, cache_set

logger = logging.getLogger(__name__)

ORDER_SOURCE = "REBALANCE"
ACCOUNT_SOURCES = ("paper", "kis")
SECTOR_PREFIX = "SECTOR:"          # plan.targets 항목 symbol 이 "SECTOR:반도체" 면 섹터 목표
OTHER_SECTOR = "기타"              # 유니버스 밖 보유 종목
SECTOR_FILL_CANDIDATES = 2        # 섹터 목표가 있는데 보유 종목이 없으면 유니버스에서 채울 종목 수


# ── 섹터 ────────────────────────────────────────────────────────────────


def is_sector_target(symbol: str) -> bool:
    return str(symbol or "").upper().startswith(SECTOR_PREFIX)


def sector_name(symbol: str) -> str:
    return str(symbol)[len(SECTOR_PREFIX):] if is_sector_target(symbol) else ""


def sector_of_symbol(symbol: str) -> str:
    """lumina 심볼/6자리 코드 → 유니버스 섹터(반도체·IT·K뷰티), 없으면 '기타'."""
    from app.services.stock import QUANT_STOCKS
    code = str(symbol)[:6]
    for s in QUANT_STOCKS:
        if s["symbol"][:6] == code:
            return s["sector"]
    return OTHER_SECTOR


def sector_universe(sector: str) -> list[dict]:
    from app.services.stock import QUANT_STOCKS
    return [s for s in QUANT_STOCKS if s["sector"] == sector]


def split_targets(targets: list[dict]) -> tuple[dict[str, float], dict[str, dict]]:
    """plan.targets → (섹터 목표 {섹터: %}, 종목 목표 {심볼: {name, weight_pct}})"""
    sectors: dict[str, float] = {}
    symbols: dict[str, dict] = {}
    for t in targets or []:
        sym = str(t.get("symbol") or "")
        w = float(t.get("weight_pct") or 0)
        if is_sector_target(sym):
            sectors[sector_name(sym)] = sectors.get(sector_name(sym), 0.0) + w
        elif sym:
            symbols[sym] = {"name": t.get("name") or sym, "weight_pct": w}
    return sectors, symbols


def effective_symbol_targets(targets: list[dict], held_symbols: list[str], rank_fn=None) -> dict[str, dict]:
    """섹터 목표를 종목 목표로 내린다.

    섹터 S 의 목표 W: 명시된 종목 목표(S 소속) 합 E 를 빼고 남은 R=W−E 를 **S 소속 보유 종목 중 명시되지 않은 것** 에 균등 분배.
    보유가 없으면 유니버스에서 SECTOR_FILL_CANDIDATES 개(rank_fn 순, 기본 유니버스 순서)를 골라 분배.
    반환 {symbol: {name, weight_pct, sector, derived(bool)}}. 섹터 목표가 없는 종목 목표는 그대로(섹터는 소속 섹터).
    """
    sectors, explicit = split_targets(targets)
    out: dict[str, dict] = {}
    for sym, t in explicit.items():
        out[sym] = {"name": t["name"], "weight_pct": round(float(t["weight_pct"]), 4), "sector": sector_of_symbol(sym), "derived": False}
    for sector, W in sectors.items():
        E = sum(v["weight_pct"] for v in out.values() if v["sector"] == sector)
        R = max(0.0, W - E)
        if R <= 0:
            continue
        members = [s for s in held_symbols if sector_of_symbol(s) == sector and s not in out]
        if not members:
            cands = sector_universe(sector)
            if rank_fn:
                cands = rank_fn(cands)
            members = [c["symbol"] for c in cands[:SECTOR_FILL_CANDIDATES] if c["symbol"] not in out]
        if not members:
            continue
        each = R / len(members)
        for m in members:
            uni = next((u for u in sector_universe(sector) if u["symbol"] == m), None)
            out[m] = {"name": (uni or {}).get("name") or m, "weight_pct": round(each, 4), "sector": sector, "derived": True}
    return out


# ── 계좌 소스 ────────────────────────────────────────────────────────────


def _source_key(user_id: uuid.UUID) -> str:
    return f"rebalance:source:{user_id}"


async def get_account_source(user_id: uuid.UUID) -> str:
    v = await cache_get(_source_key(user_id), max_age_hours=24 * 3650) or {}
    if not v.get("source"):
        # 미설정이면 KIS 게이트웨이가 있을 때 KIS 모의투자 계좌를 기본으로 (2026-10-06 요구: KIS 종목·섹터 리밸런싱)
        from app.services.brokers import stock_coin_trade_gateway as gateway
        return "kis" if gateway.is_configured() else "paper"
    src = str(v.get("source") or "paper").lower()
    return src if src in ACCOUNT_SOURCES else "paper"


async def set_account_source(user_id: uuid.UUID, source: str) -> str:
    src = str(source or "paper").lower()
    if src not in ACCOUNT_SOURCES:
        raise RebalanceError(f"지원하지 않는 계좌 소스입니다: {source} (paper | kis)")
    if src == "kis":
        from app.services.brokers import stock_coin_trade_gateway as gateway
        if not gateway.is_configured():
            raise RebalanceError("KIS 모의투자 계좌를 쓰려면 stock-coin-trade 게이트웨이(STOCK_COIN_TRADE_*)가 설정되어 있어야 합니다.")
    await cache_set(_source_key(user_id), {"source": src, "updated_at": datetime.now(timezone.utc).isoformat()})
    return src


def _kis_symbol(code: str, name: str | None = None) -> str:
    """KIS 6자리 코드 → lumina 표기. 유니버스에 있으면 그 심볼(.KS/.KQ), 없으면 .KS 로 둔다(게이트웨이는 접미사를 떼고 보낸다)."""
    from app.services.stock import QUANT_STOCKS
    for s in QUANT_STOCKS:
        if s["symbol"][:6] == code:
            return s["symbol"]
    return f"{code}.KS"


async def _kis_account_inputs() -> tuple[float, list[dict]]:
    """KIS Testbed 잔고 → (현금, paper_trading.stock_positions 와 같은 모양의 포지션 목록)."""
    from app.services.brokers import stock_coin_trade_gateway as gateway
    if not gateway.is_configured():
        raise RebalanceError("stock-coin-trade 게이트웨이가 설정되지 않아 KIS 계좌를 읽을 수 없습니다.")
    try:
        bal = await gateway.get_balance()
    except gateway.GatewayError as exc:
        raise RebalanceError(f"KIS 잔고 조회 실패: [{exc.code}] {exc}") from exc
    positions = []
    for h in bal.get("holdings") or []:
        code = gateway.normalize_symbol(str(h.get("symbol") or ""))
        qty = int(h.get("quantity") or 0)
        if not code or qty <= 0:
            continue
        price = float(h.get("currentPrice") or 0) or None
        eval_amt = float(h.get("evalAmount") or 0) or (qty * price if price else 0.0)
        positions.append({"symbol": _kis_symbol(code, h.get("name")), "name": h.get("name") or code, "quantity": qty,
                          "avgPrice": float(h.get("avgPrice") or 0), "currentPrice": price if price else (eval_amt / qty if qty else None),
                          "evalAmount": eval_amt})
    return float(bal.get("cashBalance") or 0), positions


async def _kis_price(symbol: str) -> float:
    from app.services.stock import get_quote
    q = await get_quote(symbol)
    price = float(q.get("price") or 0)
    if price <= 0:
        raise RebalanceError(f"KIS 시세 없음: {symbol}")
    return price


class RebalanceError(Exception):
    pass


# ── 플랜 ────────────────────────────────────────────────────────────────


async def get_plan(db: AsyncSession, user_id: uuid.UUID, create: bool = True) -> RebalancePlan | None:
    row = (await db.execute(select(RebalancePlan).where(RebalancePlan.user_id == user_id))).scalar_one_or_none()
    if row is None and create:
        row = RebalancePlan(user_id=user_id, targets=[])
        db.add(row)
        await db.flush()
    return row


def normalize_targets(raw: list[dict]) -> list[dict]:
    """[{symbol, name?, weight_pct}] 검증. 합계 100 초과 금지, 중복 심볼 병합."""
    merged: dict[str, dict] = {}
    for t in raw or []:
        raw_sym = str(t.get("symbol", "")).strip()
        if is_sector_target(raw_sym):
            from app.services.stock import QUANT_SECTORS
            name = raw_sym[len(SECTOR_PREFIX):].strip()
            if name not in QUANT_SECTORS:
                raise RebalanceError(f"지원하지 않는 섹터입니다: {name} ({' / '.join(QUANT_SECTORS)})")
            symbol = f"{SECTOR_PREFIX}{name}"
        else:
            symbol = pt.normalize_stock_symbol(raw_sym)
        if not symbol:
            continue
        try:
            w = float(t.get("weight_pct", 0))
        except (TypeError, ValueError):
            raise RebalanceError(f"{symbol}: 비중은 숫자여야 합니다.")
        if w < 0 or w > 100:
            raise RebalanceError(f"{symbol}: 비중은 0~100 사이여야 합니다.")
        if symbol in merged:
            merged[symbol]["weight_pct"] += w
        else:
            merged[symbol] = {"symbol": symbol, "name": str(t.get("name") or (sector_name(symbol) if is_sector_target(symbol) else symbol))[:100], "weight_pct": w}
    items = [{**t, "weight_pct": round(t["weight_pct"], 2)} for t in merged.values()]
    sectors, symbols = split_targets(items)
    # 섹터 목표가 있는 섹터의 명시 종목 합은 그 섹터 목표를 넘을 수 없다
    for sector, W in sectors.items():
        E = sum(v["weight_pct"] for s, v in symbols.items() if sector_of_symbol(s) == sector)
        if E > W + 0.0001:
            raise RebalanceError(f"{sector} 섹터의 종목 비중 합({E:.1f}%)이 섹터 목표({W:.1f}%)를 초과합니다.")
    # 총합 = 섹터 목표 + (섹터 목표 없는 섹터의 종목 목표)
    total = sum(sectors.values()) + sum(v["weight_pct"] for s, v in symbols.items() if sector_of_symbol(s) not in sectors)
    if total > 100.0001:
        raise RebalanceError(f"목표 비중 합계가 100%를 초과합니다 ({total:.1f}%). 잔여분은 현금으로 배분됩니다.")
    return items


async def resolve_targets(raw: list[dict]) -> list[dict]:
    """입력 심볼을 모의투자 표준 심볼(005930 → 005930.KS)과 종목명으로 확정한다."""
    resolved = []
    for t in raw or []:
        sym = str(t.get("symbol", "")).strip()
        if not sym:
            continue
        if is_sector_target(sym):
            resolved.append({"symbol": sym, "name": sector_name(sym), "weight_pct": t.get("weight_pct", 0)})
            continue
        try:
            info = await pt.resolve_stock(sym)
        except pt.PaperTradeError as exc:
            raise RebalanceError(str(exc))
        resolved.append({"symbol": info["symbol"], "name": info["name"], "weight_pct": t.get("weight_pct", 0)})
    return normalize_targets(resolved)


def _add_months(dt: datetime, months: int) -> datetime:
    month0 = dt.month - 1 + months
    year = dt.year + month0 // 12
    month = month0 % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def next_period_start(now: datetime, period: str) -> datetime | None:
    """다음 주기 시작 시각(월초/분기초/연초 00:00 UTC)."""
    if period not in TIME_PERIODS or period == "none":
        return None
    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if period == "monthly":
        return _add_months(first, 1)
    if period == "quarterly":
        q_start_month = ((now.month - 1) // 3) * 3 + 1
        return _add_months(first.replace(month=q_start_month), 3)
    return first.replace(month=1, year=now.year + 1)


def apply_plan_update(plan: RebalancePlan, data: dict) -> RebalancePlan:
    if "name" in data and data["name"]:
        plan.name = str(data["name"])[:60]
    if "is_active" in data:
        plan.is_active = bool(data["is_active"])
    if "targets" in data:
        plan.targets = normalize_targets(data["targets"])
    if "time_period" in data:
        period = str(data["time_period"] or "none")
        if period not in TIME_PERIODS:
            raise RebalanceError(f"지원하지 않는 주기입니다: {period}")
        if period != plan.time_period or plan.next_run_at is None:
            plan.next_run_at = next_period_start(datetime.now(timezone.utc), period)
        plan.time_period = period
    if "drift_enabled" in data:
        plan.drift_enabled = bool(data["drift_enabled"])
    if "drift_threshold_pct" in data:
        v = float(data["drift_threshold_pct"])
        if not 0.5 <= v <= 50:
            raise RebalanceError("허용 이탈률은 0.5~50%p 사이여야 합니다.")
        plan.drift_threshold_pct = v
    if "cashflow_enabled" in data:
        plan.cashflow_enabled = bool(data["cashflow_enabled"])
    if "cashflow_min_amount" in data:
        plan.cashflow_min_amount = max(0.0, float(data["cashflow_min_amount"]))
    if "auto_execute" in data:
        plan.auto_execute = bool(data["auto_execute"])
    if "min_order_amount" in data:
        plan.min_order_amount = max(0.0, float(data["min_order_amount"]))
    return plan


def plan_to_dict(plan: RebalancePlan) -> dict:
    sectors, symbols = split_targets(plan.targets or [])
    stock_total = sum(sectors.values()) + sum(v["weight_pct"] for s, v in symbols.items() if sector_of_symbol(s) not in sectors)
    return {
        "id": str(plan.id), "name": plan.name, "is_active": plan.is_active,
        "targets": plan.targets or [], "cash_weight_pct": round(100 - stock_total, 2),
        "sector_targets": {k: round(v, 2) for k, v in sectors.items()},
        "symbol_targets": [{"symbol": s, **v} for s, v in symbols.items()],
        "time_period": plan.time_period,
        "next_run_at": plan.next_run_at.isoformat() if plan.next_run_at else None,
        "drift_enabled": plan.drift_enabled, "drift_threshold_pct": plan.drift_threshold_pct,
        "cashflow_enabled": plan.cashflow_enabled, "cashflow_min_amount": plan.cashflow_min_amount,
        "auto_execute": plan.auto_execute, "min_order_amount": plan.min_order_amount,
        "last_run_at": plan.last_run_at.isoformat() if plan.last_run_at else None,
        "updated_at": plan.updated_at.isoformat() if plan.updated_at else None,
    }


# ── 스냅샷(현재 비중·이탈률) ──────────────────────────────────────────────


async def snapshot(db: AsyncSession, user_id: uuid.UUID, plan: RebalancePlan, source: str | None = None) -> dict:
    """현금 + 주식 포지션 기준 현재 비중과 목표 대비 이탈률. source=kis 면 KIS Testbed 계좌 기준."""
    source = source or await get_account_source(user_id)
    if source == "kis":
        cash_value, positions = await _kis_account_inputs()
    else:
        account = await pt.get_account(db, user_id)
        cash_value = float(account.cash)
        positions = await pt.stock_positions(db, user_id)
    pos_map = {p["symbol"]: p for p in positions}
    stock_eval = sum(p["evalAmount"] for p in positions)
    total = cash_value + stock_eval

    # 섹터 목표 → 종목 목표 (보유 종목 균등 / 미보유 섹터는 유니버스 후보)
    eff = effective_symbol_targets(plan.targets or [], list(pos_map.keys()))
    target_map = {s: float(v["weight_pct"]) for s, v in eff.items()}
    sector_targets, _ = split_targets(plan.targets or [])
    symbols = sorted(set(target_map) | set(pos_map))

    rows = []
    max_drift = 0.0
    for sym in symbols:
        p = pos_map.get(sym)
        cur_amt = p["evalAmount"] if p else 0.0
        cur_w = (cur_amt / total * 100) if total > 0 else 0.0
        tgt_w = target_map.get(sym, 0.0)
        drift = cur_w - tgt_w
        max_drift = max(max_drift, abs(drift))
        rows.append({
            "symbol": sym,
            "name": (p["name"] if p else (eff.get(sym) or {}).get("name") or sym),
            "sector": sector_of_symbol(sym),
            "quantity": p["quantity"] if p else 0,
            "price": p["currentPrice"] if p else None,
            "current_amount": round(cur_amt, 2),
            "current_weight_pct": round(cur_w, 2),
            "target_weight_pct": round(tgt_w, 2),
            "target_derived": bool((eff.get(sym) or {}).get("derived")),
            "drift_pct": round(drift, 2),
            "in_plan": sym in target_map,
        })
    cash_w = (cash_value / total * 100) if total > 0 else 100.0
    cash_target = 100 - sum(target_map.values())
    cash_drift = cash_w - cash_target
    max_drift = max(max_drift, abs(cash_drift))

    # 섹터 집계: 현재/목표/이탈 (섹터 목표가 없는 섹터의 목표 = 소속 종목 목표 합)
    from app.services.stock import QUANT_SECTORS
    sector_rows = []
    for sector in list(QUANT_SECTORS) + [OTHER_SECTOR]:
        members = [r for r in rows if r["sector"] == sector]
        if not members and sector not in sector_targets:
            continue
        cur_amt = sum(r["current_amount"] for r in members)
        cur_w = (cur_amt / total * 100) if total > 0 else 0.0
        tgt_w = sector_targets.get(sector, sum(r["target_weight_pct"] for r in members))
        drift = cur_w - tgt_w
        max_drift = max(max_drift, abs(drift))
        sector_rows.append({"sector": sector, "current_amount": round(cur_amt, 2), "current_weight_pct": round(cur_w, 2),
                            "target_weight_pct": round(tgt_w, 2), "drift_pct": round(drift, 2),
                            "symbols": [r["symbol"] for r in members], "explicit": sector in sector_targets})

    return {
        "account_source": source,
        "sectors": sector_rows,
        "total_asset": round(total, 2),
        "cash": round(cash_value, 2),
        "cash_weight_pct": round(cash_w, 2),
        "cash_target_pct": round(cash_target, 2),
        "cash_drift_pct": round(cash_drift, 2),
        "stock_eval": round(stock_eval, 2),
        "rows": rows,
        "max_drift_pct": round(max_drift, 2),
        "drift_exceeded": bool(plan.drift_enabled and max_drift >= plan.drift_threshold_pct and target_map),
    }


def _weights_from_snapshot(snap: dict) -> dict:
    w = {r["symbol"]: r["current_weight_pct"] for r in snap["rows"]}
    w["CASH"] = snap["cash_weight_pct"]
    for sr in snap.get("sectors") or []:
        w[f"{SECTOR_PREFIX}{sr['sector']}"] = sr["current_weight_pct"]
    return w


# ── 주문 산출 ────────────────────────────────────────────────────────────


async def propose(db: AsyncSession, user_id: uuid.UUID, plan: RebalancePlan, snap: dict | None = None,
                  source: str | None = None) -> dict:
    """목표 비중과의 차액을 정수 주식 수량의 매도/매수 주문으로 변환한다.

    - 플랜에 없는 보유 종목은 전량 매도(목표 0%)
    - 매도를 먼저 산출해 확보되는 현금까지 포함해 매수 가능액 계산
    - 1주 미만 또는 min_order_amount 미만의 차액은 생략
    """
    if not plan.targets:
        raise RebalanceError("목표 비중이 비어 있습니다. 먼저 종목과 비중을 저장하세요.")
    source = source or (snap or {}).get("account_source") or await get_account_source(user_id)
    snap = snap or await snapshot(db, user_id, plan, source)
    total = snap["total_asset"]
    if total <= 0:
        raise RebalanceError("평가 가능한 자산이 없습니다.")

    price_map: dict[str, float] = {}
    for r in snap["rows"]:
        if r["price"]:
            price_map[r["symbol"]] = float(r["price"])
    # 미보유 목표 종목의 현재가 조회
    for t in plan.targets:
        if t["symbol"] not in price_map:
            try:
                price_map[t["symbol"]] = (await _kis_price(t["symbol"])) if source == "kis" else float((await pt.resolve_stock(t["symbol"]))["price"])
            except Exception as exc:  # 시세 실패 종목은 건너뛰고 note에 남김
                logger.warning("리밸런싱 시세 조회 실패 %s: %s", t["symbol"], exc)

    sells, buys, skipped = [], [], []
    for r in snap["rows"]:
        sym = r["symbol"]
        price = price_map.get(sym)
        if not price:
            skipped.append({"symbol": sym, "reason": "시세 조회 실패"})
            continue
        target_amt = total * r["target_weight_pct"] / 100
        diff = target_amt - r["current_amount"]
        if abs(diff) < max(price, plan.min_order_amount):
            continue
        qty = int(abs(diff) // price)
        if qty <= 0:
            continue
        if diff < 0:
            qty = min(qty, int(r["quantity"]))
            if qty <= 0:
                continue
            sells.append({"symbol": sym, "name": r["name"], "side": "SELL", "quantity": qty,
                          "price": price, "amount": round(qty * price, 2), "status": "proposed"})
        else:
            buys.append({"symbol": sym, "name": r["name"], "side": "BUY", "quantity": qty,
                         "price": price, "amount": round(qty * price, 2), "status": "proposed"})

    # 매수는 (현금 + 매도대금) 범위 안에서만
    available = snap["cash"] + sum(o["amount"] for o in sells)
    for o in buys:
        if o["amount"] > available:
            qty = int(available // o["price"])
            if qty <= 0:
                o["quantity"], o["amount"], o["status"] = 0, 0.0, "skipped"
                o["error"] = "현금 부족"
                continue
            o["quantity"], o["amount"] = qty, round(qty * o["price"], 2)
        available -= o["amount"]
    buys = [o for o in buys if o["quantity"] > 0]

    orders = sells + buys
    est_cash = snap["cash"] + sum(o["amount"] for o in sells) - sum(o["amount"] for o in buys)
    return {
        "account_source": source,
        "orders": orders,
        "skipped": skipped,
        "estimated_cash_after": round(est_cash, 2),
        "estimated_turnover": round(sum(o["amount"] for o in orders), 2),
        "snapshot": snap,
    }


# ── 실행 ────────────────────────────────────────────────────────────────


async def execute(db: AsyncSession, user_id: uuid.UUID, plan: RebalancePlan, trigger: str,
                  proposal: dict | None = None, note: str = "") -> RebalanceRun:
    """제안 주문을 모의 체결하고 RebalanceRun(executed)을 기록한다. 커밋은 호출자가 한다."""
    proposal = proposal or await propose(db, user_id, plan)
    source = proposal.get("account_source") or await get_account_source(user_id)
    before = _weights_from_snapshot(proposal["snapshot"])
    target = {r["symbol"]: float(r["target_weight_pct"]) for r in proposal["snapshot"]["rows"]}
    target["CASH"] = round(proposal["snapshot"].get("cash_target_pct", 100 - sum(target.values())), 2)
    for sr in proposal["snapshot"].get("sectors") or []:
        target[f"{SECTOR_PREFIX}{sr['sector']}"] = float(sr["target_weight_pct"])

    executed = []
    for o in proposal["orders"]:
        rec = dict(o)
        if source == "kis":
            # KIS Testbed 실주문(게이트웨이). 체결은 비동기(quant.confirm_fills) → 여기서는 submitted/skipped/failed 만 기록
            from app.services import auto_trade
            res = await auto_trade._place_live_order_via_gateway(db, None, o["symbol"], o["name"], o["side"].lower(),
                                                                 int(o["quantity"]), float(o["price"]), str(user_id))
            st = res.get("status")
            rec.update({"status": "submitted" if st == "submitted" else ("skipped" if st == "skipped" else "failed"),
                        "live_order": {k: res.get(k) for k in ("status", "order_no", "client_order_id", "reason", "error", "environment") if res.get(k) is not None}})
            if st != "submitted":
                rec["error"] = res.get("reason") or res.get("error") or st
            executed.append(rec)
            continue
        try:
            res = await pt.stock_order(db, user_id, o["symbol"], o["side"], int(o["quantity"]), source=ORDER_SOURCE)
            rec.update({"status": "filled", "price": res["price"], "amount": res["amount"]})
        except pt.PaperTradeError as exc:
            rec.update({"status": "failed", "error": str(exc)})
        executed.append(rec)

    after_snap = await snapshot(db, user_id, plan, source)
    ok_statuses = ("filled", "submitted")
    run = RebalanceRun(
        user_id=user_id, plan_id=plan.id, trigger=trigger,
        status="executed" if any(o["status"] in ok_statuses for o in executed) else "skipped",
        total_asset=proposal["snapshot"]["total_asset"],
        max_drift_pct=proposal["snapshot"]["max_drift_pct"],
        before_weights=before, target_weights=target, after_weights=_weights_from_snapshot(after_snap),
        orders=executed, note=((f"[{source}] " if source != "paper" else "") + (note or ""))[:300],
    )
    db.add(run)
    now = datetime.now(timezone.utc)
    plan.last_run_at = now
    if trigger == "TIME":
        plan.next_run_at = next_period_start(now, plan.time_period)
    await db.flush()
    return run


async def record_proposal(db: AsyncSession, user_id: uuid.UUID, plan: RebalancePlan, trigger: str,
                          proposal: dict, note: str = "") -> RebalanceRun:
    """자동 체결이 꺼진 플랜: 주문을 실행하지 않고 제안(proposed)만 남긴다."""
    target = {t["symbol"]: float(t["weight_pct"]) for t in plan.targets}
    target["CASH"] = round(100 - sum(target.values()), 2)
    run = RebalanceRun(
        user_id=user_id, plan_id=plan.id, trigger=trigger, status="proposed",
        total_asset=proposal["snapshot"]["total_asset"], max_drift_pct=proposal["snapshot"]["max_drift_pct"],
        before_weights=_weights_from_snapshot(proposal["snapshot"]), target_weights=target,
        orders=proposal["orders"], note=(note or "")[:300],
    )
    db.add(run)
    if trigger == "TIME":
        plan.next_run_at = next_period_start(datetime.now(timezone.utc), plan.time_period)
    await db.flush()
    return run


async def _has_recent_proposal(db: AsyncSession, user_id: uuid.UUID, trigger: str, hours: int = 24) -> bool:
    from datetime import timedelta
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    row = (await db.execute(
        select(RebalanceRun.id).where(
            RebalanceRun.user_id == user_id, RebalanceRun.trigger == trigger,
            RebalanceRun.status == "proposed", RebalanceRun.created_at >= since,
        ).limit(1)
    )).scalar_one_or_none()
    return row is not None


async def trigger_rebalance(db: AsyncSession, user_id: uuid.UUID, plan: RebalancePlan, trigger: str,
                            note: str = "") -> RebalanceRun | None:
    """트리거 충족 시 auto_execute 여부에 따라 체결 또는 제안 기록."""
    try:
        proposal = await propose(db, user_id, plan)
    except RebalanceError as exc:
        logger.info("리밸런싱 제안 불가 user=%s: %s", user_id, exc)
        return None
    if not proposal["orders"]:
        return None
    if plan.auto_execute:
        return await execute(db, user_id, plan, trigger, proposal, note)
    if await _has_recent_proposal(db, user_id, trigger):
        return None  # 같은 트리거의 미처리 제안이 24시간 내 있으면 중복 생성 안 함
    return await record_proposal(db, user_id, plan, trigger, proposal, note)


# ── 트리거 점검 ─────────────────────────────────────────────────────────


async def check_due(db: AsyncSession, user_id: uuid.UUID, plan: RebalancePlan) -> dict:
    """TIME·DRIFT 트리거를 점검하고 충족 시 실행/제안. 결과 요약 반환."""
    now = datetime.now(timezone.utc)
    result: dict = {"time_due": False, "drift_due": False, "run_id": None, "trigger": None}
    if not plan.is_active or not plan.targets:
        return result

    if plan.time_period != "none" and plan.next_run_at and plan.next_run_at <= now:
        result["time_due"] = True
    snap = await snapshot(db, user_id, plan)
    result["max_drift_pct"] = snap["max_drift_pct"]
    if snap["drift_exceeded"]:
        result["drift_due"] = True

    trigger = "TIME" if result["time_due"] else ("DRIFT" if result["drift_due"] else None)
    if trigger:
        note = (f"{plan.time_period} 주기 도래" if trigger == "TIME"
                else f"최대 이탈 {snap['max_drift_pct']:.2f}%p ≥ 허용 {plan.drift_threshold_pct}%p")
        run = await trigger_rebalance(db, user_id, plan, trigger, note)
        if run:
            result["run_id"] = str(run.id)
            result["trigger"] = trigger
            result["status"] = run.status
        elif trigger == "TIME":
            plan.next_run_at = next_period_start(now, plan.time_period)  # 주문 없음 → 다음 주기로
    return result


async def check_all_due(session_factory) -> dict:
    """스케줄러용: 활성 플랜 전체 점검."""
    checked = executed = proposed = 0
    async with session_factory() as db:
        plans = (await db.execute(select(RebalancePlan).where(RebalancePlan.is_active.is_(True)))).scalars().all()
        for plan in plans:
            checked += 1
            try:
                r = await check_due(db, plan.user_id, plan)
                if r.get("status") == "executed":
                    executed += 1
                elif r.get("status") == "proposed":
                    proposed += 1
                await db.commit()
            except Exception:
                await db.rollback()
                logger.exception("리밸런싱 점검 실패 user=%s", plan.user_id)
    return {"checked": checked, "executed": executed, "proposed": proposed}


# ── 현금흐름(입금·출금·배당) ────────────────────────────────────────────


async def record_cashflow(db: AsyncSession, user_id: uuid.UUID, kind: str, amount: float,
                          symbol: str = "", memo: str = "") -> dict:
    """현금흐름을 모의계좌에 반영하고, 플랜 조건 충족 시 CASHFLOW 리밸런싱을 실행/제안한다."""
    kind = (kind or "").upper()
    if kind not in CASHFLOW_KINDS:
        raise RebalanceError("kind는 DEPOSIT, WITHDRAW, DIVIDEND 중 하나여야 합니다.")
    amount = float(amount)
    if amount <= 0:
        raise RebalanceError("금액은 0보다 커야 합니다.")

    account = await pt.get_account(db, user_id, lock=True)
    if kind == "WITHDRAW":
        if amount > account.cash:
            raise RebalanceError(f"출금 가능 현금이 부족합니다 (보유 {account.cash:,.0f}원).")
        account.cash = float(account.cash - amount)
    else:
        account.cash = float(account.cash + amount)
    if kind == "DIVIDEND" and symbol:
        symbol = pt.normalize_stock_symbol(symbol)

    event = CashflowEvent(user_id=user_id, kind=kind, amount=amount, symbol=symbol or "",
                          memo=(memo or "")[:200], cash_after=account.cash)
    db.add(event)
    await db.flush()
    await db.refresh(event)

    run = None
    plan = await get_plan(db, user_id, create=False)
    if plan and plan.is_active and plan.cashflow_enabled and plan.targets and amount >= plan.cashflow_min_amount:
        label = {"DEPOSIT": "입금", "WITHDRAW": "출금", "DIVIDEND": "배당"}[kind]
        run = await trigger_rebalance(db, user_id, plan, "CASHFLOW", f"{label} {amount:,.0f}원 발생")
        if run:
            event.rebalance_run_id = run.id
            await db.flush()
            await db.refresh(run)
    return {"event": cashflow_to_dict(event), "run": run_to_dict(run) if run else None}


def cashflow_to_dict(e: CashflowEvent) -> dict:
    return {"id": str(e.id), "kind": e.kind, "amount": e.amount, "symbol": e.symbol, "memo": e.memo,
            "cash_after": e.cash_after, "rebalance_run_id": str(e.rebalance_run_id) if e.rebalance_run_id else None,
            "created_at": e.created_at.isoformat() if e.created_at else None}


def run_to_dict(r: RebalanceRun) -> dict:
    return {"id": str(r.id), "trigger": r.trigger, "status": r.status, "total_asset": r.total_asset,
            "max_drift_pct": r.max_drift_pct, "before_weights": r.before_weights, "target_weights": r.target_weights,
            "after_weights": r.after_weights, "orders": r.orders, "note": r.note,
            "created_at": r.created_at.isoformat() if r.created_at else None}


async def list_runs(db: AsyncSession, user_id: uuid.UUID, limit: int = 30) -> list[dict]:
    rows = (await db.execute(select(RebalanceRun).where(RebalanceRun.user_id == user_id)
                             .order_by(RebalanceRun.created_at.desc()).limit(limit))).scalars().all()
    return [run_to_dict(r) for r in rows]


async def list_cashflows(db: AsyncSession, user_id: uuid.UUID, limit: int = 50) -> list[dict]:
    rows = (await db.execute(select(CashflowEvent).where(CashflowEvent.user_id == user_id)
                             .order_by(CashflowEvent.created_at.desc()).limit(limit))).scalars().all()
    return [cashflow_to_dict(e) for e in rows]


async def execute_proposal(db: AsyncSession, user_id: uuid.UUID, run_id: uuid.UUID) -> RebalanceRun:
    """제안(proposed) 상태의 실행 이력을 사용자가 승인해 체결한다(현재 시세로 재산출)."""
    row = (await db.execute(select(RebalanceRun).where(RebalanceRun.id == run_id, RebalanceRun.user_id == user_id))).scalar_one_or_none()
    if row is None:
        raise RebalanceError("제안을 찾을 수 없습니다.")
    if row.status != "proposed":
        raise RebalanceError("이미 처리된 제안입니다.")
    plan = await get_plan(db, user_id)
    new_run = await execute(db, user_id, plan, row.trigger, None, f"제안 승인 · {row.note}")
    row.status = "skipped"
    row.note = (row.note + " → 승인되어 새 실행으로 대체")[:300]
    return new_run
