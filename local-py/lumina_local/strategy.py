"""전략 스펙 — strategies/<id>.json 을 읽어 시그널을 다시 판정한다.

app 은 domain-rag-lab 서버에서 스펙을 받아왔지만(strategy_loader), 로컬에서는 같은 스키마의
JSON 파일을 쓴다. 평가 로직(apply_strategy_spec_to_symbols / evaluate_spec_rules /
apply_strategy_spec_to_signal)은 app/services/auto_trade.py 와 동일하다.

스펙 스키마(필요한 키만 쓰면 된다):
{
  "strategy_id": "ma-cross-basic", "version": 1,
  "universe": ["005930", "000660"],
  "position_sizing": {"max_symbols": 3},
  "signal_weights": {"buy_threshold": 0.3, "sell_threshold": -0.3, "technical": 1.0, "lightgbm": 0.0},
  "entry": {"indicator": "ma_cross", "condition": "short_above_long", "params": {"short_window": 5, "long_window": 20}},
  "exit":  {"indicator": "ma_cross", "condition": "short_below_long", "params": {"short_window": 5, "long_window": 20}}
}
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

STRATEGY_DIR = Path(__file__).resolve().parent.parent / "strategies"

# stock.py 지표 점수(대략 -8~+8)를 스펙 임계값([-1,1])과 비교하기 위한 정규화 분모
SIGNAL_SCORE_SCALE = 8.0


def available() -> list[str]:
    return sorted(p.stem for p in STRATEGY_DIR.glob("*.json"))


def load(strategy_id: str) -> dict | None:
    if not strategy_id:
        return None
    path = STRATEGY_DIR / f"{strategy_id}.json"
    if not path.exists():
        logger.warning("전략 스펙 없음: %s (사용 가능: %s)", path, ", ".join(available()) or "없음")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.warning("전략 스펙 파싱 실패 %s: %s", path, exc)
        return None


def apply_to_symbols(target_symbols: list[str], spec: dict) -> list[str]:
    """스펙 universe(6자리 코드)에 있는 종목만 남기고 position_sizing.max_symbols 로 자른다."""
    universe = {str(c)[:6] for c in (spec.get("universe") or [])}
    restricted = [s for s in target_symbols if str(s)[:6] in universe] if universe else list(target_symbols)
    if not restricted:
        return list(target_symbols)
    max_symbols = int((spec.get("position_sizing") or {}).get("max_symbols") or 0)
    return restricted[:max_symbols] if max_symbols > 0 else restricted


def _sma_last(values: list[float], window: int, offset: int = 0) -> float | None:
    """values[-1-offset] 기준 단순이동평균. 데이터 부족이면 None."""
    end = len(values) - offset
    if window <= 0 or end - window < 0:
        return None
    return sum(values[end - window:end]) / window


def evaluate_rules(spec: dict, indicators: dict) -> dict | None:
    """스펙 entry/exit 규칙(ma_cross / momentum / always / never)을 종가 시계열로 평가한다.

    반환 {"entry": bool, "exit": bool, "detail": str} 또는 평가 불가면 None.
    """
    closes = [float(c) for c in (indicators.get("closes") or []) if c is not None]
    if not closes:
        return None

    def _eval(rule: dict) -> bool | None:
        ind = str(rule.get("indicator") or "")
        cond = str(rule.get("condition") or "")
        params = rule.get("params") or {}
        if cond == "always":
            return True
        if cond == "never":
            return False
        if ind == "ma_cross":
            s, l = int(params.get("short_window", 5)), int(params.get("long_window", 20))
            ss, ll = _sma_last(closes, s), _sma_last(closes, l)
            if ss is None or ll is None:
                return None
            return ss > ll if cond == "short_above_long" else ss < ll if cond == "short_below_long" else None
        if ind == "momentum":
            w = int(params.get("breakout_window", 20))
            if len(closes) < w + 1:
                return None
            window = closes[-w - 1:-1]
            return closes[-1] > max(window) if cond == "breakout_high" else closes[-1] < min(window) if cond == "breakdown_low" else None
        return None

    entry, exit_ = _eval(spec.get("entry") or {}), _eval(spec.get("exit") or {})
    if entry is None and exit_ is None:
        return None
    detail = f"규칙 {((spec.get('entry') or {}).get('indicator'))}: 진입={entry} 청산={exit_}"
    return {"entry": bool(entry), "exit": bool(exit_), "detail": detail}


def apply_to_signal(signal: dict, spec: dict, ml_score: float | None = None,
                    indicators: dict | None = None) -> dict:
    """지표 점수를 [-1,1]로 정규화하고 buy/sell 임계값으로 action 을 다시 판정한다.

    ml_score 를 주면 스펙의 lightgbm 가중치로 섞는다(로컬에는 배치 학습 점수가 없으므로 기본 None).
    """
    weights = spec.get("signal_weights") or {}
    buy_th = float(weights.get("buy_threshold", 0.6))
    sell_th = float(weights.get("sell_threshold", -0.6))
    w_tech = float(weights.get("technical", 1.0))
    w_ml = float(weights.get("lightgbm", 0.0))
    score = float(signal.get("score", 0) or 0)
    technical = max(-1.0, min(1.0, score / SIGNAL_SCORE_SCALE))
    if ml_score is not None and w_ml > 0 and (w_tech + w_ml) > 0:
        normalized = (w_tech * technical + w_ml * max(-1.0, min(1.0, float(ml_score)))) / (w_tech + w_ml)
        ml_tag = f", ML {float(ml_score):+.2f}×{w_ml:g} + 지표 {technical:+.2f}×{w_tech:g}"
    else:
        normalized = technical
        ml_tag = "" if w_ml <= 0 else " (ML 점수 없음 → 지표만)"
    normalized = round(max(-1.0, min(1.0, normalized)), 4)
    rules = evaluate_rules(spec, indicators) if indicators else None
    if normalized >= buy_th:
        action = "강력 매수" if normalized >= min(1.0, buy_th + 0.25) else "매수"
    elif normalized <= sell_th:
        action = "강력 매도" if normalized <= max(-1.0, sell_th - 0.25) else "매도"
    else:
        action = "관망"
    tag = (f"전략 {spec.get('strategy_id')} v{spec.get('version')}: 정규화 점수 {normalized:+.2f} "
           f"(매수≥{buy_th:+.2f} / 매도≤{sell_th:+.2f}){ml_tag}")
    reasons = [*(signal.get("reasons") or []), tag]
    if rules is not None:
        # 규칙을 직접 평가할 수 있으면 규칙이 action 을 결정한다(청산 우선). 임계값 점수는 참고로 남긴다.
        action = "매도" if rules["exit"] else "매수" if rules["entry"] else "관망"
        reasons.append(rules["detail"] + " → " + action)
    return {**signal, "action": action, "normalized_score": normalized,
            "rule_based": rules is not None, "reasons": reasons}
