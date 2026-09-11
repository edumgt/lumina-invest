resource "aws_secretsmanager_secret" "app" {
  name        = "lumina-invest/prod/app"
  description = "Lumina Invest: DB credentials, JWT secrets, connection URLs"
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id

  # Placeholder values — update DATABASE_URL password after RDS creation
  secret_string = jsonencode({
    SESSION_SECRET  = "change-me-32-char-random-session-secret"
    JWT_SECRET      = "change-me-32-char-random-jwt-secret"
    DATABASE_URL    = "postgresql+asyncpg://pguser:ChangeMe123!@${aws_db_instance.main.endpoint}/fin_ai"
    REDIS_URL       = "redis://172.30.2.131:6379"
    OLLAMA_BASE_URL = "http://172.30.2.131:11434"
    QDRANT_URL      = "http://172.30.2.131:6333"
    NEO4J_URI       = "bolt://172.30.2.131:7687"
    NEO4J_USER      = "neo4j"
    NEO4J_PASSWORD  = "change-me-neo4j"

    # LLM 서빙 백엔드 선택 (ollama 기본값 — Bedrock/SageMaker/vLLM으로 바꾸려면
    # 아래 값을 채우고 LLM_PROVIDER를 변경. 자세한 내용은 .env.example 참고)
    LLM_PROVIDER            = "ollama"
    BEDROCK_MODEL_ID        = ""
    SAGEMAKER_ENDPOINT_NAME = ""
    VLLM_BASE_URL           = ""
    VLLM_MODEL              = ""
  })

  # Prevent Terraform from overwriting secrets updated outside of Terraform
  lifecycle {
    ignore_changes = [secret_string]
  }
}
