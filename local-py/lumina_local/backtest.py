"""단일 종목 백테스트 — 같은 시그널 규칙을 과거 캔들에 하루씩 적용해 본다.

실매매 전에 지표 규칙(또는 전략 스펙)이 이 종목에서 어떻게 동작했는지 확인하는 용도다.
룩어헤드가 없도록 i 번째 봉까지만 보고 i 번째 종가로 체결한다(종가 체결 가정).
수수료·세금은 fee_bps 로 한 번에 반영한다(기본 왕복 0.25%).
"""
from __future__ import annotations

from .indicators import quant_indicators
from .strategy import apply_to_signal


def run(symbol: str, candles: list[dict], spec: dict | None = None,
        initial_capital: float = 10_000_000.0, buy_ratio: float = 1.0,
        sell_ratio: float = 1.0, per_trade_budget: float | None = None,
        fee_bps: float = 25.0, warmup: int = 60) -> dict:
    cash = float(initial_capital)
    qty = 0
    avg = 0.0
    budget = per_trade_budget if per_trade_budget is not None else initial_capital
    trades: list[dict] = []
    equity_curve: list[float] = []
    fee_rate = fee_bps / 10_000

    for i in range(warmup, len(candles)):
        window = candles[:i + 1]
        indicators = quant_indicators(symbol, window)
        signal = indicators.get("signal") or {}
        if not signal:
            continue
        if spec:
            signal = apply_to_signal(signal, spec, None, indicators)
        price = float(window[-1]["close"])
        action = signal.get("action", "관망")

        if action in ("강력 매수", "매수"):
            want = max(1, int(budget * buy_ratio / price))
            affordable = int(cash // (price * (1 + fee_rate)))
            buy_qty = min(want, affordable)
            if buy_qty > 0:
                cost = price * buy_qty * (1 + fee_rate)
                avg = (avg * qty + price * buy_qty) / (qty + buy_qty)
                qty += buy_qty
                cash -= cost
                trades.append({"i": i, "time": window[-1].get("time"), "action": "buy",
                               "price": price, "quantity": buy_qty})
        elif action in ("강력 매도", "매도") and qty > 0:
            sell_qty = qty if sell_ratio >= 1.0 else max(1, int(qty * sell_ratio))
            cash += price * sell_qty * (1 - fee_rate)
            qty -= sell_qty
            trades.append({"i": i, "time": window[-1].get("time"), "action": "sell",
                           "price": price, "quantity": sell_qty,
                           "pnl_pct": round((price / avg - 1) * 100, 2) if avg else None})
            if qty == 0:
                avg = 0.0
        equity_curve.append(cash + qty * price)

    if not equity_curve:
        return {"symbol": symbol, "error": "캔들이 부족해 백테스트할 수 없습니다", "bars": len(candles)}

    final = equity_curve[-1]
    peak = equity_curve[0]
    mdd = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        mdd = min(mdd, value / peak - 1)
    first_close = float(candles[warmup]["close"])
    last_close = float(candles[-1]["close"])
    wins = [t for t in trades if t["action"] == "sell" and (t.get("pnl_pct") or 0) > 0]
    sells = [t for t in trades if t["action"] == "sell"]
    return {
        "symbol": symbol,
        "strategy": (spec or {}).get("strategy_id") or "indicator-default",
        "bars": len(equity_curve),
        "initial_capital": round(initial_capital, 2),
        "final_equity": round(final, 2),
        "return_pct": round((final / initial_capital - 1) * 100, 2),
        "buy_and_hold_pct": round((last_close / first_close - 1) * 100, 2),
        "max_drawdown_pct": round(mdd * 100, 2),
        "trades": len(trades),
        "win_rate_pct": round(len(wins) / len(sells) * 100, 1) if sells else None,
        "open_position": {"quantity": qty, "avg_price": round(avg, 2)} if qty else None,
        "trade_log": trades[-20:],
    }
