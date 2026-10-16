"""notebooks/dl_lstm_transformer.ipynb 생성기. 셀 본문을 여기서 고치고 `python notebooks/build_dl_notebook.py notebooks/dl_lstm_transformer.ipynb` 로 다시 만든다 (nbformat 필요).

로컬 실행 검증: 스크래치 venv 에 torch(cpu)·nbclient·ipykernel 을 넣고 설정 셀의 CSV_PATH 를 /api/ml/dl/dataset 로 받은 CSV 로 바꿔 nbclient 로 실행(2026-10-16 합성 캔들로 65초 통과)."""
import sys
import nbformat as nbf

MD = lambda s: nbf.v4.new_markdown_cell(s.strip("\n"))
CODE = lambda s: nbf.v4.new_code_cell(s.strip("\n"))

cells = []

cells.append(MD(r"""
# 딥러닝 시계열 예측 실습 — LSTM · Transformer (Lumina Invest 연동)

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/edumgt/lumina-invest/blob/main/notebooks/dl_lstm_transformer.ipynb)

이 노트북은 **fd.edumgt.co.kr › ML·딥러닝 › 딥러닝(LSTM·Transformer)** 화면과 짝을 이룹니다.
앱의 ML 모델 비교·회귀·튜닝 화면이 쓰는 **같은 피처 12개와 같은 레이블 규칙**으로 LSTM 과 Transformer 를 학습하고,
단순 기준선(다수 클래스·로지스틱 회귀)과 정직하게 비교합니다.

**따라 하는 순서**

1. 앱 화면에서 종목·기간을 고르고 **「CSV 내려받기」** 로 `dl_dataset_<종목>_<기간>.csv` 를 받습니다.
2. 아래 설정 셀의 `SYMBOL` 을 맞춘 뒤 **런타임 › 모두 실행**. 데이터 셀에서 업로드 창이 뜨면 받은 CSV 를 올립니다.
   (업로드를 취소하면 Yahoo Finance 에서 직접 받아 같은 피처를 계산합니다.)
3. 마지막 결과표에서 **검증 정확도가 다수 클래스 기준선을 얼마나 넘는지** 봅니다. 넘지 못하면 그 이유를 해석 가이드에서 찾아보세요.

> 레이블: 5거래일 후 수익률이 **+2% 초과 → 매수(1)**, **-2% 미만 → 매도(-1)**, 그 사이 → **관망(0)**.
> 피처: `ret_1, ret_5, ret_20, ma5_ratio, ma20_ratio, rsi, macd, macd_hist, bb_width, bb_pos, vol_ratio, atr`
"""))

cells.append(CODE(r"""
# ── 설정 ──────────────────────────────────────────────────────────────
SYMBOL   = "005930.KS"   # 앱에서 받은 CSV 의 종목과 맞추세요 (Yahoo 표기: 코스피 .KS, 코스닥 .KQ)
PERIOD   = "2y"          # CSV 가 없을 때 Yahoo Finance 에서 받을 기간 (1y/2y/3y/5y)
CSV_PATH = ""            # 업로드한 CSV 경로. 비우면 업로드 창(Colab) → 없으면 Yahoo 대체

SEQ_LEN   = 60           # 한 표본이 보는 과거 거래일 수 (앱 다이어그램의 seq=60)
HORIZON   = 5            # 레이블 기간(거래일). train/val 경계에서 이만큼 비워 누수를 막는다
VAL_RATIO = 0.2          # 뒤쪽 20% 를 검증 구간으로 (시간순, 셔플 금지)
EPOCHS    = 30
BATCH     = 64
LR        = 1e-3
PATIENCE  = 6            # 검증 손실이 이만큼 연속 개선되지 않으면 조기 종료
SEED      = 42
"""))

cells.append(CODE(r"""
# ── 라이브러리 ────────────────────────────────────────────────────────
import os, glob, math, random, warnings
import numpy as np
import pandas as pd
warnings.filterwarnings("ignore")

try:
    import torch, torch.nn as nn
except ImportError:
    raise SystemExit("PyTorch 가 없습니다. Colab 에서는 기본 설치되어 있고, 로컬이면 pip install torch 후 다시 실행하세요.")

from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, confusion_matrix, classification_report

random.seed(SEED); np.random.seed(SEED); torch.manual_seed(SEED)
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
CLASS_NAMES = ["매도(-1)", "관망(0)", "매수(1)"]   # 내부 인덱스 0,1,2 = target -1,0,1
print("torch", torch.__version__, "| device:", DEVICE)
"""))

cells.append(CODE(r"""
# ── 피처 계산 (앱 app/services/quant_pipeline.feature_engineer 와 동일) ──
# 앱에서 받은 CSV 에는 이미 들어 있으므로 Yahoo 대체 경로에서만 쓰인다. 수식을 바꾸면 앱 결과와 비교할 수 없게 된다.
FEATURE_COLS = ["ret_1", "ret_5", "ret_20", "ma5_ratio", "ma20_ratio",
                "rsi", "macd", "macd_hist", "bb_width", "bb_pos", "vol_ratio", "atr"]

def _ema(s, span): return s.ewm(span=span, adjust=False).mean()

def build_features(df: pd.DataFrame) -> pd.DataFrame:
    # df: DatetimeIndex, 열 open/high/low/close/volume
    out = df.copy()
    c = out["close"].astype(float); v = out["volume"].astype(float)
    out["ret_1"], out["ret_5"], out["ret_20"] = c.pct_change(1), c.pct_change(5), c.pct_change(20)
    ma5, ma20 = c.rolling(5).mean(), c.rolling(20).mean()
    out["ma5_ratio"]  = c / ma5.replace(0, np.nan)
    out["ma20_ratio"] = c / ma20.replace(0, np.nan)
    delta = c.diff(); up, down = delta.clip(lower=0), -delta.clip(upper=0)
    rs = up.rolling(14).mean() / down.rolling(14).mean().replace(0, np.nan)
    out["rsi"] = 100 - 100 / (1 + rs)
    macd_line = _ema(c, 12) - _ema(c, 26); signal = macd_line.ewm(span=9, adjust=False).mean()
    out["macd"], out["macd_hist"] = macd_line, macd_line - signal
    mid, std = c.rolling(20).mean(), c.rolling(20).std()
    upper, lower = mid + 2 * std, mid - 2 * std
    rng = (upper - lower).replace(0, np.nan)
    out["bb_width"] = rng / mid.replace(0, np.nan)
    out["bb_pos"]   = (c - lower) / rng
    out["vol_ratio"] = v / v.rolling(20).mean().replace(0, np.nan)
    h, l = out["high"].astype(float), out["low"].astype(float); pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    out["atr"] = tr.rolling(14).mean()
    fut = c.pct_change(HORIZON).shift(-HORIZON)
    out["target"] = 0
    out.loc[fut >  0.02, "target"] =  1
    out.loc[fut < -0.02, "target"] = -1
    return out.dropna()
"""))

cells.append(CODE(r"""
# ── 데이터 불러오기: ① CSV_PATH ② 현재 폴더의 dl_dataset_*.csv ③ Colab 업로드 ④ Yahoo Finance ──
df = None
if CSV_PATH and os.path.exists(CSV_PATH):
    df = pd.read_csv(CSV_PATH); src = CSV_PATH
else:
    found = sorted(glob.glob("dl_dataset_*.csv"))
    if found:
        df = pd.read_csv(found[-1]); src = found[-1]
    else:
        try:
            from google.colab import files   # Colab 에서만 존재
            print("앱에서 받은 dl_dataset_*.csv 를 올리세요. (취소하면 Yahoo Finance 로 대체)")
            up = files.upload()
            if up:
                src = list(up)[0]; df = pd.read_csv(src)
        except ImportError:
            pass

if df is None:
    try:
        import yfinance as yf
    except ImportError:
        import subprocess, sys
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "yfinance"], check=True)
        import yfinance as yf
    raw = yf.download(SYMBOL, period=PERIOD, interval="1d", auto_adjust=False, progress=False)
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    raw = raw.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].ffill().bfill()
    raw = raw[raw["close"] > 0]
    df = build_features(raw)
    df.insert(0, "date", df.index.strftime("%Y-%m-%d")); df = df.reset_index(drop=True)
    src = f"Yahoo Finance {SYMBOL} {PERIOD}"

missing = [c for c in FEATURE_COLS + ["target", "close"] if c not in df.columns]
assert not missing, f"열 누락: {missing} — 앱의 CSV 내려받기로 받은 파일인지 확인하세요"
df["date"] = pd.to_datetime(df["date"]); df = df.sort_values("date").reset_index(drop=True)
print(f"출처: {src}\n행 {len(df)}개 · {df['date'].iloc[0].date()} ~ {df['date'].iloc[-1].date()}")
df.tail(3)
"""))

cells.append(CODE(r"""
# ── 데이터 요약: 클래스 분포와 '아무것도 안 배운' 기준선 ──
counts = df["target"].value_counts().reindex([-1, 0, 1], fill_value=0)
ratio = counts / counts.sum()
majority_all = ratio.max()
print(pd.DataFrame({"건수": counts, "비율": ratio.round(3)}).rename(index={-1: "매도(-1)", 0: "관망(0)", 1: "매수(1)"}))
print(f"\n다수 클래스 비율(전체) = {majority_all:.3f}  ← 모델이 항상 이 클래스만 찍어도 나오는 정확도")
print(f"윈도우 길이 {SEQ_LEN} → 표본 {max(0, len(df) - SEQ_LEN + 1)}개 (딥러닝치고는 매우 적다는 점을 기억하세요)")
"""))

cells.append(CODE(r"""
# ── 윈도우 만들기 (시간순 분할 · 스케일러는 train 에만 fit · 경계 HORIZON 만큼 purge) ──
X_feat = df[FEATURE_COLS].values.astype(np.float32)
y_all  = (df["target"].values + 1).astype(np.int64)     # -1,0,1 → 0,1,2

n = len(df); n_val = int(n * VAL_RATIO); split = n - n_val
scaler = StandardScaler().fit(X_feat[:split])             # 검증 구간 통계를 보면 안 된다
X_s = scaler.transform(X_feat).astype(np.float32)

def make_windows(start, end):
    # 윈도우의 마지막 날 인덱스 i 가 [start, end) 에 들어가는 표본들
    idx = list(range(max(start, SEQ_LEN - 1), end))
    X = np.stack([X_s[i - SEQ_LEN + 1 : i + 1] for i in idx]) if idx else np.zeros((0, SEQ_LEN, len(FEATURE_COLS)), np.float32)
    return X, y_all[idx], idx

# train 의 마지막 HORIZON 개 레이블은 val 첫 날들의 가격으로 만들어지므로 버린다(누수 차단)
X_tr, y_tr, idx_tr = make_windows(0, split - HORIZON)
X_va, y_va, idx_va = make_windows(split, n)
assert len(X_tr) >= 50 and len(X_va) >= 20, "표본이 너무 적습니다. 기간을 늘리거나 SEQ_LEN 을 줄이세요"
print(f"train {X_tr.shape}  val {X_va.shape}")
print(f"train 기간 {df['date'].iloc[idx_tr[0]].date()} ~ {df['date'].iloc[idx_tr[-1]].date()} | val 기간 {df['date'].iloc[idx_va[0]].date()} ~ {df['date'].iloc[idx_va[-1]].date()}")
"""))

cells.append(CODE(r"""
# ── 기준선 2개: 다수 클래스, 로지스틱 회귀(마지막 날 피처만 사용 = 앱의 비시계열 모델과 같은 입력) ──
results = {}

maj = np.bincount(y_tr, minlength=3).argmax()
results["다수 클래스"] = accuracy_score(y_va, np.full_like(y_va, maj))

logreg = LogisticRegression(max_iter=2000, class_weight="balanced").fit(X_tr[:, -1, :], y_tr)
results["로지스틱 회귀(마지막 날)"] = accuracy_score(y_va, logreg.predict(X_va[:, -1, :]))

for k, v in results.items(): print(f"{k:<22} 검증 정확도 {v:.3f}")
"""))

cells.append(CODE(r"""
# ── 공통 학습 루프 (클래스 가중치 · 조기 종료 · 최고 검증 손실 시점 가중치 복원) ──
def to_loader(X, y, shuffle):
    ds = torch.utils.data.TensorDataset(torch.from_numpy(X), torch.from_numpy(y))
    return torch.utils.data.DataLoader(ds, batch_size=BATCH, shuffle=shuffle)

train_loader = to_loader(X_tr, y_tr, shuffle=True)    # 윈도우 단위 셔플은 OK (윈도우 안의 순서는 유지됨)
val_loader   = to_loader(X_va, y_va, shuffle=False)

cw = len(y_tr) / (3 * np.bincount(y_tr, minlength=3).clip(min=1))
class_weight = torch.tensor(cw, dtype=torch.float32, device=DEVICE)

@torch.no_grad()
def predict_proba(model, X):
    model.eval()
    out = model(torch.from_numpy(X).to(DEVICE))
    return torch.softmax(out, dim=1).cpu().numpy()

def train_model(model, name):
    model.to(DEVICE)
    opt = torch.optim.Adam(model.parameters(), lr=LR)
    crit = nn.CrossEntropyLoss(weight=class_weight)
    hist, best, best_state, bad = [], float("inf"), None, 0
    for ep in range(1, EPOCHS + 1):
        model.train(); tr_loss = 0.0
        for xb, yb in train_loader:
            xb, yb = xb.to(DEVICE), yb.to(DEVICE)
            opt.zero_grad(); loss = crit(model(xb), yb); loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0); opt.step()
            tr_loss += loss.item() * len(xb)
        model.eval(); va_loss = 0.0
        with torch.no_grad():
            for xb, yb in val_loader:
                xb, yb = xb.to(DEVICE), yb.to(DEVICE)
                va_loss += crit(model(xb), yb).item() * len(xb)
        tr_loss /= len(y_tr); va_loss /= len(y_va)
        va_acc = accuracy_score(y_va, predict_proba(model, X_va).argmax(1))
        hist.append((ep, tr_loss, va_loss, va_acc))
        if va_loss < best - 1e-4:
            best, best_state, bad = va_loss, {k: v.detach().clone() for k, v in model.state_dict().items()}, 0
        else:
            bad += 1
        if ep % 5 == 0 or ep == 1: print(f"[{name}] ep{ep:>3} train {tr_loss:.4f} | val {va_loss:.4f} | val acc {va_acc:.3f}")
        if bad >= PATIENCE: print(f"[{name}] 조기 종료 (ep{ep}, 최적 ep{ep - bad})"); break
    model.load_state_dict(best_state)
    return pd.DataFrame(hist, columns=["epoch", "train_loss", "val_loss", "val_acc"])

def report(model, name):
    pred = predict_proba(model, X_va).argmax(1)
    acc = accuracy_score(y_va, pred); results[name] = acc
    print(f"\n[{name}] 검증 정확도 {acc:.3f}")
    print(pd.DataFrame(confusion_matrix(y_va, pred, labels=[0, 1, 2]), index=[f"실제 {c}" for c in CLASS_NAMES], columns=[f"예측 {c}" for c in CLASS_NAMES]))
    print(classification_report(y_va, pred, labels=[0, 1, 2], target_names=CLASS_NAMES, zero_division=0))
"""))

cells.append(CODE(r"""
# ── ① LSTM (앱 다이어그램: LSTM(128) → LSTM(64) → Dense(32) → Dense(3)) ──
class StockLSTM(nn.Module):
    def __init__(self, n_feat=len(FEATURE_COLS), n_class=3):
        super().__init__()
        self.lstm1 = nn.LSTM(n_feat, 128, batch_first=True)
        self.lstm2 = nn.LSTM(128, 64, batch_first=True)
        self.drop  = nn.Dropout(0.2)
        self.head  = nn.Sequential(nn.Linear(64, 32), nn.ReLU(), nn.Linear(32, n_class))
    def forward(self, x):                 # x: (batch, seq, feat)
        out, _ = self.lstm1(x); out = self.drop(out)
        out, _ = self.lstm2(out); out = self.drop(out)
        return self.head(out[:, -1])      # 마지막 타임스텝만 사용

lstm = StockLSTM()
hist_lstm = train_model(lstm, "LSTM")
report(lstm, "LSTM")
"""))

cells.append(CODE(r"""
# ── ② Transformer 인코더 (앱 다이어그램: PosEnc → MHA(4, d=64) → FFN(128) → 평균 풀링 → Dense(3)) ──
class StockTransformer(nn.Module):
    def __init__(self, n_feat=len(FEATURE_COLS), d_model=64, heads=4, d_ff=128, layers=2, n_class=3, seq_len=SEQ_LEN):
        super().__init__()
        self.proj = nn.Linear(n_feat, d_model)
        self.pos  = nn.Parameter(torch.zeros(1, seq_len, d_model)); nn.init.trunc_normal_(self.pos, std=0.02)
        layer = nn.TransformerEncoderLayer(d_model, heads, d_ff, dropout=0.2, batch_first=True)
        self.enc  = nn.TransformerEncoder(layer, num_layers=layers)
        self.head = nn.Sequential(nn.LayerNorm(d_model), nn.Linear(d_model, n_class))
    def forward(self, x):
        h = self.proj(x) + self.pos[:, : x.size(1)]
        h = self.enc(h)
        return self.head(h.mean(dim=1))   # GlobalAvgPool1D

trf = StockTransformer()
hist_trf = train_model(trf, "Transformer")
report(trf, "Transformer")
"""))

cells.append(CODE(r"""
# ── 결과 비교표 + 학습 곡선 ──
table = pd.DataFrame({"검증 정확도": results}).sort_values("검증 정확도", ascending=False)
table["기준선 대비"] = (table["검증 정확도"] - results["다수 클래스"]).round(3)
print(table.round(3))

import matplotlib.pyplot as plt
fig, ax = plt.subplots(1, 2, figsize=(11, 3.5))
for h, name in [(hist_lstm, "LSTM"), (hist_trf, "Transformer")]:
    ax[0].plot(h["epoch"], h["train_loss"], "--", label=f"{name} train"); ax[0].plot(h["epoch"], h["val_loss"], label=f"{name} val")
    ax[1].plot(h["epoch"], h["val_acc"], label=name)
ax[1].axhline(results["다수 클래스"], color="gray", ls=":", label="다수 클래스")
ax[0].set_title("손실"); ax[1].set_title("검증 정확도"); ax[0].legend(fontsize=8); ax[1].legend(fontsize=8)
plt.tight_layout(); plt.show()
"""))

cells.append(CODE(r"""
# ── 가장 최근 윈도우로 '다음 5거래일' 예측 (학습 결과의 사용 예시) ──
last = X_s[-SEQ_LEN:][None]                       # (1, seq, feat)
rows = []
for name, m in [("LSTM", lstm), ("Transformer", trf)]:
    p = predict_proba(m, last)[0]
    rows.append({"모델": name, **{c: f"{v:.1%}" for c, v in zip(CLASS_NAMES, p)}, "판정": CLASS_NAMES[p.argmax()]})
p = logreg.predict_proba(last[:, -1, :])[0]
rows.append({"모델": "로지스틱 회귀", **{c: f"{v:.1%}" for c, v in zip(CLASS_NAMES, p)}, "판정": CLASS_NAMES[p.argmax()]})
print(f"기준일 {df['date'].iloc[-1].date()} (종가 {df['close'].iloc[-1]:,.0f}) 이후 {HORIZON}거래일 전망")
pd.DataFrame(rows)
"""))

cells.append(MD(r"""
## 결과를 읽는 법

| 관찰 | 뜻 | 다음 행동 |
|---|---|---|
| 두 모델 모두 **다수 클래스 기준선 ± 몇 %p** | 일봉 2~3년(표본 수백 개)으로는 패턴을 못 배운 것. 흔한 결과이며 **실패가 아니라 정상** | 기간 5y 이상, 여러 종목을 합쳐 학습, 레이블 임계값(±2%) 조정 |
| train 손실은 계속 떨어지는데 val 손실은 올라감 | 과적합. 모델이 날짜를 외우기 시작 | Dropout↑, hidden↓, 조기 종료 PATIENCE↓, 피처 수 줄이기 |
| 혼동행렬이 한 열에 몰림 | 클래스 가중치를 줘도 한 쪽만 찍음 | 임계값을 바꿔 클래스 균형 맞추기, 관망 제거 후 2-class 로 단순화 |
| 로지스틱 회귀가 딥러닝과 비슷하거나 더 좋음 | 마지막 날 피처만으로도 정보가 거의 다 담김 | 앱의 **모델 비교(7종)** 화면 LightGBM 과도 비교해 보세요. 딥러닝이 이기려면 시퀀스 자체에 정보가 있어야 합니다 |
| 최근 윈도우 예측 확률이 40% 안팎으로 고름 | 모델이 확신이 없음 | 이 신호로 매매하면 안 됩니다. 신뢰도 임계값(예: 60%) 아래는 관망 처리 |

## 시계열 딥러닝에서 꼭 지킬 것 (이 노트북이 지킨 방식)

- **셔플 금지, 시간순 분할** — 검증 구간은 항상 train 보다 미래여야 합니다. (윈도우 단위 셔플은 가능)
- **스케일러는 train 에만 fit** — 검증 구간 평균·표준편차를 보면 미래를 훔쳐보는 것입니다.
- **경계 purge** — 레이블이 5일 뒤 가격을 쓰므로 train 마지막 5개 표본은 val 첫 날들과 겹칩니다. 버렸습니다.
- **기준선 먼저** — 다수 클래스 정확도를 모르면 "정확도 45%" 가 좋은지 나쁜지 알 수 없습니다.
- **정확도만 보지 말 것** — 매수 클래스의 정밀도(precision)가 매매 성과를 좌우합니다. classification_report 를 읽으세요.

## 다음 실험

1. `SEQ_LEN` 20 / 120 으로 바꿔 보기 (짧으면 로지스틱과 같아지고, 길면 표본이 줄어듭니다)
2. 여러 종목 CSV 를 합쳐 학습하고 종목별로 검증 (앱에서 종목을 바꿔 가며 여러 번 내려받기)
3. 레이블을 수익률 자체(회귀)로 바꾸고 `nn.MSELoss` 로 학습 → 앱의 **회귀 분석** 화면 RMSE 와 비교
4. 결과가 안정적으로 기준선을 넘으면, 앱의 **인디케이터 › API 연동** 화면 구조(신호 계산 → 주문 생성)에 맞춰 신호를 내보내 보기
"""))

cells.append(CODE(r"""
# ── (선택) 검증 구간 예측을 CSV 로 저장해 앱 데이터와 대조 ──
out = pd.DataFrame({"date": df["date"].iloc[idx_va].dt.strftime("%Y-%m-%d").values,
                    "close": df["close"].iloc[idx_va].values,
                    "actual": y_va - 1,
                    "lstm": predict_proba(lstm, X_va).argmax(1) - 1,
                    "transformer": predict_proba(trf, X_va).argmax(1) - 1})
out.to_csv("dl_predictions.csv", index=False)
print("dl_predictions.csv 저장 (-1 매도 / 0 관망 / 1 매수)"); out.tail()
"""))

nb = nbf.v4.new_notebook()
nb["cells"] = cells
nb["metadata"] = {
    "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
    "language_info": {"name": "python"},
    "colab": {"provenance": [], "name": "dl_lstm_transformer.ipynb"},
}
nbf.write(nb, sys.argv[1])
print("written", sys.argv[1], len(cells), "cells")
