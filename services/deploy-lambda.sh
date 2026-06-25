#!/bin/bash
# Lambda 서비스 배포 스크립트
# 사용법: ./deploy-lambda.sh [auth|crawl|all]

set -e

REGION=ap-northeast-2
ACCOUNT_ID=086015456585
SERVICE=${1:-all}

deploy_service() {
  local name=$1           # auth | crawl
  local dir="services/${name}-service"
  local repo="${name}-service"
  local func="lumina-${name}-service"
  local ecr_uri="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${repo}"

  echo "=== Deploying ${name}-service ==="

  # ECR 레포 생성 (없으면)
  aws ecr describe-repositories --repository-names "$repo" --region $REGION > /dev/null 2>&1 || \
    aws ecr create-repository --repository-name "$repo" --region $REGION > /dev/null

  # ECR 로그인
  aws ecr get-login-password --region $REGION | \
    docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

  # 빌드 & 푸시
  docker build --platform linux/amd64 -t "${repo}:latest" "$dir"
  docker tag "${repo}:latest" "${ecr_uri}:latest"
  docker push "${ecr_uri}:latest"

  # Lambda 함수 생성 또는 업데이트
  if aws lambda get-function --function-name "$func" --region $REGION > /dev/null 2>&1; then
    aws lambda update-function-code \
      --function-name "$func" \
      --image-uri "${ecr_uri}:latest" \
      --region $REGION
  else
    aws lambda create-function \
      --function-name "$func" \
      --package-type Image \
      --code ImageUri="${ecr_uri}:latest" \
      --role "arn:aws:iam::${ACCOUNT_ID}:role/luminaInvestTaskRole" \
      --timeout 30 \
      --memory-size 512 \
      --environment "Variables={}" \
      --region $REGION
  fi

  echo "=== ${name}-service deployed ==="
}

case $SERVICE in
  auth)  deploy_service auth ;;
  crawl) deploy_service crawl ;;
  all)
    deploy_service auth
    deploy_service crawl
    ;;
  *)
    echo "Usage: $0 [auth|crawl|all]"
    exit 1
    ;;
esac
