"""risk_guard 경계: 쿨다운 만료 직전·직후, 일 주문 수 한도 도달, kill switch 행 매핑, 실전 주문의 Redis 폴백 차단."""
import asyncio
import time
from types import SimpleNamespace

from app.services import risk_guard as rg


def test_cooldown_expiry_boundary():
    rg._mem_keys.clear()
    assert asyncio.run(rg.acquire_order_slot("u", "005930.KS", "buy", 30))
    key = "quant:order-slot:u:005930.KS:buy"
    rg._mem_keys[key] = time.time() + 0.5          # 만료 직전 → 여전히 중복
    assert not asyncio.run(rg.acquire_order_slot("u", "005930.KS", "buy", 30))
    rg._mem_keys[key] = time.time() - 0.01         # 만료 직후 → 허용
    assert asyncio.run(rg.acquire_order_slot("u", "005930.KS", "buy", 30))
    assert asyncio.run(rg.acquire_order_slot("u", "005930.KS", "buy", 0))   # 쿨다운 0 = 비활성


def test_daily_order_counter_reaches_limit():
    rg._mem_counters.clear()
    limits = rg.RiskLimits(max_orders_per_day=2)
    assert asyncio.run(rg.orders_today("u2")) == 0
    for expected in (1, 2):
        assert asyncio.run(rg.increment_orders_today("u2")) == expected
    assert asyncio.run(rg.orders_today("u2")) >= limits.max_orders_per_day   # 사이클의 _risk_gate 가 이 비교로 차단


def test_risk_limits_from_row_and_kill_switch():
    row = SimpleNamespace(risk_daily_loss_limit_pct=2.5, risk_max_position_pct=20, risk_max_orders_per_day=5,
                          risk_cooldown_min=10, risk_kill_switch=True)
    limits = rg.RiskLimits.from_row(row)
    assert limits.kill_switch is True and limits.max_orders_per_day == 5 and limits.cooldown_min == 10
    assert rg.RiskLimits.from_row(None) == rg.RiskLimits()


def test_real_order_skipped_when_risk_store_is_memory_fallback(monkeypatch):
    import uuid
    from unittest.mock import patch

    from app.config import settings
    from app.services import auto_trade
    from app.services.brokers import stock_coin_trade_gateway as gw

    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_BASE_URL", "https://sct.test")
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_API_KEY", "k")
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_KIS_ENVIRONMENT", "real")
    broker = SimpleNamespace(quant_mode="live", broker="kis", app_key="", app_secret="", account_no="")
    with patch.object(gw, "is_krx_market_open", return_value=True), patch.object(rg, "_redis", return_value=None):
        result = asyncio.run(auto_trade._place_live_order(broker, "005930.KS", "삼성전자", "buy", 1, 70_000.0, str(uuid.uuid4()), db=None))
    assert result["status"] == "skipped" and result["reason"] == "risk_store_unavailable"
