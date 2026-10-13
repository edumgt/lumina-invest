#!/usr/bin/env bash
# 6-47 배포: GNB 「전략설정」(봇 파라미터 설정 + 설정별 성과) + 6-48 우측 하단 기능 완성도 위젯 제거. lumina-invest 루트에서 실행.
# 호스트 소스 갱신 + 컨테이너 docker cp + fin-ai-app / celery-worker / celery-beat 재시작 (python 모듈이 바뀌어 워커도 재시작).
# 사이클 중 재시작을 피하려면 장 마감 후 또는 사이클 직후(beat 로그 "quant_auto_trade_cycle 완료" 직후)에 실행.
set -euo pipefail
cd "$(dirname "$0")/.."
K=fd.edumgt.co.kr.pem; H=ubuntu@43.201.229.188
PY="app/config.py app/main.py app/services/strategy_settings.py app/services/aggressive_mode.py app/services/auto_trade.py app/routes/strategy_settings.py"
FE="public/app.html public/login.html public/register.html public/js/core.js public/js/main.js public/js/kis_monitor.js public/js/strategy_settings.js"
REMOVED="public/js/completion.js public/css/completion.css"   # 6-48: 완성도 위젯 파일 삭제 (호스트 소스 + 컨테이너)
TESTS="tests/test_strategy_settings.py"
tar czf - $PY $FE $TESTS deploy/fd-strategy-settings.sh | ssh -i "$K" -o StrictHostKeyChecking=no "$H" "
  set -e; cd ~/lumina-invest && tar xzf - && echo 'host source updated'
  tar cf - $PY $FE | sudo -n docker cp - fin-ai-app:/app/
  rm -f $REMOVED; for f in $REMOVED; do sudo -n docker exec fin-ai-app rm -f /app/\$f; done; echo 'completion widget files removed'
  for c in fin-ai-celery-worker fin-ai-celery-beat; do tar cf - $PY | sudo -n docker cp - \$c:/app/; done
  sudo -n docker restart fin-ai-celery-beat fin-ai-celery-worker fin-ai-app >/dev/null && echo 'restarted: beat, worker, app'; sleep 10
  sudo -n docker logs --since 60s fin-ai-app 2>&1 | grep -iE 'traceback|error|startup' | tail -5 || true
  sudo -n docker logs --since 60s fin-ai-celery-worker 2>&1 | grep -iE 'traceback|ready|error' | tail -3 || true
  sudo -n docker exec fin-ai-app python -c \"import urllib.request as u
try: u.urlopen(u.Request('http://127.0.0.1:8000/api/quant/strategy/settings'))
except Exception as e: print('route check:', getattr(e, 'code', e))\"   # 401 이면 라우트 등록 OK, 404 면 실패
"
echo "done. 화면: https://fd.edumgt.co.kr/app.html#strategy-settings"
