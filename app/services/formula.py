"""자유 산식 커스텀 지표 엔진 (안전한 수식 DSL).

사용자가 문자열로 쓴 산식을 Python AST 로 파싱해 화이트리스트 노드·함수만 허용하고,
OHLCV 시리즈(pandas) 위에서 벡터 연산으로 계산한다. 모든 함수는 과거 봉만 참조하는 causal 연산이며
shift() 는 양수(과거)만 허용해 미래 데이터 참조(look-ahead)를 문법 단계에서 차단한다.

예)
  indicator : (close - sma(close, 20)) / std(close, 20)          # 20일 z-score
  buy       : crossover(ema(close, 5), ema(close, 20)) and rsi(close, 14) < 60
  sell      : crossunder(ema(close, 5), ema(close, 20)) or rsi(close, 14) > 75
params 는 {"n": 20} 처럼 이름→숫자 매핑으로 산식 안에서 변수처럼 쓴다.
"""
from __future__ import annotations

import ast
import hashlib
import json
import math
import re
from typing import Any

import numpy as np
import pandas as pd

from app.services import ta_utils as ta
from app.services.quant_pipeline import preprocess, backtest


class FormulaError(ValueError):
    pass


MAX_EXPR_LEN = 2000
MAX_NODES = 400
SERIES_VARS = ("open", "high", "low", "close", "volume")


# ── DSL 함수 (모두 causal) ────────────────────────────────────────────────

def _s(x, ref: pd.Series) -> pd.Series:
    """스칼라를 시리즈로 브로드캐스트."""
    if isinstance(x, pd.Series):
        return x
    return pd.Series(float(x), index=ref.index)


def _n(v, name: str, lo: int = 1, hi: int = 500) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise FormulaError(f"{name}: 기간은 정수여야 합니다.")
    if not lo <= n <= hi:
        raise FormulaError(f"{name}: 기간은 {lo}~{hi} 사이여야 합니다.")
    return n


def _series_arg(x, name):
    if not isinstance(x, pd.Series):
        raise FormulaError(f"{name}: 첫 인자는 시리즈(close 등)여야 합니다.")
    return x


def make_functions(df: pd.DataFrame) -> dict[str, Any]:
    high, low, close = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)

    def f_sma(x, n): return ta.sma(_series_arg(x, "sma"), _n(n, "sma"))
    def f_ema(x, n): return ta.ema(_series_arg(x, "ema"), _n(n, "ema"))
    def f_rsi(x, n=14): return ta.rsi(_series_arg(x, "rsi"), _n(n, "rsi", 2))
    def f_macd(x, f=12, s=26, sig=9): return ta.macd(_series_arg(x, "macd"), _n(f, "macd"), _n(s, "macd"), _n(sig, "macd"))[0]
    def f_macd_signal(x, f=12, s=26, sig=9): return ta.macd(_series_arg(x, "macd_signal"), _n(f, "macd_signal"), _n(s, "macd_signal"), _n(sig, "macd_signal"))[1]
    def f_macd_hist(x, f=12, s=26, sig=9): return ta.macd(_series_arg(x, "macd_hist"), _n(f, "macd_hist"), _n(s, "macd_hist"), _n(sig, "macd_hist"))[2]
    def f_bb_upper(x, n=20, k=2.0): return ta.bollinger(_series_arg(x, "bb_upper"), _n(n, "bb_upper", 2), float(k))[0]
    def f_bb_mid(x, n=20, k=2.0): return ta.bollinger(_series_arg(x, "bb_mid"), _n(n, "bb_mid", 2), float(k))[1]
    def f_bb_lower(x, n=20, k=2.0): return ta.bollinger(_series_arg(x, "bb_lower"), _n(n, "bb_lower", 2), float(k))[2]
    def f_atr(n=14): return ta.atr(high, low, close, _n(n, "atr"))
    def f_std(x, n): return _series_arg(x, "std").rolling(_n(n, "std", 2)).std()
    def f_highest(x, n): return _series_arg(x, "highest").rolling(_n(n, "highest")).max()
    def f_lowest(x, n): return _series_arg(x, "lowest").rolling(_n(n, "lowest")).min()
    def f_sum(x, n): return _series_arg(x, "sum").rolling(_n(n, "sum")).sum()
    def f_mean(x, n): return f_sma(x, n)
    def f_shift(x, n=1):
        k = _n(n, "shift", 1, 500)  # 0/음수 금지 → 미래 참조 차단
        return _series_arg(x, "shift").shift(k)
    def f_change(x, n=1): return _series_arg(x, "change").diff(_n(n, "change"))
    def f_pct_change(x, n=1): return _series_arg(x, "pct_change").pct_change(_n(n, "pct_change"))
    def f_zscore(x, n=20):
        x = _series_arg(x, "zscore"); k = _n(n, "zscore", 2)
        return (x - x.rolling(k).mean()) / x.rolling(k).std().replace(0, np.nan)
    def f_normalize(x, n=20):
        x = _series_arg(x, "normalize"); k = _n(n, "normalize", 2)
        lo_, hi_ = x.rolling(k).min(), x.rolling(k).max()
        return (x - lo_) / (hi_ - lo_).replace(0, np.nan)
    def f_rank(x, n=20):
        x = _series_arg(x, "rank"); k = _n(n, "rank", 2)
        return x.rolling(k).apply(lambda w: (w[:-1] < w[-1]).mean() if len(w) > 1 else np.nan, raw=True)
    def f_crossover(a, b):
        a, b = _s(a, close), _s(b, close)
        return ((a > b) & (a.shift(1) <= b.shift(1))).fillna(False)
    def f_crossunder(a, b):
        a, b = _s(a, close), _s(b, close)
        return ((a < b) & (a.shift(1) >= b.shift(1))).fillna(False)
    def f_where(cond, a, b):
        cond = _s(cond, close).astype(bool)
        return pd.Series(np.where(cond, _s(a, close), _s(b, close)), index=close.index)
    def f_abs(x): return _s(x, close).abs()
    def f_log(x): return np.log(_s(x, close).where(lambda v: v > 0))
    def f_sqrt(x): return np.sqrt(_s(x, close).clip(lower=0))
    def f_sign(x): return np.sign(_s(x, close))
    def f_clip(x, lo_, hi_): return _s(x, close).clip(float(lo_), float(hi_))
    def f_max(a, b): return np.maximum(_s(a, close), _s(b, close))
    def f_min(a, b): return np.minimum(_s(a, close), _s(b, close))
    def f_nz(x, v=0.0): return _s(x, close).fillna(float(v))
    def f_typical(): return (high + low + close) / 3
    def f_vwap(n=20):
        tp = (high + low + close) / 3; v = df["volume"].astype(float); k = _n(n, "vwap")
        return (tp * v).rolling(k).sum() / v.rolling(k).sum().replace(0, np.nan)
    def f_obv():
        v = df["volume"].astype(float)
        return (np.sign(close.diff()).fillna(0) * v).cumsum()
    def f_barssince(cond):
        c = _s(cond, close).astype(bool).values
        out, last = np.full(len(c), np.nan), None
        for i, flag in enumerate(c):
            if flag: last = i
            if last is not None: out[i] = i - last
        return pd.Series(out, index=close.index)

    return {
        "sma": f_sma, "ema": f_ema, "rsi": f_rsi, "macd": f_macd, "macd_signal": f_macd_signal, "macd_hist": f_macd_hist,
        "bb_upper": f_bb_upper, "bb_mid": f_bb_mid, "bb_lower": f_bb_lower, "atr": f_atr, "std": f_std,
        "highest": f_highest, "lowest": f_lowest, "sum": f_sum, "mean": f_mean, "shift": f_shift, "change": f_change,
        "pct_change": f_pct_change, "zscore": f_zscore, "normalize": f_normalize, "rank": f_rank,
        "crossover": f_crossover, "crossunder": f_crossunder, "where": f_where, "abs": f_abs, "log": f_log, "sqrt": f_sqrt,
        "sign": f_sign, "clip": f_clip, "max": f_max, "min": f_min, "nz": f_nz, "typical": f_typical, "vwap": f_vwap,
        "obv": f_obv, "barssince": f_barssince,
    }


FUNCTION_DOCS = [
    ("sma(x, n)", "단순이동평균", "sma(close, 20)"), ("ema(x, n)", "지수이동평균", "ema(close, 12)"),
    ("rsi(x, n=14)", "RSI", "rsi(close, 14)"), ("macd(x, 12, 26, 9)", "MACD 선 (macd_signal, macd_hist 도 있음)", "macd(close) > macd_signal(close)"),
    ("bb_upper/bb_mid/bb_lower(x, n=20, k=2)", "볼린저 밴드", "close < bb_lower(close, 20, 2)"), ("atr(n=14)", "평균진폭 (high/low/close 자동)", "atr(14) / close"),
    ("std(x, n)", "이동 표준편차", "std(close, 20)"), ("highest(x, n) / lowest(x, n)", "기간 최고/최저", "close > highest(shift(close,1), 20)"),
    ("shift(x, n≥1)", "n봉 전 값 (미래 참조 불가)", "shift(close, 1)"), ("change(x, n) / pct_change(x, n)", "차분 / 수익률", "pct_change(close, 5)"),
    ("zscore(x, n)", "이동 z-score 정규화", "zscore(close, 20)"), ("normalize(x, n)", "이동 min-max 정규화(0~1)", "normalize(volume, 20)"),
    ("rank(x, n)", "이동 백분위 순위(0~1)", "rank(close, 60)"), ("crossover(a, b) / crossunder(a, b)", "상향/하향 교차", "crossover(ema(close,5), ema(close,20))"),
    ("where(cond, a, b)", "조건 선택", "where(rsi(close) < 30, 1, 0)"), ("abs, log, sqrt, sign, clip(x,lo,hi), max(a,b), min(a,b), nz(x,v)", "수학 함수", "clip(zscore(close,20), -3, 3)"),
    ("typical()", "(고+저+종)/3", "typical()"), ("vwap(n)", "n봉 거래량가중평균가", "close / vwap(20) - 1"),
    ("obv()", "누적 거래량(OBV)", "zscore(obv(), 20)"), ("barssince(cond)", "조건 충족 후 경과 봉 수", "barssince(crossover(ema(close,5), ema(close,20))) < 3"),
    ("변수", "open high low close volume + params 의 키", "close / sma(close, n) - 1  (params: {\"n\": 20})"),
    ("연산", "+ - * / ** % 비교(< <= > >= == !=) and or not", "rsi(close) < 30 and close > sma(close, 200)"),
]

TEMPLATES = [
    {"name": "Z-Score 평균회귀", "indicator_expr": "zscore(close, n)", "buy_expr": "zscore(close, n) < -2", "sell_expr": "zscore(close, n) > 0", "params": {"n": 20},
     "description": "20일 z-score 가 -2 아래로 과매도되면 매수, 평균(0) 복귀 시 매도"},
    {"name": "거래량 확인 돌파", "indicator_expr": "close / highest(shift(close, 1), n) - 1", "buy_expr": "close > highest(shift(close, 1), n) and volume > sma(volume, n) * 1.5",
     "sell_expr": "close < lowest(shift(close, 1), m)", "params": {"n": 20, "m": 10}, "description": "20일 신고가를 평균 1.5배 거래량으로 돌파하면 매수, 10일 신저가 이탈 시 매도"},
    {"name": "RSI+추세 필터", "indicator_expr": "rsi(close, 14)", "buy_expr": "rsi(close, 14) < 35 and close > sma(close, 200)", "sell_expr": "rsi(close, 14) > 70",
     "params": {}, "description": "장기 상승 추세(200일선 위)에서만 RSI 과매도 매수"},
    {"name": "변동성 정규화 모멘텀", "indicator_expr": "clip(pct_change(close, 20) / (std(pct_change(close, 1), 20) * sqrt(20)), -3, 3)",
     "buy_expr": "clip(pct_change(close, 20) / (std(pct_change(close, 1), 20) * sqrt(20)), -3, 3) > 1", "sell_expr": "pct_change(close, 20) < 0", "params": {},
     "description": "20일 수익률을 변동성으로 나눈 정규화 모멘텀이 1 이상이면 매수"},
]


# ── 안전 파서/평가기 ───────────────────────────────────────────────────────

_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.Call, ast.Name, ast.Constant, ast.Load,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.USub, ast.UAdd, ast.Not, ast.And, ast.Or,
    ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq, ast.BitAnd, ast.BitOr, ast.Invert, ast.keyword,
)


def parse_expr(expr: str) -> ast.Expression:
    if not isinstance(expr, str) or not expr.strip():
        raise FormulaError("산식이 비어 있습니다.")
    if len(expr) > MAX_EXPR_LEN:
        raise FormulaError(f"산식이 너무 깁니다 ({MAX_EXPR_LEN}자 초과).")
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as exc:
        raise FormulaError(f"문법 오류: {exc.msg} (위치 {exc.offset})")
    count = 0
    for node in ast.walk(tree):
        count += 1
        if not isinstance(node, _ALLOWED_NODES):
            raise FormulaError(f"허용되지 않는 구문입니다: {type(node).__name__}")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float, bool)):
            raise FormulaError("숫자 상수만 사용할 수 있습니다.")
        if isinstance(node, ast.Call) and not isinstance(node.func, ast.Name):
            raise FormulaError("함수는 이름으로만 호출할 수 있습니다 (속성 접근 불가).")
        if isinstance(node, ast.Name) and node.id.startswith("_"):
            raise FormulaError("밑줄로 시작하는 이름은 사용할 수 없습니다.")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "shift" and len(node.args) >= 2:
            arg = node.args[1]
            neg = isinstance(arg, ast.UnaryOp) and isinstance(arg.op, ast.USub)
            zero = isinstance(arg, ast.Constant) and isinstance(arg.value, (int, float)) and arg.value <= 0
            if neg or zero:
                raise FormulaError("shift() 의 기간은 1 이상이어야 합니다 — 미래 봉 참조(look-ahead)는 허용되지 않습니다.")
    if count > MAX_NODES:
        raise FormulaError("산식이 너무 복잡합니다.")
    return tree


def referenced_names(expr: str) -> tuple[set[str], set[str]]:
    """(변수 이름, 함수 이름)."""
    tree = parse_expr(expr)
    funcs = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call)}
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} - funcs
    return names, funcs


class _Evaluator:
    def __init__(self, df: pd.DataFrame, params: dict[str, float]):
        self.df = df
        self.close = df["close"].astype(float)
        self.funcs = make_functions(df)
        self.vars: dict[str, Any] = {c: df[c].astype(float) for c in SERIES_VARS}
        for k, v in (params or {}).items():
            if k in self.vars or k in self.funcs:
                raise FormulaError(f"파라미터 이름 '{k}' 은 예약어와 겹칩니다.")
            self.vars[k] = float(v)
        self.vars.update({"True": True, "False": False, "pi": math.pi})

    def eval(self, tree: ast.Expression):
        return self._ev(tree.body)

    def _ev(self, node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in self.vars:
                raise FormulaError(f"알 수 없는 이름: {node.id} (사용 가능: open/high/low/close/volume, params 키, 함수 목록 참고)")
            return self.vars[node.id]
        if isinstance(node, ast.UnaryOp):
            v = self._ev(node.operand)
            if isinstance(node.op, ast.USub): return -v
            if isinstance(node.op, ast.UAdd): return v
            if isinstance(node.op, (ast.Not, ast.Invert)): return ~_s(v, self.close).astype(bool) if isinstance(v, pd.Series) else (not v)
        if isinstance(node, ast.BinOp):
            a, b = self._ev(node.left), self._ev(node.right)
            op = node.op
            if isinstance(op, ast.Add): return a + b
            if isinstance(op, ast.Sub): return a - b
            if isinstance(op, ast.Mult): return a * b
            if isinstance(op, ast.Div):
                bb = _s(b, self.close).replace(0, np.nan) if isinstance(b, pd.Series) else (b if b != 0 else np.nan)
                return a / bb
            if isinstance(op, ast.Mod): return a % b
            if isinstance(op, ast.Pow):
                if not isinstance(b, (int, float)) or abs(b) > 10:
                    raise FormulaError("거듭제곱 지수는 |n| ≤ 10 인 숫자여야 합니다.")
                return _s(a, self.close) ** b if isinstance(a, pd.Series) else a ** b
            if isinstance(op, ast.BitAnd): return _s(a, self.close).astype(bool) & _s(b, self.close).astype(bool)
            if isinstance(op, ast.BitOr): return _s(a, self.close).astype(bool) | _s(b, self.close).astype(bool)
        if isinstance(node, ast.BoolOp):
            vals = [self._ev(v) for v in node.values]
            out = _s(vals[0], self.close).astype(bool) if any(isinstance(v, pd.Series) for v in vals) else bool(vals[0])
            for v in vals[1:]:
                vv = _s(v, self.close).astype(bool) if isinstance(out, pd.Series) else bool(v)
                out = (out & vv) if isinstance(node.op, ast.And) else (out | vv)
            return out
        if isinstance(node, ast.Compare):
            left = self._ev(node.left)
            result = None
            for op, comp in zip(node.ops, node.comparators):
                right = self._ev(comp)
                if isinstance(op, ast.Lt): r = left < right
                elif isinstance(op, ast.LtE): r = left <= right
                elif isinstance(op, ast.Gt): r = left > right
                elif isinstance(op, ast.GtE): r = left >= right
                elif isinstance(op, ast.Eq): r = left == right
                else: r = left != right
                result = r if result is None else (_s(result, self.close).astype(bool) & _s(r, self.close).astype(bool))
                left = right
            return result
        if isinstance(node, ast.Call):
            name = node.func.id
            if name not in self.funcs:
                raise FormulaError(f"알 수 없는 함수: {name}")
            args = [self._ev(a) for a in node.args]
            kwargs = {k.arg: self._ev(k.value) for k in node.keywords}
            try:
                return self.funcs[name](*args, **kwargs)
            except TypeError as exc:
                raise FormulaError(f"{name}: 인자 오류 ({exc})")
        raise FormulaError(f"지원하지 않는 구문: {type(node).__name__}")


# ── 정의/버전 ──────────────────────────────────────────────────────────────

def definition_checksum(indicator_expr: str, buy_expr: str | None, sell_expr: str | None, params: dict | None) -> str:
    payload = json.dumps({"i": (indicator_expr or "").strip(), "b": (buy_expr or "").strip(), "s": (sell_expr or "").strip(),
                          "p": {k: float(v) for k, v in sorted((params or {}).items())}}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def validate_definition(indicator_expr: str, buy_expr: str | None = None, sell_expr: str | None = None, params: dict | None = None) -> dict:
    """문법·이름 검사(데이터 없이). 반환: 사용 변수/함수, 경고."""
    params = params or {}
    dummy = pd.DataFrame({c: np.linspace(1, 2, 30) for c in SERIES_VARS}, index=pd.date_range("2020-01-01", periods=30))
    funcs = set(make_functions(dummy))
    for k, v in params.items():
        if not isinstance(k, str) or not k.isidentifier() or k.startswith("_"):
            raise FormulaError(f"파라미터 이름이 올바르지 않습니다: {k}")
        if k in SERIES_VARS or k in funcs or k in ("True", "False", "pi"):
            raise FormulaError(f"파라미터 이름 '{k}' 은 예약어(시리즈/함수 이름)와 겹칩니다.")
        try: float(v)
        except (TypeError, ValueError): raise FormulaError(f"파라미터 {k} 값은 숫자여야 합니다.")
    used_vars, used_funcs, warnings = set(), set(), []
    for label, expr in (("indicator", indicator_expr), ("buy", buy_expr), ("sell", sell_expr)):
        if not expr or not str(expr).strip():
            if label == "indicator": raise FormulaError("indicator 산식은 필수입니다.")
            continue
        names, fs = referenced_names(expr)
        unknown_f = fs - funcs
        if unknown_f: raise FormulaError(f"{label}: 알 수 없는 함수 {sorted(unknown_f)}")
        unknown_v = names - set(SERIES_VARS) - set(params) - {"True", "False", "pi"}
        if unknown_v: raise FormulaError(f"{label}: 알 수 없는 이름 {sorted(unknown_v)} — params 에 정의하거나 오타를 확인하세요.")
        used_vars |= names; used_funcs |= fs
    if buy_expr and not sell_expr: warnings.append("sell 산식이 없어 매수 후 신호가 꺼질 때까지 보유합니다.")
    if not buy_expr: warnings.append("buy 산식이 없어 백테스트는 실행되지 않고 지표만 계산됩니다.")
    return {"ok": True, "variables": sorted(used_vars), "functions": sorted(used_funcs), "warnings": warnings,
            "checksum": definition_checksum(indicator_expr, buy_expr, sell_expr, params)}


def compute(candles: list[dict], indicator_expr: str, buy_expr: str | None = None, sell_expr: str | None = None,
            params: dict | None = None, *, commission_bps: float = 0.0, slippage_bps: float = 0.0,
            stop_loss_pct: float | None = None, take_profit_pct: float | None = None, preview_points: int = 250) -> dict:
    """산식 계산 + (buy/sell 있으면) 포지션 → 백테스트. 결과는 저장 가능한 dict."""
    params = {k: float(v) for k, v in (params or {}).items()}
    df = preprocess(candles)
    if len(df) < 30:
        raise FormulaError(f"데이터 부족: {len(df)}봉 (최소 30)")
    ev = _Evaluator(df, params)
    ind = ev.eval(parse_expr(indicator_expr))
    if not isinstance(ind, pd.Series):
        ind = pd.Series(float(ind), index=df.index)
    ind = pd.to_numeric(ind, errors="coerce").astype(float)

    position, bt, buy_sig, sell_sig = None, None, None, None
    if buy_expr and str(buy_expr).strip():
        buy_sig = _s(ev.eval(parse_expr(buy_expr)), ev.close).astype(bool)
        sell_sig = _s(ev.eval(parse_expr(sell_expr)), ev.close).astype(bool) if sell_expr and str(sell_expr).strip() else ~buy_sig
        regime = pd.Series(np.nan, index=df.index)
        regime.loc[buy_sig] = 1.0
        regime.loc[sell_sig & ~buy_sig] = 0.0
        position = regime.ffill().fillna(0.0)
        bt = backtest(df, position, commission_bps, slippage_bps, stop_loss_pct, take_profit_pct)

    tail = df.index[-preview_points:]
    def _ser(s): return [None if (v is None or (isinstance(v, float) and not math.isfinite(v))) else round(float(v), 6) for v in s.reindex(tail).values]
    latest = ind.dropna()
    stats = {"mean": float(latest.tail(252).mean()), "std": float(latest.tail(252).std()), "min": float(latest.tail(252).min()),
             "max": float(latest.tail(252).max()), "last": float(latest.iloc[-1])} if len(latest) else {}
    last_signal = "HOLD"
    if buy_sig is not None and len(buy_sig):
        if bool(buy_sig.iloc[-1]): last_signal = "BUY"
        elif bool(sell_sig.iloc[-1]): last_signal = "SELL"
        elif position is not None and position.iloc[-1] == 1: last_signal = "HOLD_LONG"
    return {
        "rows": int(len(df)), "as_of": df.index[-1].strftime("%Y-%m-%d"),
        "checksum": definition_checksum(indicator_expr, buy_expr, sell_expr, params),
        "latest_value": stats.get("last"), "latest_signal": last_signal, "indicator_stats": {k: round(v, 6) for k, v in stats.items()},
        "backtest": bt,
        "series": {"times": [t.strftime("%Y-%m-%d") for t in tail], "close": _ser(df["close"].astype(float)), "indicator": _ser(ind),
                   "position": _ser(position) if position is not None else None,
                   # 차트 마커는 조건이 참인 모든 날이 아니라 실제 진입(0→1)·청산(1→0) 전환 시점만
                   "buy_dates": [t.strftime("%Y-%m-%d") for t in tail if position is not None and position.diff().get(t, 0) == 1],
                   "sell_dates": [t.strftime("%Y-%m-%d") for t in tail if position is not None and position.diff().get(t, 0) == -1],
                   "buy_condition_dates": [t.strftime("%Y-%m-%d") for t in tail if buy_sig is not None and bool(buy_sig.get(t, False))]},
        "signal_counts": {"buy": int(buy_sig.sum()) if buy_sig is not None else 0, "sell": int(sell_sig.sum()) if sell_sig is not None else 0},
    }


# ── 코드 생성 (Pine / Python) ─────────────────────────────────────────────

# DSL 함수 → Pine Script v6 식. 렌더러가 인자를 문자열로 넘기므로 기본값도 문자열이며,
# 키워드 인자를 그대로 쓸 수 있도록 파라미터 이름을 DSL(make_functions) 쪽과 똑같이 맞춰 두었다.
# Pine 의 함정: 거듭제곱 연산자(^, **)가 없어 math.pow 를 써야 하고, ta.obv 는 함수가 아닌 내장 변수이며,
# 참·거짓은 소문자(true/false), 원주율은 math.pi 다. 길이 인자는 series int 여야 해 input.int 로 낸다.
def _pine_len(a: str) -> str:
    """기간 인자. Pine 의 length 는 정수라 식(n*2, n/2 …)은 int() 로 감싼다 — v6 는 정수끼리 나눠도 실수다."""
    return a if re.fullmatch(r"[A-Za-z_]\w*|\d+", a.strip()) else f"int({a})"


def _pine_hist(x: str, n: str) -> str:
    """Pine 의 과거 참조 연산자. 같은 값에 두 번 쓸 수 없다(close[1][2] 는 오류)."""
    if x.rstrip().endswith("]"):
        raise FormulaError("shift()/pct_change() 를 중첩할 수 없습니다 — Pine 은 같은 값에 과거 참조([]) 를 "
                           "한 번만 허용합니다. shift(close, 3) 처럼 기간을 합쳐 쓰세요.")
    return f"({x})[{n}]"


_PINE_SPEC: dict[str, Any] = {
    "sma":         lambda x, n: f"ta.sma({x}, {_pine_len(n)})",
    "ema":         lambda x, n: f"ta.ema({x}, {_pine_len(n)})",
    "rsi":         lambda x, n="14": f"ta.rsi({x}, {_pine_len(n)})",
    "macd":        lambda x, f="12", s="26", sig="9": f"(ta.ema({x}, {_pine_len(f)}) - ta.ema({x}, {_pine_len(s)}))",
    "macd_signal": lambda x, f="12", s="26", sig="9": f"ta.ema(ta.ema({x}, {_pine_len(f)}) - ta.ema({x}, {_pine_len(s)}), {_pine_len(sig)})",
    "macd_hist":   lambda x, f="12", s="26", sig="9": f"((ta.ema({x}, {_pine_len(f)}) - ta.ema({x}, {_pine_len(s)})) - ta.ema(ta.ema({x}, {_pine_len(f)}) - ta.ema({x}, {_pine_len(s)}), {_pine_len(sig)}))",
    "bb_upper":    lambda x, n="20", k="2.0": f"(ta.sma({x}, {_pine_len(n)}) + {k} * ta.stdev({x}, {_pine_len(n)}))",
    "bb_mid":      lambda x, n="20", k="2.0": f"ta.sma({x}, {_pine_len(n)})",
    "bb_lower":    lambda x, n="20", k="2.0": f"(ta.sma({x}, {_pine_len(n)}) - {k} * ta.stdev({x}, {_pine_len(n)}))",
    "atr":         lambda n="14": f"ta.atr({_pine_len(n)})",
    "std":         lambda x, n: f"ta.stdev({x}, {_pine_len(n)})",
    "highest":     lambda x, n: f"ta.highest({x}, {_pine_len(n)})",
    "lowest":      lambda x, n: f"ta.lowest({x}, {_pine_len(n)})",
    "sum":         lambda x, n: f"math.sum({x}, {_pine_len(n)})",
    "mean":        lambda x, n: f"ta.sma({x}, {_pine_len(n)})",
    "shift":       lambda x, n="1": _pine_hist(x, _pine_len(n)),
    "change":      lambda x, n="1": f"ta.change({x}, {_pine_len(n)})",
    "pct_change":  lambda x, n="1": f"(({x}) / {_pine_hist(x, _pine_len(n))} - 1)",
    "zscore":      lambda x, n="20": f"(({x} - ta.sma({x}, {_pine_len(n)})) / ta.stdev({x}, {_pine_len(n)}))",
    "normalize":   lambda x, n="20": f"(({x} - ta.lowest({x}, {_pine_len(n)})) / (ta.highest({x}, {_pine_len(n)}) - ta.lowest({x}, {_pine_len(n)})))",
    "rank":        lambda x, n="20": f"(ta.percentrank({x}, {_pine_len(n)}) / 100)",
    "crossover":   lambda a, b: f"ta.crossover({a}, {b})",
    "crossunder":  lambda a, b: f"ta.crossunder({a}, {b})",
    "where":       lambda cond, a, b: f"({cond} ? {a} : {b})",
    "abs":         lambda x: f"math.abs({x})",
    "log":         lambda x: f"math.log({x})",
    "sqrt":        lambda x: f"math.sqrt({x})",
    "sign":        lambda x: f"math.sign({x})",
    "clip":        lambda x, lo_, hi_: f"math.min(math.max({x}, {lo_}), {hi_})",
    "max":         lambda a, b: f"math.max({a}, {b})",
    "min":         lambda a, b: f"math.min({a}, {b})",
    "nz":          lambda x, v="0.0": f"nz({x}, {v})",
    "typical":     lambda: "hlc3",
    "vwap":        lambda n="20": f"(math.sum(hlc3 * volume, {_pine_len(n)}) / math.sum(volume, {_pine_len(n)}))",
    "obv":         lambda: "ta.obv",
    "barssince":   lambda cond: f"ta.barssince({cond})",
}

_PINE_BINOPS = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.Mod: "%"}
_PINE_CMPOPS = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=", ast.Eq: "==", ast.NotEq: "!="}
_PINE_BOOL_FUNCS = ("crossover", "crossunder")


def _pine_is_bool(node: ast.AST) -> bool:
    """이 노드가 Pine 에서 bool 로 평가되는지. v6 는 숫자→bool 암묵 변환을 없앴다."""
    if isinstance(node, ast.Expression):
        return _pine_is_bool(node.body)
    if isinstance(node, (ast.Compare, ast.BoolOp)):
        return True
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.Not, ast.Invert)):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.BitAnd, ast.BitOr)):
        return True
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id in _PINE_BOOL_FUNCS
    return False


def _pine_expr(node: ast.AST) -> str:
    """검증된 DSL AST → Pine Script v6 식 문자열."""
    if isinstance(node, ast.Expression):
        return _pine_expr(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool):
            return "true" if node.value else "false"
        return repr(node.value)
    if isinstance(node, ast.Name):
        return {"pi": "math.pi", "True": "true", "False": "false"}.get(node.id, node.id)
    if isinstance(node, ast.BinOp):
        left, right = _pine_expr(node.left), _pine_expr(node.right)
        if isinstance(node.op, ast.Pow):           # Pine 에는 거듭제곱 연산자가 없다
            return f"math.pow({left}, {right})"
        if isinstance(node.op, (ast.BitAnd, ast.BitOr)):   # DSL 의 & | → Pine and/or
            return f"({left} {'and' if isinstance(node.op, ast.BitAnd) else 'or'} {right})"
        op = _PINE_BINOPS.get(type(node.op))
        if op is None:
            raise FormulaError(f"Pine 으로 변환할 수 없는 연산자입니다: {type(node.op).__name__}")
        return f"({left} {op} {right})"
    if isinstance(node, ast.UnaryOp):
        operand = _pine_expr(node.operand)
        if isinstance(node.op, (ast.Not, ast.Invert)):
            return f"(not {operand})"
        return f"({'-' if isinstance(node.op, ast.USub) else '+'}{operand})"
    if isinstance(node, ast.BoolOp):
        op = "and" if isinstance(node.op, ast.And) else "or"
        return "(" + f" {op} ".join(_pine_expr(v) for v in node.values) + ")"
    if isinstance(node, ast.Compare):
        # Pine 은 a < b < c 연쇄 비교가 없어 and 로 풀어 쓴다.
        parts, left = [], node.left
        for op, right in zip(node.ops, node.comparators):
            sym = _PINE_CMPOPS.get(type(op))
            if sym is None:
                raise FormulaError(f"Pine 으로 변환할 수 없는 비교 연산자입니다: {type(op).__name__}")
            parts.append(f"({_pine_expr(left)} {sym} {_pine_expr(right)})")
            left = right
        return parts[0] if len(parts) == 1 else "(" + " and ".join(parts) + ")"
    if isinstance(node, ast.Call):
        name = node.func.id
        spec = _PINE_SPEC.get(name)
        if spec is None:
            raise FormulaError(f"Pine 으로 변환할 수 없는 함수입니다: {name}()")
        try:
            return spec(*[_pine_expr(a) for a in node.args], **{k.arg: _pine_expr(k.value) for k in node.keywords})
        except TypeError as exc:
            raise FormulaError(f"{name}(): Pine 변환 인자가 맞지 않습니다 ({exc}).")
    raise FormulaError(f"Pine 으로 변환할 수 없는 구문입니다: {type(node).__name__}")


# 각 함수에서 '기간(length)' 에 해당하는 인자 이름 — 이 자리에 바로 쓰인 파라미터는 input.int 로 낸다.
_PINE_LEN_ARGS = {
    "sma": ("n",), "ema": ("n",), "rsi": ("n",), "macd": ("f", "s", "sig"), "macd_signal": ("f", "s", "sig"),
    "macd_hist": ("f", "s", "sig"), "bb_upper": ("n",), "bb_mid": ("n",), "bb_lower": ("n",), "atr": ("n",),
    "std": ("n",), "highest": ("n",), "lowest": ("n",), "sum": ("n",), "mean": ("n",), "shift": ("n",),
    "change": ("n",), "pct_change": ("n",), "zscore": ("n",), "normalize": ("n",), "rank": ("n",), "vwap": ("n",),
}


def _pine_length_params(*exprs: str | None) -> set[str]:
    """기간 자리에 직접 쓰인 파라미터 이름. 나머지(배수 k 등)는 input.float 로 둬야 2.5 를 넣을 수 있다."""
    import inspect
    out: set[str] = set()
    for e in exprs:
        if not e or not str(e).strip():
            continue
        for node in ast.walk(parse_expr(e)):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            lens, spec = _PINE_LEN_ARGS.get(node.func.id), _PINE_SPEC.get(node.func.id)
            if not lens or spec is None:
                continue
            bound = dict(zip(inspect.signature(spec).parameters, node.args))
            bound.update({k.arg: k.value for k in node.keywords if k.arg})
            out |= {bound[ln].id for ln in lens if isinstance(bound.get(ln), ast.Name)}
    return out


def to_pine(name: str, indicator_expr: str, buy_expr: str | None, sell_expr: str | None, params: dict | None) -> str:
    """DSL → Pine Script v6. 변환할 수 없는 함수·구문은 깨진 코드를 내보내지 않고 FormulaError 로 알린다."""
    def expr(e: str, as_bool: bool = False) -> str:
        tree = parse_expr(e)
        out = _pine_expr(tree)
        # v6 는 숫자를 bool 로 암묵 변환하지 않는다 → 신호 자리에는 bool() 로 명시 캐스팅.
        return f"bool({out})" if as_bool and not _pine_is_bool(tree) else out

    title = str(name).replace("\\", "").replace('"', "'") or "lumina indicator"
    lines = ["//@version=6",
             f'indicator("{title}", overlay=false)',
             "// lumina-invest 자유 산식 DSL 에서 자동 생성 (Pine Script v6).",
             "// 근사한 부분: bb_* 는 ta.stdev(표본), rank() 는 ta.percentrank/100(<= 기준),",
             "//            vwap(n) 은 세션 ta.vwap 이 아니라 hlc3·거래량의 n봉 롤링 가중평균입니다."]
    length_params = _pine_length_params(indicator_expr, buy_expr, sell_expr)
    for k, v in (params or {}).items():
        fv = float(v)
        lines.append(f'{k} = input.int({int(round(fv))}, "{k}")' if k in length_params
                     else f'{k} = input.float({fv}, "{k}")')
    lines.append(f"ind = {expr(indicator_expr)}")
    lines.append('plot(ind, title="indicator", color=color.blue)')
    if buy_expr and str(buy_expr).strip():
        lines.append(f"buy_signal  = {expr(buy_expr, as_bool=True)}")
        lines.append('plotshape(buy_signal, title="buy", style=shape.triangleup, location=location.bottom, color=color.green)')
    if sell_expr and str(sell_expr).strip():
        lines.append(f"sell_signal = {expr(sell_expr, as_bool=True)}")
        lines.append('plotshape(sell_signal, title="sell", style=shape.triangledown, location=location.top, color=color.red)')
    return "\n".join(lines)


def to_python(name: str, indicator_expr: str, buy_expr: str | None, sell_expr: str | None, params: dict | None) -> str:
    return f'''# {name} — lumina-invest 자유 산식 DSL (app/services/formula.py 로 실행)
from app.services.formula import compute

DEFINITION = {{
    "indicator_expr": {indicator_expr!r},
    "buy_expr": {buy_expr!r},
    "sell_expr": {sell_expr!r},
    "params": {json.dumps(params or {}, ensure_ascii=False)},
}}

# candles: [{{"time": unix, "open":..., "high":..., "low":..., "close":..., "volume":...}}, ...]
result = compute(candles, **DEFINITION, commission_bps=10, slippage_bps=5)
print(result["latest_value"], result["latest_signal"], result["backtest"])
'''
