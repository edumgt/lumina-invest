"""자동매매 사이클 — app/services/auto_trade.py 의 _run_quant_cycle 을 로컬 단일 프로세스로 옮긴 것.

차이점만 정리하면:
  - DB(PostgreSQL) → store.Store (data/state.json)
  - Redis 위험관리 → risk_guard (같은 파일의 risk 섹션)
  - Celery Beat → run_loop() 의 time.sleep 루프
  - 알림(Slack/메일)·감사로그·게이트웨이·SageMaker 배치점수 → 콘솔 로그
판단 로직(지표 → 시그널 → 전략 스펙 → 공격 모드 계획 → 위험관리 → 체결)과 순서는 그대로다.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from . import aggressive as aggressive_mod
from . import risk_guard, strategy
from .broker import BrokerError, build_broker, build_market_client
from .config import Settings
from .indicators import quant_indicators, signal_basis
from .market_data import MarketData, is_krx_market_open
from .store import Store
from .universe import QUANT_STOCKS, STOCK_MAP, name_of

logger = logging.getLogger(__name__)

BUY_ACTIONS = ("강력 매수", "매수")
SELL_ACTIONS = ("강력 매도", "매도")


class AutoTrader:
    def __init__(self, settings: Settings, store: Store | None = None,
                 market: MarketData | None = None, broker=None):
        self.settings = settings.clamped()
        self.store = store or Store(self.settings.state_path, self.settings.user_id,
                                    self.settings.initial_capital)
        self.market = market or MarketData(self.settings, build_market_client(self.settings))
        self.broker = broker if broker is not None else build_broker(self.settings)

    # ── 지표 수집 ───────────────────────────────────────────────────────
    def collect_indicators(self, cycle_log: dict) -> tuple[dict[str, dict], dict[str, float]]:
        """유니버스 전체의 지표와 현재가. 데이터를 못 받은 종목은 '판단 불가'로 남긴다."""
        s = self.settings
        indicator_map: dict[str, dict] = {}
        price_map: dict[str, float] = {}
        for stock in QUANT_STOCKS:
            symbol = stock["symbol"]
            try:
                if s.aggressive:
                    indicators = aggressive_mod.intraday_indicators(symbol, self.market, s)
                else:
                    data = self.market.daily_candles(symbol)
                    indicators = quant_indicators(symbol, data.get("candles") or [], data.get("source") or "")
                indicator_map[symbol] = indicators
                price = indicators.get("current_price")
                if price:
                    price_map[symbol] = float(price)
            except Exception as exc:
                logger.warning("지표 계산 실패 %s: %s", symbol, exc)
                cycle_log["signals"].append({
                    "symbol": symbol, "name": stock["name"], "error": f"지표 계산 실패: {exc}",
                    "action": "판단 불가", "score": 0,
                    "reasons": ["시장 데이터를 받지 못해 판단하지 않았습니다"], "basis": {},
                })
        return indicator_map, price_map

    # ── 종목 선정 ───────────────────────────────────────────────────────
    def pick_symbols(self, indicator_map: dict[str, dict]) -> list[str]:
        s = self.settings
        if s.symbol_source == "manual":
            picked = [sym for sym in s.selected_symbols if sym in STOCK_MAP]
            if picked:
                return picked
            logger.warning("manual 종목이 유니버스에 없어 상위 %d개로 대체합니다", s.ai_top_n)
            return [x["symbol"] for x in QUANT_STOCKS[:s.ai_top_n]]
        ranked = sorted(
            ((stock["symbol"], int(((indicator_map.get(stock["symbol"]) or {}).get("signal") or {}).get("score", 0)))
             for stock in QUANT_STOCKS),
            key=lambda item: item[1], reverse=True,
        )
        top_n = min(s.ai_top_n, len(QUANT_STOCKS))
        pool = max(top_n, 2 * int(s.aggressive_max_buys_per_cycle), 5) if s.aggressive else top_n
        return [sym for sym, _ in ranked[:pool]]

    # ── 실주문 ──────────────────────────────────────────────────────────
    def place_live_order(self, symbol: str, side: str, quantity: int, price: float) -> dict | None:
        """live 모드에서 가상 장부 체결과 별도로 실제 주문을 보낸다. 실패해도 사이클은 계속된다."""
        s = self.settings
        if s.mode != "live" or self.broker is None or quantity <= 0:
            return None
        if s.kis_enforce_market_hours and not is_krx_market_open():
            logger.info("장 운영시간 외 — 실주문 생략 (%s %s x%d)", side, symbol, quantity)
            return {"status": "skipped", "reason": "market_closed"}
        order_type = "MARKET" if s.aggressive else "LIMIT"
        try:
            result = self.broker.place_order(symbol, side, quantity, price, order_type=order_type)
            logger.info("실주문 접수 %s %s x%d @%s → %s", side, symbol, quantity,
                        f"{price:,.0f}", result.get("order_no"))
            return result
        except (BrokerError, Exception) as exc:
            logger.warning("실주문 실패 (%s %s x%d): %s", side, symbol, quantity, exc)
            return {"status": "error", "error": str(exc)}

    # ── 사이클 ──────────────────────────────────────────────────────────
    def run_cycle(self, dry_run: bool = False) -> dict:
        s = self.settings
        store = self.store
        cycle_log: dict = {"time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                           "trades": [], "signals": [], "dry_run": dry_run}

        limits = risk_guard.RiskLimits.from_settings(s, store)
        if s.aggressive:
            limits = limits.with_aggressive(s)
        if limits.kill_switch:
            cycle_log["risk"] = {"halted": True,
                                 "reason": store.risk.get("halt_reason") or "비상 정지 스위치 ON"}
            cycle_log["note"] = "비상 정지 상태 — 주문을 내지 않습니다. `resume` 명령으로 해제하세요."
            store.append_cycle(cycle_log)
            return cycle_log

        indicator_map, price_map = self.collect_indicators(cycle_log)
        target_symbols = self.pick_symbols(indicator_map)

        # ── 전략 스펙 ──
        spec = strategy.load(s.strategy_id) if s.strategy_id else None
        strategy_log = {"id": s.strategy_id, "version": 0, "applied": False}
        if s.strategy_id:
            if spec:
                target_symbols = strategy.apply_to_symbols(target_symbols, spec)
                strategy_log.update(version=int(spec.get("version") or 0), applied=True)
            else:
                strategy_log["error"] = f"strategies/{s.strategy_id}.json 을 읽지 못해 기본 규칙 사용"

        # ── 공격 모드 계획 ──
        forced_action: dict[str, dict] = {}
        if s.aggressive:
            holdings = {sym: (int(p["quantity"]), float(p.get("avg_price") or 0))
                        for sym, p in store.positions().items()}
            ag_plan = aggressive_mod.plan(s, indicator_map, target_symbols, holdings, price_map)
            for sym in ag_plan["buy"]:
                forced_action[sym] = {"action": "매수", "ratio": None, "note": "공격 모드 매수"}
            for sym, info in ag_plan["sell"].items():
                forced_action[sym] = {"action": "매도", "ratio": info["ratio"], "note": info["reason"]}
            for sym in list(forced_action):
                if sym not in target_symbols and sym in STOCK_MAP:
                    target_symbols.append(sym)
            cycle_log["aggressive"] = {
                "buy": ag_plan["buy"], "sell": {k: v["reason"] for k, v in ag_plan["sell"].items()},
                "ranked": ag_plan["ranked"][:10], "notes": ag_plan["notes"],
                "interval": s.aggressive_candle_interval,
                "take_profit_pct": s.aggressive_take_profit_pct, "stop_loss_pct": s.aggressive_stop_loss_pct,
            }

        cycle_log["settings"] = {
            "mode": s.mode, "aggressive": s.aggressive, "symbol_source": s.symbol_source,
            "market_data_source": s.market_data_source, "strategy": strategy_log,
            "symbols": target_symbols, "per_trade_budget": s.per_trade_budget,
            "buy_ratio": s.buy_ratio, "sell_ratio": s.sell_ratio,
        }

        # ── 위험관리 사전 점검: 일손실 한도 ──
        cash_now, equity_now, position_values = store.equity(price_map)
        start_equity = risk_guard.day_start_equity(store, equity_now)
        day_pnl = risk_guard.daily_pnl_pct(start_equity, equity_now)
        cycle_log["risk"] = {
            "day_start_equity": round(start_equity, 2), "equity": round(equity_now, 2),
            "day_pnl_pct": day_pnl, "orders_today": risk_guard.orders_today(store),
            "limits": limits.to_dict(), "skipped": [],
        }
        if risk_guard.daily_loss_breached(start_equity, equity_now, limits.daily_loss_limit_pct):
            reason = f"일손실 한도 초과: 당일 {day_pnl:+.2f}% ≤ -{limits.daily_loss_limit_pct}%"
            cycle_log["risk"].update(halted=True, reason=reason)
            store.append_cycle(cycle_log)
            if not dry_run:
                self._emergency_halt(reason, day_pnl)
            return cycle_log

        def risk_gate(symbol: str, side: str, qty: int, price: float) -> tuple[int, str | None]:
            """주문 직전 위험관리 게이트. (허용 수량, 생략/조정 사유)"""
            if limits.max_orders_per_day > 0 and risk_guard.orders_today(store) >= limits.max_orders_per_day:
                return 0, f"일 주문 수 한도 {limits.max_orders_per_day}건 도달"
            note = None
            if side == "buy":
                qty, note = risk_guard.cap_buy_quantity(qty, price, position_values.get(symbol, 0.0),
                                                        equity_now, limits.max_position_pct)
                if qty <= 0:
                    return 0, note
            if dry_run:
                return qty, note
            if not risk_guard.acquire_order_slot(store, symbol, side, limits.cooldown_min):
                return 0, f"중복 주문 방지: {limits.cooldown_min}분 내 동일 종목·방향 주문 존재"
            return qty, note

        def risk_skip(symbol: str, name: str, side: str, price: float, reason: str) -> None:
            cycle_log["risk"]["skipped"].append({"symbol": symbol, "name": name, "side": side, "reason": reason})
            cycle_log["trades"].append({
                "time": datetime.now(timezone.utc).isoformat(), "symbol": symbol, "name": name,
                "action": side, "quantity": 0, "price": price,
                "reason": f"[위험관리] {reason}", "status": "skipped", "type": "risk",
            })
            logger.info("[위험관리] %s %s 생략 — %s", side, symbol, reason)

        # ── 종목별 판단 · 체결 ──
        for symbol in target_symbols:
            stock = STOCK_MAP.get(symbol)
            if not stock:
                continue
            indicators = indicator_map.get(symbol) or {}
            signal = dict(indicators.get("signal") or {})
            if spec:
                signal = strategy.apply_to_signal(signal, spec, None, indicators)
            price = indicators.get("current_price")
            if not price:
                continue
            price = float(price)

            action = signal.get("action", "관망")
            reasons = list(signal.get("reasons") or [])
            sell_ratio_here = s.sell_ratio
            if s.aggressive:
                forced = forced_action.get(symbol)
                if forced:
                    action = forced["action"]
                    reasons = [forced["note"], *reasons]
                    if forced["ratio"] is not None:
                        sell_ratio_here = float(forced["ratio"])
                else:
                    action = "관망"   # 공격 모드에서는 plan 이 정한 종목만 거래한다

            cycle_log["signals"].append({
                "symbol": symbol, "name": stock["name"], "price": price, "action": action,
                "score": signal.get("score", 0), "reasons": [str(r) for r in reasons[:6]],
                "error": signal.get("error"), "basis": signal_basis(indicators),
            })

            if action in BUY_ACTIONS:
                qty = max(1, int(s.per_trade_budget * s.buy_ratio / price))
                qty, note = risk_gate(symbol, "buy", qty, price)
                if qty <= 0:
                    risk_skip(symbol, stock["name"], "buy", price, note or "위험관리 규칙")
                    continue
                if note:
                    reasons = [*reasons, f"위험관리: {note}"]
                self._settle(cycle_log, symbol, stock["name"], "buy", price, qty, reasons,
                             position_values, dry_run)

            elif action in SELL_ACTIONS:
                held = store.position(symbol)
                if not held:
                    continue
                qty = int(held["quantity"]) if sell_ratio_here >= 1.0 else max(1, int(int(held["quantity"]) * sell_ratio_here))
                qty, note = risk_gate(symbol, "sell", qty, price)
                if qty <= 0:
                    risk_skip(symbol, stock["name"], "sell", price, note or "위험관리 규칙")
                    continue
                self._settle(cycle_log, symbol, stock["name"], "sell", price, qty, reasons,
                             position_values, dry_run)

        # ── 사후 점검 ──
        cycle_log["account"] = store.account_summary(price_map)
        total_equity = cycle_log["account"]["total_equity"]
        day_pnl_after = risk_guard.daily_pnl_pct(start_equity, total_equity)
        cycle_log["risk"].update({"equity": round(total_equity, 2), "day_pnl_pct": day_pnl_after})
        store.append_cycle(cycle_log)
        if not dry_run and risk_guard.daily_loss_breached(start_equity, total_equity, limits.daily_loss_limit_pct):
            self._emergency_halt(
                f"일손실 한도 초과(체결 후): 당일 {day_pnl_after:+.2f}% ≤ -{limits.daily_loss_limit_pct}%",
                day_pnl_after)
        return cycle_log

    def _settle(self, cycle_log: dict, symbol: str, name: str, side: str, price: float, qty: int,
                reasons: list, position_values: dict[str, float], dry_run: bool) -> None:
        """가상 장부 체결 + (live 면) 실주문 + 일 주문 수 반영."""
        s = self.settings
        reason_text = f"[{s.mode}] " + " | ".join(str(r) for r in reasons)
        if dry_run:
            cycle_log["trades"].append({"time": datetime.now(timezone.utc).isoformat(), "symbol": symbol,
                                        "name": name, "action": side, "quantity": qty, "price": price,
                                        "reason": reason_text, "status": "dry_run", "type": "auto"})
            logger.info("[DRY-RUN] %s %s %d주 @%s", side, symbol, qty, f"{price:,.0f}")
            return

        trade = self.store.execute_trade(symbol, name, side, price, qty, reason_text)
        cycle_log["trades"].append({**trade, "type": "auto"})
        if trade.get("status") != "filled":
            risk_guard.release_order_slot(self.store, symbol, side)
            logger.info("%s %s 미체결 — %s", side, symbol, trade.get("reason"))
            return

        executed = int(trade.get("quantity") or qty)
        cycle_log["risk"]["orders_today"] = risk_guard.increment_orders_today(self.store)
        delta = price * executed
        position_values[symbol] = max(0.0, position_values.get(symbol, 0.0) + (delta if side == "buy" else -delta))
        logger.info("체결 %s %s %d주 @%s (현금 %s원)", side, symbol, executed,
                    f"{price:,.0f}", f"{trade.get('cash_balance', 0):,.0f}")
        live = self.place_live_order(symbol, side, executed, price)
        if live:
            cycle_log["trades"][-1]["live_order"] = live

    def _emergency_halt(self, reason: str, day_pnl_pct: float | None = None) -> None:
        risk_guard.halt(self.store, reason)
        logger.warning("자동매매 비상 정지: %s (당일 %s)", reason,
                       f"{day_pnl_pct:+.2f}%" if day_pnl_pct is not None else "-")

    # ── 루프 ────────────────────────────────────────────────────────────
    def run_loop(self, max_cycles: int | None = None, dry_run: bool = False,
                 market_hours_only: bool = False) -> None:
        s = self.settings
        logger.info("자동매매 루프 시작 — %d초 주기 / mode=%s / 시세=%s / 공격모드=%s",
                    s.cycle_sec, s.mode, s.market_data_source, s.aggressive)
        count = 0
        try:
            while max_cycles is None or count < max_cycles:
                count += 1
                if market_hours_only and not is_krx_market_open():
                    logger.info("[%d] 장 운영시간 외 — 사이클 생략", count)
                else:
                    started = time.time()
                    cycle = self.run_cycle(dry_run=dry_run)
                    summarize(cycle, prefix=f"[{count}] ")
                    logger.debug("사이클 소요 %.1fs", time.time() - started)
                    if cycle.get("risk", {}).get("halted"):
                        logger.warning("비상 정지로 루프를 종료합니다")
                        return
                if max_cycles is not None and count >= max_cycles:
                    return
                time.sleep(s.cycle_sec)
        except KeyboardInterrupt:
            logger.info("중단(Ctrl-C) — 상태는 %s 에 저장되어 있습니다", s.state_path)


def summarize(cycle: dict, prefix: str = "") -> None:
    """사이클 로그를 한 줄 요약으로 출력한다."""
    acct = cycle.get("account") or {}
    trades = [t for t in cycle.get("trades") or [] if t.get("status") in ("filled", "dry_run")]
    skipped = len([t for t in cycle.get("trades") or [] if t.get("status") == "skipped"])
    equity = acct.get("total_equity")
    if equity is None:
        print(f"{prefix}{cycle['time']} | {cycle.get('note') or '사이클 종료(체결 없음)'}")
    else:
        print(f"{prefix}{cycle['time']} | 판단 {len(cycle.get('signals') or [])}종목 | "
              f"체결 {len(trades)}건 / 생략 {skipped}건 | "
              f"총자산 {equity:,.0f}원 ({acct.get('pnl_pct')}%)")
    for t in trades:
        print(f"    {t['action']:4s} {t['symbol']:>10s} {t['name']:<10s} {t['quantity']:>5d}주 "
              f"@{t['price']:>10,.0f}  {str(t.get('reason'))[:90]}")
