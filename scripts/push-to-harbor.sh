#!/usr/bin/env bash
set -euo pipefail

HARBOR="192.168.56.32"
PROJECT="library"
# Windows Docker CLI (Docker Hub credential helper 정상 동작)
WIN_DOCKER="/mnt/c/Program Files/Docker/Docker/resources/bin/docker"

echo ">>> Harbor 로그인"
docker login "$HARBOR"

# ── node:24-alpine ─────────────────────────────────────────
# WSL2 docker는 Docker Hub credential helper(.exe) 호출 실패하므로
# Windows Docker CLI로 pull 후 Harbor에 push
if ! docker image inspect node:24-alpine &>/dev/null; then
  echo ">>> node:24-alpine: Windows Docker CLI로 pull"
  "$WIN_DOCKER" pull node:24-alpine
fi
docker tag node:24-alpine "${HARBOR}/${PROJECT}/node:24-alpine"
docker push "${HARBOR}/${PROJECT}/node:24-alpine"
echo "✓ node:24-alpine → Harbor"

# ── ollama (이미 로컬에 존재) ──────────────────────────────
docker tag ollama/ollama:latest "${HARBOR}/${PROJECT}/ollama:latest"
docker push "${HARBOR}/${PROJECT}/ollama:latest"
echo "✓ ollama → Harbor"

# ── qdrant (이미 로컬에 존재) ─────────────────────────────
docker tag qdrant/qdrant:latest "${HARBOR}/${PROJECT}/qdrant:latest"
docker push "${HARBOR}/${PROJECT}/qdrant:latest"
echo "✓ qdrant → Harbor"

echo ""
echo "모든 이미지가 Harbor에 등록됐습니다."
echo "이제 docker compose up -d 로 실행하세요."
