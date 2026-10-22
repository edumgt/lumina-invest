"""KIS 모의계좌 목표 보유수량 맞추기 — fd `fin-ai-app` 컨테이너 안에서 실행된다(deploy/fd-kis-paper-trade.sh 가 stdin 으로 넣는다).

사용: python - [--execute] [--add]
  (기본)     dry-run: 잔고·보유·현재가·주문 계획만 출력, 주문 없음
  --execute  계획대로 주문 실행(화면 수동 주문과 같은 경로 auto_trade.place_manual_kis_order, origin=manual, 사용자 SYSTEM)
  --add      목표를 '현재 보유 + 수량' 으로 해석(기본은 '보유를 그 수량으로 맞춤': 부족분 매수, 초과분 매도)

규칙(6-59 교훈): 1회 주문 1천만 원 한도로 분할, 같은 종목·같은 분(分)의 client_order_id 중복을 피하려 분할 건 사이에 분이 바뀔 때까지 대기,
주문마다 새 DB 세션 사용(한 세션에서 예외가 나면 이후 전부 실패). 비상 정지(kill switch)면 place_manual_kis_order 가 거절한다.
"""
import asyncio
import json
import sys
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
TARGETS = [  # (6자리 코드, 이름, 목표 수량)
    ("005380", "현대차", 8),
    ("005930", "삼성전자", 86),
    ("329180", "HD현대중공업", 1),
    ("128940", "한미약품", 2),
]
PER_ORDER_LIMIT_KRW = 10_000_000
EXECUTE = "--execute" in sys.argv
ADDITIVE = "--add" in sys.argv


async def main() -> int:
    from app.database.postgres import connect_postgres, close_postgres, get_session_factory
    from app.services.brokers import stock_coin_trade_gateway as gateway
    from app.services.stock import get_quote
    from app.services import auto_trade
    from app.models.base import SYSTEM_USER_ID

    if not gateway.is_configured():
        print("게이트웨이 미설정 — 중단"); return 2
    env = gateway.environment()
    if env != "paper":
        print(f"환경이 {env} (paper 아님) — 중단"); return 2
    print(f"[{datetime.now(KST):%Y-%m-%d %H:%M:%S} KST] environment={env} market_open={gateway.is_krx_market_open()} mode={'EXECUTE' if EXECUTE else 'DRY-RUN'} target={'add' if ADDITIVE else 'absolute'}")

    balance = await gateway.get_balance()
    cash = float(balance.get("cashBalance") or 0)
    held = {str(h.get("symbol")): int(h.get("quantity") or 0) for h in balance.get("holdings", [])}
    print(f"예수금 {cash:,.0f}원 · 총평가 {float(balance.get('totalEvalAmount') or 0):,.0f}원 · 보유 {len(held)}종목")
    for h in balance.get("holdings", []):
        print(f"  보유 {h.get('symbol')} {h.get('name')} {h.get('quantity')}주 @ {h.get('currentPrice')}")

    plan: list[tuple[str, str, str, int, float]] = []  # (code, name, side, qty, price)
    need_cash = 0.0
    for code, name, want in TARGETS:
        q = await get_quote(f"{code}.KS")
        price = float(q.get("price") or 0)
        if price <= 0:
            print(f"  {name}({code}) 현재가 조회 실패 — 이 종목은 건너뜀"); continue
        cur = held.get(code, 0)
        delta = want if ADDITIVE else want - cur
        side = "buy" if delta > 0 else "sell"
        qty = abs(delta)
        print(f"  {name}({code}) 현재가 {price:,.0f} · 보유 {cur} → 목표 {cur + delta if ADDITIVE else want} · {side.upper()} {qty}주 (약 {price * qty:,.0f}원)")
        if qty == 0:
            continue
        chunk = max(1, int(PER_ORDER_LIMIT_KRW // price))
        while qty > 0:
            n = min(qty, chunk); plan.append((code, name, side, n, price)); qty -= n
        if side == "buy":
            need_cash += price * abs(delta)
    print(f"주문 계획 {len(plan)}건 · 매수 필요금액 약 {need_cash:,.0f}원 · 예수금 {cash:,.0f}원{'  ※ 예수금 부족 — 게이트웨이가 409 로 거절할 수 있음' if need_cash > cash else ''}")
    for i, (code, name, side, n, price) in enumerate(plan, 1):
        print(f"  #{i} {side.upper()} {name}({code}) {n}주 @ {price:,.0f} ≈ {price * n:,.0f}원")
    if not EXECUTE:
        print("dry-run 종료 — 실행하려면 --execute"); return 0
    if not plan:
        print("할 주문 없음"); return 0

    await connect_postgres()
    factory = get_session_factory()
    results = []
    last_minute: dict[tuple[str, str], str] = {}
    try:
        for i, (code, name, side, n, price) in enumerate(plan, 1):
            key = (code, side)
            while datetime.now(KST).strftime("%Y%m%d%H%M") == last_minute.get(key):  # 같은 종목·방향은 분이 바뀐 뒤(멱등키 중복 방지)
                await asyncio.sleep(2)
            last_minute[key] = datetime.now(KST).strftime("%Y%m%d%H%M")
            async with factory() as db:
                try:
                    r = await auto_trade.place_manual_kis_order(db, str(SYSTEM_USER_ID), f"{code}.KS", name, side, n,
                                                                client_ip="deploy/fd-kis-paper-trade", user_agent="agent-script")
                except auto_trade.ManualOrderBlocked as e:
                    r = {"status": "blocked", "reason": e.reason, "error": e.message}
                except Exception as e:  # noqa: BLE001
                    r = {"status": "exception", "error": repr(e)}
            summary = {k: r.get(k) for k in ("status", "order_no", "client_order_id", "code", "reason", "error")}
            print(f"  #{i} {side.upper()} {name} {n}주 → {json.dumps(summary, ensure_ascii=False)}")
            results.append((code, name, side, n, r))
            if r.get("status") in ("blocked",):
                print("  주문 거절(비상 정지 등) — 나머지 중단"); break
    finally:
        await close_postgres()
    ok = sum(1 for *_, r in results if r.get("status") == "submitted")
    print(f"완료: 접수 {ok}/{len(plan)}. 체결은 kis-monitor 화면 또는 confirm_fills 로 확인(모의투자는 보유수량 변화로 추정).")
    return 0 if ok == len(plan) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
