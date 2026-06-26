resource "aws_secretsmanager_secret" "app" {
  name        = "lumina-invest/prod/app"
  description = "Lumina Invest: DB credentials, JWT secrets, connection URLs"
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id

  # Placeholder values — update MONGO_URI password after DocumentDB creation
  secret_string = jsonencode({
    SESSION_SECRET  = "change-me-32-char-random-session-secret"
    JWT_SECRET      = "change-me-32-char-random-jwt-secret"
    MONGO_URI       = "mongodb://docdbuser:ChangeMe123!@${aws_docdb_cluster.main.endpoint}:27017/fin_agent?tls=true&replicaSet=rs0&readPreference=secondaryPreferred&retryWrites=false"
    REDIS_URL       = "redis://172.30.2.131:6379"
    OLLAMA_BASE_URL = "http://172.30.2.131:11434"
    QDRANT_URL      = "http://172.30.2.131:6333"
    NEO4J_URI       = "bolt://172.30.2.131:7687"
    NEO4J_USER      = "neo4j"
    NEO4J_PASSWORD  = "change-me-neo4j"
  })

  # Prevent Terraform from overwriting secrets updated outside of Terraform
  lifecycle {
    ignore_changes = [secret_string]
  }
}
