"""유니버스(반도체·IT·K뷰티 31종목)와 5분 자동매매 주기의 정합성."""
import re
from collections import Counter

from app.services.stock import QUANT_STOCKS, QUANT_SECTORS
from app.celery_app import celery_app
from app.services import auto_trade, kis_quickstart
from app.services.brokers.stock_coin_trade_gateway import normalize_symbol


def test_universe_three_sectors_about_thirty():
    assert set(QUANT_SECTORS) == {"반도체", "IT", "K뷰티"}
    assert 28 <= len(QUANT_STOCKS) <= 35
    by_sector = Counter(s["sector"] for s in QUANT_STOCKS)
    assert set(by_sector) == set(QUANT_SECTORS)
    assert all(v >= 8 for v in by_sector.values()), by_sector


def test_universe_symbols_unique_and_krx_format():
    symbols = [s["symbol"] for s in QUANT_STOCKS]
    assert len(symbols) == len(set(symbols))
    for sym in symbols:
        assert re.fullmatch(r"\d{6}\.(KS|KQ)", sym), sym
        assert normalize_symbol(sym) == sym[:6]     # 게이트웨이는 6자리 코드로 보낸다
    assert all(s["name"] for s in QUANT_STOCKS)


def test_universe_contains_core_names():
    names = {s["name"] for s in QUANT_STOCKS}
    assert {"삼성전자", "SK하이닉스", "NAVER", "카카오", "아모레퍼시픽", "LG생활건강"} <= names


def test_auto_trade_cycle_is_three_minutes():
    """2026-10-07: 5분 → 3분. 스케줄·_INTERVAL_SEC·expires 가 QUANT_CYCLE_SEC(기본 180) 하나를 따른다."""
    from app.config import settings
    assert settings.QUANT_CYCLE_SEC == 180
    entry = celery_app.conf.beat_schedule["quant-auto-trade-cycle"]
    assert entry["task"] == "quant.auto_trade_cycle" and entry["schedule"] == 180.0
    assert entry["options"]["expires"] < 180
    assert auto_trade._INTERVAL_SEC == 180
    assert "quant-auto-trade-5min" not in celery_app.conf.beat_schedule
    assert "quant-auto-trade-10min" not in celery_app.conf.beat_schedule


def test_cycle_task_time_limit_fits_in_period():
    import app.tasks.sync_tasks  # noqa: F401  태스크 등록
    task = celery_app.tasks["quant.auto_trade_cycle"]
    assert task.time_limit is not None and 60 <= task.time_limit < 180


def test_quickstart_reports_three_minute_interval():
    assert kis_quickstart.interval_min() == 3
    assert "interval_min()" in __import__("inspect").getsource(kis_quickstart.start)


def test_health_reports_cycle_sec():
    import asyncio
    from app.routes.health import health
    assert asyncio.run(health())["quant"]["cycle_sec"] == 180
