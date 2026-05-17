#!/usr/bin/env bash
# .env의 OLLAMA_BASE_URL이 응답하면 그대로 사용하고,
# 없거나 응답 없으면 로컬 포트를 탐색합니다.
# Qdrant도 동일한 방식으로 처리합니다.
set -e

# ── .env 로드 ─────────────────────────────────────────────────
ENV_FILE="${ENV_FILE:-.env}"
if [[ -f "$ENV_FILE" ]]; then
  # export 가능한 변수만 읽기 (주석·빈 줄 제외)
  set -a; source <(grep -v '^\s*#' "$ENV_FILE" | grep '='); set +a
fi

PROFILES=()

# ── Ollama 감지 ──────────────────────────────────────────────
check_ollama_url() {
  curl -sf --connect-timeout 2 "${1}/api/tags" >/dev/null 2>&1
}

if [[ -n "$OLLAMA_BASE_URL" ]] && check_ollama_url "$OLLAMA_BASE_URL"; then
  echo "[start.sh] .env Ollama 사용: $OLLAMA_BASE_URL"
  # OLLAMA_BASE_URL을 shell에서 export해 compose가 env_file보다 우선 사용
else
  # 공통 로컬 포트 탐색 (11434: 기본, 11435: ai-lab-ollama 등)
  for port in 11434 11435; do
    if curl -sf --connect-timeout 2 "http://localhost:${port}/api/tags" >/dev/null 2>&1; then
      echo "[start.sh] Ollama 감지 (포트 ${port}) → host.docker.internal:${port} 사용"
      export OLLAMA_BASE_URL="http://host.docker.internal:${port}"
      break
    fi
  done
  if [[ -z "$OLLAMA_BASE_URL" ]]; then
    echo "[start.sh] Ollama 없음 → local-ollama 컨테이너 시작"
    unset OLLAMA_BASE_URL   # compose 기본값(http://ollama:11434) 사용
    PROFILES+=(local-ollama)
  fi
fi

# ── Qdrant 감지 ──────────────────────────────────────────────
check_qdrant_url() {
  curl -sf --connect-timeout 2 "${1}/healthz" >/dev/null 2>&1
}

if [[ -n "$QDRANT_URL" ]] && check_qdrant_url "$QDRANT_URL"; then
  echo "[start.sh] .env Qdrant 사용: $QDRANT_URL"
else
  if curl -sf --connect-timeout 2 "http://localhost:6333/healthz" >/dev/null 2>&1; then
    echo "[start.sh] Qdrant 감지 (포트 6333) → host.docker.internal:6333 사용"
    export QDRANT_URL="http://host.docker.internal:6333"
  else
    echo "[start.sh] Qdrant 없음 → local-qdrant 컨테이너 시작"
    unset QDRANT_URL
    PROFILES+=(local-qdrant)
  fi
fi

# ── Docker Compose 실행 ───────────────────────────────────────
PROFILE_ARGS=()
for p in "${PROFILES[@]}"; do
  PROFILE_ARGS+=(--profile "$p")
done

docker compose "${PROFILE_ARGS[@]}" up -d "$@"
