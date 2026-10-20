"""주기 자동매매 Agentic AI(QUANT_CYCLE_SEC, 기본 3분) - PostgreSQL 기반.

실행 모델
  - 활성 여부는 BrokerSettings.quant_auto_enabled(DB)에 저장한다. 앱 재시작·다중 인스턴스에서도 상태가 유지된다.
  - 주기 실행은 Celery Beat(`quant.auto_trade_cycle`, QUANT_CYCLE_SEC 기본 3분)이 활성 사용자 전원에 대해 run_cycle_for_enabled_users()를 돌린다.
  - 시작 시에는 즉시 1회 사이클을 백그라운드로 실행해 화면 반응을 준다(인프로세스 루프는 더 이상 쓰지 않는다).
  - 사이클 로그는 data_cache(`quant:cycle_log:{uid}`)에 최근 50개를 남겨 API 프로세스와 워커가 공유한다.
"""
import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.stock import get_quant_indicators, QUANT_STOCKS
from app.database.postgres import get_session_factory
from app.models import BrokerSettings, Order, Portfolio, QuantVirtualAccount, PORTFOLIO_BOOK_QUANT, LiveOrder, LIVE_ORDER_OPEN_STATUSES
from app.models.base import SYSTEM_USER_ID
from app.services import notification
from app.services import strategy_loader
from app.services.quant_ai_scores import get_batch_training_scores
from app.config import settings as app_settings
from datetime import timedelta
from app.services import risk_guard
from app.services.audit import audit
from app.services.brokers.factory import get_broker_client
from app.services import kis_credentials
from app.services.brokers import stock_coin_trade_gateway as gateway
from app.services.data_cache import cache_get, cache_set
from app.services import aggressive_mode, signal_mode
from app.services import strategy_settings

logger = logging.getLogger(__name__)

_auto_trade_task: asyncio.Task | None = None
_trade_log: list[dict] = []
_is_running = False
_auto_trade_user_id = "quant_system"
_INTERVAL_SEC = int(app_settings.QUANT_CYCLE_SEC)   # celery_app.beat_schedule 과 같은 값(기본 180 = 3분)
_INITIAL_CAPITAL = 10_000_000
_last_risk: dict = {}          # 마지막 사이클의 위험관리 상태 (status 응답용)


def _resolve_user_id(raw: str) -> uuid.UUID:
    if raw == "quant_system" or not raw:
        return SYSTEM_USER_ID
    return uuid.UUID(raw)


def _log_key(uid: uuid.UUID) -> str:
    return f"quant:cycle_log:{uid}"


async def _persist_cycle(uid: uuid.UUID, cycle_log: dict) -> None:
    """사이클 로그를 프로세스 메모리와 data_cache 양쪽에 남긴다 (워커↔API 공유)."""
    global _trade_log, _last_risk
    _trade_log.append(cycle_log)
    if len(_trade_log) > 100:
        _trade_log = _trade_log[-100:]
    _last_risk = cycle_log.get("risk") or _last_risk
    try:
        cached = await cache_get(_log_key(uid), max_age_hours=24 * 30) or {}
        cycles = (cached.get("cycles") or [])[-49:] + [cycle_log]
        await cache_set(_log_key(uid), {"cycles": cycles})
    except Exception:
        logger.exception("사이클 로그 저장 실패 user=%s", uid)


async def is_enabled(db: AsyncSession, uid: uuid.UUID) -> bool:
    row = (await db.execute(select(BrokerSettings.quant_auto_enabled).where(BrokerSettings.user_id == uid))).scalar_one_or_none()
    return bool(row)


async def set_enabled(db: AsyncSession, uid: uuid.UUID, enabled: bool) -> None:
    row = (await db.execute(select(BrokerSettings).where(BrokerSettings.user_id == uid))).scalar_one_or_none()
    if row is None:
        row = BrokerSettings(user_id=uid)
        db.add(row)
    row.quant_auto_enabled = enabled
    await db.commit()


def _signal_basis(indicators: dict) -> dict:
    """판단 근거가 어느 시장 데이터에서 나왔는지 — 의사결정 화면 「판단 근거」 카드에 그대로 표시한다.

    source: kis | yahoo, interval: 5m|1d, bars: 사용한 봉 수, as_of: 마지막 봉 시각(epoch).
    """
    return {
        "source":   indicators.get("source"),
        "interval": indicators.get("interval"),
        "bars":     indicators.get("bars"),
        "as_of":    indicators.get("as_of"),
        "price_source": indicators.get("price_source"),
    }


async def get_status(db: AsyncSession, uid: uuid.UUID) -> dict:
    """DB 플래그 + 공유 캐시 로그 기반 상태 (프로세스에 무관)."""
    enabled = await is_enabled(db, uid)
    cached = await cache_get(_log_key(uid), max_age_hours=24 * 30) or {}
    cycles = cached.get("cycles") or list(_trade_log[-50:])
    last_risk = next((c.get("risk") for c in reversed(cycles) if c.get("risk")), {})
    return {
        "running":      enabled,
        "scheduler":    f"celery-beat ({max(1, _INTERVAL_SEC // 60)}분)",
        "user_id":      str(uid),
        "interval_sec": _INTERVAL_SEC,
        "risk":         last_risk,
        "log":          cycles[-50:],
        "last_cycle_at": cycles[-1]["time"] if cycles else None,
    }


def is_running() -> bool:
    """(하위 호환) 인프로세스 즉시 실행 태스크가 돌고 있는지."""
    return _is_running


async def _execute_virtual_trade(
    db: AsyncSession,
    user_id: uuid.UUID,
    symbol: str,
    name: str,
    action: str,
    price: float,
    quantity: int,
    reason: str,
) -> dict:
    """가상계좌 체결. 주문 삽입 + 포트폴리오 갱신 + 현금 갱신을 한 트랜잭션으로 커밋한다."""
    now = datetime.now(timezone.utc).isoformat()

    result = await db.execute(select(QuantVirtualAccount).where(QuantVirtualAccount.user_id == user_id))
    account = result.scalar_one_or_none()
    if not account:
        account = QuantVirtualAccount(
            user_id=user_id, initial_capital=float(_INITIAL_CAPITAL), cash_balance=float(_INITIAL_CAPITAL),
        )
        db.add(account)
        await db.flush()

    cash_balance = account.cash_balance
    executed_quantity = quantity

    if action == "buy":
        cost = price * quantity
        if cash_balance < cost:
            max_qty = int(cash_balance // price) if price > 0 else 0
            if max_qty <= 0:
                shortfall = cost - cash_balance
                await db.commit()
                return {
                    "time": now, "symbol": symbol, "name": name,
                    "action": action, "quantity": 0, "price": price,
                    "reason": (
                        f"{reason} | 잔고 부족으로 미체결 "
                        f"(필요 {cost:,.0f}원 / 부족 {shortfall:,.0f}원 / 가용현금 {cash_balance:,.0f}원)"
                    ),
                    "status": "skipped",
                    "cash_balance": round(cash_balance, 2),
                }
            executed_quantity = max_qty

    port_result = await db.execute(select(Portfolio).where(Portfolio.user_id == user_id, Portfolio.symbol == symbol, Portfolio.book == PORTFOLIO_BOOK_QUANT))
    existing = port_result.scalar_one_or_none()

    if action == "sell":
        if not existing or existing.quantity <= 0:
            await db.commit()
            return {
                "time": now, "symbol": symbol, "name": name,
                "action": action, "quantity": 0, "price": price,
                "reason": f"{reason} | 보유 수량 없음",
                "status": "skipped",
                "cash_balance": round(cash_balance, 2),
            }
        executed_quantity = min(quantity, existing.quantity)

    db.add(Order(
        user_id=user_id, symbol=symbol, name=name, order_type=action,
        quantity=executed_quantity, price=price, status="filled", broker="quant_ai", source="QUANT",
    ))

    if action == "buy":
        if existing:
            new_qty = existing.quantity + executed_quantity
            existing.avg_price = (existing.avg_price * existing.quantity + price * executed_quantity) / new_qty
            existing.quantity = new_qty
        else:
            db.add(Portfolio(user_id=user_id, symbol=symbol, name=name, quantity=executed_quantity, avg_price=price, book=PORTFOLIO_BOOK_QUANT))
        cash_balance -= price * executed_quantity
    elif action == "sell":
        new_qty = max(0, existing.quantity - executed_quantity)
        if new_qty == 0:
            await db.delete(existing)
        else:
            existing.quantity = new_qty
        cash_balance += price * executed_quantity

    account.cash_balance = cash_balance
    await db.commit()

    return {
        "time": now, "symbol": symbol, "name": name,
        "action": action, "quantity": executed_quantity, "price": price, "reason": reason,
        "status": "filled",
        "cash_balance": round(cash_balance, 2),
    }


async def _place_live_order(
    broker_row: BrokerSettings | None,
    symbol: str,
    name: str,
    side: str,
    quantity: int,
    price: float,
    user_id: str,
    db: AsyncSession | None = None,
) -> dict | None:
    """실전(live) 모드에서 가상계좌 체결과 별도로 실제 증권사에 주문을 전송한다.

    가상계좌 기록(포트폴리오/현금)은 앱 대시보드 표시용으로 항상 남기고,
    이 함수는 그 위에 실제 브로커 주문을 얹는다. 브로커 미승인/오류 시에도
    자동매매 사이클 자체는 계속 진행되도록 예외를 여기서 흡수한다.
    """
    if not broker_row or broker_row.quant_mode != "live":
        return None
    broker = (broker_row.broker or "mock").strip().lower()
    if broker == "kis" and gateway.is_configured():
        # 구축안 경로: stock-coin-trade Open API(승인 토큰 → 주문 → 감사로그) 경유. 체결은 quant.confirm_fills 가 확인한다.
        return await _place_live_order_via_gateway(db, broker_row, symbol, name, side, quantity, price, user_id)
    paper = False
    if kis_credentials.is_managed(broker):
        # KIS 자격증명은 서버(Secrets Manager)가 관리한다. DB 행의 키는 쓰지 않는다.
        creds = await kis_credentials.get_credentials()
        if creds is None:
            logger.warning("KIS 자격증명 미연동 — live 주문 생략 (%s %s)", side, symbol)
            return {"status": "skipped", "broker": broker, "reason": "kis_credentials_not_configured"}
        app_key, app_secret, account_no, paper = creds.app_key, creds.app_secret, creds.account_no, creds.paper
    else:
        app_key = broker_row.app_key
        app_secret = broker_row.app_secret
        account_no = broker_row.account_no
    if broker == "mock" or not app_key or not app_secret or not account_no:
        return None

    client = get_broker_client(broker, app_key, app_secret, paper=paper)
    try:
        result = await client.place_order(account_no, symbol, side, quantity, price)
        await notification.notify_order_placed(
            symbol=symbol, side=side, quantity=quantity, price=price, user_id=user_id,
        )
        return {"status": "submitted", "broker": broker, "response": result}
    except Exception as e:
        logger.warning("실전 자동매매 주문 실패 (%s, %s %s): %s", broker, side, symbol, e)
        await notification.notify_order_error(
            symbol=symbol, side=side, quantity=quantity, price=price,
            error=str(e), user_id=user_id,
        )
        return {"status": "error", "broker": broker, "error": str(e)}


_SIGNAL_SCORE_SCALE = 8.0   # stock.py 지표 점수(대략 -8~+8)를 스펙 임계값([-1,1])과 비교하기 위한 정규화 분모


def apply_strategy_spec_to_symbols(target_symbols: list[str], spec: dict, strict: bool = False) -> list[str]:
    """스펙 universe(6자리 코드)에 있는 종목만 남기고 position_sizing.max_symbols 로 자른다.

    교집합이 없으면 기본은 원본 유지(전략이 종목 선정 화면 선택을 완전히 무효화하지 않음). strict=True(전략설정
    「유니버스 엄격 적용」)면 빈 목록을 돌려줘 백테스트하지 않은 종목에 규칙이 걸리지 않게 한다.
    """
    universe = {str(c)[:6] for c in (spec.get("universe") or [])}
    restricted = [s for s in target_symbols if str(s)[:6] in universe] if universe else list(target_symbols)
    if not restricted:
        return [] if (strict and universe) else list(target_symbols)
    max_symbols = int((spec.get("position_sizing") or {}).get("max_symbols") or 0)
    return restricted[:max_symbols] if max_symbols > 0 else restricted


_ml_meta: dict = {}   # 마지막으로 로드한 배치 학습 메타(generated_at 등) — 사이클 로그에 기록


async def symbol_ml_score(symbol: str) -> float | None:
    """배치 점수가 없는 종목의 보조 ML 점수: 캔들로 Ridge 5일 수익률 예측(ml_symbol_score). 실패 시 None."""
    try:
        from app.services.stock import get_candles
        from app.services.ml_symbol_score import symbol_score

        data = await get_candles(symbol, period="2y")
        return symbol_score(data.get("candles") or [])
    except Exception as exc:
        logger.info("종목별 ML 점수 계산 생략 (%s): %s", symbol, exc)
        return None


async def ml_scores_by_symbol() -> dict[str, float]:
    """SageMaker 배치 학습 예측(연수익률 %)을 [-1,1]로 정규화한 종목별 점수. 없으면 빈 dict (기술지표만 사용)."""
    try:
        data = await get_batch_training_scores()
    except Exception:
        return {}
    _ml_meta.clear()
    if data:
        _ml_meta.update(generated_at=data.get("generated_at"), model=data.get("model") or "lightgbm-batch")
    scale = float(getattr(app_settings, "ML_SCORE_SCALE_PCT", 30.0) or 30.0)
    out: dict[str, float] = {}
    for symbol, row in ((data or {}).get("scores") or {}).items():
        try:
            pred = float((row or {}).get("pred_ann_return_pct"))
        except (TypeError, ValueError):
            continue
        normalized = max(-1.0, min(1.0, pred / scale))
        out[str(symbol)] = normalized
        out[str(symbol)[:6]] = normalized   # 005930.KS ↔ 005930 둘 다로 조회 가능
    return out


def _sma_last(values: list[float], window: int, offset: int = 0) -> float | None:
    """values[-1-offset] 기준 단순이동평균. 데이터 부족이면 None."""
    end = len(values) - offset
    if window <= 0 or end - window < 0:
        return None
    seg = values[end - window:end]
    return sum(seg) / window


def evaluate_spec_rules(spec: dict, indicators: dict) -> dict | None:
    """스펙 entry/exit 규칙(ma_cross / momentum / buy_hold / dca)을 지표의 종가 시계열로 직접 평가한다.

    반환 {"entry": bool, "exit": bool, "detail": str} 또는 평가 불가(지원하지 않는 indicator·데이터 부족)면 None.
    """
    closes = [float(c) for c in (indicators.get("closes") or []) if c is not None]
    if not closes:
        return None

    def _eval(rule: dict) -> bool | None:
        ind = str(rule.get("indicator") or "")
        cond = str(rule.get("condition") or "")
        params = rule.get("params") or {}
        if cond == "always":
            return True
        if cond == "never":
            return False
        if ind == "ma_cross":
            s, l = int(params.get("short_window", 5)), int(params.get("long_window", 20))
            ss, ll = _sma_last(closes, s), _sma_last(closes, l)
            if ss is None or ll is None:
                return None
            return ss > ll if cond == "short_above_long" else ss < ll if cond == "short_below_long" else None
        if ind == "momentum":
            w = int(params.get("breakout_window", 20))
            if len(closes) < w + 1:
                return None
            window = closes[-w - 1:-1]
            return closes[-1] > max(window) if cond == "breakout_high" else closes[-1] < min(window) if cond == "breakdown_low" else None
        return None

    entry, exit_ = _eval(spec.get("entry") or {}), _eval(spec.get("exit") or {})
    if entry is None and exit_ is None:
        return None
    detail = f"규칙 {((spec.get('entry') or {}).get('indicator'))}: 진입={entry} 청산={exit_}"
    return {"entry": bool(entry), "exit": bool(exit_), "detail": detail}


def apply_strategy_spec_to_signal(signal: dict, spec: dict, ml_score: float | None = None, indicators: dict | None = None) -> dict:
    """지표 점수를 [-1,1]로 정규화하고(LightGBM 점수가 있으면 스펙 가중 합산) buy/sell 임계값으로 action 을 다시 판정한다."""
    weights = spec.get("signal_weights") or {}
    buy_th = float(weights.get("buy_threshold", 0.6))
    sell_th = float(weights.get("sell_threshold", -0.6))
    w_tech = float(weights.get("technical", 1.0))
    w_ml = float(weights.get("lightgbm", 0.0))
    score = float(signal.get("score", 0) or 0)
    technical = max(-1.0, min(1.0, score / _SIGNAL_SCORE_SCALE))
    if ml_score is not None and w_ml > 0 and (w_tech + w_ml) > 0:
        normalized = (w_tech * technical + w_ml * max(-1.0, min(1.0, float(ml_score)))) / (w_tech + w_ml)
        ml_tag = f", ML {float(ml_score):+.2f}×{w_ml:g} + 지표 {technical:+.2f}×{w_tech:g}"
    else:
        normalized = technical
        ml_tag = "" if w_ml <= 0 else " (ML 점수 없음 → 지표만)"
    normalized = round(max(-1.0, min(1.0, normalized)), 4)
    rules = evaluate_spec_rules(spec, indicators) if indicators else None
    if normalized >= buy_th:
        action = "강력 매수" if normalized >= min(1.0, buy_th + 0.25) else "매수"
    elif normalized <= sell_th:
        action = "강력 매도" if normalized <= max(-1.0, sell_th - 0.25) else "매도"
    else:
        action = "관망"
    tag = f"전략 {spec.get('strategy_id')} v{spec.get('version')}: 정규화 점수 {normalized:+.2f} (매수≥{buy_th:+.2f} / 매도≤{sell_th:+.2f}){ml_tag}"
    reasons = [*(signal.get("reasons") or []), tag]
    if rules is not None:
        # 스펙의 entry/exit 규칙을 직접 평가할 수 있으면 규칙이 action 을 결정한다(청산 우선). 임계값 점수는 참고로만 남긴다.
        action = "매도" if rules["exit"] else "매수" if rules["entry"] else "관망"
        reasons.append(rules["detail"] + " → " + action)
    return {**signal, "action": action, "normalized_score": normalized, "rule_based": rules is not None, "reasons": reasons}


async def _place_live_order_via_gateway(
    db: AsyncSession | None,
    broker_row: BrokerSettings,
    symbol: str,
    name: str,
    side: str,
    quantity: int,
    price: float,
    user_id: str,
    origin: str = "",
    client_ip: str = "",
    user_agent: str = "",
) -> dict:
    """stock-coin-trade 게이트웨이로 KIS 주문을 내고 live_orders 에 추적 행을 남긴다.

    origin: batch_auto(배치 봇) | user_auto(사용자 계정 자동매매) | manual(사람·화면) | rebalance. 비우면 user_id 로 추정한다.
    client_ip/user_agent 는 사람이 낸 주문(manual·rebalance)에서만 채운다 — 모니터 그리드에 표시.

    실패해도 예외를 올리지 않는다(사이클 지속). 가상계좌 체결은 이미 끝났으므로 쿨다운 슬롯은 반납하지 않는다 —
    반납하면 다음 사이클에 가상 포지션이 중복으로 쌓인다. 실패는 알림 + live_orders.status=ERROR 로 남긴다.
    """
    uid = _resolve_user_id(user_id)
    origin = origin or ("batch_auto" if uid == SYSTEM_USER_ID else "user_auto")
    env = gateway.environment()
    if gateway.enforce_market_hours() and not gateway.is_krx_market_open():
        logger.info("장 운영시간 외 — 실주문 생략 (%s %s x%d)", side, symbol, quantity)
        return {"status": "skipped", "broker": "kis", "via": "stock-coin-trade", "environment": env, "reason": "market_closed"}
    if env == "real" and risk_guard._redis() is None:
        # 쿨다운·일 주문 수가 메모리 폴백(프로세스 재시작 시 초기화) 상태면 실전 주문은 내지 않는다. 모의(paper)는 허용.
        logger.warning("Redis 미연결(위험관리 메모리 폴백) — 실전 주문 생략 (%s %s x%d)", side, symbol, quantity)
        return {"status": "skipped", "broker": "kis", "via": "stock-coin-trade", "environment": env, "reason": "risk_store_unavailable"}
    try:
        client_order_id = gateway.make_client_order_id(str(uid), symbol, side)
    except gateway.GatewayError as e:
        return {"status": "error", "broker": "kis", "via": "stock-coin-trade", "error": str(e)}

    row: LiveOrder | None = None
    if db is not None:
        row = LiveOrder(
            user_id=uid, client_order_id=client_order_id, environment=env, broker="kis",
            symbol=symbol, name=name, side=side.upper(), order_type=_live_order_type(),
            quantity=quantity, price=float(price), status="PENDING",
            origin=origin[:20], client_ip=(client_ip or "")[:64], user_agent=(user_agent or "")[:200],
        )
        db.add(row)
        await db.commit()

    try:
        result = await gateway.place_order(symbol, side, quantity, price, client_order_id=client_order_id, order_type=_live_order_type())
    except gateway.GatewayError as e:
        logger.warning("게이트웨이 실주문 실패 (%s %s x%d): [%s] %s", side, symbol, quantity, e.code, e)
        if row is not None:
            # UNREACHABLE 은 KIS가 받았을 수도 있으므로 UNKNOWN 으로 두고 confirm_fills 가 clientOrderId 로 확인한다.
            row.status = "UNKNOWN" if e.code == "GATEWAY_UNREACHABLE" else "ERROR"
            row.message = f"[{e.code}] {e}"[:300]
            row.raw = json.dumps(e.details, ensure_ascii=False)[:4000]
            await db.commit()
        await notification.notify_order_error(symbol=symbol, side=side, quantity=quantity, price=price, error=f"[{e.code}] {e}", user_id=user_id)
        return {"status": "error", "broker": "kis", "via": "stock-coin-trade", "environment": env, "code": e.code, "error": str(e), "client_order_id": client_order_id}

    order = result["order"]
    if row is not None:
        row.status = str(order.get("status") or "ACCEPTED")
        row.order_no = str(order.get("orderNo") or "")
        row.price = float(result["intent"].get("price") or price)
        row.message = str(order.get("message") or "")[:300]
        row.raw = json.dumps(order, ensure_ascii=False)[:4000]
        await db.commit()
    await notification.notify_order_placed(symbol=symbol, side=side, quantity=quantity, price=float(result["intent"].get("price") or price),
                                           broker=f"KIS({env}) via stock-coin-trade", user_id=user_id)
    return {"status": "submitted", "broker": "kis", "via": "stock-coin-trade", "environment": env,
            "order_no": order.get("orderNo"), "client_order_id": client_order_id, "duplicate": result["duplicate"], "response": order}


MANUAL_ORDER_MAX_QTY = 10_000


class ManualOrderBlocked(Exception):
    """화면에서 낸 주문을 막은 이유. message 는 사용자에게 그대로 보여 준다."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


async def place_manual_kis_order(db: AsyncSession, user_id: str, symbol: str, name: str,
                                 side: str, quantity: int, price: float | None = None,
                                 client_ip: str = "", user_agent: str = "") -> dict:
    """사용자가 화면에서 직접 낸 KIS 모의투자 주문.

    자동매매 사이클과 **같은 경로**(stock-coin-trade 게이트웨이 → live_orders 추적행 → 알림)를 쓴다.
    다만 가상계좌 체결은 만들지 않는다 — 사이클이 만든 포지션과 섞이면 성과 집계가 어긋난다.
    실전(real) 환경과 비상 정지 상태에서는 내지 않는다. 가격을 안 주면 현재가를 서버에서 조회한다
    (클라이언트가 보낸 가격을 그대로 믿지 않는다).
    """
    from app.services import kis_quickstart   # 지연 import (순환 방지)
    from app.services.stock import get_quote

    side = str(side or "").strip().lower()
    if side not in ("buy", "sell"):
        raise ManualOrderBlocked("bad_side", "매수 또는 매도만 가능합니다.")
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise ManualOrderBlocked("bad_quantity", "수량은 정수여야 합니다.")
    if not 1 <= quantity <= MANUAL_ORDER_MAX_QTY:
        raise ManualOrderBlocked("bad_quantity", f"수량은 1~{MANUAL_ORDER_MAX_QTY:,}주 사이여야 합니다.")

    route = await kis_quickstart.resolve_route()
    if not route.configured:
        raise ManualOrderBlocked("not_connected", f"KIS 연동이 되어 있지 않습니다 — {route.detail}")
    if route.environment != "paper":
        raise ManualOrderBlocked("real_environment", "KIS 경로가 실전(real)이라 화면에서 내는 주문을 막습니다. 모의(Testbed)에서만 가능합니다.")

    uid = _resolve_user_id(user_id)
    broker_row = (await db.execute(select(BrokerSettings).where(BrokerSettings.user_id == uid))).scalar_one_or_none()
    if broker_row and broker_row.risk_kill_switch:
        raise ManualOrderBlocked("kill_switch",
                                 f"비상 정지 상태입니다. 자동매매 현황에서 해제하세요. (사유: {broker_row.risk_halt_reason or '수동 정지'})")

    if price is None:
        quote = await get_quote(symbol)
        price = quote.get("price")
    if not price or float(price) <= 0:
        raise ManualOrderBlocked("no_price", "현재가를 가져오지 못해 주문하지 않았습니다. 잠시 후 다시 시도하세요.")
    price = float(price)

    if gateway.is_configured():
        result = await _place_live_order_via_gateway(db, broker_row, symbol, name or symbol, side, quantity, price, str(uid),
                                                     origin="manual", client_ip=client_ip, user_agent=user_agent)
    else:
        creds = await kis_credentials.get_credentials()
        if creds is None:
            raise ManualOrderBlocked("not_connected", "KIS 자격증명을 가져오지 못했습니다.")
        client = get_broker_client("kis", creds.app_key, creds.app_secret, paper=creds.paper)
        try:
            response = await client.place_order(creds.account_no, symbol, side, quantity, price)
        except Exception as exc:
            logger.warning("수동 KIS 주문 실패 (%s %s x%d): %s", side, symbol, quantity, exc)
            await notification.notify_order_error(symbol=symbol, side=side, quantity=quantity, price=price,
                                                  error=str(exc), user_id=str(uid))
            result = {"status": "error", "broker": "kis", "via": "kis-direct", "environment": route.environment, "error": str(exc)}
        else:
            await notification.notify_order_placed(symbol=symbol, side=side, quantity=quantity, price=price,
                                                   broker=f"KIS({route.environment})", user_id=str(uid))
            result = {"status": "submitted", "broker": "kis", "via": "kis-direct",
                      "environment": route.environment, "response": response}

    await audit(str(uid), "", "order.manual_kis", {
        "symbol": symbol, "side": side, "quantity": quantity, "price": price,
        "via": result.get("via"), "status": result.get("status"), "environment": result.get("environment"),
        "client_ip": client_ip, "user_agent": (user_agent or "")[:200],
    })
    return {**result, "symbol": symbol, "name": name or symbol, "side": side,
            "quantity": quantity, "price": price, "amount": round(price * quantity)}


def _live_order_type() -> str:
    """게이트웨이 주문 유형: 공격 모드면 QUANT_AGGRESSIVE_ORDER_TYPE(기본 MARKET), 아니면 env 기본값."""
    if aggressive_mode.is_enabled():
        ot = aggressive_mode.order_type()
        if ot:
            return ot
    return gateway.default_order_type()


async def open_live_order_exposure(db: AsyncSession, uid: uuid.UUID) -> dict[str, float]:
    """미체결 매수 실주문의 (잔여수량 × 주문가) 합을 종목별로 돌려준다."""
    result = await db.execute(
        select(LiveOrder).where(LiveOrder.user_id == uid, LiveOrder.side == "BUY", LiveOrder.status.in_(LIVE_ORDER_OPEN_STATUSES))
    )
    exposure: dict[str, float] = {}
    for row in result.scalars().all():
        remaining = max(0, int(row.quantity) - int(row.filled_quantity or 0))
        if remaining and row.price:
            exposure[row.symbol] = exposure.get(row.symbol, 0.0) + remaining * float(row.price)
    return exposure


async def live_account_daily_loss(user_id: str, limit_pct: float) -> dict:
    """게이트웨이 잔고의 총평가액으로 실계좌 당일 손익률을 계산한다. 조회 실패 시 breached=False 로 사이클을 막지 않는다."""
    env = gateway.environment()
    try:
        balance = await gateway.get_balance()
    except gateway.GatewayError as e:
        logger.warning("실계좌 잔고 조회 실패 — 실계좌 일손실 점검 생략: [%s] %s", e.code, e)
        return {"environment": env, "error": f"[{e.code}] {e}", "breached": False}
    equity = float(balance.get("totalEvalAmount") or 0) or float(balance.get("cashBalance") or 0)
    if equity <= 0:
        return {"environment": env, "error": "평가액 0", "breached": False}
    start = await risk_guard.day_start_equity(f"{user_id}:live:{env}", equity)
    pnl = risk_guard.daily_pnl_pct(start, equity)
    return {"environment": env, "day_start_equity": round(start, 2), "equity": round(equity, 2), "day_pnl_pct": pnl,
            "breached": risk_guard.daily_loss_breached(start, equity, limit_pct)}


async def cancel_open_live_orders(db: AsyncSession, uid: uuid.UUID, reason: str = "") -> dict:
    """사용자의 미체결 실주문(ACCEPTED/PARTIALLY_FILLED)을 게이트웨이로 전량 취소 요청한다. kill switch 에서 호출."""
    summary = {"requested": 0, "failed": 0, "skipped": 0}
    if not gateway.is_configured():
        summary["skipped"] = -1
        return summary
    result = await db.execute(
        select(LiveOrder).where(LiveOrder.user_id == uid, LiveOrder.status.in_(("ACCEPTED", "PARTIALLY_FILLED")))
    )
    for row in result.scalars().all():
        if not row.order_no:
            summary["skipped"] += 1
            continue
        try:
            latest = await gateway.cancel_order(row.order_no, env=row.environment)
            row.status = str(latest.get("status") or "CANCEL_REQUESTED")
            row.message = (f"비상 정지 취소: {reason}" if reason else "비상 정지 취소")[:300]
            summary["requested"] += 1
        except gateway.GatewayError as e:
            row.message = f"취소 실패 [{e.code}] {e}"[:300]
            summary["failed"] += 1
            logger.warning("비상 정지 취소 실패 (%s): [%s] %s", row.client_order_id, e.code, e)
    await db.commit()
    return summary


async def confirm_live_fills(limit: int = 100) -> dict:
    """열린 live_orders 를 게이트웨이 체결 조회로 갱신한다. Celery `quant.confirm_fills`(2분)가 호출."""
    summary = {"checked": 0, "updated": 0, "filled": 0, "errors": 0, "skipped": 0}
    if not gateway.is_configured():
        summary["skipped"] = -1
        return summary
    session_factory = get_session_factory()
    # st 는 KIS 호출을 전역 1.05초 간격으로 직렬화한다. 이 루프가 time_limit(100초) 에 걸려 SIGKILL 되면
    # 루프 끝의 db.commit() 이 실행되지 않아 **그 회차의 갱신이 전부 버려진다** → 열린 주문이 영원히 안 닫히고
    # 다음 회차의 조회량이 더 늘어난다(2026-10-12 자동화 정지 사고). 소프트 데드라인에서 멈추고 커밋해
    # 부분 진행이라도 남긴다. 마지막 확인 시각(updated_at) 순으로 순환해 미확정 과거 주문이 뒤쪽 주문을 굶기지 않는다.
    deadline = asyncio.get_running_loop().time() + max(1.0, float(getattr(app_settings, "CONFIRM_FILLS_BUDGET_SEC", 75.0) or 75.0))
    # 당일 주문 목록은 회차당 1회만 받는다. 예전에는 주문번호 없는 행마다 새로 조회해(N+1) 주문 7건에
    # 전체목록 호출 35회가 나갔다.
    today_orders: dict[str, list[dict[str, Any]]] = {}

    async def _today_orders(env: str | None) -> list[dict[str, Any]]:
        key = str(env or "")
        if key not in today_orders:
            today_orders[key] = await gateway.list_today_orders(env=env)
        return today_orders[key]

    async with session_factory() as db:
        result = await db.execute(
            select(LiveOrder).where(LiveOrder.status.in_(LIVE_ORDER_OPEN_STATUSES)).order_by(LiveOrder.updated_at, LiveOrder.created_at, LiveOrder.id).limit(limit)
        )
        rows = list(result.scalars().all())
        for row in rows:
            if asyncio.get_running_loop().time() > deadline:
                summary["deferred"] = len(rows) - summary["checked"]
                logger.warning("confirm_live_fills 예산 초과 — %d건을 다음 회차로 넘긴다", summary["deferred"])
                break
            summary["checked"] += 1
            # 상태가 같거나 조회가 실패해도 확인 시각을 남겨 다음 회차는 나머지 주문부터 조회한다.
            row.updated_at = datetime.now(timezone.utc)
            try:
                if row.order_no:
                    latest = await gateway.get_order_status(row.order_no, env=row.environment)
                elif row.status == "UNKNOWN" and row.client_order_id:
                    # 응답을 못 받은 주문: 계약서의 멱등키(clientOrderId)로 **같은 intent 를 재전송**한다.
                    # st 가 원 주문을 기록했으면 새 주문 없이 저장된 order 를 duplicate=true 로 돌려주고,
                    # 원 요청이 st 에 닿지 않았으면 이번에 접수된다(최대 1회 실행 보장). 시간 창을 넘기면 LOST 로 종료해 비중 점유를 푼다.
                    window = int(getattr(app_settings, "STOCK_COIN_TRADE_UNKNOWN_RESUBMIT_MIN", 10) or 0)
                    created = row.created_at.astimezone(timezone.utc) if row.created_at else datetime.now(timezone.utc)
                    age_min = (datetime.now(timezone.utc) - created).total_seconds() / 60
                    if window <= 0 or age_min > window:
                        row.status = "LOST"
                        row.message = f"응답 미수신 {age_min:.0f}분 경과 — 재전송 창({window}분) 초과로 종료. KIS 앱/st 기록에서 clientOrderId 로 수동 확인 필요"[:300]
                        summary["lost"] = summary.get("lost", 0) + 1
                        summary["updated"] += 1
                        logger.warning("UNKNOWN 주문 LOST 처리 (%s %s x%d, %s)", row.side, row.symbol, row.quantity, row.client_order_id)
                        await notification.notify_order_error(symbol=row.symbol, side=row.side.lower(), quantity=row.quantity, price=row.price,
                                                              error=row.message, user_id=str(row.user_id))
                        continue
                    if gateway.enforce_market_hours() and not gateway.is_krx_market_open():
                        summary["skipped"] += 1
                        continue
                    res = await gateway.place_order(row.symbol, row.side, row.quantity, row.price, client_order_id=row.client_order_id,
                                                    order_type=row.order_type, env=row.environment)
                    latest = res.get("order") or {}
                    row.order_no = str(latest.get("orderNo") or "") or None
                    row.message = ("응답 미수신 → 멱등 재전송: " + ("기존 주문 확인(duplicate)" if res.get("duplicate") else "이번에 신규 접수"))[:300]
                    summary["resubmitted"] = summary.get("resubmitted", 0) + 1
                    if not latest:
                        summary["skipped"] += 1
                        continue
                else:
                    # 주문번호를 못 받은(PENDING) 건: 당일 목록에서 같은 종목·방향·수량으로 추정 매칭
                    code = gateway.normalize_symbol(row.symbol)
                    candidates = [o for o in await _today_orders(row.environment)
                                  if o.get("symbol") == code and o.get("side") == row.side and int(o.get("orderedQuantity") or 0) == row.quantity]
                    if not candidates:
                        summary["skipped"] += 1
                        continue
                    latest = candidates[-1]
                    row.order_no = str(latest.get("orderNo") or "")
            except gateway.GatewayError as e:
                summary["errors"] += 1
                logger.warning("체결 확인 실패 (%s): [%s] %s", row.client_order_id, e.code, e)
                continue
            new_status = str(latest.get("status") or row.status)
            changed = new_status != row.status or int(latest.get("filledQuantity") or 0) != row.filled_quantity
            row.status = new_status
            row.filled_quantity = int(latest.get("filledQuantity") or 0)
            row.avg_filled_price = float(latest.get("avgFilledPrice") or 0)
            row.raw = json.dumps(latest, ensure_ascii=False)[:4000]
            if changed:
                summary["updated"] += 1
            if changed and new_status == "FILLED":
                summary["filled"] += 1
                # 가상계좌 체결가(row.price) 대비 실체결 괴리(슬리피지). 매수는 +가 불리, 매도는 -가 불리.
                if row.price and row.avg_filled_price:
                    slip = (row.avg_filled_price / row.price - 1) * 100
                    row.message = f"체결 완료 · 슬리피지 {slip:+.3f}% (가상 {row.price:,.0f} → 실체결 {row.avg_filled_price:,.0f})"[:300]
                    summary.setdefault("slippage_pct", []).append(round(slip, 3))
                await notification.notify_order_filled(
                    symbol=row.symbol, side=row.side.lower(), quantity=row.filled_quantity,
                    price=row.avg_filled_price, environment=row.environment, user_id=str(row.user_id),
                )
            # 미체결 N분 경과 → 취소 요청 (설정 0이면 끔)
            cancel_after = int(getattr(app_settings, "STOCK_COIN_TRADE_CANCEL_OPEN_AFTER_MIN", 0) or 0)
            if cancel_after > 0 and new_status in ("ACCEPTED", "PARTIALLY_FILLED") and row.order_no and row.created_at:
                age = datetime.now(timezone.utc) - row.created_at.astimezone(timezone.utc)
                if age >= timedelta(minutes=cancel_after):
                    try:
                        cancelled = await gateway.cancel_order(row.order_no, env=row.environment)
                        row.status = str(cancelled.get("status") or "CANCEL_REQUESTED")
                        row.message = f"{cancel_after}분 미체결 자동 취소 요청"[:300]
                        summary["cancelled"] = summary.get("cancelled", 0) + 1
                    except gateway.GatewayError as e:
                        summary["errors"] += 1
                        logger.warning("미체결 자동 취소 실패 (%s): [%s] %s", row.client_order_id, e.code, e)
        await db.commit()
    return summary


async def _equity_snapshot(db: AsyncSession, uid: uuid.UUID, price_map: dict[str, float]) -> tuple[float, float, dict[str, float]]:
    """(현금, 총자산, 종목별 평가액)."""
    acc = (await db.execute(select(QuantVirtualAccount).where(QuantVirtualAccount.user_id == uid))).scalar_one_or_none()
    cash = float(acc.cash_balance) if acc else float(_INITIAL_CAPITAL)
    values: dict[str, float] = {}
    for p in (await db.execute(select(Portfolio).where(Portfolio.user_id == uid, Portfolio.book == PORTFOLIO_BOOK_QUANT))).scalars().all():
        if p.quantity > 0:
            values[p.symbol] = p.quantity * float(price_map.get(p.symbol, p.avg_price))
    return cash, cash + sum(values.values()), values


async def emergency_halt(db: AsyncSession, broker_row: BrokerSettings | None, user_id: str, reason: str,
                         day_pnl_pct: float | None = None) -> None:
    """비상 정지: kill switch를 DB에 켜고 루프를 멈추고 알림을 보낸다."""
    if broker_row is not None:
        broker_row.risk_kill_switch = True
        broker_row.risk_halt_reason = reason[:300]
        broker_row.quant_auto_enabled = False
        await db.commit()
    stop_auto_trade()
    try:
        cancelled = await cancel_open_live_orders(db, _resolve_user_id(user_id), reason)
        if cancelled.get("requested") or cancelled.get("failed"):
            logger.warning("비상 정지 — 미체결 실주문 취소 요청 %s", cancelled)
    except Exception as exc:  # 취소 실패가 비상 정지 자체를 막으면 안 된다
        logger.exception("비상 정지 중 미체결 취소 실패: %s", exc)
    await notification.notify_risk_halt(reason, day_pnl_pct, user_id=user_id)
    await audit(user_id, "", "auto_trade.emergency_halt", {"reason": reason, "day_pnl_pct": day_pnl_pct})
    logger.warning("자동매매 비상 정지 user=%s: %s", user_id, reason)


async def _run_quant_cycle(user_id: str = "quant_system") -> None:
    global _trade_log, _last_risk
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    cycle_log: dict = {"time": now_str, "trades": [], "signals": []}

    try:
        session_factory = get_session_factory()
    except Exception:
        return  # PostgreSQL 미연결 시 스킵

    uid = _resolve_user_id(user_id)
    # 전략설정 화면 값(data_cache) 을 settings 에 반영 — 워커 재시작 없이 다음 사이클부터 적용된다
    settings_doc = await strategy_settings.load_and_apply()
    profile = strategy_settings.current_profile(settings_doc) or {}

    async with session_factory() as db:
        # 가상계좌 idempotent 초기화
        result = await db.execute(select(QuantVirtualAccount).where(QuantVirtualAccount.user_id == uid))
        if not result.scalar_one_or_none():
            db.add(QuantVirtualAccount(
                user_id=uid, initial_capital=float(_INITIAL_CAPITAL), cash_balance=float(_INITIAL_CAPITAL),
            ))
            await db.commit()

        bs_result = await db.execute(select(BrokerSettings).where(BrokerSettings.user_id == uid))
        broker_row = bs_result.scalar_one_or_none()

        mode = broker_row.quant_mode if broker_row and broker_row.quant_mode in ("paper", "live") else (
            "live" if broker_row and broker_row.paper is False else "paper"
        )
        symbol_source = broker_row.quant_symbol_source if broker_row and broker_row.quant_symbol_source in ("ai", "manual") else "ai"
        selected_symbols = list(broker_row.quant_selected_symbols or []) if broker_row else []
        ai_top_n = max(1, min(int(broker_row.quant_ai_top_n if broker_row else 3), len(QUANT_STOCKS)))
        per_trade_budget = float(broker_row.quant_per_trade_budget if broker_row else 1_000_000)
        per_trade_budget = max(10_000.0, min(per_trade_budget, 10_000_000.0))
        buy_ratio = float(broker_row.quant_buy_ratio if broker_row else 1.0)
        buy_ratio = max(0.1, min(buy_ratio, 1.0))
        sell_ratio = float(broker_row.quant_sell_ratio if broker_row else 0.5)
        sell_ratio = max(0.1, min(sell_ratio, 1.0))

        limits = risk_guard.RiskLimits.from_row(broker_row)
        aggressive = aggressive_mode.is_enabled()
        # 운영 모드 3분할(전략설정): aggressive / backtest / custom. is_enabled() 가 공격이면 그대로, 아니면 명시 모드 → 이전 호환.
        _sid = (broker_row.quant_strategy_id if broker_row else "") or ""
        signal_mode_name = signal_mode.resolve(_sid)
        if aggressive:
            signal_mode_name = signal_mode.AGGRESSIVE
        elif signal_mode_name == signal_mode.AGGRESSIVE:   # 공격 플래그만 따로 꺼진 경우(테스트·env)
            signal_mode_name = signal_mode.BACKTEST if _sid else signal_mode.CUSTOM
        if aggressive:
            limits = aggressive_mode.apply_limits(limits)   # 쿨다운·일 주문 수만 덮어씀
        if limits.kill_switch:
            cycle_log["risk"] = {"halted": True, "reason": (broker_row.risk_halt_reason if broker_row else "") or "비상 정지 스위치 ON"}
            cycle_log["note"] = "비상 정지 상태 — 주문을 내지 않습니다."
            await _persist_cycle(uid, cycle_log)
            await set_enabled(db, uid, False)   # 비상 정지 상태면 자동매매 플래그도 내린다
            return

        price_map: dict[str, float] = {}
        indicator_map: dict[str, dict] = {}
        from app.services.korea_market_scan import candidate_stocks
        from app.services.korea_universe import refresh_master
        await refresh_master()
        held = (await db.execute(select(Portfolio).where(Portfolio.user_id == uid, Portfolio.book == PORTFOLIO_BOOK_QUANT, Portfolio.quantity > 0))).scalars().all()
        held_symbols = {p.symbol for p in held}
        approved = None
        # Expanding analysis/paper scope does not authorize new real-order symbols.
        if mode == "live" and not app_settings.QUANT_KOREA_ALLOW_LIVE_EXPANSION:
            from pathlib import Path
            import json
            approved = set(json.loads((Path(__file__).resolve().parents[1] / 'data' / 'legacy-trading-symbols.json').read_text()))
            cycle_log['live_expansion_blocked'] = True
        candidates = await candidate_stocks(allowed_symbols=approved)
        selected = set(selected_symbols) if symbol_source == "manual" else {s['symbol'] for s in candidates}
        if approved is not None:
            selected &= approved
        cycle_stocks = [s for s in QUANT_STOCKS if s['symbol'] in selected | held_symbols]
        known = {s['symbol'] for s in cycle_stocks}
        cycle_stocks += [{'symbol':p.symbol,'name':getattr(p,'name',None) or p.symbol,'sector':'기존 보유','buy_eligible':False} for p in held if p.symbol not in known]
        cycle_log['universe_count'] = len(QUANT_STOCKS)
        cycle_log['candidate_count'] = len(candidates)
        stock_map = {s["symbol"]: s for s in QUANT_STOCKS}

        # 후보·보유종목만 선조회 — 전종목 분석은 배치 스냅샷에서 수행한다. st 는 KIS 호출을 전역 1.05초 간격으로 직렬화하므로
        # 종목마다 KIS_CHART_TIMEOUT 을 통째로 소진하고(31종목 × 10초 = 310초) 사이클이 하드 타임리밋
        # (165초 = 주기 180초 − 15초, 중복 실행 방지 장치)에 걸려 죽는다 → 주문이 한 건도 나가지 않는다.
        # 동시에 보내면 st 처리량은 그대로여도 한 사이클이 약 31 × 1.05초 ≈ 35초에 전부 받는다.
        # st 의 외부 읽기 동시 제한은 12슬롯이고 초과 시 즉시 503 이므로(ExternalReadLimitMiddleware)
        # 다른 호출자 몫을 남겨 그보다 낮게 묶는다.
        _sem = asyncio.Semaphore(max(1, int(getattr(app_settings, "QUANT_INDICATOR_CONCURRENCY", 6) or 6)))

        async def _indicators_for(symbol: str) -> dict:
            async with _sem:
                return (await aggressive_mode.get_intraday_indicators(symbol) if aggressive
                        else await get_quant_indicators(symbol, "2y"))

        _prefetched = dict(zip(
            [s["symbol"] for s in cycle_stocks],
            await asyncio.gather(*(_indicators_for(s["symbol"]) for s in cycle_stocks), return_exceptions=True),
        ))

        for stock in cycle_stocks:
            try:
                indicators = _prefetched.get(stock["symbol"])
                if isinstance(indicators, BaseException):
                    raise indicators            # 종목별 예외 처리는 아래 except 로 그대로 넘긴다
                if indicators is None:
                    indicators = await _indicators_for(stock["symbol"])
                indicator_map[stock["symbol"]] = indicators
                signal = indicators.get("signal", {})
                price = indicators.get("current_price")
                if not price:
                    continue
                price_map[stock["symbol"]] = float(price)

            except Exception:
                logger.exception("자동매매 지표 계산 실패: %s", stock["symbol"])
                # 시장 데이터를 못 받은 종목은 판단하지 않는다 — 화면에서 '관망' 으로 보이면 근거 없는 판단이 된다.
                cycle_log["signals"].append({
                    "symbol": stock["symbol"], "name": stock["name"], "error": "지표 계산 실패",
                    "action": "판단 불가", "score": 0,
                    "reasons": ["시장 데이터를 받지 못해 판단하지 않았습니다"], "basis": {},
                })

        if symbol_source == "manual":
            target_symbols = [s for s in selected_symbols if s in selected and s in stock_map and stock_map[s].get("buy_eligible", True) and stock_map[s].get("order_supported", True)]
        else:
            ranked = []
            for stock in cycle_stocks:
                if stock["symbol"] not in selected or not stock.get("buy_eligible", True):
                    continue
                indicators = indicator_map.get(stock["symbol"], {})
                sig = indicators.get("signal", {})
                score = sig.get("score", 0)
                ranked.append((stock["symbol"], score))
            ranked.sort(key=lambda item: item[1], reverse=True)
            pool = max(ai_top_n, 2 * int(app_settings.QUANT_AGGRESSIVE_MAX_BUYS_PER_CYCLE), 5) if aggressive else ai_top_n
            target_symbols = [sym for sym, _ in ranked[:pool]]

        strategy_spec: dict | None = None
        ml_scores: dict[str, float] = {}
        ml_weight_on = False
        strategy_log = {"id": "", "version": 0, "applied": False, "mode": signal_mode_name}
        if broker_row and broker_row.quant_strategy_id and signal_mode_name == signal_mode.CUSTOM:
            # ③ 사용자 설정: 전략이 선택돼 있어도 스펙을 쓰지 않는다 (화면에 그렇게 안내)
            strategy_log.update(id=broker_row.quant_strategy_id, version=broker_row.quant_strategy_version or 0, ignored="사용자 설정 모드 — 전략 스펙 미적용")
        elif broker_row and broker_row.quant_strategy_id:
            strategy_log.update(id=broker_row.quant_strategy_id, version=broker_row.quant_strategy_version or 0)
            strategy_spec = await strategy_loader.get_strategy(broker_row.quant_strategy_id, broker_row.quant_strategy_version or None)
            if strategy_spec:
                strict = bool(getattr(app_settings, "QUANT_SPEC_UNIVERSE_STRICT", False)) and signal_mode_name == signal_mode.BACKTEST
                target_symbols = apply_strategy_spec_to_symbols(target_symbols, strategy_spec, strict=strict)
                if strict and not target_symbols:
                    strategy_log["universe_empty"] = True
                    cycle_log["note"] = "스펙 universe 와 거래 대상의 교집합이 없어 매수를 보류합니다(유니버스 엄격 적용 ON)."
                strategy_log.update(version=int(strategy_spec.get("version") or strategy_log["version"]), applied=True)
                if float((strategy_spec.get("signal_weights") or {}).get("lightgbm", 0) or 0) > 0:
                    ml_weight_on = True
                    ml_scores = await ml_scores_by_symbol()
                    strategy_log["ml_scores_loaded"] = bool(ml_scores)
                    strategy_log["ml_generated_at"] = _ml_meta.get("generated_at")
                    strategy_log["ml_model"] = _ml_meta.get("model")
            else:
                strategy_log["error"] = "domain-rag-lab 에서 스펙을 받지 못해 기본 규칙 사용"
        if signal_mode_name == signal_mode.BACKTEST and not strategy_spec:
            # ② 백테스트 기반 규칙인데 합격 스펙이 없으면 기본 규칙으로 '대신' 사지 않는다 — 청산(익절·손절)만 돌고 매수는 보류
            strategy_log["error"] = strategy_log.get("error") or "백테스트 기반 규칙 모드인데 선택된 합격 전략 스펙이 없음"
            cycle_log["note"] = "백테스트 기반 규칙 모드 — 합격 전략 스펙이 없어 이번 사이클 매수를 보류합니다(청산 규칙만 동작)."
            target_symbols = []

        # ── 공격 모드: 보유분 점검 + 이번 사이클 매수/매도 계획 ──
        forced_action: dict[str, dict] = {}
        if aggressive:
            holdings: dict[str, tuple[int, float]] = {}
            for p in (await db.execute(select(Portfolio).where(Portfolio.user_id == uid, Portfolio.book == PORTFOLIO_BOOK_QUANT))).scalars().all():
                if p.quantity > 0:
                    holdings[p.symbol] = (int(p.quantity), float(p.avg_price or 0))
            ag_plan = aggressive_mode.plan(indicator_map, target_symbols, holdings, price_map)
            for sym in ag_plan["buy"]:
                forced_action[sym] = {"action": "매수", "ratio": None, "note": "공격 모드 매수"}
            for sym, info in ag_plan["sell"].items():
                forced_action[sym] = {"action": "매도", "ratio": info["ratio"], "note": info["reason"]}
            for sym in list(forced_action):
                if sym not in target_symbols and sym in stock_map:
                    target_symbols.append(sym)
            cycle_log["aggressive"] = {"buy": ag_plan["buy"], "sell": {k: v["reason"] for k, v in ag_plan["sell"].items()},
                                       "ranked": ag_plan["ranked"][:10], "notes": ag_plan["notes"],
                                       "interval": app_settings.QUANT_AGGRESSIVE_CANDLE_INTERVAL,
                                       "take_profit_pct": app_settings.QUANT_AGGRESSIVE_TAKE_PROFIT_PCT,
                                       "stop_loss_pct": app_settings.QUANT_AGGRESSIVE_STOP_LOSS_PCT}

        # ── 기본 모드 익절·손절(전략설정 QUANT_BASE_*): 공격 모드 OFF 일 때 보유분을 시그널과 무관하게 전량 청산 ──
        if not aggressive:
            base_tp = float(getattr(app_settings, "QUANT_BASE_TAKE_PROFIT_PCT", 0) or 0)
            base_sl = float(getattr(app_settings, "QUANT_BASE_STOP_LOSS_PCT", 0) or 0)
            if base_tp > 0 or base_sl > 0:
                base_exits: dict[str, str] = {}
                for p in (await db.execute(select(Portfolio).where(Portfolio.user_id == uid, Portfolio.book == PORTFOLIO_BOOK_QUANT))).scalars().all():
                    price_now, avg = price_map.get(p.symbol), float(p.avg_price or 0)
                    if p.quantity <= 0 or not price_now or not avg:
                        continue
                    pnl = (price_now / avg - 1) * 100
                    if base_tp > 0 and pnl >= base_tp:
                        base_exits[p.symbol] = f"기본 모드 익절 {pnl:+.2f}% ≥ +{base_tp:g}%"
                    elif base_sl > 0 and pnl <= -base_sl:
                        base_exits[p.symbol] = f"기본 모드 손절 {pnl:+.2f}% ≤ -{base_sl:g}%"
                for sym, note in base_exits.items():
                    forced_action[sym] = {"action": "매도", "ratio": 1.0, "note": note}
                    if sym not in target_symbols and sym in stock_map:
                        target_symbols.append(sym)
                if base_exits:
                    cycle_log["base_exits"] = base_exits

        cycle_log["settings"] = {
            "mode": mode,
            "aggressive": aggressive,
            "signal_mode": signal_mode_name,
            "profile_id": profile.get("id") or "",
            "symbol_source": symbol_source,
            "strategy": strategy_log,
            "symbols": target_symbols,
            "per_trade_budget": per_trade_budget,
            "buy_ratio": buy_ratio,
            "sell_ratio": sell_ratio,
        }

        # ── 위험관리 사전 점검: 일손실 한도 ───────────────────────────────
        cash_now, equity_now, position_values = await _equity_snapshot(db, uid, price_map)
        start_equity = await risk_guard.day_start_equity(user_id, equity_now)
        day_pnl = risk_guard.daily_pnl_pct(start_equity, equity_now)
        cycle_log["risk"] = {
            "day_start_equity": round(start_equity, 2), "equity": round(equity_now, 2), "day_pnl_pct": day_pnl,
            "orders_today": await risk_guard.orders_today(user_id), "limits": limits.to_dict(), "skipped": [],
        }
        if risk_guard.daily_loss_breached(start_equity, equity_now, limits.daily_loss_limit_pct):
            reason = f"일손실 한도 초과: 당일 {day_pnl:+.2f}% ≤ -{limits.daily_loss_limit_pct}%"
            cycle_log["risk"]["halted"] = True
            cycle_log["risk"]["reason"] = reason
            await _persist_cycle(uid, cycle_log)
            await emergency_halt(db, broker_row, user_id, reason, day_pnl)
            return

        # ── 실계좌(게이트웨이) 기준 일손실: live 모드에서 가상계좌와 별도로 KIS 계좌 평가액을 본다 ──
        if mode == "live" and gateway.is_configured():
            # 미체결 매수 실주문은 체결되면 비중이 되므로 종목 비중 한도 계산에 미리 포함한다.
            open_exposure = await open_live_order_exposure(db, uid)
            for sym, value in open_exposure.items():
                position_values[sym] = position_values.get(sym, 0.0) + value
            if open_exposure:
                cycle_log["risk"]["open_live_exposure"] = {k: round(v, 2) for k, v in open_exposure.items()}
            live_risk = await live_account_daily_loss(user_id, limits.daily_loss_limit_pct)
            cycle_log["risk"]["live"] = live_risk
            if live_risk.get("breached"):
                reason = f"실계좌 일손실 한도 초과({live_risk['environment']}): 당일 {live_risk['day_pnl_pct']:+.2f}% ≤ -{limits.daily_loss_limit_pct}%"
                cycle_log["risk"]["halted"] = True
                cycle_log["risk"]["reason"] = reason
                await _persist_cycle(uid, cycle_log)
                await emergency_halt(db, broker_row, user_id, reason, live_risk["day_pnl_pct"])
                return

        async def _risk_gate(symbol: str, name: str, side: str, qty: int, price: float) -> tuple[int, str | None]:
            """주문 직전 위험관리 게이트. (허용 수량, 생략/조정 사유)"""
            if limits.max_orders_per_day > 0 and await risk_guard.orders_today(user_id) >= limits.max_orders_per_day:
                return 0, f"일 주문 수 한도 {limits.max_orders_per_day}건 도달"
            if side == "buy":
                qty, note = risk_guard.cap_buy_quantity(qty, price, position_values.get(symbol, 0.0), equity_now, limits.max_position_pct)
                if qty <= 0:
                    return 0, note
            else:
                note = None
            if not await risk_guard.acquire_order_slot(user_id, symbol, side, limits.cooldown_min):
                return 0, f"중복 주문 방지: {limits.cooldown_min}분 내 동일 종목·방향 주문 존재"
            return qty, note

        async def _risk_skip(symbol: str, name: str, side: str, price: float, reason: str) -> None:
            cycle_log["risk"]["skipped"].append({"symbol": symbol, "name": name, "side": side, "reason": reason})
            cycle_log["trades"].append({
                "time": datetime.now(timezone.utc).isoformat(), "symbol": symbol, "name": name, "action": side,
                "quantity": 0, "price": price, "reason": f"[위험관리] {reason}", "status": "skipped", "type": "risk",
            })
            await notification.notify_risk_skip(symbol, name, side, reason, user_id=user_id)

        for symbol in target_symbols:
            stock = stock_map.get(symbol)
            if not stock:
                continue
            indicators = indicator_map.get(symbol) or {}
            signal = indicators.get("signal", {})
            if strategy_spec:
                ml_score = ml_scores.get(symbol, ml_scores.get(str(symbol)[:6]))
                if ml_score is None and ml_weight_on:
                    ml_score = await symbol_ml_score(symbol)
                    if ml_score is not None:
                        strategy_log["ml_source"] = "symbol_ridge"
                signal = apply_strategy_spec_to_signal(signal, strategy_spec, ml_score, indicators)
            elif signal_mode_name == signal_mode.CUSTOM:
                signal = signal_mode.apply_custom_rules(signal, indicators)   # ③ 사용자 임계값·추세 필터로 재판정
            price = indicators.get("current_price")
            if not price:
                continue

            action = signal.get("action", "관망")
            reasons = signal.get("reasons", [])
            score = signal.get("score", 0)
            sell_ratio_here = sell_ratio
            forced = forced_action.get(symbol)
            if forced:
                action = forced["action"]
                reasons = [forced["note"], *reasons]
                if forced["ratio"] is not None:
                    sell_ratio_here = float(forced["ratio"])
            elif aggressive:
                action = "관망"   # 공격 모드에서는 plan 이 정한 종목만 거래한다(사이클당 매수·매도 수 한도)
            elif (strategy_spec and action in ("강력 매수", "매수") and position_values.get(symbol, 0.0) > 0
                  and str((strategy_spec.get("entry") or {}).get("condition") or "") == "always"):
                # buy_hold/dca 스펙은 entry 가 '항상' 이라 매 사이클 매수가 나온다 → 보유 중이면 추가 매수 생략(1종목 1포지션)
                action = "관망"
                reasons = [*reasons, "buy_hold/dca 스펙: 이미 보유 중 — 추가 매수 생략(1종목 1포지션)"]
            cycle_log["signals"].append({
                "symbol": stock["symbol"], "name": stock["name"],
                "price": price, "action": action, "score": score,
                "reasons": [str(r) for r in list(reasons)[:6]],   # 의사결정 화면 「판단 근거」 카드에 실제 사유를 보여 준다
                "error": signal.get("error"),
                "basis": _signal_basis(indicators),               # 그 근거가 어느 시장 데이터에서 나왔는지
            })

            if action in ("강력 매수", "매수"):
                budget = per_trade_budget * buy_ratio
                qty = max(1, int(budget / price))
                qty, risk_note = await _risk_gate(stock["symbol"], stock["name"], "buy", qty, price)
                if qty <= 0:
                    await _risk_skip(stock["symbol"], stock["name"], "buy", price, risk_note or "위험관리 규칙")
                    continue
                if risk_note:
                    reasons = [*reasons, f"위험관리: {risk_note}"]
                trade = await _execute_virtual_trade(
                    db, uid, stock["symbol"], stock["name"],
                    "buy", price, qty, f"[{mode}] " + " | ".join(reasons),
                )
                cycle_log["trades"].append({**trade, "type": "auto"})
                if trade.get("status") != "filled":
                    await risk_guard.release_order_slot(user_id, stock["symbol"], "buy")
                if trade.get("status") == "filled":
                    cycle_log["risk"]["orders_today"] = await risk_guard.increment_orders_today(user_id)
                    position_values[stock["symbol"]] = position_values.get(stock["symbol"], 0.0) + price * trade.get("quantity", qty)
                    await notification.notify_auto_trade_executed(
                        symbol   = stock["symbol"],
                        name     = stock["name"],
                        action   = "buy",
                        quantity = trade.get("quantity", qty),
                        price    = price,
                        reason   = " | ".join(reasons),
                        user_id  = user_id,
                    )
                    live_result = await _place_live_order(
                        broker_row, stock["symbol"], stock["name"], "buy",
                        trade.get("quantity", qty), price, user_id, db=db,
                    )
                    if live_result:
                        cycle_log["trades"][-1]["live_order"] = live_result

            elif action in ("강력 매도", "매도"):
                port_result = await db.execute(
                    select(Portfolio).where(Portfolio.user_id == uid, Portfolio.symbol == stock["symbol"], Portfolio.book == PORTFOLIO_BOOK_QUANT)
                )
                existing = port_result.scalar_one_or_none()
                if existing and existing.quantity > 0:
                    qty = max(1, int(existing.quantity * sell_ratio_here))
                    if sell_ratio_here >= 1.0:
                        qty = int(existing.quantity)
                    qty, risk_note = await _risk_gate(stock["symbol"], stock["name"], "sell", qty, price)
                    if qty <= 0:
                        await _risk_skip(stock["symbol"], stock["name"], "sell", price, risk_note or "위험관리 규칙")
                        continue
                    trade = await _execute_virtual_trade(
                        db, uid, stock["symbol"], stock["name"],
                        "sell", price, qty, f"[{mode}] " + " | ".join(reasons),
                    )
                    cycle_log["trades"].append({**trade, "type": "auto"})
                    if trade.get("status") != "filled":
                        await risk_guard.release_order_slot(user_id, stock["symbol"], "sell")
                    if trade.get("status") == "filled":
                        cycle_log["risk"]["orders_today"] = await risk_guard.increment_orders_today(user_id)
                        position_values[stock["symbol"]] = max(0.0, position_values.get(stock["symbol"], 0.0) - price * trade.get("quantity", qty))
                        await notification.notify_auto_trade_executed(
                            symbol   = stock["symbol"],
                            name     = stock["name"],
                            action   = "sell",
                            quantity = trade.get("quantity", qty),
                            price    = price,
                            reason   = " | ".join(reasons),
                            user_id  = user_id,
                        )
                        live_result = await _place_live_order(
                            broker_row, stock["symbol"], stock["name"], "sell",
                            trade.get("quantity", qty), price, user_id, db=db,
                        )
                        if live_result:
                            cycle_log["trades"][-1]["live_order"] = live_result

        acc_result = await db.execute(select(QuantVirtualAccount).where(QuantVirtualAccount.user_id == uid))
        account = acc_result.scalar_one_or_none()
        cash_balance = float(account.cash_balance) if account else float(_INITIAL_CAPITAL)

        holdings_value = 0.0
        pf_result = await db.execute(select(Portfolio).where(Portfolio.user_id == uid, Portfolio.book == PORTFOLIO_BOOK_QUANT))
        for p in pf_result.scalars().all():
            if p.quantity <= 0:
                continue
            if p.symbol not in price_map:
                logger.warning("현재가 미수신되어 평균단가 사용: user=%s symbol=%s", user_id, p.symbol)
            mark_price = float(price_map.get(p.symbol, p.avg_price))
            holdings_value += p.quantity * mark_price

    total_equity = cash_balance + holdings_value
    initial_capital = float(account.initial_capital) if account else float(_INITIAL_CAPITAL)
    pnl_pct = round((total_equity / initial_capital - 1) * 100, 2) if initial_capital > 0 else None
    cycle_log["account"] = {
        "initial_capital": initial_capital,
        "cash_balance": round(cash_balance, 2),
        "holdings_value": round(holdings_value, 2),
        "total_equity": round(total_equity, 2),
        "pnl_pct": pnl_pct,
    }

    # ── 위험관리 사후 점검: 체결 후 일손실 한도 재확인 ────────────────────
    day_pnl_after = risk_guard.daily_pnl_pct(start_equity, total_equity)
    cycle_log["risk"].update({"equity": round(total_equity, 2), "day_pnl_pct": day_pnl_after})
    await _persist_cycle(uid, cycle_log)
    if risk_guard.daily_loss_breached(start_equity, total_equity, limits.daily_loss_limit_pct):
        async with session_factory() as db2:
            row2 = (await db2.execute(select(BrokerSettings).where(BrokerSettings.user_id == uid))).scalar_one_or_none()
            await emergency_halt(db2, row2, user_id,
                                 f"일손실 한도 초과(체결 후): 당일 {day_pnl_after:+.2f}% ≤ -{limits.daily_loss_limit_pct}%", day_pnl_after)


async def _auto_trade_loop(user_id: str) -> None:
    global _is_running
    _is_running = True
    try:
        while True:
            await _run_quant_cycle(user_id)
            await asyncio.sleep(_INTERVAL_SEC)
    except asyncio.CancelledError:
        pass
    finally:
        _is_running = False


async def _run_once(user_id: str) -> None:
    global _is_running
    _is_running = True
    try:
        await _run_quant_cycle(user_id)
    except Exception:
        logger.exception("자동매매 즉시 실행 실패 user=%s", user_id)
    finally:
        _is_running = False


async def start_auto_trade(db: AsyncSession, user_id: str) -> bool:
    """자동매매 활성화: DB 플래그 ON + 즉시 1회 사이클(백그라운드). 이후 주기 실행은 Celery Beat."""
    global _auto_trade_task, _auto_trade_user_id
    uid = _resolve_user_id(user_id)
    if await is_enabled(db, uid):
        return False
    await set_enabled(db, uid, True)
    _auto_trade_user_id = user_id or "quant_system"
    if not (_auto_trade_task and not _auto_trade_task.done()):
        _auto_trade_task = asyncio.create_task(_run_once(_auto_trade_user_id))
    asyncio.create_task(notification.notify_auto_trade_started(user_id=_auto_trade_user_id))
    asyncio.create_task(audit(_auto_trade_user_id, "", "auto_trade.start", {"scheduler": "celery-beat"}))
    return True


async def stop_auto_trade_for(db: AsyncSession, user_id: str) -> bool:
    uid = _resolve_user_id(user_id)
    was = await is_enabled(db, uid)
    await set_enabled(db, uid, False)
    stop_auto_trade()
    if was:
        asyncio.create_task(notification.notify_auto_trade_stopped(user_id=user_id))
        asyncio.create_task(audit(user_id, "", "auto_trade.stop", {}))
    return was


def stop_auto_trade() -> bool:
    """(하위 호환) 인프로세스 즉시 실행 태스크 취소. 플래그는 호출자가 내린다."""
    global _auto_trade_task
    if _auto_trade_task and not _auto_trade_task.done():
        _auto_trade_task.cancel()
        return True
    return False


async def run_cycle_for_enabled_users() -> dict:
    """Celery Beat 진입점: quant_auto_enabled=true 인 사용자 전원의 사이클을 순차 실행.

    먼저 kis_batch.ensure_system_batch 로 KIS 모의투자 백그라운드 배치(시스템 사용자 행)를 켜거나 끈다.
    그래서 KIS_PAPER_BATCH_ENABLED=true 이면 로그인·대시보드 조작 없이도 사이클이 돈다.
    """
    from app.services import kis_batch  # 지연 import (kis_batch → kis_quickstart → auto_trade 순환 방지)

    # 전략설정(화면) 을 먼저 반영 — ensure_system_batch._sync_env 가 투자금·종목 수를 유효값으로 맞추게
    settings_doc = await strategy_settings.load_and_apply()
    skip, why = strategy_settings.should_skip_cycle(settings_doc)
    if skip:
        logger.info("자동매매 사이클 건너뜀: %s", why)
        return {"enabled_users": 0, "ran": 0, "failed": 0, "skipped": why}
    await strategy_settings.mark_cycle_run(settings_doc)

    session_factory = get_session_factory()
    async with session_factory() as db:
        try:
            batch = await kis_batch.ensure_system_batch(db)
        except Exception as exc:  # 배치 점검 실패가 사용자 계정 사이클을 막으면 안 된다
            logger.exception("KIS 모의투자 배치 점검 실패: %s", exc)
            batch = {"enabled": kis_batch.is_configured(), "running": False, "error": str(exc)}
            try:
                await db.rollback()   # 실패한 트랜잭션을 정리하지 않으면 아래 select 가 PendingRollbackError 로 전체 사이클을 막는다
            except Exception:
                pass
        uids = (await db.execute(select(BrokerSettings.user_id).where(BrokerSettings.quant_auto_enabled.is_(True)))).scalars().all()
    ran, failed = 0, 0
    for uid in uids:
        try:
            await _run_quant_cycle(str(uid))
            ran += 1
        except Exception:
            failed += 1
            logger.exception("자동매매 사이클 실패 user=%s", uid)
    return {"enabled_users": len(uids), "ran": ran, "failed": failed, "kis_batch": batch}
