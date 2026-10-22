#!/usr/bin/env bash
# 6-63 배포: 감리 FD-FIND-001(CRITICAL) — 로보어드바이저 자산배분 장기 구간 음수 현금 비중 수정.
# lumina-invest 루트에서 사용자가 직접 실행. 호스트 소스 갱신 + fin-ai-app 에 docker cp + fin-ai-app 재시작(파이썬 코드라 재시작 필수).
# 변경 파일: app/services/robo_profile.py(compute_allocation 추가), app/routes/ml.py(/api/ml/robo/allocation 이 그것을 사용), tests/test_robo_profile.py
set -euo pipefail
cd "$(dirname "$0")/.."
K=fd.edumgt.co.kr.pem; H=ubuntu@43.201.229.188
FILES="app/services/robo_profile.py app/routes/ml.py tests/test_robo_profile.py"
.venv/bin/python -m pytest tests/test_robo_profile.py -q >/dev/null && echo "local tests ok" || { echo "로컬 테스트 실패 — 배포 중단"; exit 1; }
tar czf - $FILES | ssh -i "$K" -o StrictHostKeyChecking=no "$H" '
  set -e; cd ~/lumina-invest && tar xzf - && echo "host source updated"
  tar cf - app/services/robo_profile.py app/routes/ml.py | sudo -n docker cp - fin-ai-app:/app/
  sudo -n docker restart fin-ai-app >/dev/null && echo "fin-ai-app restarted"; sleep 8
  sudo -n docker logs --since 40s fin-ai-app 2>&1 | grep -iE "traceback|error" | tail -5 || true
  # 감리 재현 케이스를 컨테이너 안에서 수식만 격리 실행(로그인·시세 불필요): 경계 위반이 하나라도 있으면 FAIL
  sudo -n docker exec fin-ai-app python -c "
from app.services.robo_profile import compute_allocation, ALLOCATION_BOUNDS, BASE_ALLOCATIONS
bad=[(r,h,a) for r in BASE_ALLOCATIONS for h in range(1,11) for a in [compute_allocation(r,h)]
     if any(not ALLOCATION_BOUNDS[k][0]<=v<=ALLOCATION_BOUNDS[k][1] for k,v in a.items()) or abs(sum(a.values())-100)>0.05]
print(\"aggressive/10y:\", compute_allocation(\"aggressive\",10)); print(\"moderate/10y:\", compute_allocation(\"moderate\",10))
print(\"FAIL\" if bad else \"ok: 30 cases within bounds, sum 100\", bad)"
'
echo "done. 운영 확인: 로그인 후 POST https://fd.edumgt.co.kr/api/ml/robo/allocation {\"risk_profile\":\"aggressive\",\"horizon_years\":10,\"amount_manwon\":1000} → 현금 2.0 기대"
