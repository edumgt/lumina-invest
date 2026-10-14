"""퀀트 ML 파이프라인 API 엔드포인트."""
from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel
from app.lib.session import get_current_user
from app.services.stock import get_candles, QUANT_STOCKS
from app.services.quant_pipeline import run_pipeline

router = APIRouter(prefix="/api/quant/ml")


@router.get("/stocks")
async def pipeline_stock_list():
    """파이프라인 적용 가능 종목 목록."""
    return {"stocks": QUANT_STOCKS}


@router.get("/run")
async def run_ml_pipeline(
    symbol: str = Query(..., description="종목 코드 (예: 005930.KS)"),
    period: str = Query("2y", description="데이터 기간"),
    model:  str = Query("lgb", description="모델 선택: lgb | mlp | rule"),
    commission_bps: float = Query(0.0, ge=0.0, le=500.0),
    slippage_bps: float = Query(0.0, ge=0.0, le=500.0),
    stop_loss_pct: float | None = Query(None, ge=0.1, le=90.0),
    take_profit_pct: float | None = Query(None, ge=0.1, le=500.0),
    _user=Depends(get_current_user),
):
    """
    OHLCV → 전처리 → 피처 → ML/DL → 시그널 → 백테스트 → Alpaca 순서 실행.

    - model=lgb  : LightGBM 방향성 분류 (권장)
    - model=mlp  : MLP Neural Net (scikit-learn)
    - model=rule : 규칙 기반 RSI+MACD+BB (ML 라이브러리 없을 때 fallback)
    """
    candle_data = await get_candles(symbol, period=period, interval="1d")
    candles = candle_data.get("candles", [])
    if not candles:
        raise HTTPException(404, f"종목 데이터 없음: {symbol}")

    result = await run_pipeline(symbol, candles, model_type=model, commission_bps=commission_bps,
                                slippage_bps=slippage_bps, stop_loss_pct=stop_loss_pct, take_profit_pct=take_profit_pct)
    if "error" in result:
        raise HTTPException(422, result["error"])
    return result


class BatchRunBody(BaseModel):
    symbols: list[str] = []
    period:  str = "2y"
    model:   str = "lgb"


@router.post("/run/batch")
async def run_batch_pipeline(
    body: BatchRunBody,
    _user=Depends(get_current_user),
):
    """여러 종목 파이프라인 일괄 실행."""
    from app.services.korea_market_scan import candidate_stocks
    targets = body.symbols or [s["symbol"] for s in await candidate_stocks()]
    results = []
    for sym in targets:
        candle_data = await get_candles(sym, period=body.period, interval="1d")
        candles = candle_data.get("candles", [])
        res = await run_pipeline(sym, candles, model_type=body.model)
        results.append(res)
    return {"results": results}
