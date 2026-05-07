"""
다중 ML 모델 비교 · 클러스터링 · 하이퍼파라미터 튜닝 서비스.

커리큘럼: 퀀트를 위한 머신러닝과 딥러닝
 - 회귀(선형/Ridge/Lasso), SVM(SVR/SVC), RandomForest, GradientBoosting, MLP, Ensemble
 - 교차 검증(5-fold CV), GridSearchCV 하이퍼파라미터 튜닝
 - KMeans / 계층적 군집화 / DBSCAN 클러스터링
 - 계절성 분석(월별·요일·연말 효과)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Any

from app.services.quant_pipeline import preprocess, feature_engineer, FEATURE_COLS

try:
    from sklearn.linear_model import LinearRegression, Ridge, Lasso
    from sklearn.svm import SVR, SVC
    from sklearn.ensemble import (
        RandomForestClassifier, GradientBoostingClassifier, VotingClassifier,
        BaggingClassifier, StackingClassifier,
    )
    from sklearn.neural_network import MLPClassifier
    from sklearn.preprocessing import StandardScaler
    from sklearn.model_selection import cross_val_score, GridSearchCV
    from sklearn.cluster import KMeans, AgglomerativeClustering, DBSCAN
    from sklearn.metrics import silhouette_score
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False

try:
    import lightgbm as lgb
    HAS_LGB = True
except ImportError:
    HAS_LGB = False


# ── 공통 유틸 ─────────────────────────────────────────────────────────

def _prepare_xy(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    X = df[FEATURE_COLS].values
    y = df["target"].values  # -1, 0, 1
    return X, y


def _accuracy(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float((y_true == y_pred).mean())


# ── 1. 다중 모델 교차검증 비교 ────────────────────────────────────────

def compare_models(candles: list[dict]) -> dict:
    """
    7가지 ML 모델을 5-fold 교차검증으로 비교.

    Returns: {models: [{name, cv_mean, cv_std, train_acc, val_acc}], best_model}
    """
    if not HAS_SKLEARN:
        return {"error": "scikit-learn 미설치"}

    df = preprocess(candles)
    df = feature_engineer(df)
    if len(df) < 80:
        return {"error": f"데이터 부족: {len(df)}행 (최소 80 필요)"}

    X, y = _prepare_xy(df)
    split = int(len(X) * 0.8)
    X_tr, X_va = X[:split], X[split:]
    y_tr, y_va = y[:split], y[split:]

    scaler = StandardScaler()
    X_tr_s = scaler.fit_transform(X_tr)
    X_va_s = scaler.transform(X_va)
    X_s = scaler.transform(X)

    classifiers = {
        "LinearSVM":         SVC(kernel="linear", C=0.1, random_state=42, max_iter=500),
        "RBF SVM":           SVC(kernel="rbf",    C=1.0, random_state=42, max_iter=500),
        "RandomForest":      RandomForestClassifier(n_estimators=100, random_state=42, n_jobs=-1),
        "GradientBoosting":  GradientBoostingClassifier(n_estimators=100, random_state=42),
        "MLP":               MLPClassifier(hidden_layer_sizes=(64, 32), max_iter=300,
                                           early_stopping=True, random_state=42, verbose=False),
        "Ensemble(Voting)":  VotingClassifier(estimators=[
            ("rf",  RandomForestClassifier(n_estimators=50, random_state=42)),
            ("gb",  GradientBoostingClassifier(n_estimators=50, random_state=42)),
            ("mlp", MLPClassifier(hidden_layer_sizes=(32,), max_iter=200, random_state=42, verbose=False)),
        ], voting="hard"),
    }
    if HAS_LGB:
        classifiers["LightGBM"] = None  # handled separately

    results = []
    best_name = ""
    best_cv = -1.0

    for name, clf in classifiers.items():
        if name == "LightGBM":
            # LightGBM – train/val split only (no sklearn CV interface mismatch)
            y_lgb = (y + 1).astype(int)
            d_tr = lgb.Dataset(X_tr, label=(y_tr + 1).astype(int))
            d_va = lgb.Dataset(X_va, label=(y_va + 1).astype(int), reference=d_tr)
            params = {"objective": "multiclass", "num_class": 3,
                      "num_leaves": 31, "learning_rate": 0.05,
                      "feature_fraction": 0.8, "verbosity": -1, "random_state": 42}
            model = lgb.train(params, d_tr, num_boost_round=100,
                              valid_sets=[d_va],
                              callbacks=[lgb.early_stopping(10, verbose=False),
                                         lgb.log_evaluation(-1)])
            tr_pred = np.argmax(model.predict(X_tr), axis=1)
            va_pred = np.argmax(model.predict(X_va), axis=1)
            tr_acc = _accuracy((y_tr + 1).astype(int), tr_pred)
            va_acc = _accuracy((y_va + 1).astype(int), va_pred)
            cv_mean = va_acc
            cv_std = 0.0
        else:
            clf.fit(X_tr_s, y_tr)
            cv_scores = cross_val_score(clf, X_s, y, cv=5, scoring="accuracy", n_jobs=-1)
            tr_acc = _accuracy(y_tr, clf.predict(X_tr_s))
            va_acc = _accuracy(y_va, clf.predict(X_va_s))
            cv_mean = float(cv_scores.mean())
            cv_std  = float(cv_scores.std())

        results.append({
            "name":      name,
            "cv_mean":   round(cv_mean, 4),
            "cv_std":    round(cv_std, 4),
            "train_acc": round(tr_acc, 4),
            "val_acc":   round(va_acc, 4),
        })
        if cv_mean > best_cv:
            best_cv   = cv_mean
            best_name = name

    results.sort(key=lambda r: r["cv_mean"], reverse=True)
    return {
        "models":     results,
        "best_model": best_name,
        "data_rows":  len(df),
        "note":       "5-fold 교차검증 평균 정확도 기준 정렬",
    }


# ── 2. 하이퍼파라미터 튜닝 (GridSearchCV) ────────────────────────────

PARAM_GRIDS: dict[str, tuple[Any, dict]] = {
    "svm": (
        SVC(random_state=42) if HAS_SKLEARN else None,
        {"C": [0.1, 1, 10], "kernel": ["linear", "rbf"], "gamma": ["scale", "auto"]},
    ),
    "rf": (
        RandomForestClassifier(random_state=42) if HAS_SKLEARN else None,
        {"n_estimators": [50, 100, 200], "max_depth": [3, 5, None], "min_samples_split": [2, 5]},
    ),
    "gb": (
        GradientBoostingClassifier(random_state=42) if HAS_SKLEARN else None,
        {"n_estimators": [50, 100], "learning_rate": [0.05, 0.1, 0.2], "max_depth": [3, 5]},
    ),
}


def tune_hyperparams(candles: list[dict], model_name: str = "rf") -> dict:
    """
    GridSearchCV 하이퍼파라미터 탐색.

    model_name: 'svm' | 'rf' | 'gb'
    Returns: {best_params, best_score, all_results}
    """
    if not HAS_SKLEARN:
        return {"error": "scikit-learn 미설치"}
    if model_name not in PARAM_GRIDS:
        return {"error": f"지원 모델: {list(PARAM_GRIDS.keys())}"}

    df = preprocess(candles)
    df = feature_engineer(df)
    if len(df) < 60:
        return {"error": f"데이터 부족: {len(df)}행"}

    X, y = _prepare_xy(df)
    scaler = StandardScaler()
    X_s = scaler.fit_transform(X)

    estimator, param_grid = PARAM_GRIDS[model_name]
    gs = GridSearchCV(estimator, param_grid, cv=3, scoring="accuracy", n_jobs=-1, refit=True)
    gs.fit(X_s, y)

    cv_df = pd.DataFrame(gs.cv_results_)
    top = cv_df.nlargest(5, "mean_test_score")[
        ["params", "mean_test_score", "std_test_score"]
    ].to_dict(orient="records")

    return {
        "model":       model_name,
        "best_params": gs.best_params_,
        "best_score":  round(float(gs.best_score_), 4),
        "top_results": [
            {
                "params": r["params"],
                "mean":   round(float(r["mean_test_score"]), 4),
                "std":    round(float(r["std_test_score"]), 4),
            }
            for r in top
        ],
    }


# ── 3. 클러스터링 ─────────────────────────────────────────────────────

def cluster_stocks(stocks_data: list[dict]) -> dict:
    """
    여러 종목의 피처를 KMeans로 군집화.

    stocks_data: [{"symbol": str, "candles": [...]}]
    Returns: {clusters: [{symbol, cluster, features}], centroids, method}
    """
    if not HAS_SKLEARN:
        return {"error": "scikit-learn 미설치"}
    if len(stocks_data) < 2:
        return {"error": "종목 2개 이상 필요"}

    rows = []
    symbols = []
    for item in stocks_data:
        try:
            df = preprocess(item["candles"])
            df = feature_engineer(df)
            if len(df) < 20:
                continue
            feat_mean = df[FEATURE_COLS].mean().values
            rows.append(feat_mean)
            symbols.append(item["symbol"])
        except Exception:
            continue

    if len(rows) < 2:
        return {"error": "유효한 종목 부족"}

    X = np.array(rows)
    scaler = StandardScaler()
    X_s = scaler.fit_transform(X)

    n_clusters = min(3, len(rows))
    km = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    labels = km.fit_predict(X_s)

    sil = float(silhouette_score(X_s, labels)) if len(set(labels)) > 1 else 0.0

    cluster_items = []
    for sym, lab, feat in zip(symbols, labels, rows):
        cluster_items.append({
            "symbol":  sym,
            "cluster": int(lab),
            "features": {
                col: round(float(v), 4) for col, v in zip(FEATURE_COLS, feat)
            },
        })

    # 군집 요약
    cluster_summary = []
    for c in range(n_clusters):
        members = [ci for ci in cluster_items if ci["cluster"] == c]
        cluster_summary.append({
            "cluster":   c,
            "members":   [m["symbol"] for m in members],
            "count":     len(members),
        })

    return {
        "method":           "KMeans",
        "n_clusters":       n_clusters,
        "silhouette_score": round(sil, 4),
        "clusters":         cluster_items,
        "summary":          cluster_summary,
    }


# ── 4. 계절성 분석 ────────────────────────────────────────────────────

def seasonality_analysis(candles: list[dict]) -> dict:
    """
    월별 · 요일별 · 연말 효과 분석.

    Returns: {monthly, weekday, year_end_effect}
    """
    df = preprocess(candles)
    df["ret"] = df["close"].pct_change()
    df = df.dropna(subset=["ret"])

    if len(df) < 60:
        return {"error": f"데이터 부족: {len(df)}행"}

    # 월별 평균 수익률
    monthly = (
        df.groupby(df.index.month)["ret"]
        .agg(["mean", "std", "count"])
        .reset_index()
        .rename(columns={"time": "month"})
    )
    month_names = ["1월","2월","3월","4월","5월","6월",
                   "7월","8월","9월","10월","11월","12월"]
    monthly_result = [
        {
            "month":    month_names[int(r["month"]) - 1],
            "avg_ret":  round(float(r["mean"]) * 100, 3),
            "std":      round(float(r["std"])  * 100, 3),
            "count":    int(r["count"]),
        }
        for _, r in monthly.iterrows()
    ]

    # 요일별 평균 수익률
    day_names = ["월","화","수","목","금"]
    weekday = (
        df.groupby(df.index.dayofweek)["ret"]
        .agg(["mean", "std", "count"])
        .reset_index()
        .rename(columns={"time": "dayofweek"})
    )
    weekday_result = [
        {
            "day":     day_names[int(r["dayofweek"])] if int(r["dayofweek"]) < 5 else str(int(r["dayofweek"])),
            "avg_ret": round(float(r["mean"]) * 100, 3),
            "std":     round(float(r["std"])  * 100, 3),
            "count":   int(r["count"]),
        }
        for _, r in weekday.iterrows()
        if int(r["dayofweek"]) < 5
    ]

    # 연말 효과 (11~12월 vs 나머지)
    df["is_year_end"] = df.index.month.isin([11, 12])
    ye_mean  = float(df.loc[df["is_year_end"],  "ret"].mean()) * 100
    non_mean = float(df.loc[~df["is_year_end"], "ret"].mean()) * 100

    return {
        "monthly":         monthly_result,
        "weekday":         weekday_result,
        "year_end_effect": {
            "nov_dec_avg_ret_pct": round(ye_mean,  3),
            "other_avg_ret_pct":   round(non_mean, 3),
            "premium_pct":         round(ye_mean - non_mean, 3),
        },
    }


# ── 5. 회귀 모델 (Linear / Ridge / Lasso + SVR) ─────────────────────

def regression_forecast(candles: list[dict]) -> dict:
    """
    선형회귀 / Ridge / Lasso / SVR 로 5일 후 수익률 예측 비교.

    Returns: {models: [{name, val_rmse, val_mae}]}
    """
    if not HAS_SKLEARN:
        return {"error": "scikit-learn 미설치"}

    df = preprocess(candles)
    df = feature_engineer(df)
    if len(df) < 60:
        return {"error": f"데이터 부족: {len(df)}행"}

    # 회귀 타깃: 5일 후 실제 수익률 (연속값)
    c = df["close"].astype(float)
    fut = c.pct_change(5).shift(-5)
    df = df.copy()
    df["reg_target"] = fut
    df = df.dropna(subset=["reg_target"])

    X = df[FEATURE_COLS].values
    y = df["reg_target"].values

    split = int(len(X) * 0.8)
    X_tr, X_va = X[:split], X[split:]
    y_tr, y_va = y[:split], y[split:]

    scaler = StandardScaler()
    X_tr_s = scaler.fit_transform(X_tr)
    X_va_s = scaler.transform(X_va)

    regressors = {
        "LinearRegression": LinearRegression(),
        "Ridge(α=1.0)":     Ridge(alpha=1.0),
        "Lasso(α=0.001)":   Lasso(alpha=0.001, max_iter=2000),
        "SVR(RBF)":         SVR(kernel="rbf", C=1.0, epsilon=0.01),
    }

    results = []
    for name, reg in regressors.items():
        reg.fit(X_tr_s, y_tr)
        pred = reg.predict(X_va_s)
        rmse = float(np.sqrt(((pred - y_va) ** 2).mean()))
        mae  = float(np.abs(pred - y_va).mean())
        results.append({
            "name":     name,
            "val_rmse": round(rmse * 100, 4),
            "val_mae":  round(mae  * 100, 4),
        })

    results.sort(key=lambda r: r["val_rmse"])
    return {
        "models":     results,
        "data_rows":  len(df),
        "note":       "RMSE/MAE 단위: %, 낮을수록 좋음",
    }
