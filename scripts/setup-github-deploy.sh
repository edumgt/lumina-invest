#!/usr/bin/env bash
# GitHub Actions 배포(.github/workflows/deploy.yml)에 필요한 Secret/Variable 을 gh CLI 로 등록한다.
#
# 사전 조건: gh auth login (repo 권한 포함)
# 사용법:
#   scripts/setup-github-deploy.sh \
#     --host <EC2 IP 또는 DNS> --user ubuntu --key ~/.ssh/fund-web.pem \
#     [--app-dir /home/ubuntu/lumina-invest] [--domain example.com] [--repo owner/name]
set -euo pipefail

HOST="" USER_="" KEY="" APP_DIR="" DOMAIN="" REPO=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)    HOST="$2";    shift 2 ;;
    --user)    USER_="$2";   shift 2 ;;
    --key)     KEY="$2";     shift 2 ;;
    --app-dir) APP_DIR="$2"; shift 2 ;;
    --domain)  DOMAIN="$2";  shift 2 ;;
    --repo)    REPO="$2";    shift 2 ;;
    -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
    *) echo "알 수 없는 옵션: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$HOST" && -n "$USER_" && -n "$KEY" ]] || { echo "--host, --user, --key 는 필수입니다" >&2; exit 2; }
[[ -f "$KEY" ]] || { echo "키 파일이 없습니다: $KEY" >&2; exit 2; }

command -v gh >/dev/null || { echo "gh CLI 가 없습니다" >&2; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "gh 에 로그인되어 있지 않습니다: gh auth login" >&2; exit 1; }

REPO_ARGS=()
[[ -n "$REPO" ]] && REPO_ARGS=(--repo "$REPO")

echo "→ Secret  FUND_WEB_SSH_KEY  (from $KEY)"
gh secret set FUND_WEB_SSH_KEY "${REPO_ARGS[@]}" < "$KEY"

echo "→ Variable FUND_WEB_HOST = $HOST"
gh variable set FUND_WEB_HOST "${REPO_ARGS[@]}" --body "$HOST"
echo "→ Variable FUND_WEB_USER = $USER_"
gh variable set FUND_WEB_USER "${REPO_ARGS[@]}" --body "$USER_"
if [[ -n "$APP_DIR" ]]; then
  echo "→ Variable FUND_WEB_APP_DIR = $APP_DIR"
  gh variable set FUND_WEB_APP_DIR "${REPO_ARGS[@]}" --body "$APP_DIR"
fi
if [[ -n "$DOMAIN" ]]; then
  echo "→ Variable FUND_WEB_DOMAIN = $DOMAIN"
  gh variable set FUND_WEB_DOMAIN "${REPO_ARGS[@]}" --body "$DOMAIN"
fi

echo
echo "등록 결과:"
gh secret list "${REPO_ARGS[@]}"
gh variable list "${REPO_ARGS[@]}"
echo
echo "완료. 배포 실행: gh workflow run 'Deploy to fund-web EC2' ${REPO_ARGS[*]:-}"
