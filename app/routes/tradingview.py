"""TradingView 연동 API.

- POST /api/webhooks/tradingview        : TradingView 알림 Webhook 수신 (세션 불필요, 본문 token=API 키)
- GET  /api/tradingview/webhook-info     : Webhook URL·알림 메시지 템플릿
- GET  /api/tradingview/signals          : 수신 신호 이력
- POST /api/tradingview/compare          : Strategy Tester 결과 ↔ LEAN 백테스트 교차 검증
- GET  /api/tradingview/comparisons      : 비교 이력
"""
from __future__ import annotations

import json
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.postgres import get_pg_session
from app.lib.jwt_auth import get_current_user_any
from app.models import StrategyComparison
from app.services import notification
from app.services import tradingview as tv
from app.services.audit import audit
from app.services.lean_backtest import STRATEGY_LABELS, LeanBacktestError, service as lean_service
from app.routes.openapi import _check_rate_limit

router = APIRouter(tags=["tradingview"])


def _uid(user: dict) -> uuid.UUID:
    return uuid.UUID(str(user["id"]))


@router.post("/api/webhooks/tradingview")
async def tradingview_webhook(request: Request, db: AsyncSession = Depends(get_pg_session)):
    """TradingView 알림 Webhook. 본문은 JSON(권장) 또는 'key=value' 텍스트."""
    raw = await request.body()
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else {}
        if not isinstance(payload, dict):
            raise ValueError
    except Exception:
        # TradingView 기본 메시지가 JSON이 아닐 때: "token=..., ticker=..., action=buy" 형태 허용
        payload = {}
        for part in raw.decode("utf-8", "ignore").replace("\n", ",").split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                payload[k.strip()] = v.strip()
        if not payload:
            raise HTTPException(400, {"error": "BAD_REQUEST", "message": "JSON 본문을 해석할 수 없습니다."})
    # 발신 IP 허용 목록 (운영: TRADINGVIEW_ENFORCE_IP=true)
    if settings.TRADINGVIEW_ENFORCE_IP:
        fwd = request.headers.get("x-forwarded-for", "")
        client_ip = (fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else ""))
        allowed = {ip.strip() for ip in settings.TRADINGVIEW_ALLOWED_IPS.split(",") if ip.strip()}
        if client_ip not in allowed:
            raise HTTPException(403, {"error": "FORBIDDEN", "message": f"허용되지 않은 발신 IP: {client_ip}"})
    try:
        key = await tv.resolve_user_by_token(db, str(payload.get("token") or payload.get("api_key") or ""))
        if not await _check_rate_limit(f"tv:{key.id}", settings.TRADINGVIEW_RATE_LIMIT_MAX):
            raise HTTPException(429, {"error": "RATE_LIMITED", "message": f"분당 {settings.TRADINGVIEW_RATE_LIMIT_MAX}회 알림 제한을 초과했습니다."})
        result = await tv.handle_alert(db, payload)
    except tv.WebhookError as exc:
        await db.rollback()
        raise HTTPException(exc.status, {"error": "UNAUTHORIZED" if exc.status == 401 else "BAD_REQUEST", "message": str(exc)})
    await db.commit()
    sig = result["signal"]
    await audit(result["user_id"], "", "tradingview.webhook", {k: sig[k] for k in ("symbol", "side", "quantity", "status")})
    # 신호 전달 (Slack/Telegram/Email 등 사용자 알림 채널)
    try:
        label = {"BUY": "매수", "SELL": "매도"}.get(sig["side"], "알림")
        text = (f" [TradingView] {label} 신호 수신 — {sig['symbol']} {sig['quantity']}주"
                f"{f' @ {sig['signal_price']:,.0f}' if sig['signal_price'] else ''}"
                f"\n전략: {sig['strategy'] or '-'} · 처리: {sig['status']} · {sig['message']}")
        await notification.dispatch(text, html_message=text.replace("\n", "\n\n"), subject=f"[TradingView] {label} 신호 – {sig['symbol']}",
                                    user_id=result["user_id"])
    except Exception:
        pass
    return {"ok": sig["status"] in ("filled", "alert"), "status": sig["status"], "message": sig["message"],
            "symbol": sig["symbol"], "side": sig["side"], "quantity": sig["quantity"], "fill_price": sig["fill_price"]}


@router.get("/api/tradingview/webhook-info")
async def webhook_info(request: Request, user=Depends(get_current_user_any)):
    base = str(request.base_url).rstrip("/")
    public = getattr(settings, "PUBLIC_BASE_URL", "") or base
    return {
        "webhook_url": f"{public}/api/webhooks/tradingview",
        "alert_template": tv.ALERT_TEMPLATE,
        "alert_template_text": json.dumps(tv.ALERT_TEMPLATE, ensure_ascii=False, indent=2),
        "notes": [
            "TradingView 알림 만들기 › 알림 액션 › Webhook URL 에 위 주소를 넣고, 메시지 칸에 템플릿 JSON을 붙여 넣으세요.",
            "token 값은 모의투자 › Open API 화면에서 발급한 API 키입니다(발급 시 1회만 표시).",
            "action 이 buy/sell 이고 contracts ≥ 1 이면 모의계좌에 시장가 체결(source=TRADINGVIEW), 그 외는 알림만 전달합니다.",
            f"같은 종목·방향 알림이 {tv.DUPLICATE_WINDOW_MIN}분 내 반복되면 중복으로 무시합니다.",
            "TradingView Webhook은 공개 URL(HTTPS 권장)만 호출할 수 있습니다. 로컬 개발 시 ngrok 등으로 터널링하세요.",
        ],
    }


@router.get("/api/tradingview/signals")
async def signals(limit: int = Query(50, ge=1, le=200), user=Depends(get_current_user_any),
                  db: AsyncSession = Depends(get_pg_session)):
    return {"signals": await tv.list_signals(db, _uid(user), limit)}


class TvMetricsBody(BaseModel):
    net_profit_pct: float | None = None
    max_drawdown_pct: float | None = None
    total_trades: int | None = None
    win_rate_pct: float | None = None
    sharpe_ratio: float | None = None


class CompareBody(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=12)
    strategy: str = Field("ma_cross", description="buy_hold | ma_cross | dca | momentum")
    start_date: date
    end_date: date
    initial_cash: float = Field(10_000, ge=1_000)
    short_window: int = Field(20, ge=2, le=120)
    long_window: int = Field(60, ge=5, le=300)
    dca_interval_days: int = Field(21, ge=1, le=120)
    breakout_window: int = Field(20, ge=5, le=120)
    tv_metrics: TvMetricsBody | None = None
    tv_trades_csv: str | None = Field(None, description="Strategy Tester 거래 목록 CSV 원문")


@router.post("/api/tradingview/compare")
async def compare(body: CompareBody, user=Depends(get_current_user_any), db: AsyncSession = Depends(get_pg_session)):
    """TradingView Strategy Tester 성과를 LEAN 백테스트와 교차 검증한다."""
    if body.strategy not in STRATEGY_LABELS:
        raise HTTPException(422, f"strategy는 {', '.join(STRATEGY_LABELS)} 중 하나여야 합니다.")
    if body.start_date >= body.end_date:
        raise HTTPException(422, "종료일은 시작일보다 뒤여야 합니다.")

    # 1) TradingView 지표
    tv_metrics: dict = {}
    if body.tv_trades_csv and body.tv_trades_csv.strip():
        try:
            tv_metrics = tv.parse_trade_list_csv(body.tv_trades_csv)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
    if body.tv_metrics:
        manual = {k: v for k, v in body.tv_metrics.model_dump().items() if v is not None}
        tv_metrics = {**tv_metrics, **manual, "source": (tv_metrics.get("source", "") + " + " if tv_metrics else "") + "직접 입력"} if manual else tv_metrics
    if not tv_metrics:
        raise HTTPException(422, "TradingView 성과(직접 입력) 또는 거래 목록 CSV 중 하나는 필요합니다.")

    # 2) LEAN 백테스트 (동일 종목·기간·전략)
    ticker = body.ticker.strip().upper()
    if ticker.isdigit() and len(ticker) == 6:
        ticker += ".KS"
    try:
        lean_res = await lean_service.run(
            ticker=ticker, start_date=body.start_date, end_date=body.end_date,
            compare_start_date=body.start_date, compare_end_date=body.end_date, initial_cash=body.initial_cash,
            strategy=body.strategy, short_window=body.short_window, long_window=body.long_window,
            dca_interval_days=body.dca_interval_days, breakout_window=body.breakout_window,
        )
    except LeanBacktestError as exc:
        raise HTTPException(422, str(exc))
    except Exception as exc:
        raise HTTPException(502, f"LEAN 백테스트 실행 오류: {exc}")

    lean_metrics = tv.lean_metrics_from_result(lean_res)
    result = tv.compare_metrics(tv_metrics, lean_metrics, ticker, body.strategy)
    result["strategy_label"] = STRATEGY_LABELS.get(body.strategy, body.strategy)
    result["period"] = {"start": body.start_date.isoformat(), "end": body.end_date.isoformat()}
    result["lean_points"] = lean_res.get("points", [])

    row = StrategyComparison(
        user_id=_uid(user), ticker=ticker, strategy=body.strategy, start_date=body.start_date.isoformat(),
        end_date=body.end_date.isoformat(), verdict=result["verdict"], tv_metrics=tv_metrics,
        lean_metrics={k: v for k, v in lean_metrics.items() if k != "lean_statistics"}, diff=result["diff"], causes=result["causes"],
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    result["id"] = str(row.id)
    await audit(user["id"], user.get("client_id", ""), "tradingview.compare",
                {"ticker": ticker, "strategy": body.strategy, "verdict": result["verdict"]})
    return result


@router.get("/api/tradingview/comparisons")
async def comparisons(limit: int = Query(30, ge=1, le=200), user=Depends(get_current_user_any),
                      db: AsyncSession = Depends(get_pg_session)):
    return {"comparisons": await tv.list_comparisons(db, _uid(user), limit)}
