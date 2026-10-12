"""confirm_live_fills 의 호출량·예산 회귀 테스트 (2026-10-12 자동화 정지 사고).

사고: st 가 KIS 호출을 전역 1.05초 간격으로 직렬화하는데 confirm_live_fills 가
 ① 주문번호 없는 행마다 당일 주문목록을 새로 조회(N+1 — 주문 7건에 목록 호출 35회)
 ② 루프 끝에서 한 번만 commit
해서 task time_limit(100초)에 SIGKILL 되면 그 회차의 갱신이 전부 사라졌다 → 열린 주문이
영원히 닫히지 않고 다음 회차 조회량이 더 늘어나는 악순환.

여기서 검증하는 것: 목록 조회는 회차당 1회, 예산 초과 시 중단하고 커밋한다.
"""
import asyncio
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from app.models import LiveOrder
from app.services import auto_trade

KST = timezone(timedelta(hours=9))
UID = uuid.UUID("0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0")


class FakeResult:
    def __init__(self, rows): self._rows = rows
    def scalars(self): return self
    def all(self): return list(self._rows)


class FakeDb:
    def __init__(self, rows): self.rows = rows; self.commits = 0
    def add(self, obj): pass
    async def commit(self): self.commits += 1
    async def execute(self, *_a, **_k): return FakeResult(self.rows)


def _factory(db):
    class Factory:
        def __call__(self): return self
        async def __aenter__(self): return db
        async def __aexit__(self, *_a): return False
    return Factory()


def _pending_row(i: int) -> LiveOrder:
    """주문번호를 못 받은(PENDING) 행 — 당일 목록 추정 매칭 경로를 탄다."""
    return LiveOrder(user_id=UID, symbol=f"00{i:04d}.KS", side="BUY", quantity=1, price=1000.0,
                     status="PENDING", environment="paper", order_type="LIMIT",
                     client_order_id=f"cid-{i}", order_no=None,
                     created_at=datetime(2026, 10, 12, 10, 0, tzinfo=KST))


def _open_row(i: int) -> LiveOrder:
    """주문번호를 받은 열린 행 — 건별 체결 조회 경로를 탄다."""
    return LiveOrder(user_id=UID, symbol=f"00{i:04d}.KS", side="BUY", quantity=1, price=1000.0,
                     status="ACCEPTED", environment="paper", order_type="LIMIT",
                     client_order_id=f"cid-{i}", order_no=f"000000{i:04d}",
                     created_at=datetime(2026, 10, 12, 10, 0, tzinfo=KST))


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_BASE_URL", "https://sct.test")
    monkeypatch.setattr(settings, "STOCK_COIN_TRADE_API_KEY", "key")
    with patch.object(auto_trade.notification, "notify_order_filled", AsyncMock()), \
         patch.object(auto_trade.notification, "notify_order_error", AsyncMock()):
        yield


def test_today_orders_fetched_once_per_run():
    """PENDING 행이 여러 개여도 당일 주문목록 조회는 1회여야 한다(사고 전에는 행마다 1회)."""
    rows = [_pending_row(i) for i in range(8)]
    db = FakeDb(rows)
    calls = {"n": 0}

    async def fake_list(env=None, open_only=False):
        calls["n"] += 1
        return []                      # 매칭 후보 없음 → 각 행은 skipped

    with patch.object(auto_trade.gateway, "list_today_orders", side_effect=fake_list), \
         patch.object(auto_trade.gateway, "is_configured", return_value=True), \
         patch.object(auto_trade, "get_session_factory", return_value=_factory(db)):
        summary = asyncio.run(auto_trade.confirm_live_fills())

    assert calls["n"] == 1, f"당일 주문목록 조회가 {calls['n']}회 — 회차당 1회여야 한다"
    assert summary["checked"] == 8
    assert summary["skipped"] == 8
    assert db.commits == 1


def test_budget_exceeded_commits_partial_progress():
    """예산을 넘기면 남은 행을 다음 회차로 넘기고, 그때까지의 진행을 커밋한다.

    st 의 직렬화 때문에 주문당 조회가 느린 상황을 모사한다(실측 8초/건). 예산이 없으면
    task time_limit 에 SIGKILL 돼 루프 끝의 commit 이 날아가고 진행이 0 이 된다.
    """
    rows = [_open_row(i) for i in range(20)]
    db = FakeDb(rows)
    seen = {"n": 0}

    async def slow_status(order_no, env=None):
        seen["n"] += 1
        await asyncio.sleep(0.2)                       # st 직렬화로 느린 건별 조회
        return {"status": "ACCEPTED", "filledQuantity": 0, "avgFilledPrice": 0}

    with patch.object(settings, "CONFIRM_FILLS_BUDGET_SEC", 1.0), \
         patch.object(auto_trade.gateway, "get_order_status", side_effect=slow_status), \
         patch.object(auto_trade.gateway, "is_configured", return_value=True), \
         patch.object(auto_trade, "get_session_factory", return_value=_factory(db)):
        summary = asyncio.run(auto_trade.confirm_live_fills())

    assert summary["checked"] < 20, f"예산을 넘겼는데 {summary['checked']}건을 다 돌았다"
    assert summary.get("deferred", 0) > 0, "남은 행이 deferred 로 보고되지 않았다"
    assert summary["checked"] + summary["deferred"] == 20
    assert db.commits == 1, "중단 후에도 커밋이 일어나야 부분 진행이 남는다"
