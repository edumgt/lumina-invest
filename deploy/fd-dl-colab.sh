#!/usr/bin/env bash
# 6-62 배포: 딥러닝(LSTM·Transformer) 화면 Colab 연동 — /api/ml/dl/dataset(CSV)·/summary·/notebook + 화면 재구성 + notebooks/dl_lstm_transformer.ipynb.
# lumina-invest 루트에서 실행. 호스트 소스 갱신 + fin-ai-app 에 docker cp + app 재시작(ml.py 는 worker 가 import 하지 않으므로 app 만).
# DB·env 변경 없음. torch 는 서버에 넣지 않는다(학습은 Colab). Dockerfile 에 COPY notebooks/ 가 추가되어 다음 재빌드부터는 이미지에 포함된다.
set -euo pipefail
cd "$(dirname "$0")/.."
K=fd.edumgt.co.kr.pem; H=ubuntu@43.201.229.188
PY="app/routes/ml.py"
FE="public/app.html public/js/ml.js public/js/core.js"
NB="notebooks/dl_lstm_transformer.ipynb"
TESTS="tests/test_ml_dl_colab.py"
tar czf - $PY $FE $NB Dockerfile $TESTS deploy/fd-dl-colab.sh | ssh -i "$K" -o StrictHostKeyChecking=no "$H" "
  set -e; cd ~/lumina-invest && tar xzf - && echo 'host source updated'
  tar cf - $PY $FE $NB | sudo -n docker cp - fin-ai-app:/app/
  sudo -n docker restart fin-ai-app >/dev/null && echo 'restarted: app'; sleep 12
  sudo -n docker logs --since 60s fin-ai-app 2>&1 | grep -iE 'traceback|error|startup' | tail -5 || true
  # 라우트 등록·노트북 경로 확인 (인증 없이 401 이면 라우트는 살아 있는 것)
  for p in /api/ml/dl/dataset/summary /api/ml/dl/dataset /api/ml/dl/notebook; do
    printf '%s -> ' \$p; sudo -n docker exec fin-ai-app python -c \"import urllib.request,urllib.error
try: print(urllib.request.urlopen('http://localhost:8000$p?symbol=005930.KS&period=2y').status)
except urllib.error.HTTPError as e: print(e.code)\"
  done
  sudo -n docker exec fin-ai-app python -c \"from app.routes.ml import DL_NOTEBOOK_PATH as p; print('notebook in container:', p.is_file(), p)\"
"
echo "done. 화면: https://fd.edumgt.co.kr/app.html#ml-deeplearning (강력 새로고침) — 미리보기 → CSV 내려받기 → Colab 에서 열기"
echo "주의: 'Colab 에서 열기' 는 GitHub main 의 notebooks/dl_lstm_transformer.ipynb 를 여므로 커밋·push 가 먼저 되어야 한다."
