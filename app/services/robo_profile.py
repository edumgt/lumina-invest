"""로보 어드바이저 UI 보조 계산.

1) 투자성향 진단 설문(7문항) → 점수 → 5단계 성향 → 시스템 위험 프로파일(conservative/moderate/aggressive)
2) 목표 수익률 달성 확률 시뮬레이션: 월 단위 GBM 몬테카를로로 원금+월 적립의 미래 가치 분포를 만들고
   목표 금액(목표 연수익률 기준) 도달 확률·백분위 경로·필요 연수익률을 산출한다.
"""
from __future__ import annotations

import numpy as np

QUESTIONS: list[dict] = [
    {"id": "age", "text": "연령대는?", "options": [
        {"label": "20대 이하", "score": 5}, {"label": "30대", "score": 4}, {"label": "40대", "score": 3}, {"label": "50대", "score": 2}, {"label": "60대 이상", "score": 1}]},
    {"id": "horizon", "text": "이 자금을 언제 사용할 계획인가요?", "options": [
        {"label": "1년 이내", "score": 1}, {"label": "1~3년", "score": 2}, {"label": "3~5년", "score": 3}, {"label": "5~10년", "score": 4}, {"label": "10년 이상", "score": 5}]},
    {"id": "experience", "text": "투자 경험은?", "options": [
        {"label": "예·적금만", "score": 1}, {"label": "채권·원금보장형 상품", "score": 2}, {"label": "펀드·ETF", "score": 3}, {"label": "주식 직접투자", "score": 4}, {"label": "파생·레버리지·코인", "score": 5}]},
    {"id": "income", "text": "소득의 안정성과 투자금 비중은?", "options": [
        {"label": "소득 불안정, 여유자금 거의 없음", "score": 1}, {"label": "소득 안정, 투자금이 자산의 50% 이상", "score": 2},
        {"label": "소득 안정, 투자금이 자산의 20~50%", "score": 3}, {"label": "소득 안정, 투자금이 자산의 20% 미만", "score": 4}, {"label": "여유자금이 충분해 손실도 감내 가능", "score": 5}]},
    {"id": "loss", "text": "1년 내 투자금이 얼마나 줄어도 견딜 수 있나요?", "options": [
        {"label": "원금 손실은 절대 불가", "score": 1}, {"label": "-5%까지", "score": 2}, {"label": "-10%까지", "score": 3}, {"label": "-20%까지", "score": 4}, {"label": "-30% 이상도 감내", "score": 5}]},
    {"id": "goal", "text": "투자 목적은?", "options": [
        {"label": "원금 보존", "score": 1}, {"label": "물가상승률 방어", "score": 2}, {"label": "예금+α 안정 수익", "score": 3}, {"label": "자산 증식", "score": 4}, {"label": "높은 수익 추구", "score": 5}]},
    {"id": "knowledge", "text": "금융 지식 수준은?", "options": [
        {"label": "금융상품 구분이 어렵다", "score": 1}, {"label": "예금·펀드 차이는 안다", "score": 2}, {"label": "주식·채권·ETF 특성을 안다", "score": 3},
        {"label": "파생·구조화 상품도 이해한다", "score": 4}, {"label": "전문가 수준", "score": 5}]},
]

PROFILE_LEVELS = [  # (최소 점수, 5단계 성향, 시스템 프로파일, 설명)
    (0,  "안정형",       "conservative", "원금 보존이 최우선입니다. 예금·국공채·MMF 중심, 주식 비중은 최소로 유지합니다."),
    (13, "안정추구형",   "conservative", "손실 위험은 낮게, 예금보다 조금 높은 수익을 노립니다. 채권 비중을 높게, 주식은 우량·배당 중심으로."),
    (19, "위험중립형",   "moderate",     "위험과 수익의 균형을 추구합니다. 주식·채권을 비슷한 비중으로 나눈 분산 포트폴리오가 적합합니다."),
    (25, "적극투자형",   "aggressive",   "높은 수익을 위해 상당한 변동성을 감내합니다. 주식 비중을 높이고 성장·해외 자산을 더합니다."),
    (31, "공격투자형",   "aggressive",   "시장 평균 이상의 수익을 목표로 큰 손실 가능성도 받아들입니다. 주식·대체자산 중심의 공격적 배분."),
]


# ── 자산배분 비중(성향 × 투자기간) ──────────────────────────────────────────
# 감리 FD-FIND-001(2026-10-21): 기간 가중치를 현금에서 하한 확인 없이 차감해 장기(≥9년) 구간에서 현금이 음수가 됐다.
# 수정: 자산군별 [하한, 상한] 제약을 두고, 기간 가중치는 '현금·채권에서 하한까지 실제로 뺄 수 있는 만큼만' 주식으로 옮긴다.
# 이동량 = 실제 차감량이므로 합계 100 이 항상 유지되고, 어떤 성향·기간에서도 비중이 하한 아래/상한 위로 가지 않는다.
BASE_ALLOCATIONS: dict[str, dict[str, float]] = {
    "conservative": {"국내주식": 15, "해외주식": 10, "국내채권": 50, "대체자산": 10, "현금": 15},
    "moderate":     {"국내주식": 30, "해외주식": 25, "국내채권": 30, "대체자산": 10, "현금": 5},
    "aggressive":   {"국내주식": 45, "해외주식": 35, "국내채권": 10, "대체자산": 8, "현금": 2},
}
ALLOCATION_BOUNDS: dict[str, tuple[float, float]] = {  # (하한 %, 상한 %)
    "국내주식": (0, 60), "해외주식": (0, 50), "국내채권": (0, 60), "대체자산": (0, 20), "현금": (2, 30),
}
HORIZON_MAX_BOOST = 6  # 투자기간 4년째부터 1년당 1%p, 최대 6%p 를 현금·채권 각각에서 주식으로 이동


def compute_allocation(risk_profile: str, horizon_years: int) -> dict[str, float]:
    """성향·투자기간 → 자산군 비중(%, 소수 1자리). 합계 100, 모든 자산군이 ALLOCATION_BOUNDS 안에 있음을 보장한다."""
    risk = risk_profile if risk_profile in BASE_ALLOCATIONS else "moderate"
    horizon = max(1, min(10, int(horizon_years)))
    alloc = {k: float(v) for k, v in BASE_ALLOCATIONS[risk].items()}

    boost = min(HORIZON_MAX_BOOST, max(0, horizon - 3))
    moved = 0.0
    for src in ("국내채권", "현금"):                     # 하한까지만 차감
        give = min(boost, alloc[src] - ALLOCATION_BOUNDS[src][0])
        if give > 0:
            alloc[src] -= give; moved += give
    share = moved / 2
    for dst in ("국내주식", "해외주식"):                 # 실제 차감량을 반씩, 상한까지만 주식에 배분. 못 넣은 잔량은 현금으로 복귀
        take = min(share, ALLOCATION_BOUNDS[dst][1] - alloc[dst])
        alloc[dst] += take; moved -= take
    alloc["현금"] += moved

    total = sum(alloc.values()) or 100.0
    out = {k: round(v * 100 / total, 1) for k, v in alloc.items()}
    largest = max(out, key=out.get)                      # 반올림 오차는 가장 큰 자산군에 반영(현금 하한을 건드리지 않음)
    out[largest] = round(out[largest] + round(100 - sum(out.values()), 1), 1)
    for k, (lo, hi) in ALLOCATION_BOUNDS.items():
        if not lo <= out[k] <= hi:
            raise ValueError(f"자산배분 경계 위반: {k}={out[k]}% (허용 {lo}~{hi}%)")
    return out


def score_answers(answers: dict[str, int]) -> dict:
    """answers: {question_id: 선택 인덱스(0~4)} → 진단 결과."""
    total, detail, missing = 0, [], []
    for q in QUESTIONS:
        idx = answers.get(q["id"])
        if idx is None or not (0 <= int(idx) < len(q["options"])):
            missing.append(q["id"])
            continue
        opt = q["options"][int(idx)]
        total += opt["score"]
        detail.append({"id": q["id"], "text": q["text"], "answer": opt["label"], "score": opt["score"]})
    if missing:
        raise ValueError(f"응답이 없는 문항이 있습니다: {', '.join(missing)}")
    level = PROFILE_LEVELS[0]
    for lv in PROFILE_LEVELS:
        if total >= lv[0]:
            level = lv
    max_score = 5 * len(QUESTIONS)
    return {"score": total, "max_score": max_score, "level": level[1], "risk_profile": level[2], "description": level[3],
            "detail": detail, "scale": [{"min": lv[0], "level": lv[1], "risk_profile": lv[2]} for lv in PROFILE_LEVELS]}


def goal_simulation(amount_manwon: float, horizon_years: int, target_return_pct: float, expected_return_pct: float,
                    expected_volatility_pct: float, monthly_contribution_manwon: float = 0.0, n_paths: int = 3000,
                    seed: int = 42) -> dict:
    """월 단위 GBM 몬테카를로.

    - target_return_pct : 목표 연수익률(%). 목표 금액 = 원금·적립금을 이 수익률로 복리 적용한 값
    - expected_return_pct / expected_volatility_pct : 포트폴리오 기대 연수익률·연변동성(%)
    """
    years = max(1, min(40, int(horizon_years)))
    months = years * 12
    mu, sigma = float(expected_return_pct) / 100, max(0.0, float(expected_volatility_pct)) / 100
    tgt = float(target_return_pct) / 100
    p0 = max(0.0, float(amount_manwon)); c = max(0.0, float(monthly_contribution_manwon))
    rng = np.random.default_rng(seed)
    dt = 1 / 12
    # 변동성 0일 때 중앙 경로가 이산 복리 (1+mu)^years 와 정확히 일치하도록 log(1+mu) 기준 드리프트 사용
    drift = np.log1p(mu) * dt - 0.5 * sigma ** 2 * dt
    shocks = rng.normal(0, sigma * np.sqrt(dt), size=(n_paths, months)) if sigma > 0 else np.zeros((n_paths, months))
    growth = np.exp(drift + shocks)                     # (paths, months)
    values = np.empty((n_paths, months + 1)); values[:, 0] = p0
    for m in range(1, months + 1):
        values[:, m] = values[:, m - 1] * growth[:, m - 1] + c
    final = values[:, -1]

    # 목표 금액: 원금과 매월 적립금을 목표 연수익률로 복리 성장
    r_m = (1 + tgt) ** (1 / 12) - 1
    target_value = p0 * (1 + tgt) ** years + (c * (((1 + r_m) ** months - 1) / r_m) if r_m > 0 else c * months)
    invested = p0 + c * months
    prob = float((final >= target_value).mean())
    pct = {k: float(np.percentile(final, k)) for k in (5, 25, 50, 75, 95)}
    # 원금 손실 확률, 필요 연수익률(적립 없이 원금만 기준 근사)
    loss_prob = float((final < invested).mean())
    req = ((target_value / invested) ** (1 / years) - 1) if invested > 0 else 0.0
    step = max(1, months // 60)
    curves = {f"p{k}": [round(float(v), 1) for v in np.percentile(values, k, axis=0)[::step]] for k in (5, 50, 95)}
    labels = [round(i * step / 12, 2) for i in range(len(curves["p50"]))]
    if prob >= 0.8: verdict = "달성 가능성 높음"
    elif prob >= 0.5: verdict = "달성 가능성 보통"
    elif prob >= 0.25: verdict = "달성 가능성 낮음 — 목표를 낮추거나 적립·기간을 늘리세요"
    else: verdict = "달성 어려움 — 목표 수익률이 포트폴리오 기대수익 대비 과도합니다"
    return {
        "inputs": {"amount_manwon": p0, "monthly_contribution_manwon": c, "horizon_years": years, "target_return_pct": float(target_return_pct),
                   "expected_return_pct": float(expected_return_pct), "expected_volatility_pct": float(expected_volatility_pct), "n_paths": n_paths},
        "invested_manwon": round(invested, 1), "target_value_manwon": round(target_value, 1),
        "probability_pct": round(prob * 100, 1), "loss_probability_pct": round(loss_prob * 100, 1),
        "required_annual_return_pct": round(req * 100, 2),
        "percentiles_manwon": {f"p{k}": round(v, 1) for k, v in pct.items()},
        "median_return_pct": round((pct[50] / invested - 1) * 100, 2) if invested > 0 else 0.0,
        "curve_years": labels, "curves": curves, "verdict": verdict,
        "disclaimer": "기대수익률·변동성이 미래에도 유지된다는 가정의 확률 추정치이며 수익을 보장하지 않습니다.",
    }
