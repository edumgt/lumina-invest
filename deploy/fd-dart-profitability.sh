#!/usr/bin/env bash
# 6-57 배포: 기업 대시보드 펀더멘탈 — DART 계산 ROE·ROA·영업이익률·순이익률·부채비율·배당(DPS) + 순이익 계정명 보강(반기순이익(손실)).
# lumina-invest 루트에서 실행. 호스트 소스 갱신 + 컨테이너 docker cp + fin-ai-app 재시작(stock.py 를 worker 도 import 하므로 worker/beat 도 재시작).
# DB 변경 없음. 캐시 키를 올렸으므로(fundamentals:v6, dart:fin:v3, dart:pershare:v2) 옛 캐시는 그냥 남는다.
# 사이클 중 재시작을 피하려면 장 마감 후 또는 사이클 직후에 실행.
set -euo pipefail
cd "$(dirname "$0")/.."
K=fd.edumgt.co.kr.pem; H=ubuntu@43.201.229.188
PY="app/services/dart.py app/services/stock.py"
FE="public/js/company.js"
TESTS="tests/test_dart_profitability.py"
tar czf - $PY $FE $TESTS deploy/fd-dart-profitability.sh | ssh -i "$K" -o StrictHostKeyChecking=no "$H" "
  set -e; cd ~/lumina-invest && tar xzf - && echo 'host source updated'
  tar cf - $PY $FE | sudo -n docker cp - fin-ai-app:/app/
  for c in fin-ai-celery-worker fin-ai-celery-beat; do tar cf - $PY | sudo -n docker cp - \$c:/app/; done
  sudo -n docker restart fin-ai-app >/dev/null && echo 'restarted: app'; sleep 12
  sudo -n docker restart fin-ai-celery-beat fin-ai-celery-worker >/dev/null && echo 'restarted: beat, worker'; sleep 8
  sudo -n docker logs --since 60s fin-ai-app 2>&1 | grep -iE 'traceback|error|startup' | tail -5 || true
  # 아모텍 실측: EPS·ROE·ROA·영업이익률·부채비율·배당(0) 이 채워지는지 확인(캐시 미사용 경로라 DART 를 직접 두드린다)
  sudo -n docker exec fin-ai-app python -c \"import asyncio, json
from app.services import dart
async def m():
    pm = await dart.profitability_metrics('052710.KQ'); ps = await dart.per_share_metrics('052710.KQ'); dv = await dart.dividend_info('052710.KQ')
    print('profit', json.dumps({k: (pm or {}).get(k) for k in ('roe','roa','opMargin','debtRatio','notes')}, ensure_ascii=False))
    print('eps', (ps or {}).get('eps'), (ps or {}).get('epsBasis'), '| dps', (dv or {}).get('dps'), (dv or {}).get('noDividend'))
asyncio.run(m())\" 2>&1 | grep -v 'PostgreSQL not connected'
"
echo "done. 화면: https://fd.edumgt.co.kr/app.html#company-dashboard → 아모텍 (강력 새로고침)"
