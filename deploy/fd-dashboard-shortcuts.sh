#!/usr/bin/env bash
# 6-56 배포: 대시보드(#dashboard) 두 카드 하단 「기능 바로가기」 메뉴 제거. lumina-invest 루트에서 실행.
# 정적 파일만 바뀌므로 호스트 소스 갱신 + fin-ai-app 에 docker cp 로 끝(재시작 불필요, /js /css 는 no-cache 라 새로고침이면 반영).
# 주의: 로컬 public/app.html 에는 아직 미배포인 인디케이터 작업(TradingView 차트·Pine 편집기, indicator.js/indicator-chart.js)이
#       같이 들어 있어, 서버에는 커밋본(HEAD)에서 「기능 바로가기」 두 줄만 뺀 app.html 을 보낸다. 인디케이터 작업은 별도 배포.
set -euo pipefail
cd "$(dirname "$0")/.."
K=fd.edumgt.co.kr.pem; H=ubuntu@43.201.229.188
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/public/js" "$TMP/public/css"
git show HEAD:public/app.html | grep -v '<h3>기능 바로가기</h3>' > "$TMP/public/app.html"
[ "$(grep -c '기능 바로가기' "$TMP/public/app.html")" = 0 ] || { echo "app.html 에 바로가기가 남아 있음"; exit 1; }
cp public/js/dashboard.js "$TMP/public/js/"; cp public/css/app.css "$TMP/public/css/"
FILES="public/app.html public/js/dashboard.js public/css/app.css"
tar czf - -C "$TMP" $FILES | ssh -i "$K" -o StrictHostKeyChecking=no "$H" "
  set -e; cd ~/lumina-invest && tar xzf - && echo 'host source updated'
  tar cf - $FILES | sudo -n docker cp - fin-ai-app:/app/
  sudo -n docker exec fin-ai-app grep -c '기능 바로가기' /app/public/app.html && echo 'FAIL: still present' || echo 'ok: shortcut menu removed from container'
  sudo -n docker exec fin-ai-app grep -c 'overview-feature' /app/public/js/dashboard.js /app/public/css/app.css || true
"
echo "done. 화면: https://fd.edumgt.co.kr/app.html#dashboard (강력 새로고침)"
