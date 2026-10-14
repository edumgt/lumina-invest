#!/usr/bin/env bash
# 6-58 배포: 수익율검증 — 장중 실시간 평가액 타일(GET 응답 live). lumina-invest 루트에서 실행.
# 호스트 소스 갱신 + fin-ai-app 에 docker cp + fin-ai-app 만 재시작. worker/beat 에도 파일은 복사하지만 재시작하지 않는다
# (추가 함수뿐이라 비트 태스크 quant.equity_snapshot 동작과 무관. 15:40 KST 자동 스냅샷을 방해하지 않도록 beat/worker 는 건드리지 않음).
set -euo pipefail
cd "$(dirname "$0")/.."
K=fd.edumgt.co.kr.pem; H=ubuntu@43.201.229.188
PY="app/services/return_review.py app/routes/return_review.py"
FE="public/js/return_review.js"
TESTS="tests/test_return_review.py"
tar czf - $PY $FE $TESTS deploy/fd-return-review-live.sh | ssh -i "$K" -o StrictHostKeyChecking=no "$H" "
  set -e; cd ~/lumina-invest && tar xzf - && echo 'host source updated'
  tar cf - $PY $FE | sudo -n docker cp - fin-ai-app:/app/
  for c in fin-ai-celery-worker fin-ai-celery-beat; do tar cf - $PY | sudo -n docker cp - \$c:/app/; done
  sudo -n docker restart fin-ai-app >/dev/null && echo 'restarted: app'; sleep 12
  sudo -n docker logs --since 60s fin-ai-app 2>&1 | grep -iE 'traceback|error|startup' | tail -5 || true
  sudo -n docker exec fin-ai-app python -c \"import asyncio, json
from app.services import return_review as rr
async def m():
    live = await rr.live_equity([{'snap_date': '2026-10-12', 'principal': 100000000, 'equity': 93338237}])
    print(json.dumps({k: live.get(k) for k in ('ok','market','equity','change_pnl','change_pct','cum_return_pct','error')}, ensure_ascii=False))
asyncio.run(m())\" 2>&1 | grep -v 'PostgreSQL not connected'
"
echo "done. 화면: https://fd.edumgt.co.kr/app.html#return-review (강력 새로고침)"
