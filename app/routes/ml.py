"""ML 모델 비교 · 클러스터링 · 계절성 · 회귀 API."""
from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel
from app.lib.session import get_current_user
from app.services.stock import get_candles, QUANT_STOCKS
from app.services.ml_models import (
    compare_models,
    tune_hyperparams,
    cluster_stocks,
    seasonality_analysis,
    regression_forecast,
)

router = APIRouter(prefix="/api/ml")


@router.get("/compare")
async def ml_compare(
    symbol: str = Query("005930.KS", description="종목 코드"),
    period: str = Query("2y",        description="데이터 기간"),
    _user=Depends(get_current_user),
):
    """7가지 ML 모델 5-fold 교차검증 비교."""
    data = await get_candles(symbol, period=period, interval="1d")
    candles = data.get("candles", [])
    if not candles:
        raise HTTPException(404, f"데이터 없음: {symbol}")
    result = compare_models(candles)
    if "error" in result:
        raise HTTPException(422, result["error"])
    return result


@router.get("/tune")
async def ml_tune(
    symbol:     str = Query("005930.KS"),
    period:     str = Query("2y"),
    model_name: str = Query("rf", description="svm | rf | gb"),
    _user=Depends(get_current_user),
):
    """GridSearchCV 하이퍼파라미터 튜닝."""
    data = await get_candles(symbol, period=period, interval="1d")
    candles = data.get("candles", [])
    if not candles:
        raise HTTPException(404, f"데이터 없음: {symbol}")
    result = tune_hyperparams(candles, model_name)
    if "error" in result:
        raise HTTPException(422, result["error"])
    return result


class ClusterBody(BaseModel):
    symbols: list[str] = []
    period:  str = "2y"


@router.post("/cluster")
async def ml_cluster(
    body: ClusterBody,
    _user=Depends(get_current_user),
):
    """종목 군집화 (KMeans)."""
    targets = body.symbols or [s["symbol"] for s in QUANT_STOCKS]
    stocks_data = []
    for sym in targets:
        data = await get_candles(sym, period=body.period, interval="1d")
        candles = data.get("candles", [])
        if candles:
            stocks_data.append({"symbol": sym, "candles": candles})
    result = cluster_stocks(stocks_data)
    if "error" in result:
        raise HTTPException(422, result["error"])
    return result


@router.get("/seasonality")
async def ml_seasonality(
    symbol: str = Query("005930.KS"),
    period: str = Query("5y"),
    _user=Depends(get_current_user),
):
    """월별·요일별·연말 계절성 분석."""
    data = await get_candles(symbol, period=period, interval="1d")
    candles = data.get("candles", [])
    if not candles:
        raise HTTPException(404, f"데이터 없음: {symbol}")
    result = seasonality_analysis(candles)
    if "error" in result:
        raise HTTPException(422, result["error"])
    return result


@router.get("/regression")
async def ml_regression(
    symbol: str = Query("005930.KS"),
    period: str = Query("2y"),
    _user=Depends(get_current_user),
):
    """선형회귀·Ridge·Lasso·SVR 수익률 예측 비교."""
    data = await get_candles(symbol, period=period, interval="1d")
    candles = data.get("candles", [])
    if not candles:
        raise HTTPException(404, f"데이터 없음: {symbol}")
    result = regression_forecast(candles)
    if "error" in result:
        raise HTTPException(422, result["error"])
    return result
