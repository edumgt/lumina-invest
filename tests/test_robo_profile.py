"""투자성향 진단 점수화 · 목표 달성 확률 시뮬레이션."""
import pytest

from app.services.robo_profile import QUESTIONS, goal_simulation, score_answers


def _answers(idx):
    return {q["id"]: idx for q in QUESTIONS}


def test_profile_levels_by_score():
    assert score_answers(_answers(0))["level"] == "안정형"          # 7점
    assert score_answers(_answers(2))["risk_profile"] == "moderate"   # 21점 → 위험중립형
    r = score_answers(_answers(4))                                    # 연령(역순) 1점 + 나머지 5점 = 31점
    assert r["level"] == "공격투자형" and r["risk_profile"] == "aggressive"
    best = {q["id"]: max(range(len(q["options"])), key=lambda i: q["options"][i]["score"]) for q in QUESTIONS}
    assert score_answers(best)["score"] == score_answers(best)["max_score"]


def test_profile_requires_all_answers():
    a = _answers(2); del a["loss"]
    with pytest.raises(ValueError):
        score_answers(a)
    with pytest.raises(ValueError):
        score_answers({**_answers(2), "age": 9})


def test_goal_simulation_deterministic_when_no_volatility():
    r = goal_simulation(1000, 5, target_return_pct=5, expected_return_pct=8, expected_volatility_pct=0, n_paths=500)
    assert r["probability_pct"] == 100.0
    assert r["percentiles_manwon"]["p50"] == pytest.approx(1000 * 1.08 ** 5, rel=1e-3)
    r2 = goal_simulation(1000, 5, target_return_pct=10, expected_return_pct=8, expected_volatility_pct=0, n_paths=500)
    assert r2["probability_pct"] == 0.0


def test_goal_simulation_with_contributions_and_volatility():
    r = goal_simulation(1000, 3, target_return_pct=6, expected_return_pct=7, expected_volatility_pct=15,
                        monthly_contribution_manwon=50, n_paths=2000)
    assert r["invested_manwon"] == 1000 + 50 * 36
    assert 0 < r["probability_pct"] < 100
    p = r["percentiles_manwon"]
    assert p["p5"] <= p["p25"] <= p["p50"] <= p["p75"] <= p["p95"]
    assert len(r["curves"]["p50"]) == len(r["curve_years"])
    assert r["required_annual_return_pct"] > 0


# ── 자산배분 비중 경계 (감리 FD-FIND-001: 장기 구간 음수 현금 비중) ─────────────

from app.services.robo_profile import ALLOCATION_BOUNDS, BASE_ALLOCATIONS, compute_allocation


@pytest.mark.parametrize("risk", list(BASE_ALLOCATIONS))
@pytest.mark.parametrize("horizon", range(1, 11))
def test_allocation_within_bounds_and_sums_to_100(risk, horizon):
    alloc = compute_allocation(risk, horizon)
    assert set(alloc) == set(ALLOCATION_BOUNDS)
    assert sum(alloc.values()) == pytest.approx(100.0, abs=0.05)
    for cls, pct in alloc.items():
        lo, hi = ALLOCATION_BOUNDS[cls]
        assert lo <= pct <= hi, f"{risk}/{horizon}y {cls}={pct}"
        assert pct >= 0


def test_allocation_audit_cases_no_negative_cash():
    # 감리 관측값: aggressive·10년 현금 -4%, moderate·10년 현금 -1%
    a = compute_allocation("aggressive", 10)
    assert a["현금"] >= ALLOCATION_BOUNDS["현금"][0] and a["국내채권"] >= 0
    assert a == {"국내주식": 48.0, "해외주식": 38.0, "국내채권": 4.0, "대체자산": 8.0, "현금": 2.0}
    m = compute_allocation("moderate", 10)
    assert m["현금"] >= ALLOCATION_BOUNDS["현금"][0]
    assert m == {"국내주식": 34.5, "해외주식": 29.5, "국내채권": 24.0, "대체자산": 10.0, "현금": 2.0}


def test_allocation_horizon_monotone_and_short_horizon_unchanged():
    # 3년 이하는 기본표 그대로, 기간이 길수록 주식 비중은 줄지 않고 현금·채권은 늘지 않는다
    for risk, base in BASE_ALLOCATIONS.items():
        assert compute_allocation(risk, 3) == {k: float(v) for k, v in base.items()}
        prev = compute_allocation(risk, 1)
        for h in range(2, 11):
            cur = compute_allocation(risk, h)
            assert cur["국내주식"] + cur["해외주식"] >= prev["국내주식"] + prev["해외주식"]
            assert cur["현금"] <= prev["현금"] and cur["국내채권"] <= prev["국내채권"]
            prev = cur


def test_allocation_unknown_profile_and_clamped_horizon():
    assert compute_allocation("weird", 5) == compute_allocation("moderate", 5)
    assert compute_allocation("aggressive", 99) == compute_allocation("aggressive", 10)
    assert compute_allocation("conservative", -5) == compute_allocation("conservative", 1)
