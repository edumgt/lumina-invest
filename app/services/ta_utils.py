"""공통 기술적 지표 · 성과지표 계산 유틸.

quant_pipeline.py / investment_research.py 가 각자 구현하던 RSI·MACD·볼린저·ATR·
샤프지수·MDD 계산을 하나로 모아, 같은 지표의 정의가 파일마다 갈라지는 것을 막는다.
RSI만 두 파일이 서로 다른 평활화(단순이동평균 vs Wilder EWM)를 쓰고 있어
`method` 파라미터로 기존 동작을 각각 그대로 보존한다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def rsi(close: pd.Series, period: int = 14, method: str = "sma") -> pd.Series:
    """상대강도지수. method='sma'는 단순이동평균, 'ewm'은 Wilder식 지수평활."""
    delta = close.diff()
    up, down = delta.clip(lower=0), -delta.clip(upper=0)
    if method == "ewm":
        gain = up.ewm(alpha=1 / period, adjust=False).mean()
        loss = down.ewm(alpha=1 / period, adjust=False).mean()
    else:
        gain = up.rolling(period).mean()
        loss = down.rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def sma(close: pd.Series, period: int) -> pd.Series:
    return close.rolling(period).mean()


def ema(close: pd.Series, span: int) -> pd.Series:
    return close.ewm(span=span, adjust=False).mean()


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    """반환: (MACD선, 시그널선, 히스토그램)."""
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line, macd_line - signal_line


def bollinger(close: pd.Series, period: int = 20, std_mult: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    """반환: (상단밴드, 중심선, 하단밴드)."""
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    return mid + std_mult * std, mid, mid - std_mult * std


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def sharpe_ratio(returns: pd.Series, periods_per_year: int = 252) -> float:
    if returns.empty or returns.std() == 0:
        return 0.0
    return float(returns.mean() / returns.std() * np.sqrt(periods_per_year))


def max_drawdown(cum_returns: pd.Series) -> float:
    """누적 수익 배수 시계열(1.0 시작)을 받아 최대낙폭을 음수 비율로 반환."""
    roll_max = cum_returns.cummax()
    drawdown = (cum_returns - roll_max) / roll_max.replace(0, np.nan)
    return float(drawdown.min())


def ichimoku(high: pd.Series, low: pd.Series, close: pd.Series, tenkan: int = 9, kijun: int = 26,
             senkou_b: int = 52, displacement: int = 26) -> dict[str, pd.Series]:
    """일목균형표. 선행스팬은 displacement 만큼 앞으로 밀어 '현재 봉 위의 구름'이 되도록 정렬한다.

    반환: tenkan(전환선)·kijun(기준선)·senkou_a/senkou_b(현재 봉에 적용되는 구름)·
          lead_a/lead_b(현재 봉에서 계산한 값, 26봉 뒤의 구름)·chikou(후행스팬, 26봉 전 위치로 보낸 종가).
    """
    mid = lambda n: (high.rolling(n).max() + low.rolling(n).min()) / 2  # noqa: E731
    tk, kj = mid(tenkan), mid(kijun)
    lead_a, lead_b = (tk + kj) / 2, mid(senkou_b)
    return {"tenkan": tk, "kijun": kj,
            "senkou_a": lead_a.shift(displacement), "senkou_b": lead_b.shift(displacement),
            "lead_a": lead_a, "lead_b": lead_b, "chikou": close.shift(-displacement)}
