"""자동매매 위험관리(Risk Guard) — Redis 대신 state.json 의 risk 섹션을 쓴다.

app/services/risk_guard.py 와 같은 규칙:
  - 중복 주문 방지 : 같은 종목·방향 주문을 쿨다운 안에 다시 내지 않는다
  - 일 주문 수 한도: 하루 자동 주문 건수 상한(KST 날짜 기준)
  - 종목 비중 한도 : 매수 후 종목 평가액이 총자산의 N%를 넘지 않도록 수량 축소/생략
  - 일손실 한도    : 당일 시작 자산 대비 손실률이 한도를 넘으면 비상 정지(kill switch)
  - 비상 정지      : 켜져 있으면 사이클이 주문을 전혀 내지 않는다
"""
from __future__ import annotations

import time
from dataclasses import dataclass, asdict, replace
from datetime import datetime, timedelta, timezone

from .config import Settings
from .store import Store

KST = timezone(timedelta(hours=9))


def today_key(now: datetime | None = None) -> str:
    """거래일 키(KST 기준 날짜)."""
    return (now or datetime.now(timezone.utc)).astimezone(KST).strftime("%Y%m%d")


@dataclass
class RiskLimits:
    daily_loss_limit_pct: float = 3.0
    max_position_pct: float = 30.0
    max_orders_per_day: int = 20
    cooldown_min: int = 30
    kill_switch: bool = False

    @classmethod
    def from_settings(cls, settings: Settings, store: Store) -> "RiskLimits":
        return cls(
            daily_loss_limit_pct=float(settings.risk_daily_loss_limit_pct or 0),
            max_position_pct=float(settings.risk_max_position_pct or 0),
            max_orders_per_day=int(settings.risk_max_orders_per_day or 0),
            cooldown_min=int(settings.risk_cooldown_min or 0),
            kill_switch=bool(store.risk.get("kill_switch")),
        )

    def with_aggressive(self, settings: Settings) -> "RiskLimits":
        """공격 모드는 쿨다운·일 주문 수만 덮어쓴다. 비중·일손실·비상정지는 유지."""
        return replace(self,
                       cooldown_min=max(0, int(settings.aggressive_cooldown_min)),
                       max_orders_per_day=max(0, int(settings.aggressive_max_orders_per_day)))

    def to_dict(self) -> dict:
        return asdict(self)


# ── 중복 주문 방지(쿨다운) ──────────────────────────────────────────────
def _slot_key(symbol: str, side: str) -> str:
    return f"{symbol}:{side}"


def acquire_order_slot(store: Store, symbol: str, side: str, cooldown_min: int) -> bool:
    """쿨다운 안에 같은 종목·방향 주문이 없었으면 슬롯을 잡고 True, 있으면 False."""
    if cooldown_min <= 0:
        return True
    slots: dict = store.risk.setdefault("slots", {})
    now = time.time()
    for key, expire_at in list(slots.items()):      # 만료 슬롯 청소
        if float(expire_at) <= now:
            slots.pop(key, None)
    key = _slot_key(symbol, side)
    if float(slots.get(key, 0)) > now:
        return False
    slots[key] = now + cooldown_min * 60
    store.save()
    return True


def release_order_slot(store: Store, symbol: str, side: str) -> None:
    """체결 실패 시 슬롯 반납(다음 사이클에 재시도 가능)."""
    store.risk.setdefault("slots", {}).pop(_slot_key(symbol, side), None)
    store.save()


# ── 일 주문 수 ──────────────────────────────────────────────────────────
def orders_today(store: Store) -> int:
    return int(store.risk.setdefault("orders_count", {}).get(today_key(), 0))


def increment_orders_today(store: Store) -> int:
    counts: dict = store.risk.setdefault("orders_count", {})
    key = today_key()
    counts[key] = int(counts.get(key, 0)) + 1
    for old in [k for k in counts if k < key][:-5]:   # 최근 며칠만 남긴다
        counts.pop(old, None)
    store.save()
    return counts[key]


# ── 일손실 한도 ─────────────────────────────────────────────────────────
def day_start_equity(store: Store, current_equity: float) -> float:
    """당일 첫 호출 시 현재 자산을 '시작 자산'으로 고정하고 이후 그 값을 돌려준다."""
    values: dict = store.risk.setdefault("day_start_equity", {})
    key = today_key()
    if key not in values:
        values[key] = round(float(current_equity), 2)
        for old in [k for k in values if k < key][:-5]:
            values.pop(old, None)
        store.save()
    return float(values[key])


def daily_pnl_pct(start_equity: float, current_equity: float) -> float:
    if start_equity <= 0:
        return 0.0
    return round((current_equity / start_equity - 1) * 100, 3)


def daily_loss_breached(start_equity: float, current_equity: float, limit_pct: float) -> bool:
    if limit_pct <= 0:
        return False
    return daily_pnl_pct(start_equity, current_equity) <= -abs(limit_pct)


# ── 종목 비중 한도 ──────────────────────────────────────────────────────
def cap_buy_quantity(qty: int, price: float, existing_value: float, total_equity: float,
                     max_position_pct: float) -> tuple[int, str | None]:
    """매수 후 종목 평가액이 total_equity × max_position_pct 를 넘지 않도록 수량을 줄인다.

    Returns (허용 수량, 조정 사유 또는 None). 0이면 매수 생략.
    """
    if max_position_pct <= 0 or total_equity <= 0 or price <= 0:
        return qty, None
    cap_value = total_equity * max_position_pct / 100
    room = cap_value - existing_value
    if room <= 0:
        return 0, f"종목 비중 한도 {max_position_pct:.0f}% 도달 (현재 {existing_value / total_equity * 100:.1f}%)"
    allowed = int(room // price)
    if allowed >= qty:
        return qty, None
    if allowed <= 0:
        return 0, f"종목 비중 한도 {max_position_pct:.0f}%로 1주도 추가 매수 불가"
    return allowed, f"종목 비중 한도 {max_position_pct:.0f}%로 수량 {qty}→{allowed}주 축소"


# ── 비상 정지 ───────────────────────────────────────────────────────────
def halt(store: Store, reason: str) -> None:
    store.risk["kill_switch"] = True
    store.risk["halt_reason"] = reason[:300]
    store.risk["halted_at"] = datetime.now(timezone.utc).isoformat()
    store.save()


def resume(store: Store) -> None:
    store.risk["kill_switch"] = False
    store.risk["halt_reason"] = ""
    store.risk.pop("halted_at", None)
    store.save()


def status(store: Store, limits: RiskLimits, current_equity: float | None) -> dict:
    start = day_start_equity(store, current_equity) if current_equity is not None else None
    pnl = daily_pnl_pct(start, current_equity) if (start and current_equity is not None) else None
    used = orders_today(store)
    return {
        "limits": limits.to_dict(),
        "kill_switch": limits.kill_switch,
        "halt_reason": store.risk.get("halt_reason", ""),
        "date": today_key(),
        "day_start_equity": round(start, 2) if start else None,
        "current_equity": round(current_equity, 2) if current_equity is not None else None,
        "day_pnl_pct": pnl,
        "daily_loss_breached": bool(start and current_equity is not None
                                    and daily_loss_breached(start, current_equity, limits.daily_loss_limit_pct)),
        "orders_today": used,
        "orders_remaining": max(0, limits.max_orders_per_day - used) if limits.max_orders_per_day > 0 else None,
    }
