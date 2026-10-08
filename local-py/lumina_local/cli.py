"""로컬 자동매매 CLI.

    python trade.py signals                 # 지표·시그널만 본다(주문 없음)
    python trade.py cycle --dry-run         # 한 사이클 판단 + 체결 시뮬(장부 변경 없음)
    python trade.py cycle                   # 한 사이클 실행(가상 장부 체결)
    python trade.py loop --market-hours     # 주기 루프(장 시간에만)
    python trade.py status                  # 계좌·보유·위험관리 상태
    python trade.py orders --limit 20       # 최근 주문
    python trade.py backtest 005930.KS      # 단일 종목 백테스트
    python trade.py kill "사유" / resume     # 비상 정지 / 해제
    python trade.py reset                   # 장부 초기화
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import replace

from . import risk_guard, strategy
from .auto_trade import AutoTrader, summarize
from .backtest import run as run_backtest
from .broker import build_market_client
from .config import Settings
from .market_data import MarketData, is_krx_market_open
from .store import Store
from .universe import QUANT_STOCKS, STOCK_MAP


def _settings_from_args(args: argparse.Namespace) -> Settings:
    s = Settings.from_env()
    overrides: dict = {}
    for name in ("mode", "market_data_source", "strategy_id", "symbol_source", "user_id"):
        value = getattr(args, name, None)
        if value:
            overrides[name] = value
    for name in ("cycle_sec", "ai_top_n"):
        value = getattr(args, name, None)
        if value is not None:
            overrides[name] = int(value)
    for name in ("per_trade_budget", "buy_ratio", "sell_ratio", "initial_capital"):
        value = getattr(args, name, None)
        if value is not None:
            overrides[name] = float(value)
    if getattr(args, "symbols", None):
        overrides["selected_symbols"] = args.symbols
        overrides["symbol_source"] = "manual"
    if getattr(args, "aggressive", False):
        overrides["aggressive"] = True
    if getattr(args, "no_aggressive", False):
        overrides["aggressive"] = False
    return replace(s, **overrides).clamped() if overrides else s.clamped()


def _trader(args: argparse.Namespace) -> AutoTrader:
    s = _settings_from_args(args)
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return AutoTrader(s)


# ── 명령 ────────────────────────────────────────────────────────────────
def cmd_signals(args) -> int:
    trader = _trader(args)
    cycle_log: dict = {"signals": []}
    indicator_map, price_map = trader.collect_indicators(cycle_log)
    spec = strategy.load(trader.settings.strategy_id) if trader.settings.strategy_id else None
    targets = set(trader.pick_symbols(indicator_map))
    rows = []
    for stock in QUANT_STOCKS:
        symbol = stock["symbol"]
        ind = indicator_map.get(symbol) or {}
        sig = dict(ind.get("signal") or {})
        if spec and sig:
            sig = strategy.apply_to_signal(sig, spec, None, ind)
        rows.append((int(sig.get("score", 0) or 0), symbol, stock["name"],
                     price_map.get(symbol), sig.get("action", "판단 불가"),
                     ", ".join(str(r) for r in (sig.get("reasons") or [])[:3])))
    rows.sort(key=lambda r: r[0], reverse=True)
    print(f"시세={trader.settings.market_data_source} 전략={trader.settings.strategy_id or '기본 지표'} "
          f"공격모드={trader.settings.aggressive} / 대상종목={len(targets)}개")
    print(f"{'대상':<4}{'점수':>5} {'종목':<22}{'현재가':>12} {'판단':<8} 근거")
    for score, symbol, name, price, action, reasons in rows:
        mark = " * " if symbol in targets else "   "
        price_text = f"{price:,.0f}" if price else "-"
        print(f"{mark:<4}{score:>5} {symbol} {name:<10}{price_text:>12} {action:<8} {reasons[:70]}")
    return 0


def cmd_cycle(args) -> int:
    trader = _trader(args)
    cycle = trader.run_cycle(dry_run=args.dry_run)
    if args.json:
        print(json.dumps(cycle, ensure_ascii=False, indent=2))
    else:
        summarize(cycle)
        for row in cycle.get("risk", {}).get("skipped") or []:
            print(f"    [생략] {row['side']} {row['symbol']} — {row['reason']}")
    return 0


def cmd_loop(args) -> int:
    trader = _trader(args)
    trader.run_loop(max_cycles=args.max_cycles, dry_run=args.dry_run,
                    market_hours_only=args.market_hours)
    return 0


def cmd_status(args) -> int:
    s = _settings_from_args(args)
    store = Store(s.state_path, s.user_id, s.initial_capital)
    market = MarketData(s, build_market_client(s))
    price_map: dict[str, float] = {}
    for symbol in store.positions():
        try:
            quote = market.quote(symbol)
            if quote.get("price"):
                price_map[symbol] = float(quote["price"])
        except Exception as exc:
            print(f"  (현재가 조회 실패 {symbol}: {exc} — 평균단가로 평가)")
    acct = store.account_summary(price_map)
    limits = risk_guard.RiskLimits.from_settings(s, store)
    if s.aggressive:
        limits = limits.with_aggressive(s)

    print(f"계정 {s.user_id} / mode={s.mode} / 시세={s.market_data_source} / 장운영={'열림' if is_krx_market_open() else '닫힘'}")
    print(f"상태파일 {s.state_path}")
    print(f"\n[계좌] 원금 {acct['initial_capital']:,.0f} → 총자산 {acct['total_equity']:,.0f}원 "
          f"({acct['pnl_pct']}%)  현금 {acct['cash_balance']:,.0f} / 평가 {acct['holdings_value']:,.0f}")
    positions = store.positions()
    if positions:
        print(f"\n[보유 {len(positions)}종목]")
        for symbol, p in positions.items():
            mark = price_map.get(symbol) or float(p["avg_price"])
            pnl = (mark / float(p["avg_price"]) - 1) * 100 if p["avg_price"] else 0
            print(f"  {symbol} {p.get('name') or STOCK_MAP.get(symbol, {}).get('name', ''):<10} "
                  f"{int(p['quantity']):>5d}주  평단 {float(p['avg_price']):>10,.0f}  "
                  f"현재 {mark:>10,.0f}  {pnl:+.2f}%")
    else:
        print("\n[보유] 없음")

    risk = risk_guard.status(store, limits, acct["total_equity"])
    print(f"\n[위험관리] 비상정지={'ON — ' + (risk['halt_reason'] or '') if risk['kill_switch'] else 'OFF'}")
    print(f"  당일손익 {risk['day_pnl_pct']}% (한도 -{limits.daily_loss_limit_pct}%) / "
          f"주문 {risk['orders_today']}건" + (f" (잔여 {risk['orders_remaining']})" if risk['orders_remaining'] is not None else ""))
    print(f"  종목비중 한도 {limits.max_position_pct}% / 쿨다운 {limits.cooldown_min}분")
    last = store.last_cycle()
    if last:
        print(f"\n[마지막 사이클] {last['time']} — 체결 "
              f"{len([t for t in last.get('trades') or [] if t.get('status') == 'filled'])}건")
    return 0


def cmd_orders(args) -> int:
    s = _settings_from_args(args)
    store = Store(s.state_path, s.user_id, s.initial_capital)
    orders = store.account["orders"][-args.limit:]
    if not orders:
        print("주문 기록이 없습니다.")
        return 0
    for o in orders:
        print(f"{o['time'][:19]} {o['status']:<8} {o['action']:<4} {o['symbol']:>10} "
              f"{o['name']:<10} {o['quantity']:>5}주 @{o['price']:>10,.0f}  {str(o.get('reason'))[:80]}")
    return 0


def cmd_backtest(args) -> int:
    s = _settings_from_args(args)
    market = MarketData(s, build_market_client(s))
    spec = strategy.load(s.strategy_id) if s.strategy_id else None
    for symbol in args.symbols or [x["symbol"] for x in QUANT_STOCKS[:5]]:
        data = market.daily_candles(symbol, period=args.period)
        result = run_backtest(symbol, data.get("candles") or [], spec,
                              initial_capital=s.initial_capital, buy_ratio=s.buy_ratio,
                              sell_ratio=1.0, per_trade_budget=s.per_trade_budget,
                              fee_bps=args.fee_bps)
        if result.get("error"):
            print(f"{symbol}: {result['error']}")
            continue
        print(f"{symbol} {STOCK_MAP.get(symbol, {}).get('name', '')} [{result['strategy']}] "
              f"{result['bars']}봉 · 거래 {result['trades']}건 · 승률 {result['win_rate_pct']}% · "
              f"수익 {result['return_pct']:+.2f}% (매수후보유 {result['buy_and_hold_pct']:+.2f}%) · "
              f"MDD {result['max_drawdown_pct']:.2f}%")
    return 0


def cmd_kill(args) -> int:
    s = _settings_from_args(args)
    store = Store(s.state_path, s.user_id, s.initial_capital)
    risk_guard.halt(store, args.reason or "수동 정지")
    print(f"비상 정지 ON — {store.risk['halt_reason']}")
    return 0


def cmd_resume(args) -> int:
    s = _settings_from_args(args)
    store = Store(s.state_path, s.user_id, s.initial_capital)
    risk_guard.resume(store)
    print("비상 정지 해제 — 다음 사이클부터 주문합니다.")
    return 0


def cmd_reset(args) -> int:
    s = _settings_from_args(args)
    store = Store(s.state_path, s.user_id, s.initial_capital)
    if not args.yes:
        answer = input(f"{s.state_path} 의 계정 '{s.user_id}' 장부를 초기화합니다. 계속할까요? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("취소했습니다.")
            return 1
    store.reset()
    print(f"초기화 완료 — 현금 {s.initial_capital:,.0f}원")
    return 0


def cmd_config(args) -> int:
    s = _settings_from_args(args)
    print(json.dumps(s.to_dict(), ensure_ascii=False, indent=2))
    print(f"\n사용 가능한 전략: {', '.join(strategy.available()) or '없음'}")
    return 0


# ── 파서 ────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trade.py", description="lumina 로컬 자동매매")
    parser.add_argument("-v", "--verbose", action="store_true", help="디버그 로그")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--mode", choices=["paper", "live"], help="paper=가상장부만, live=실주문까지")
    common.add_argument("--source", dest="market_data_source", choices=["yahoo", "kis", "mock"], help="시세 소스")
    common.add_argument("--strategy", dest="strategy_id", help="strategies/<id>.json")
    common.add_argument("--symbols", nargs="+", help="대상 종목 직접 지정(manual)")
    common.add_argument("--top-n", dest="ai_top_n", type=int, help="점수 상위 N종목")
    common.add_argument("--budget", dest="per_trade_budget", type=float, help="1회 매수 예산(원)")
    common.add_argument("--buy-ratio", dest="buy_ratio", type=float)
    common.add_argument("--sell-ratio", dest="sell_ratio", type=float)
    common.add_argument("--aggressive", action="store_true", help="분봉 공격 모드")
    common.add_argument("--no-aggressive", action="store_true")
    common.add_argument("--user", dest="user_id", help="상태 파일 안의 계정 키")

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("signals", parents=[common], help="지표·시그널만 출력(주문 없음)")
    p.set_defaults(func=cmd_signals)

    p = sub.add_parser("cycle", parents=[common], help="한 사이클 실행")
    p.add_argument("--dry-run", action="store_true", help="판단만 하고 장부를 바꾸지 않는다")
    p.add_argument("--json", action="store_true", help="사이클 로그 전체를 JSON 으로 출력")
    p.set_defaults(func=cmd_cycle)

    p = sub.add_parser("loop", parents=[common], help="주기 루프 실행")
    p.add_argument("--interval", dest="cycle_sec", type=int, help="사이클 주기(초)")
    p.add_argument("--max-cycles", type=int, help="N회 후 종료")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--market-hours", action="store_true", help="KRX 정규장(09:00~15:30 KST)에만 실행")
    p.set_defaults(func=cmd_loop)

    p = sub.add_parser("status", parents=[common], help="계좌·보유·위험관리 상태")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("orders", parents=[common], help="주문 기록")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_orders)

    p = sub.add_parser("backtest", parents=[common], help="단일 종목 백테스트")
    p.add_argument("symbols", nargs="*", help="종목(미지정 시 유니버스 상위 5개)")
    p.add_argument("--period", default="2y")
    p.add_argument("--fee-bps", type=float, default=25.0, help="왕복 비용(bp). 기본 25 = 0.25%%")
    p.set_defaults(func=cmd_backtest)

    p = sub.add_parser("kill", parents=[common], help="비상 정지")
    p.add_argument("reason", nargs="?", default="수동 정지")
    p.set_defaults(func=cmd_kill)

    p = sub.add_parser("resume", parents=[common], help="비상 정지 해제")
    p.set_defaults(func=cmd_resume)

    p = sub.add_parser("reset", parents=[common], help="장부 초기화")
    p.add_argument("--yes", action="store_true", help="확인 없이 초기화")
    p.add_argument("--capital", dest="initial_capital", type=float, help="초기 자본(원)")
    p.set_defaults(func=cmd_reset)

    p = sub.add_parser("config", parents=[common], help="현재 설정 확인")
    p.set_defaults(func=cmd_config)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
                        datefmt="%H:%M:%S")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
