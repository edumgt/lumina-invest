#!/usr/bin/env bash
# 6-66 배포: 대시보드(#dashboard) 좌우 카드·타일·탭 뱃지 단 정리. lumina-invest 루트에서 사용자가 직접 실행.
# 정적 파일만 바뀌므로 호스트 소스 갱신 + fin-ai-app 에 docker cp 로 끝(재시작 불필요, /js /css 는 no-cache).
# 주의(6-56 과 같은 상황): 로컬 public/app.html 에는 Codex 가 진행 중인 company-dashboard 작업이 같이 들어 있어, 서버에는
#       커밋본(HEAD) app.html 에 대시보드 변경(.overview-intro/.overview-grid 래퍼 + app.css 버전)만 적용한 사본을 보낸다.
#       app.css / dashboard.js 는 작업본 diff 가 이 작업뿐이라 그대로 보낸다(배포 전 git diff 로 재확인).
set -euo pipefail
cd "$(dirname "$0")/.."
K=fd.edumgt.co.kr.pem; H=ubuntu@43.201.229.188
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/public/css" "$TMP/public/js"
# (heredoc 와 파이프가 모두 stdin 이라 겹치므로 HEAD 사본을 파일로 먼저 뽑는다)
git show HEAD:public/app.html > "$TMP/head-app.html"
python3 - "$TMP/head-app.html" "$TMP/public/app.html" <<'PY'
import sys
s = open(sys.argv[1], encoding="utf-8").read()
def rep(old, new):
    global s
    assert s.count(old) == 1, f"anchor not found once: {old[:60]!r}"
    s = s.replace(old, new)
rep("""          <h3>투자 사이트별 현재 투자액</h3><p class="overview-note">탭을 눌러 사이트별 계좌를 확인하세요. KIS 모의투자가 기본입니다.</p>
          <div id="overview-account-tabs" class="overview-tabs" role="tablist" aria-label="투자 사이트"></div>
          <div id="overview-account-panel" class="overview-metrics" role="tabpanel"><p class="overview-note" role="status">계좌를 불러오는 중입니다…</p></div>
          <div id="overview-robo-metrics" class="overview-metrics hidden"></div>
          <div id="overview-decision" class="overview-metrics"></div>
        </section>""",
"""          <div class="overview-intro">
            <h3>투자 사이트별 현재 투자액</h3><p class="overview-note">탭을 눌러 사이트별 계좌를 확인하세요. KIS 모의투자가 기본입니다.</p>
            <div id="overview-account-tabs" class="overview-tabs" role="tablist" aria-label="투자 사이트"></div>
          </div>
          <div class="overview-grid">
            <div id="overview-account-panel" class="overview-metrics" role="tabpanel"><p class="overview-note" role="status">계좌를 불러오는 중입니다…</p></div>
            <div id="overview-robo-metrics" class="overview-metrics hidden"></div>
            <div id="overview-decision" class="overview-metrics"></div>
          </div>
        </section>""")
rep("""          <h3>기본 전략 성능</h3><p class="overview-note">삼성전자 · 최근 1년 · RSI 역추세 · 수수료 10bp · 슬리피지 0bp<br>과거 데이터 백테스트 결과이며 내 계좌의 실현 수익률과 다릅니다.</p>
          <div id="overview-indicator-metrics" class="overview-metrics"></div>
          <div id="overview-saved" class="overview-metrics"></div>
        </section>""",
"""          <div class="overview-intro">
            <h3>기본 전략 성능</h3><p class="overview-note">삼성전자 · 최근 1년 · RSI 역추세 · 수수료 10bp · 슬리피지 0bp<br>과거 데이터 백테스트 결과이며 내 계좌의 실현 수익률과 다릅니다.</p>
          </div>
          <div class="overview-grid">
            <div id="overview-indicator-metrics" class="overview-metrics"></div>
            <div id="overview-saved" class="overview-metrics"></div>
          </div>
        </section>""")
rep('css/app.css?v=20261022-kis-compact-1', 'css/app.css?v=20261022-dashboard-tidy-1')
open(sys.argv[2], 'w', encoding='utf-8').write(s)
print("app.html built from HEAD + dashboard hunks")
PY
grep -q 'company-dashboard.css' "$TMP/public/app.html" && { echo "FAIL: 배포본 app.html 에 미완 company-dashboard 참조가 있음"; exit 1; }
cp public/css/app.css "$TMP/public/css/"; cp public/js/dashboard.js "$TMP/public/js/"
grep -q 'overview-grid' "$TMP/public/css/app.css" && grep -q "'wide'" "$TMP/public/js/dashboard.js" || { echo "FAIL: css/js 작업본 확인"; exit 1; }
FILES="public/app.html public/css/app.css public/js/dashboard.js"
if [ "${1:-}" = "--dry-run" ]; then echo "dry-run: 배포본 검증 통과 ($(grep -c overview-grid "$TMP/public/app.html") overview-grid in app.html)"; n=$( { diff <(git show HEAD:public/app.html) "$TMP/public/app.html" || true; } | grep -c "^[<>]" || true); echo "변경 줄 수: $n (HEAD 대비, 대시보드 래퍼·css 버전만)"; exit 0; fi
tar czf - -C "$TMP" $FILES | ssh -i "$K" -o StrictHostKeyChecking=no "$H" "
  set -e; cd ~/lumina-invest && tar xzf - && echo 'host source updated'
  tar cf - $FILES | sudo -n docker cp - fin-ai-app:/app/
  sudo -n docker exec fin-ai-app grep -c 'overview-grid' /app/public/app.html /app/public/css/app.css
"
echo "done. 화면: https://fd.edumgt.co.kr/app.html#dashboard (강력 새로고침)"
