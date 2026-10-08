"""재현 가능한 투자 리서치 계산: 지표, 전략 백테스트, 포트폴리오 최적화.

모든 신호는 해당 거래일 종가로 계산하고 다음 거래일 수익률에 적용한다.
이는 UI 데모에서도 미래 데이터를 미리 사용하는 오류를 피하기 위한 최소 규칙이다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.services.quant_pipeline import preprocess
from app.services import ta_utils as ta


def indicators(candles: list[dict]) -> pd.DataFrame:
    df = preprocess(candles)
    close = df["close"].astype(float)
    df["ma5"] = ta.sma(close, 5)
    df["ma20"] = ta.sma(close, 20)
    df["ma60"] = ta.sma(close, 60)
    df["rsi"] = ta.rsi(close, 14, method="ewm")
    macd_line, macd_signal, _ = ta.macd(close, 12, 26, 9)
    df["macd"] = macd_line
    df["macd_signal"] = macd_signal
    bb_upper, _bb_mid, bb_lower = ta.bollinger(close, 20, 2.0)
    df["bb_upper"] = bb_upper
    df["bb_lower"] = bb_lower
    df["volume_ma20"] = df["volume"].astype(float).rolling(20).mean()
    return df


def _position(df: pd.DataFrame, strategy: str) -> pd.Series:
    strategy = strategy.lower()
    if strategy == "buy_hold":
        return pd.Series(1.0, index=df.index)
    if strategy == "rsi":
        enter, exit_ = df["rsi"] < 30, df["rsi"] > 70
    elif strategy == "ma":
        enter = (df["ma5"] > df["ma20"]) & (df["ma5"].shift(1) <= df["ma20"].shift(1))
        exit_ = (df["ma5"] < df["ma20"]) & (df["ma5"].shift(1) >= df["ma20"].shift(1))
    elif strategy == "bollinger":
        enter, exit_ = df["close"] < df["bb_lower"], df["close"] > df["bb_upper"]
    else:  # composite: 추세 + 모멘텀을 동시에 확인
        enter = (df["ma5"] > df["ma20"]) & (df["rsi"] > 50) & (df["macd"] > df["macd_signal"])
        exit_ = (df["ma5"] < df["ma20"]) | (df["rsi"] > 75)
    state = pd.Series(np.nan, index=df.index)
    state.loc[enter] = 1.0
    state.loc[exit_] = 0.0
    return state.ffill().fillna(0.0)


def explain_strategy(df: pd.DataFrame, strategy: str, position: pd.Series) -> dict:
    """Explain the exact rule evaluated on the last two available candles."""
    x, prev = df.iloc[-1], df.iloc[-2]
    def row(label, rule, values, passed):
        return {"label": label, "rule": rule, "observed": values, "matched": bool(passed)}
    if strategy == "buy_hold":
        return {"kind": "buy_hold", "strategy": strategy, "as_of": df.index[-1].isoformat(),
                "action": "HOLD", "position": "보유", "scope": "계산 시작 시 매수 후 계속 보유합니다. 진입 비용은 반영하고 손절·익절과 마지막 날 강제 청산은 적용하지 않습니다."}
    if strategy == "rsi":
        buy = [row("과매도", "RSI(14) < 30", f"RSI {x.rsi:.2f}", x.rsi < 30)]
        sell = [row("과매수", "RSI(14) > 70", f"RSI {x.rsi:.2f}", x.rsi > 70)]
    elif strategy == "ma":
        values = f"전일 MA5/MA20 {prev.ma5:.2f}/{prev.ma20:.2f} → 당일 {x.ma5:.2f}/{x.ma20:.2f}"
        buy = [row("상향 교차", "전일 MA5 ≤ MA20, 당일 MA5 > MA20", values, prev.ma5 <= prev.ma20 and x.ma5 > x.ma20)]
        sell = [row("하향 교차", "전일 MA5 ≥ MA20, 당일 MA5 < MA20", values, prev.ma5 >= prev.ma20 and x.ma5 < x.ma20)]
    elif strategy == "bollinger":
        buy = [row("하단 이탈", "종가 < 볼린저 하단(20일·2σ)", f"종가 {x.close:.2f} / 하단 {x.bb_lower:.2f}", x.close < x.bb_lower)]
        sell = [row("상단 이탈", "종가 > 볼린저 상단(20일·2σ)", f"종가 {x.close:.2f} / 상단 {x.bb_upper:.2f}", x.close > x.bb_upper)]
    else:
        buy = [row("상승 추세", "MA5 > MA20", f"{x.ma5:.2f} / {x.ma20:.2f}", x.ma5 > x.ma20),
               row("모멘텀", "RSI(14) > 50", f"RSI {x.rsi:.2f}", x.rsi > 50),
               row("MACD", "MACD > 신호선", f"{x.macd:.3f} / {x.macd_signal:.3f}", x.macd > x.macd_signal)]
        sell = [row("추세 약화", "MA5 < MA20", f"{x.ma5:.2f} / {x.ma20:.2f}", x.ma5 < x.ma20),
                row("과매수", "RSI(14) > 75", f"RSI {x.rsi:.2f}", x.rsi > 75)]
    was, now = bool(position.iloc[-2]), bool(position.iloc[-1])
    action = "BUY" if now and not was else "SELL" if was and not now else "HOLD"
    return {"kind": "strategy_rules", "as_of": df.index[-1].isoformat(), "strategy": strategy,
            "action": action, "position": "보유" if now else "현금 대기", "buy_conditions": buy,
            "sell_conditions": sell, "buy_logic": "모두 충족", "sell_logic": "하나 이상 충족",
            "scope": "마지막 일봉의 기본 전략 신호입니다. 손절·익절을 반영한 최종 포지션이나 주문 지시가 아닙니다."}


def backtest_strategy(candles: list[dict], strategy: str = "composite", cost_bps: float = 10.0,
                      slippage_bps: float = 0.0, stop_loss_pct: float | None = None,
                      take_profit_pct: float | None = None) -> dict:
    """롱온리 일봉 백테스트. 수수료(cost_bps)·슬리피지(slippage_bps)는 포지션 변동 시 차감하고,
    손절·익절(%)은 진입가 대비 종가 기준으로 적용한다."""
    df = indicators(candles).dropna()
    if len(df) < 60:
        return {"error": f"데이터 부족: {len(df)}행 (최소 60 필요)"}
    return _backtest_frame(df, strategy, cost_bps, slippage_bps, stop_loss_pct, take_profit_pct)


def _backtest_frame(df: pd.DataFrame, strategy: str, cost_bps: float, slippage_bps: float,
                    stop_loss_pct: float | None, take_profit_pct: float | None) -> dict:
    from app.services.quant_pipeline import apply_stops
    position = _position(df, strategy)
    returns = df["close"].pct_change().fillna(0.0)
    # t일 장 마감 신호는 t+1일 수익률에만 적용
    held = position.shift(1).fillna(0.0)
    held, n_sl, n_tp = apply_stops(df["close"].astype(float), held, stop_loss_pct, take_profit_pct)
    turnover = held.diff().abs().fillna(held.abs())
    cost_rate = (max(0.0, float(cost_bps)) + max(0.0, float(slippage_bps))) / 10_000
    cost_series = turnover * cost_rate
    net = returns * held - cost_series
    equity = (1 + net).cumprod()
    benchmark = (1 + returns).cumprod()
    drawdown = equity / equity.cummax() - 1
    active = net[held > 0]
    trade_count = int((turnover > 0).sum())
    latest = df.iloc[-1]
    action = "BUY" if bool(position.iloc[-1]) and not bool(position.iloc[-2]) else "SELL" if not bool(position.iloc[-1]) and bool(position.iloc[-2]) else "HOLD"
    return {
        "strategy": strategy,
        "explanation": explain_strategy(df, strategy, position),
        "data_start": df.index[0].isoformat(),
        "data_end": df.index[-1].isoformat(),
        "data_points": len(df),
        "cost_bps": float(cost_bps),
        "slippage_bps": float(slippage_bps),
        "stop_loss_pct": stop_loss_pct,
        "take_profit_pct": take_profit_pct,
        "stop_loss_exits": n_sl,
        "take_profit_exits": n_tp,
        "gross_return_pct": round(((1 + returns * held).cumprod().iloc[-1] - 1) * 100, 2),
        "cost_pct": round(float(cost_series.sum()) * 100, 3),
        "total_return_pct": round((equity.iloc[-1] - 1) * 100, 2),
        "buy_hold_return_pct": round((benchmark.iloc[-1] - 1) * 100, 2),
        "sharpe_ratio": round(ta.sharpe_ratio(net), 3),
        "mdd_pct": round(ta.max_drawdown(equity) * 100, 2),
        "trade_count": trade_count,
        "holding_days": int((held > 0).sum()),
        "exposure_pct": round(float((held > 0).mean()) * 100, 2),
        "win_rate_pct": round(float((active > 0).mean() * 100) if len(active) else 0.0, 2),
        "latest_signal": action,
        "latest_indicators": {k: round(float(latest[k]), 3) for k in ("ma5", "ma20", "ma60", "rsi", "macd", "macd_signal", "bb_upper", "bb_lower") if pd.notna(latest[k])},
        "times": [x.isoformat() for x in df.index[-252:]],
        "cum_returns": [round(float(x - 1) * 100, 2) for x in equity.iloc[-252:]],
        "bh_returns": [round(float(x - 1) * 100, 2) for x in benchmark.iloc[-252:]],
    }


COMPARISON_STRATEGIES = ("rsi", "ma", "bollinger", "composite")


def compare_strategies(candles: list[dict], strategies: list[str], cost_bps: float = 10.0,
                       slippage_bps: float = 5.0, stop_loss_pct: float | None = None,
                       take_profit_pct: float | None = None) -> dict:
    """One candle snapshot and one indicator warm-up window for all results."""
    if not strategies or any(s not in COMPARISON_STRATEGIES for s in strategies):
        return {"error": "비교할 전략은 rsi, ma, bollinger, composite 중 하나 이상 선택하세요."}
    selected = list(dict.fromkeys(strategies))
    df = indicators(candles).dropna()
    if len(df) < 60:
        return {"error": f"데이터 부족: {len(df)}행 (최소 60 필요)"}
    results = [_backtest_frame(df, s, cost_bps, slippage_bps, stop_loss_pct, take_profit_pct) for s in selected]
    # A hold benchmark remains a hold strategy even when active strategies use stops.
    results.append(_backtest_frame(df, "buy_hold", cost_bps, slippage_bps, None, None))
    benchmark = results[-1]["total_return_pct"]
    for result in results:
        result["excess_return_pct"] = round(result["total_return_pct"] - benchmark, 2)
    return {"results": results, "data_start": df.index[0].isoformat(),
            "data_end": df.index[-1].isoformat(), "data_points": len(df),
            "settings": {"cost_bps": cost_bps, "slippage_bps": slippage_bps,
                         "stop_loss_pct": stop_loss_pct, "take_profit_pct": take_profit_pct},
            "benchmark_note": "단순 보유도 동일한 진입 비용을 반영합니다. 손절·익절은 선택한 매매 전략에만 적용하며, 마지막 날 강제 청산은 모든 전략에 적용하지 않습니다."}


def screen_pattern(candles: list[dict], model: str) -> dict:
    """선택한 패턴 규칙과 지표로 동일한 방식으로 스크리닝한다."""
    df = indicators(candles).dropna()
    if len(df) < 2:
        return {"error": "지표 계산용 데이터 부족"}
    x, prev = df.iloc[-1], df.iloc[-2]
    model = model.lower()
    if model == "rsi":
        score, reason = (30 - x.rsi) / 10, f"RSI {x.rsi:.1f}"
    elif model == "ma":
        score = 2 if x.ma5 > x.ma20 and prev.ma5 <= prev.ma20 else -2 if x.ma5 < x.ma20 and prev.ma5 >= prev.ma20 else (1 if x.ma5 > x.ma20 else -1)
        reason = f"MA5 {x.ma5:,.0f} / MA20 {x.ma20:,.0f}"
    elif model == "bollinger":
        score = 2 if x.close < x.bb_lower else -2 if x.close > x.bb_upper else 0
        reason = f"종가 {x.close:,.0f}, 밴드 {x.bb_lower:,.0f}~{x.bb_upper:,.0f}"
    else:  # lightgbm 선택 시에도 학습 모델을 가장한 값이 아닌 투명한 앙상블 점수 사용
        score = (1 if x.ma5 > x.ma20 else -1) + (1 if x.macd > x.macd_signal else -1) + (1 if x.rsi < 45 else -1 if x.rsi > 65 else 0)
        reason = f"추세·MACD·RSI 앙상블 (RSI {x.rsi:.1f})"
    signal = "BUY" if score >= 1 else "SELL" if score <= -1 else "HOLD"
    return {"signal": signal, "score": round(float(score), 2), "confidence": min(95, int(55 + abs(score) * 13)), "reason": reason, "rsi": round(float(x.rsi), 2), "price": round(float(x.close), 2)}


def ai_predict_return(candles: list[dict]) -> dict | None:
    """피처 엔지니어링 후 시계열 교차검증(TimeSeriesSplit)으로 회귀(수익률) +
    분류(방향성) 신호를 함께 산출한다.

    단일 80/20 분할 대신 여러 폴드의 평균 성능으로 모델을 고르고 confidence를
    매겨서, 한 번의 운 좋은/나쁜 분할에 흔들리지 않게 한다. 종목마다 즉석에서
    가볍게 학습하므로(그리드서치 없음) 유니버스 전체를 스캔해도 응답 지연이 크지
    않다. sklearn 미설치 시 None을 반환하며, 호출측은 과거 샤프비율만으로 순위를
    매기는 방식으로 대체한다.

    confidence(0~1)는 회귀 CV R^2와 분류 정확도/신뢰도를 반반 섞은 대략적인
    품질 지표다 — 캘리브레이션된 확률이 아니라, 호출측이 "이 종목의 AI 예측을
    얼마나 반영할지" 가중치로 쓰기 위한 상대적 지표다.
    """
    try:
        from sklearn.linear_model import Ridge
        from sklearn.ensemble import GradientBoostingRegressor
        from sklearn.model_selection import TimeSeriesSplit
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        return None

    from app.services.quant_pipeline import feature_engineer, FEATURE_COLS

    df = preprocess(candles)
    df = feature_engineer(df)  # 'target'(방향성 3-class 라벨) 포함
    if len(df) < 80:
        return None

    close = df["close"].astype(float)
    df = df.copy()
    df["fwd_return"] = close.pct_change(5).shift(-5)
    latest_features = df[FEATURE_COLS].iloc[-1:].values
    labeled = df.dropna(subset=["fwd_return"])
    if len(labeled) < 60:
        return None

    X = labeled[FEATURE_COLS].values
    y_reg = labeled["fwd_return"].values

    n_splits = 4 if len(X) >= 150 else 3
    tscv = TimeSeriesSplit(n_splits=n_splits)
    candidates = {
        "Ridge": lambda: Ridge(alpha=1.0),
        "GradientBoosting": lambda: GradientBoostingRegressor(
            n_estimators=80, max_depth=3, learning_rate=0.05, random_state=42
        ),
    }
    fold_scores: dict[str, list[float]] = {name: [] for name in candidates}
    for tr_idx, va_idx in tscv.split(X):
        if len(va_idx) < 3:
            continue
        scaler = StandardScaler().fit(X[tr_idx])
        X_tr_s, X_va_s = scaler.transform(X[tr_idx]), scaler.transform(X[va_idx])
        for name, make in candidates.items():
            m = make()
            m.fit(X_tr_s, y_reg[tr_idx])
            fold_scores[name].append(float(m.score(X_va_s, y_reg[va_idx])))

    avg_scores = {name: (float(np.mean(s)) if s else -999.0) for name, s in fold_scores.items()}
    best_name = max(avg_scores, key=avg_scores.get)
    best_r2 = avg_scores[best_name]

    # 최종 예측은 전체 라벨 데이터로 재학습한 best 모델을 사용
    scaler_full = StandardScaler().fit(X)
    final_model = candidates[best_name]()
    final_model.fit(scaler_full.transform(X), y_reg)
    pred_5d = float(final_model.predict(scaler_full.transform(latest_features))[0])

    # 방향성 분류 (LightGBM) — 회귀와 별도로 매수/관망/매도 신호 + 신뢰도 산출
    signal, signal_confidence, cls_acc, baseline = 0, 0.5, None, None
    try:
        import lightgbm as lgb
        y_cls = (labeled["target"].values + 1).astype(int)
        split = max(1, int(len(X) * 0.8))
        if len(X[split:]) >= 5 and len(set(y_cls[split:])) > 1:
            d_tr = lgb.Dataset(X[:split], label=y_cls[:split])
            d_va = lgb.Dataset(X[split:], label=y_cls[split:], reference=d_tr)
            params = {"objective": "multiclass", "num_class": 3, "num_leaves": 15,
                      "learning_rate": 0.08, "feature_fraction": 0.8, "verbosity": -1}
            lgb_model = lgb.train(params, d_tr, num_boost_round=100, valid_sets=[d_va],
                                   callbacks=[lgb.early_stopping(10, verbose=False), lgb.log_evaluation(-1)])
            va_pred = np.argmax(lgb_model.predict(X[split:]), axis=1)
            cls_acc = float((va_pred == y_cls[split:]).mean())
            baseline = float(np.bincount(y_cls[split:], minlength=3).max() / len(y_cls[split:]))  # 다수 클래스 정확도
        else:
            lgb_model = lgb.train(
                {"objective": "multiclass", "num_class": 3, "verbosity": -1},
                lgb.Dataset(X, label=y_cls), num_boost_round=50,
            )
        probs = lgb_model.predict(X[-1:])[0]
        signal = int(np.argmax(probs)) - 1
        signal_confidence = float(np.max(probs))
        try:
            from app.services.xai import explain_signal
            latest_vals = {f: float(v) for f, v in zip(FEATURE_COLS, latest_features[0])}
            explanation = explain_signal(lgb_model, latest_features[0], FEATURE_COLS, latest_vals, probs)
        except Exception:
            explanation = None
        # 품질 게이트: 검증 정확도가 '다수 클래스만 찍는' 기준선을 2%p 이상 못 넘으면 신호를 관망으로 낮춘다
        reliable = cls_acc is None or baseline is None or cls_acc >= baseline + 0.02
        if not reliable:
            signal, signal_confidence = 0, min(signal_confidence, 0.34)
            if explanation:
                explanation["quality_warning"] = (f"검증 정확도 {cls_acc:.2f}가 기준선(다수 클래스 {baseline:.2f}) 대비 유의하게 높지 않아 "
                                                  f"모델 신호를 '관망'으로 낮췼습니다. 아래 기여도는 참고용입니다.")
                explanation["summary"] = "⚠ " + explanation["quality_warning"] + " " + explanation["summary"]
    except Exception:
        signal = 1 if pred_5d > 0 else (-1 if pred_5d < 0 else 0)
        explanation = None

    # 5일 예측을 그대로 연환산(252/5 제곱)하면 예측이 조금만 튀어도 지수적으로
    # 폭발해 비현실적인 값이 나온다 (R^2가 음수인 종목에서 특히). 표시용으로 clip.
    pred_5d_clipped = max(-0.2, min(0.2, pred_5d))
    pred_ann = (1 + pred_5d_clipped) ** (252 / 5) - 1

    reg_confidence = max(0.0, min(1.0, (best_r2 + 1) / 2))  # R^2(대략 -1~1)를 0~1로 매핑
    cls_confidence = cls_acc if cls_acc is not None else signal_confidence
    confidence = round(0.5 * reg_confidence + 0.5 * cls_confidence, 4)

    return {
        "pred_5d_return_pct": round(pred_5d * 100, 3),
        "pred_ann_return_pct": round(max(-90.0, min(300.0, pred_ann * 100)), 2),
        "val_r2": round(best_r2, 4),
        "model": f"{best_name}(TimeSeriesSplit {n_splits}-fold)",
        "signal": signal,
        "signal_confidence": round(signal_confidence, 4),
        "confidence": confidence,
        "quality": {"cls_val_accuracy": None if cls_acc is None else round(cls_acc, 4),
                    "baseline_accuracy": None if baseline is None else round(baseline, 4),
                    "reliable": bool(cls_acc is None or baseline is None or cls_acc >= baseline + 0.02)},
        "explanation": explanation,  # XAI: SHAP 기여도 + 자연어 설명 (LightGBM 실패 시 None)
    }


def optimize_portfolio(stock_data: list[dict], risk_profile: str) -> dict:
    """최근 일수익률의 공분산을 이용한 long-only 최소분산/수익 혼합 배분."""
    series, labels = [], []
    for item in stock_data:
        df = preprocess(item["candles"])
        s = df["close"].astype(float).pct_change().rename(item["symbol"])
        if s.notna().sum() >= 60:
            series.append(s)
            labels.append(item["symbol"])
    if len(series) < 2:
        return {"error": "최적화에는 유효 종목 2개 이상이 필요합니다."}
    ret = pd.concat(series, axis=1).dropna().tail(756)   # 최대 3년으로 표본을 넓혀 최근 1년 모멘텀 편향 완화
    mu_raw = ret.mean().values * 252
    # 최근 실현 수익률은 미래 기대수익의 나쁜 추정치(66% 같은 값이 나온다). ±30%로 잘라 장기 주식 기대수익(7%)과
    # 반반 섞는(James-Stein식 축소) 값을 기대수익으로 쓴다.
    LONG_RUN_EQUITY_RETURN = 0.07
    mu = 0.5 * np.clip(mu_raw, -0.30, 0.30) + 0.5 * LONG_RUN_EQUITY_RETURN
    cov = ret.cov().values * 252 + np.eye(len(labels)) * 1e-6
    inv_cov = np.linalg.pinv(cov)
    min_var = inv_cov @ np.ones(len(labels)); min_var /= min_var.sum()
    # 위험 성향에 따라 동일가중과 기대수익 틸트를 점진적으로 확대
    tilt_strength = {"conservative": .10, "moderate": .35, "aggressive": .65}.get(risk_profile, .35)
    mu_score = np.maximum(mu - np.min(mu), 0) + 1e-6; mu_score /= mu_score.sum()
    weights = (1 - tilt_strength) * min_var + tilt_strength * mu_score
    weights = np.clip(weights, 0.05, 0.60); weights /= weights.sum()
    port_ret = float(weights @ mu)
    port_vol = float(np.sqrt(weights @ cov @ weights))
    return {"weights": {label: round(float(w) * 100, 1) for label, w in zip(labels, weights)},
            "expected_return_pct": round(port_ret * 100, 2), "expected_volatility_pct": round(port_vol * 100, 2),
            "expected_return_raw_pct": round(float(weights @ mu_raw) * 100, 2),
            "method": f"최근 {len(ret)}거래일 공분산 기반 long-only 최적화 · 기대수익은 실현수익(±30% 클립)과 장기 기대수익 7%를 반반 축소 (비용·세금 미반영)"}
