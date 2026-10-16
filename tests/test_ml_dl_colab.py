"""딥러닝(LSTM·Transformer) Colab 연동 — /api/ml/dl/* 와 notebooks/dl_lstm_transformer.ipynb.

외부 시세·DB 없이 합성 캔들(conftest.make_candles)로 검증한다. torch 는 서버에 없으므로 노트북은
'구조·피처 수식 동일성'만 본다(실행 검증은 로컬 스크래치 venv 에서 nbclient 로 1회 수행, todo 6-62).
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.lib.session import get_current_user
from app.routes import ml as ml_routes
from app.services.quant_pipeline import FEATURE_COLS, feature_engineer, preprocess
from conftest import make_candles

NB_PATH = Path(__file__).resolve().parents[1] / "notebooks" / "dl_lstm_transformer.ipynb"


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(ml_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"id": "u", "email": "t@t"}
    return TestClient(app)


def _patch_candles(n=600, seed=7):
    return patch.object(ml_routes, "get_candles", AsyncMock(return_value={"symbol": "005930.KS", "candles": make_candles(n, seed=seed)}))


# ── 데이터셋 CSV ────────────────────────────────────────────────────

def test_dataset_csv_columns_and_rows(client):
    with _patch_candles():
        r = client.get("/api/ml/dl/dataset", params={"symbol": "005930.ks", "period": "2y"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert 'filename="dl_dataset_005930_KS_2y.csv"' in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert list(rows[0].keys()) == ["date", "open", "high", "low", "close", "volume", *FEATURE_COLS, "target"]
    # feature_engineer 가 dropna 한 행 수와 같고, 날짜는 오름차순
    expected = feature_engineer(preprocess(make_candles(600, seed=7)))
    assert len(rows) == len(expected) > 400
    dates = [row["date"] for row in rows]
    assert dates == sorted(dates) and len(set(dates)) == len(dates)
    assert {row["target"] for row in rows} <= {"-1", "0", "1"}


def test_dataset_values_match_pipeline(client):
    """CSV 의 피처 값이 앱 파이프라인(ml-compare 등이 쓰는 값)과 일치한다."""
    with _patch_candles():
        r = client.get("/api/ml/dl/dataset", params={"symbol": "005930.KS", "period": "2y"})
    got = pd.read_csv(io.StringIO(r.text))
    exp = feature_engineer(preprocess(make_candles(600, seed=7))).reset_index(drop=True)
    for c in FEATURE_COLS:
        np.testing.assert_allclose(got[c].values, exp[c].values, rtol=1e-4, atol=1e-6, err_msg=c)
    assert (got["target"].values == exp["target"].values).all()


def test_dataset_rejects_bad_symbol_period_and_short_data(client):
    with _patch_candles():
        assert client.get("/api/ml/dl/dataset", params={"symbol": "../etc", "period": "2y"}).status_code == 422
        assert client.get("/api/ml/dl/dataset", params={"symbol": "005930.KS", "period": "7d"}).status_code == 422
    with _patch_candles(n=50):
        assert client.get("/api/ml/dl/dataset", params={"symbol": "005930.KS", "period": "2y"}).status_code == 404


# ── 요약 ────────────────────────────────────────────────────────────

def test_summary_counts_and_baseline(client):
    with _patch_candles():
        r = client.get("/api/ml/dl/dataset/summary", params={"symbol": "005930.KS", "period": "5y"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["symbol"] == "005930.KS" and d["period"] == "5y"
    assert set(d["class_counts"]) == {"1", "0", "-1"}
    assert sum(d["class_counts"].values()) == d["rows"]
    assert d["majority_baseline"] == pytest.approx(max(d["class_counts"].values()) / d["rows"], abs=1e-3)
    assert d["windows"] == d["rows"] - d["seq_len"] + 1
    assert d["features"] == list(FEATURE_COLS)
    assert d["label_rule"]["horizon_days"] == 5
    assert d["colab_url"].startswith("https://colab.research.google.com/github/edumgt/lumina-invest/")
    assert d["date_from"] < d["date_to"]


# ── 노트북 ──────────────────────────────────────────────────────────

def test_notebook_download(client):
    r = client.get("/api/ml/dl/notebook")
    assert r.status_code == 200
    assert "dl_lstm_transformer.ipynb" in r.headers["content-disposition"]
    nb = json.loads(r.content)
    assert nb["nbformat"] == 4 and len(nb["cells"]) >= 12


def _nb_source(predicate):
    nb = json.loads(NB_PATH.read_text(encoding="utf-8"))
    for c in nb["cells"]:
        src = "".join(c["source"])
        if c["cell_type"] == "code" and predicate(src):
            return src
    raise AssertionError("cell not found")


def test_notebook_feature_formula_matches_app():
    """노트북의 build_features(Yahoo 대체 경로)가 앱 feature_engineer 와 같은 값을 낸다."""
    src = _nb_source(lambda s: "def build_features" in s)
    ns = {"np": np, "pd": pd, "HORIZON": 5}
    exec(src, ns)  # noqa: S102 — 저장소 내 노트북 셀
    raw = preprocess(make_candles(500, seed=3))
    got = ns["build_features"](raw)
    exp = feature_engineer(raw)
    assert ns["FEATURE_COLS"] == list(FEATURE_COLS)
    assert len(got) == len(exp)
    for c in FEATURE_COLS:
        np.testing.assert_allclose(got[c].values, exp[c].values, rtol=1e-9, atol=1e-9, err_msg=c)
    assert (got["target"].values == exp["target"].values).all()


def test_notebook_guards_against_time_series_leakage():
    """시간순 분할·train 에만 fit·경계 purge 가 코드에 실제로 들어 있다."""
    src = _nb_source(lambda s: "def make_windows" in s)
    assert "StandardScaler().fit(X_feat[:split])" in src
    assert "make_windows(0, split - HORIZON)" in src
    assert "make_windows(split, n)" in src
    cfg = _nb_source(lambda s: s.startswith("# ── 설정"))
    assert 'CSV_PATH = ""' in cfg and "SEQ_LEN   = 60" in cfg
    model = _nb_source(lambda s: "class StockLSTM" in s)
    assert "nn.LSTM(n_feat, 128" in model and "nn.LSTM(128, 64" in model
    trf = _nb_source(lambda s: "class StockTransformer" in s)
    assert "nn.TransformerEncoderLayer(d_model, heads, d_ff" in trf
