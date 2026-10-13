#!/usr/bin/env bash
# 6-46 배포: kis-monitor 배치 제어 버튼. lumina-invest 루트에서 실행. 호스트 소스 갱신 + 컨테이너 docker cp + fin-ai-app 재시작.
set -euo pipefail
cd "$(dirname "$0")/.."
K=fd.edumgt.co.kr.pem; H=ubuntu@43.201.229.188
FILES="app/services/kis_batch.py app/routes/kis_monitor.py public/app.html public/css/app.css public/js/kis_monitor.js public/js/kis_batch_ctl.js public/js/main.js tests/test_kis_batch.py tests/test_kis_monitor_route.py"
tar czf - $FILES | ssh -i "$K" -o StrictHostKeyChecking=no "$H" '
  set -e; cd ~/lumina-invest && tar xzf - && echo "host source updated"
  tar cf - app/services/kis_batch.py app/routes/kis_monitor.py public/app.html public/css/app.css public/js/kis_monitor.js public/js/kis_batch_ctl.js public/js/main.js | sudo -n docker cp - fin-ai-app:/app/
  for c in fin-ai-celery-worker fin-ai-celery-beat; do tar cf - app/services/kis_batch.py app/routes/kis_monitor.py | sudo -n docker cp - $c:/app/; done
  sudo -n docker restart fin-ai-app >/dev/null && echo "fin-ai-app restarted"; sleep 8
  sudo -n docker logs --since 40s fin-ai-app 2>&1 | grep -iE "traceback|error|startup" | tail -5 || true
  sudo -n docker exec fin-ai-app python -c "import urllib.request as u
try: u.urlopen(u.Request(\"http://127.0.0.1:8000/api/quant/kis/batch/control\", data=b\"{}\", headers={\"Content-Type\":\"application/json\"}, method=\"POST\"))
except Exception as e: print(\"route check:\", getattr(e, \"code\", e))"   # 401/422 면 라우트 등록 OK, 404 면 실패
'
