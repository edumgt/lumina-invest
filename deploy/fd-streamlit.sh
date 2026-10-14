#!/usr/bin/env bash
# 6-60 배포: Streamlit UI 컨테이너(streamlit) + Caddy /ui 라우트. lumina-invest 루트에서 실행.
# 호스트 소스 갱신 → 이미지 빌드(lumina-invest-streamlit:fd) → streamlit 서비스 기동 → Caddy 설정 리로드(무중단) → 확인.
# fin-ai-app·celery 는 건드리지 않는다.
set -euo pipefail
cd "$(dirname "$0")/.."
K=fd.edumgt.co.kr.pem; H=ubuntu@43.201.229.188
FILES="Dockerfile.streamlit docker-compose.yml compose.fd.yml infra/fd-edumgt/Caddyfile streamlit_app tests/test_streamlit_app.py deploy/fd-streamlit.sh"
tar czf - --exclude='__pycache__' $FILES | ssh -i "$K" -o StrictHostKeyChecking=no "$H" "
  set -e; cd ~/lumina-invest && tar xzf - && echo 'host source updated'
  sudo -n docker compose -f docker-compose.yml -f compose.fd.yml build streamlit
  sudo -n docker compose -f docker-compose.yml -f compose.fd.yml up -d --no-deps streamlit
  # Caddyfile 은 ./infra/fd-edumgt/Caddyfile 바인드 마운트(:ro)라 호스트 파일 갱신 + reload 면 된다
  sudo -n docker exec lumina-invest-proxy-1 caddy reload --config /etc/caddy/Caddyfile && echo 'caddy reloaded'
  sleep 8
  sudo -n docker ps --format '{{.Names}}\t{{.Status}}' | grep -E 'streamlit|proxy'
  sudo -n docker logs --since 60s lumina-invest-streamlit-1 2>&1 | tail -5
"
echo "== check"; curl -s -m 10 -o /dev/null -w "https://fd.edumgt.co.kr/ui/ -> %{http_code}\n" https://fd.edumgt.co.kr/ui/
echo "done. 화면: https://fd.edumgt.co.kr/ui/ (기존 app.html 은 그대로)"
