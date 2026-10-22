"""ML 모델 비교 · 클러스터링 · 계절성 · 회귀 API."""
import asyncio
from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel
import numpy as np
from app.lib.session import get_current_user
from app.services.stock import get_candles, QUANT_STOCKS
from app.services.ml_models import (
    compare_models,
    tune_hyperparams,
    cluster_stocks,
    seasonality_analysis,
    regression_forecast,
)
from app.services.investment_research import optimize_portfolio, ai_predict_return
from app.services.data_cache import cache_get, cache_set
from app.services.quant_ai_scores import get_batch_training_scores

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


class RoboAllocationBody(BaseModel):
    risk_profile: str = "moderate"  # conservative | moderate | aggressive
    horizon_years: int = 3
    amount_manwon: int = 5000


@router.post("/cluster")
async def ml_cluster(
    body: ClusterBody,
    _user=Depends(get_current_user),
):
    """종목 군집화 (KMeans)."""
    from app.services.korea_market_scan import candidate_stocks
    targets = body.symbols or [s["symbol"] for s in await candidate_stocks()]
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


@router.post("/robo/allocation")
async def robo_allocation(
    body: RoboAllocationBody,
    _user=Depends(get_current_user),
):
    """시장 데이터 기반 간단 자산배분/추천 결과."""
    from app.services.robo_profile import compute_allocation
    risk = body.risk_profile if body.risk_profile in ("conservative", "moderate", "aggressive") else "moderate"
    horizon = max(1, min(10, int(body.horizon_years)))
    amount = max(100, int(body.amount_manwon))

    # 성향 × 투자기간 비중: 자산군별 하한·상한과 합계 100 제약 적용(감리 FD-FIND-001 — 장기 구간 음수 현금 비중 수정)
    alloc = compute_allocation(risk, horizon)

    # 유니버스 전체(~30종목)를 동시 스캔해 과거 성과(샤프비율) + AI 학습 기반
    # 예측 수익률(Ridge 회귀, 피처 엔지니어링)을 함께 스코어링한다.
    # 화면에 뜨는 종목은 고정되어 있지 않고 매 요청마다 이 스캔 결과로 결정된다.
    sem = asyncio.Semaphore(8)
    batch_scores = await get_batch_training_scores()  # SageMaker 일 1회 배치 학습 (없으면 None)

    async def _screen(s: dict) -> dict | None:
        async with sem:
            data = await get_candles(s["symbol"], period="2y", interval="1d")
        candles = data.get("candles", [])
        if len(candles) < 80:
            return None
        closes = np.array([float(c["close"]) for c in candles if c.get("close") is not None], dtype=float)
        if len(closes) < 80:
            return None
        rets = np.diff(closes) / closes[:-1]
        ann_ret = float(np.mean(rets) * 252)
        ann_vol = float(np.std(rets) * np.sqrt(252)) if np.std(rets) > 0 else 0.0001
        sharpe = ann_ret / ann_vol if ann_vol > 0 else 0.0

        # TimeSeriesSplit 앙상블 + LightGBM 분류는 종목당 꽤 무거워서(유니버스 전체면
        # 응답이 수십 초까지 걸릴 수 있음) 매 요청마다 재계산하지 않고 캐시한다.
        cache_key = f"ai_predict:v3:{s['symbol']}"  # v2: XAI explanation 포함
        ai = await cache_get(cache_key, max_age_hours=3)
        if ai is None:
            # CPU 바운드(sklearn/lightgbm 학습)라 to_thread로 돌려 이벤트 루프를
            # 막지 않고, 여러 종목 학습이 실제로 병렬화되게 한다.
            ai = await asyncio.to_thread(ai_predict_return, candles)
            if ai is not None:
                await cache_set(cache_key, ai)

        code6 = s["symbol"].split(".")[0]
        sentiment = await cache_get(f"sentiment:{code6}", max_age_hours=72)
        batch = (batch_scores or {}).get("scores", {}).get(s["symbol"])
        return {
            "symbol": s["symbol"],
            "name": s["name"],
            "sector": s.get("sector", ""),
            "candles": candles,
            "ann_return_pct": round(ann_ret * 100, 2),
            "ann_vol_pct": round(ann_vol * 100, 2),
            "sharpe": sharpe,
            "ai": ai,
            "sentiment": sentiment,
            "batch": batch,
        }

    from app.services.korea_market_scan import candidate_stocks
    screened = await asyncio.gather(*(_screen(s) for s in await candidate_stocks()))
    picks = [p for p in screened if p is not None]

    if not picks:
        raise HTTPException(422, "포트폴리오 계산용 시세 데이터가 부족합니다.")

    def _rank01(values: list[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        ranks = [0.0] * len(values)
        denom = max(1, len(values) - 1)
        for r, i in enumerate(order):
            ranks[i] = r / denom
        return ranks

    def _confidence_weighted_component(
        raw: list[float | None], confidences: list[float | None], default_confidence: float,
    ) -> tuple[list[float], list[float]] | None:
        """값이 있는 종목만 순위화하고, 종목별 confidence를 가중치로 함께 반환한다.

        전체 유니버스가 아니라 "이 종목의 AI 예측을 이 종목의 최종 점수에 얼마나
        반영할지"를 종목 단위로 다르게 준다 — 예측 품질이 낮은(음의 R^2 등) 종목의
        신호는 약하게, 신뢰도가 높은 종목의 신호는 강하게 반영된다. 값이 아예 없는
        종목은 가중치 0(순위 결과에 영향 없음).
        """
        valid = [v for v in raw if v is not None]
        if not valid:
            return None
        fallback = float(np.median(valid))
        ranks = _rank01([v if v is not None else fallback for v in raw])
        weights = [
            (c if c is not None else default_confidence) if v is not None else 0.0
            for v, c in zip(raw, confidences)
        ]
        return ranks, weights

    sharpe_rank = _rank01([p["sharpe"] for p in picks])

    weighted_components: list[tuple[list[float], list[float]]] = []
    ai_component = _confidence_weighted_component(
        [p["ai"]["pred_ann_return_pct"] if p["ai"] else None for p in picks],
        [p["ai"].get("confidence") if p["ai"] else None for p in picks],
        default_confidence=0.6,
    )
    if ai_component:
        weighted_components.append(ai_component)
    batch_component = _confidence_weighted_component(
        [p["batch"]["pred_ann_return_pct"] if p["batch"] else None for p in picks],
        [p["batch"].get("confidence") if p["batch"] else None for p in picks],
        default_confidence=0.6,
    )
    if batch_component:
        weighted_components.append(batch_component)
    sentiment_component = _confidence_weighted_component(
        [p["sentiment"]["score"] if p["sentiment"] else None for p in picks],
        [None for _ in picks],  # 감성분석엔 자체 신뢰도 개념이 없어 고정 가중치 사용
        default_confidence=0.4,
    )
    if sentiment_component:
        weighted_components.append(sentiment_component)

    # 샤프비율(과거 실측치)은 항상 가중치 1.0으로 포함하고, 나머지는 종목별 confidence로 가중 평균
    for i, p in enumerate(picks):
        weighted_sum = sharpe_rank[i] * 1.0
        total_weight = 1.0
        for ranks, weights in weighted_components:
            weighted_sum += ranks[i] * weights[i]
            total_weight += weights[i]
        p["combined_score"] = round(weighted_sum / total_weight, 4)
    picks.sort(key=lambda x: x["combined_score"], reverse=True)

    # 스코어 상위 종목만 최적화 대상으로 압축 (추천 종목 수가 유니버스 전체로 흩어지지 않게)
    top_n = min(8, len(picks))
    candidates = picks[:top_n]
    stock_data = [{"symbol": c["symbol"], "candles": c["candles"]} for c in candidates]

    optimized = optimize_portfolio(stock_data, risk)
    if "error" in optimized:
        raise HTTPException(422, optimized["error"])
    optimized_weights = optimized["weights"]
    candidates.sort(key=lambda x: optimized_weights.get(x["symbol"], 0), reverse=True)
    top = [p for p in candidates if p["symbol"] in optimized_weights]
    stock_bucket = alloc["국내주식"] + alloc["해외주식"]
    stock_picks = []
    for p in top:
        w = round(stock_bucket * optimized_weights[p["symbol"]] / 100, 1)
        signal_labels = {1: "매수", 0: "관망", -1: "매도"}
        notes = []
        if p["ai"]:
            sig = signal_labels.get(p["ai"].get("signal"), "")
            notes.append(f"AI 예측 수익률 {p['ai']['pred_ann_return_pct']}% ({sig}, 신뢰도 {p['ai']['confidence']:.2f})")
        if p["batch"]:
            sig = signal_labels.get(p["batch"].get("latest_signal"), "")
            notes.append(f"배치학습 예측 {p['batch']['pred_ann_return_pct']}% ({sig}, 신뢰도 {p['batch'].get('confidence', 0):.2f})")
        if p["sentiment"]:
            notes.append(f"뉴스 감성 {p['sentiment']['score']:+.2f}")
        note_text = (", " + ", ".join(notes)) if notes else ""
        stock_picks.append({
            "name": p["name"],
            "code": p["symbol"],
            "sector": p["sector"],
            "weight": w,
            "reason": f"연환산 수익률 {p['ann_return_pct']}%, 변동성 {p['ann_vol_pct']}%{note_text}",
            "ai_prediction": p["ai"],
            "batch_training": p["batch"],
            "news_sentiment": p["sentiment"],
        })

    # 최적화된 주식 바스켓의 과거 기대수익률을 전체 자산배분 비중에 반영
    exp_ret = (optimized["expected_return_pct"] / 100) * (stock_bucket / 100) + 0.025 * (1 - stock_bucket / 100)
    exp_vol = (optimized["expected_volatility_pct"] / 100) * (stock_bucket / 100)
    years = [1, 3, 5, 10]
    projections = []
    for y in years:
        if y > horizon + 2:
            continue
        total_return = (1 + exp_ret) ** y - 1
        profit = int(round(amount * total_return))
        mdd = -(exp_vol * np.sqrt(y)) * 100
        projections.append({
            "years": y,
            "expected_return_pct": round(total_return * 100, 2),
            "expected_profit_manwon": profit,
            "expected_mdd_pct": round(mdd, 2),
        })

    return {
        "risk_profile": risk,
        "horizon_years": horizon,
        "amount_manwon": amount,
        "allocations": alloc,
        "stock_picks": stock_picks,
        "projections": projections,
        "optimization": optimized,
    }


# ── 투자성향 진단 · 목표 달성 확률 시뮬레이션 ─────────────────────────────

class RiskProfileBody(BaseModel):
    answers: dict[str, int]   # {question_id: 선택 인덱스 0~4}


class GoalSimBody(BaseModel):
    amount_manwon: float = 5000
    monthly_contribution_manwon: float = 0
    horizon_years: int = 3
    target_return_pct: float = 8.0
    expected_return_pct: float = 7.0
    expected_volatility_pct: float = 12.0
    n_paths: int = 3000


@router.get("/robo/questions")
async def robo_questions(_user=Depends(get_current_user)):
    from app.services.robo_profile import QUESTIONS, PROFILE_LEVELS
    return {"questions": QUESTIONS, "levels": [{"min": lv[0], "level": lv[1], "risk_profile": lv[2], "description": lv[3]} for lv in PROFILE_LEVELS]}


@router.post("/robo/risk-profile")
async def robo_risk_profile(body: RiskProfileBody, user=Depends(get_current_user)):
    from app.services.robo_profile import score_answers
    try:
        result = score_answers(body.answers)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return result


@router.post("/robo/goal-simulation")
async def robo_goal_simulation(body: GoalSimBody, _user=Depends(get_current_user)):
    from app.services.robo_profile import goal_simulation
    n = max(500, min(20000, int(body.n_paths)))
    return await asyncio.to_thread(goal_simulation, body.amount_manwon, body.horizon_years, body.target_return_pct,
                                   body.expected_return_pct, body.expected_volatility_pct, body.monthly_contribution_manwon, n)


@router.get("/explain")
async def ml_explain(
    symbol: str = Query(..., description="예: 005930.KS / AAPL"),
    refresh: int = Query(0, description="1이면 캐시 무시하고 재학습"),
    _user=Depends(get_current_user),
):
    """XAI: 종목의 LightGBM 매수/관망/매도 판단 근거(SHAP 기여도 + 자연어 설명)."""
    cache_key = f"ai_predict:v3:{symbol}"
    ai = None if refresh else await cache_get(cache_key, max_age_hours=3)
    if ai is None:
        data = await get_candles(symbol, period="2y", interval="1d")
        candles = data.get("candles", [])
        if len(candles) < 80:
            raise HTTPException(422, "설명을 만들 데이터가 부족합니다 (최소 80거래일).")
        ai = await asyncio.to_thread(ai_predict_return, candles)
        if ai is None:
            raise HTTPException(422, "AI 예측을 계산할 수 없습니다.")
        await cache_set(cache_key, ai)
    name = next((q["name"] for q in QUANT_STOCKS if q["symbol"] == symbol), symbol)
    return {"symbol": symbol, "name": name, "prediction": {k: v for k, v in ai.items() if k != "explanation"},
            "explanation": ai.get("explanation")}


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


# ── 딥러닝(LSTM·Transformer) Colab 연동 ──────────────────────────────
# 앱의 피처 파이프라인(quant_pipeline.feature_engineer)으로 만든 피처+레이블 데이터셋을 CSV 로 내려 주고,
# 저장소의 notebooks/dl_lstm_transformer.ipynb 를 Colab 에서 열어 학습하게 한다. 서버에서는 학습하지 않는다(torch 미설치).

import re as _re
from pathlib import Path as _Path
from fastapi.responses import FileResponse, Response

DL_NOTEBOOK_PATH = _Path(__file__).resolve().parents[2] / "notebooks" / "dl_lstm_transformer.ipynb"
DL_NOTEBOOK_GITHUB = "https://github.com/edumgt/lumina-invest/blob/main/notebooks/dl_lstm_transformer.ipynb"
DL_NOTEBOOK_COLAB = "https://colab.research.google.com/github/edumgt/lumina-invest/blob/main/notebooks/dl_lstm_transformer.ipynb"
DL_LABEL_RULE = {"horizon_days": 5, "buy_threshold": 0.02, "sell_threshold": -0.02,
                 "classes": {"1": "매수(5일 후 +2% 초과)", "0": "관망", "-1": "매도(5일 후 -2% 미만)"}}
_DL_PERIODS = ("1y", "2y", "3y", "5y", "10y", "max")


def _dl_symbol(symbol: str) -> str:
    s = symbol.strip().upper()
    if not _re.fullmatch(r"[A-Z0-9.\-^=]{1,20}", s):
        raise HTTPException(422, f"종목 코드 형식 오류: {symbol}")
    return s


def build_dl_dataset(candles: list[dict]):
    """캔들 → (날짜, OHLCV, 피처 12개, target) DataFrame. 노트북이 그대로 읽는 형태."""
    from app.services.quant_pipeline import preprocess, feature_engineer, FEATURE_COLS
    df = feature_engineer(preprocess(candles))
    cols = ["open", "high", "low", "close", "volume", *FEATURE_COLS, "target"]
    out = df[cols].copy()
    out.insert(0, "date", out.index.tz_convert(None).strftime("%Y-%m-%d"))
    out["target"] = out["target"].astype(int)
    return out.reset_index(drop=True)


async def _dl_candles(symbol: str, period: str) -> list[dict]:
    if period not in _DL_PERIODS:
        raise HTTPException(422, f"period 는 {', '.join(_DL_PERIODS)} 중 하나")
    data = await get_candles(symbol, period=period, interval="1d")
    candles = data.get("candles", [])
    if len(candles) < 120:
        raise HTTPException(404, f"데이터 부족: {symbol} ({len(candles)}봉, 최소 120봉)")
    return candles


@router.get("/dl/dataset/summary")
async def ml_dl_dataset_summary(
    symbol: str = Query("005930.KS", description="종목 코드"),
    period: str = Query("2y", description="데이터 기간"),
    _user=Depends(get_current_user),
):
    """Colab 학습용 데이터셋 요약: 표본 수·기간·클래스 분포·다수 클래스 기준선. 내려받기 전에 화면에서 확인용."""
    from app.services.quant_pipeline import FEATURE_COLS
    sym = _dl_symbol(symbol)
    df = build_dl_dataset(await _dl_candles(sym, period))
    counts = df["target"].value_counts()
    n = int(len(df))
    dist = {str(k): int(counts.get(k, 0)) for k in (1, 0, -1)}
    majority = max(dist.values()) / n if n else 0.0
    seq_len = 60
    return {
        "symbol": sym, "period": period, "rows": n,
        "date_from": df["date"].iloc[0], "date_to": df["date"].iloc[-1],
        "class_counts": dist,
        "class_ratio": {k: round(v / n, 4) for k, v in dist.items()} if n else {},
        "majority_baseline": round(majority, 4),
        "seq_len": seq_len, "windows": max(0, n - seq_len + 1),
        "features": list(FEATURE_COLS), "label_rule": DL_LABEL_RULE,
        "colab_url": DL_NOTEBOOK_COLAB, "github_url": DL_NOTEBOOK_GITHUB,
    }


@router.get("/dl/dataset")
async def ml_dl_dataset(
    symbol: str = Query("005930.KS", description="종목 코드"),
    period: str = Query("2y", description="데이터 기간"),
    _user=Depends(get_current_user),
):
    """Colab 학습용 데이터셋 CSV 내려받기 (date, OHLCV, 피처 12개, target)."""
    sym = _dl_symbol(symbol)
    df = build_dl_dataset(await _dl_candles(sym, period))
    csv = df.to_csv(index=False, float_format="%.6g")
    fname = f"dl_dataset_{sym.replace('.', '_')}_{period}.csv"
    return Response(content=csv, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.get("/dl/notebook")
async def ml_dl_notebook(_user=Depends(get_current_user)):
    """Colab 노트북(.ipynb) 내려받기. GitHub 가 막힌 환경에서 '파일 업로드' 로 열 때 사용."""
    if not DL_NOTEBOOK_PATH.is_file():
        raise HTTPException(404, "노트북 파일이 서버에 없습니다 (notebooks/dl_lstm_transformer.ipynb)")
    return FileResponse(str(DL_NOTEBOOK_PATH), media_type="application/x-ipynb+json",
                        filename=DL_NOTEBOOK_PATH.name)
