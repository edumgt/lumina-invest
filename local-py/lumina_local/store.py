"""로컬 상태 저장소 — PostgreSQL(QuantVirtualAccount·Portfolio·Order)과 Redis 를 JSON 파일 하나로 대체.

data/state.json 한 파일에 계정별로 현금·보유종목·주문내역·사이클로그·위험관리 상태를 담는다.
쓰기는 임시파일 → os.replace 로 원자적으로 처리하므로 루프 중 Ctrl-C 로 파일이 깨지지 않는다.
한 번에 한 프로세스만 돌린다는 전제(로컬 전용)로 잠금은 쓰지 않는다.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_ORDERS = 1_000
MAX_CYCLES = 50


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_account(initial_capital: float) -> dict:
    return {
        "initial_capital": float(initial_capital),
        "cash_balance": float(initial_capital),
        "positions": {},      # symbol -> {name, quantity, avg_price}
        "orders": [],         # 체결/생략 주문 기록(최근 MAX_ORDERS 건)
        "cycles": [],         # 사이클 로그(최근 MAX_CYCLES 건)
        "risk": {"kill_switch": False, "halt_reason": "", "slots": {},
                 "orders_count": {}, "day_start_equity": {}},
        "created_at": now_iso(),
    }


class Store:
    def __init__(self, path: Path, user_id: str, initial_capital: float):
        self.path = Path(path)
        self.user_id = user_id
        self.initial_capital = float(initial_capital)
        self.data: dict[str, Any] = self._load()

    # ── 파일 I/O ────────────────────────────────────────────────────────
    def _load(self) -> dict:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                backup = self.path.with_suffix(".corrupt.json")
                self.path.replace(backup)
                print(f"[store] state.json 손상 — {backup} 로 옮기고 새로 시작합니다")
                data = {}
        else:
            data = {}
        data.setdefault("version", 1)
        accounts = data.setdefault("accounts", {})
        accounts.setdefault(self.user_id, _empty_account(self.initial_capital))
        return data

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def reset(self) -> None:
        self.data["accounts"][self.user_id] = _empty_account(self.initial_capital)
        self.save()

    # ── 계정 ────────────────────────────────────────────────────────────
    @property
    def account(self) -> dict:
        return self.data["accounts"][self.user_id]

    @property
    def risk(self) -> dict:
        return self.account["risk"]

    @property
    def cash(self) -> float:
        return float(self.account["cash_balance"])

    def positions(self) -> dict[str, dict]:
        return {s: p for s, p in self.account["positions"].items() if int(p.get("quantity", 0)) > 0}

    def position(self, symbol: str) -> dict | None:
        p = self.account["positions"].get(symbol)
        return p if p and int(p.get("quantity", 0)) > 0 else None

    # ── 평가 ────────────────────────────────────────────────────────────
    def position_values(self, price_map: dict[str, float]) -> dict[str, float]:
        """종목별 평가액(현재가 없으면 평균단가로 평가)."""
        out: dict[str, float] = {}
        for symbol, p in self.positions().items():
            mark = float(price_map.get(symbol) or p.get("avg_price") or 0)
            out[symbol] = int(p["quantity"]) * mark
        return out

    def equity(self, price_map: dict[str, float]) -> tuple[float, float, dict[str, float]]:
        """(현금, 총자산, 종목별 평가액)."""
        values = self.position_values(price_map)
        return self.cash, self.cash + sum(values.values()), values

    def account_summary(self, price_map: dict[str, float]) -> dict:
        cash, total, values = self.equity(price_map)
        initial = float(self.account["initial_capital"])
        return {
            "initial_capital": initial,
            "cash_balance": round(cash, 2),
            "holdings_value": round(sum(values.values()), 2),
            "total_equity": round(total, 2),
            "pnl_pct": round((total / initial - 1) * 100, 2) if initial > 0 else None,
        }

    # ── 체결 ────────────────────────────────────────────────────────────
    def execute_trade(self, symbol: str, name: str, action: str, price: float,
                      quantity: int, reason: str) -> dict:
        """가상 장부 체결. 현금 부족·보유 없음이면 status=skipped 로 기록만 남긴다.

        app 의 _execute_virtual_trade 와 같은 판정 순서를 쓴다:
          매수 — 현금이 부족하면 가능한 수량까지만, 1주도 못 사면 생략
          매도 — 보유가 없으면 생략, 보유보다 많으면 보유 수량까지만
        """
        acct = self.account
        cash = float(acct["cash_balance"])
        executed = int(quantity)
        existing = acct["positions"].get(symbol)
        trade = {"time": now_iso(), "symbol": symbol, "name": name, "action": action,
                 "quantity": executed, "price": float(price), "reason": reason}

        if action == "buy":
            cost = price * executed
            if cash < cost:
                max_qty = int(cash // price) if price > 0 else 0
                if max_qty <= 0:
                    trade.update(quantity=0, status="skipped", cash_balance=round(cash, 2),
                                 reason=(f"{reason} | 잔고 부족으로 미체결 "
                                         f"(필요 {cost:,.0f}원 / 부족 {cost - cash:,.0f}원 / 가용현금 {cash:,.0f}원)"))
                    self._append_order(trade)
                    return trade
                executed = max_qty
        elif action == "sell":
            held = int(existing["quantity"]) if existing else 0
            if held <= 0:
                trade.update(quantity=0, status="skipped", cash_balance=round(cash, 2),
                             reason=f"{reason} | 보유 수량 없음")
                self._append_order(trade)
                return trade
            executed = min(executed, held)
        else:
            raise ValueError(f"알 수 없는 주문 방향: {action}")

        if action == "buy":
            if existing:
                new_qty = int(existing["quantity"]) + executed
                existing["avg_price"] = (float(existing["avg_price"]) * int(existing["quantity"])
                                         + price * executed) / new_qty
                existing["quantity"] = new_qty
                existing["name"] = existing.get("name") or name
            else:
                acct["positions"][symbol] = {"name": name, "quantity": executed, "avg_price": float(price)}
            cash -= price * executed
        else:
            new_qty = max(0, int(existing["quantity"]) - executed)
            if new_qty == 0:
                acct["positions"].pop(symbol, None)
            else:
                existing["quantity"] = new_qty
            cash += price * executed

        acct["cash_balance"] = cash
        trade.update(quantity=executed, status="filled", cash_balance=round(cash, 2))
        self._append_order(trade)
        return trade

    def _append_order(self, trade: dict) -> None:
        orders = self.account["orders"]
        orders.append(trade)
        if len(orders) > MAX_ORDERS:
            del orders[:-MAX_ORDERS]
        self.save()

    # ── 사이클 로그 ─────────────────────────────────────────────────────
    def append_cycle(self, cycle_log: dict) -> None:
        cycles = self.account["cycles"]
        cycles.append(cycle_log)
        if len(cycles) > MAX_CYCLES:
            del cycles[:-MAX_CYCLES]
        self.save()

    def last_cycle(self) -> dict | None:
        cycles = self.account["cycles"]
        return cycles[-1] if cycles else None
