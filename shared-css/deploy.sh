#!/usr/bin/env bash
# 4개 사이트 공통 CSS 를 www.edumgt.co.kr S3 로 배포한다.
# 런타임 정본은 S3 이고, 이 디렉터리는 그 소스의 버전 관리 사본이다.
#
#   사용법: ./shared-css/deploy.sh
#
# 스타일을 바꾼 뒤에는 common.css 만 올리면 네 사이트에 함께 반영된다.
# Cache-Control max-age=300 이므로 최대 5분 뒤 전파된다(CloudFront 무효화 권한 없음).
# HTML 쪽 ?v= 는 공통 CSS 자체에는 필요 없고, 각 저장소 CSS 를 고칠 때만 올린다.
set -euo pipefail
cd "$(dirname "$0")"

BUCKET="s3://www.edumgt.co.kr/css/common.css"
URL="https://www.edumgt.co.kr/css/common.css"

aws s3 cp common.css "$BUCKET" \
  --content-type "text/css; charset=utf-8" \
  --cache-control "public, max-age=300"

echo "배포 완료: $URL"
echo "검증:"
curl -fsS -D- -o /dev/null "$URL" | grep -iE '^(HTTP|content-type|cache-control|content-length)'
