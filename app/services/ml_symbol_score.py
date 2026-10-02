"""종목별 실시간 ML 점수 — 캔들(OHLCV)만으로 5일 후 수익률을 Ridge 회귀로 예측해 [-1,1] 점수로 만든다.

SageMaker 배치 점수(quant_ai_scores)가 없는 종목의 보조 신호다. lightgbm 에 의존하지 않아(libgomp 없는 환경 포함) 어디서나 실행된다.
피처: 1·5·20일 수익률, MA5/20/60 대비 종가 비율, RSI14, 20일 변동성, 거래량 비율. 데이터 120행 미만이면 None.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

MIN_ROWS = 120
HORIZON = 5
SCALE_PCT = 5.0   # 5일 수익률 ±5% 를 점수 ±1 로 본다


def _series(candles: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray] | None:
    closes = np.array([float(c["close"]) for c in candles if c.get("close") not in (None, 0)], dtype=float)
    vols = np.array([float(c.get("volume") or 0) for c in candles if c.get("close") not in (None, 0)], dtype=float)
    if len(closes) < MIN_ROWS:
        return None
    return closes, vols


def _features(closes: np.ndarray, vols: np.ndarray) -> np.ndarray:
    def sma(a, n):
        out = np.full_like(a, np.nan)
        if len(a) >= n:
            c = np.cumsum(np.insert(a, 0, 0.0))
            out[n - 1:] = (c[n:] - c[:-n]) / n
        return out

    def rsi(a, n=14):
        d = np.diff(a, prepend=a[0])
        gain = np.where(d > 0, d, 0.0); loss = np.where(d < 0, -d, 0.0)
        ag, al = sma(gain, n), sma(loss, n)
        rs = np.divide(ag, al, out=np.full_like(ag, np.nan), where=al != 0)
        return 100 - 100 / (1 + rs)

    def ret(a, n):
        out = np.full_like(a, np.nan); out[n:] = a[n:] / a[:-n] - 1
        return out

    def vol20(a):
        r = ret(a, 1); out = np.full_like(a, np.nan)
        for i in range(20, len(a)):
            out[i] = np.nanstd(r[i - 19:i + 1])
        return out

    vsma = sma(vols, 20)
    vol_ratio = np.divide(vols, vsma, out=np.full_like(vols, np.nan), where=vsma != 0)
    return np.column_stack([
        ret(closes, 1), ret(closes, 5), ret(closes, 20),
        closes / sma(closes, 5) - 1, closes / sma(closes, 20) - 1, closes / sma(closes, 60) - 1,
        rsi(closes) / 100.0, vol20(closes), vol_ratio,
    ])


def predict_forward_return_pct(candles: list[dict[str, Any]]) -> float | None:
    """마지막 캔들 기준 HORIZON 일 후 수익률(%) 예측. 학습 불가하면 None."""
    try:
        from sklearn.linear_model import Ridge
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        return None
    pair = _series(candles)
    if pair is None:
        return None
    closes, vols = pair
    X = _features(closes, vols)
    y = np.full_like(closes, np.nan); y[:-HORIZON] = closes[HORIZON:] / closes[:-HORIZON] - 1
    mask = ~np.isnan(X).any(axis=1) & ~np.isnan(y)
    if mask.sum() < 60:
        return None
    scaler = StandardScaler().fit(X[mask])
    model = Ridge(alpha=1.0).fit(scaler.transform(X[mask]), y[mask])
    last = X[-1:]
    if np.isnan(last).any():
        return None
    return float(model.predict(scaler.transform(last))[0] * 100)


def score_from_return_pct(pct: float | None, scale_pct: float = SCALE_PCT) -> float | None:
    if pct is None:
        return None
    return round(max(-1.0, min(1.0, pct / scale_pct)), 4)


def symbol_score(candles: list[dict[str, Any]]) -> float | None:
    return score_from_return_pct(predict_forward_return_pct(candles))
