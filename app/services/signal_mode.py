"""운영 모드 3분할 — 공격 모드 / 백테스트 기반 규칙 / 사용자 설정 (전략설정 화면 「운영 모드」).

왜 필요한가: 이전에는 QUANT_AGGRESSIVE_MODE(bool) 하나로 ON=공격, OFF=「전략이 있으면 스펙, 없으면 일봉 점수」였다.
공격 모드를 켠 채 전략을 고르면 백테스트 규칙이 매매에 쓰이지 않는데 화면에서는 구분되지 않았고, 일봉 점수 규칙은
임계값이 코드에 고정돼 사용자가 손댈 수 없었다. 이 모듈은

  1. resolve(): 유효 모드 문자열을 돌려준다. QUANT_SIGNAL_MODE 가 명시돼 있으면 그 값, 비어 있으면 이전 호환 규칙.
  2. is_aggressive(): aggressive_mode.is_enabled() 가 이 값을 따른다 → 명시 모드가 backtest/custom 이면 env 의
     QUANT_AGGRESSIVE_MODE=true(fd compose 기본값)여도 공격 모드가 돌지 않는다.
  3. apply_custom_rules(): 사용자 설정 모드에서 일봉 지표 점수를 QUANT_CUSTOM_* 임계값·추세 필터로 다시 판정한다.
"""
from __future__ import annotations

from typing import Any

from app.config import settings

AGGRESSIVE, BACKTEST, CUSTOM = "aggressive", "backtest", "custom"
MODES: tuple[str, ...] = (AGGRESSIVE, BACKTEST, CUSTOM)
LABELS: dict[str, str] = {AGGRESSIVE: "공격 모드", BACKTEST: "백테스트 기반 규칙", CUSTOM: "사용자 설정"}
DESCRIPTIONS: dict[str, str] = {
    AGGRESSIVE: "분봉(기본 5분) 단기 시그널 plan 이 매 사이클 매수·매도를 정합니다. 익절·손절·약세 매도, 시장가 주문. 전략 스펙은 종목 교집합·사유 표시에만 쓰입니다.",
    BACKTEST: "domain-rag-lab(pr) 에서 LEAN 백테스트에 합격한 전략 스펙의 entry/exit 규칙(이동평균 교차·돌파)이 매매를 정합니다. 합격 전략을 반드시 선택해야 하며, 없으면 매수를 보류합니다.",
    CUSTOM: "일봉 지표 점수(RSI·MA 교차·볼린저)에 사용자가 정한 매수·매도 임계값과 추세 필터를 적용합니다. 전략 스펙은 무시됩니다.",
}


def explicit() -> str:
    """QUANT_SIGNAL_MODE 가 유효한 값이면 그 값, 아니면 빈 문자열."""
    mode = (getattr(settings, "QUANT_SIGNAL_MODE", "") or "").strip().lower()
    return mode if mode in MODES else ""


def resolve(strategy_id: str = "") -> str:
    """유효 운영 모드. 명시값 우선, 없으면 이전 호환(공격 플래그 → 전략 유무)."""
    mode = explicit()
    if mode:
        return mode
    if bool(getattr(settings, "QUANT_AGGRESSIVE_MODE", False)):
        return AGGRESSIVE
    return BACKTEST if (strategy_id or "").strip() else CUSTOM


def is_aggressive() -> bool:
    mode = explicit()
    if mode:
        return mode == AGGRESSIVE
    return bool(getattr(settings, "QUANT_AGGRESSIVE_MODE", False))


def apply_custom_rules(signal: dict[str, Any], indicators: dict[str, Any] | None = None) -> dict[str, Any]:
    """사용자 설정 모드: 일봉 지표 점수(stock._generate_signal 의 score)를 QUANT_CUSTOM_* 임계값으로 다시 판정한다.

    추세 필터가 켜져 있고 현재가 ≤ MA20 이면 매수 판정을 관망으로 바꾼다(매도는 그대로). 사유 끝에 판정 근거를 남긴다.
    """
    buy_th = int(getattr(settings, "QUANT_CUSTOM_BUY_SCORE", 1))
    sell_th = int(getattr(settings, "QUANT_CUSTOM_SELL_SCORE", -1))
    trend = bool(getattr(settings, "QUANT_CUSTOM_TREND_FILTER", False))
    score = float(signal.get("score", 0) or 0)
    if score >= buy_th:
        action = "강력 매수" if score >= buy_th + 2 else "매수"
    elif score <= sell_th:
        action = "강력 매도" if score <= sell_th - 2 else "매도"
    else:
        action = "관망"
    reasons = [*(signal.get("reasons") or []), f"사용자 설정: 점수 {score:+.0f} (매수≥{buy_th:+d} / 매도≤{sell_th:+d}) → {action}"]
    if trend and action in ("매수", "강력 매수"):
        ind = indicators or {}
        ma20 = (ind.get("ma20") or [None])[-1] if isinstance(ind.get("ma20"), list) else ind.get("ma20")
        price = ind.get("current_price")
        if ma20 is None or price is None:
            action = "관망"
            reasons.append("추세 필터: MA20 을 계산할 데이터가 없어 매수 보류")
        elif float(price) <= float(ma20):
            action = "관망"
            reasons.append(f"추세 필터: 현재가 {float(price):,.0f} ≤ MA20 {float(ma20):,.0f} → 매수 보류")
        else:
            reasons.append(f"추세 필터 통과: 현재가 > MA20 {float(ma20):,.0f}")
    return {**signal, "action": action, "reasons": reasons, "custom_rules": True}
