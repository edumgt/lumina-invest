# ─────────────────────────────────────────
# ECS Cluster
# ─────────────────────────────────────────
resource "aws_ecs_cluster" "main" {
  name = "lumina-prod"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

# ─────────────────────────────────────────
# CloudWatch Log Groups
# ─────────────────────────────────────────
resource "aws_cloudwatch_log_group" "ecs_api" {
  name              = "/ecs/lumina-invest-api"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "ecs_worker" {
  name              = "/ecs/lumina-invest-worker"
  retention_in_days = 14
}

# ─────────────────────────────────────────
# API Task Definition
# ─────────────────────────────────────────
resource "aws_ecs_task_definition" "api" {
  family                   = "lumina-invest-api"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "1024"
  memory                   = "2048"
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.lumina_invest_task.arn

  container_definitions = jsonencode([{
    name      = "api"
    image     = "${var.account_id}.dkr.ecr.${var.region}.amazonaws.com/lumina-invest:latest"
    essential = true

    portMappings = [{
      containerPort = 8000
      protocol      = "tcp"
    }]

    environment = [
      { name = "MONGO_DB",      value = "fin_agent" },
      { name = "TRUST_PROXY",   value = "true" },
      { name = "COOKIE_SECURE", value = "false" },
    ]

    secrets = [
      { name = "SESSION_SECRET",  valueFrom = "${aws_secretsmanager_secret.app.arn}:SESSION_SECRET::" },
      { name = "JWT_SECRET",      valueFrom = "${aws_secretsmanager_secret.app.arn}:JWT_SECRET::" },
      { name = "MONGO_URI",       valueFrom = "${aws_secretsmanager_secret.app.arn}:MONGO_URI::" },
      { name = "REDIS_URL",       valueFrom = "${aws_secretsmanager_secret.app.arn}:REDIS_URL::" },
      { name = "OLLAMA_BASE_URL", valueFrom = "${aws_secretsmanager_secret.app.arn}:OLLAMA_BASE_URL::" },
      { name = "QDRANT_URL",      valueFrom = "${aws_secretsmanager_secret.app.arn}:QDRANT_URL::" },
      { name = "NEO4J_URI",       valueFrom = "${aws_secretsmanager_secret.app.arn}:NEO4J_URI::" },
      { name = "NEO4J_USER",      valueFrom = "${aws_secretsmanager_secret.app.arn}:NEO4J_USER::" },
      { name = "NEO4J_PASSWORD",  valueFrom = "${aws_secretsmanager_secret.app.arn}:NEO4J_PASSWORD::" },
    ]

    healthCheck = {
      command     = ["CMD-SHELL", "curl -f http://localhost:8000/api/health || exit 1"]
      interval    = 30
      timeout     = 10
      retries     = 3
      startPeriod = 60
    }

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.ecs_api.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "ecs"
      }
    }
  }])
}

# ─────────────────────────────────────────
# Worker Task Definition
# ─────────────────────────────────────────
resource "aws_ecs_task_definition" "worker" {
  family                   = "lumina-invest-worker"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.lumina_invest_task.arn

  container_definitions = jsonencode([{
    name      = "worker"
    image     = "${var.account_id}.dkr.ecr.${var.region}.amazonaws.com/lumina-invest:latest"
    essential = true
    command   = ["celery", "-A", "app.celery_app", "worker", "--loglevel=info", "--concurrency=2", "--queues=celery"]

    environment = [
      { name = "MONGO_DB", value = "fin_agent" },
    ]

    secrets = [
      { name = "MONGO_URI",       valueFrom = "${aws_secretsmanager_secret.app.arn}:MONGO_URI::" },
      { name = "REDIS_URL",       valueFrom = "${aws_secretsmanager_secret.app.arn}:REDIS_URL::" },
      { name = "OLLAMA_BASE_URL", valueFrom = "${aws_secretsmanager_secret.app.arn}:OLLAMA_BASE_URL::" },
      { name = "QDRANT_URL",      valueFrom = "${aws_secretsmanager_secret.app.arn}:QDRANT_URL::" },
      { name = "NEO4J_URI",       valueFrom = "${aws_secretsmanager_secret.app.arn}:NEO4J_URI::" },
      { name = "NEO4J_USER",      valueFrom = "${aws_secretsmanager_secret.app.arn}:NEO4J_USER::" },
      { name = "NEO4J_PASSWORD",  valueFrom = "${aws_secretsmanager_secret.app.arn}:NEO4J_PASSWORD::" },
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.ecs_worker.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "ecs"
      }
    }
  }])
}

# ─────────────────────────────────────────
# ECS Services
# ─────────────────────────────────────────
resource "aws_ecs_service" "api" {
  name            = "lumina-invest-api-svc"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = 2
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = [aws_subnet.private_a.id, aws_subnet.private_b.id]
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }

  depends_on = [aws_lb_listener.http]
}

resource "aws_ecs_service" "worker" {
  name            = "lumina-invest-worker-svc"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = [aws_subnet.private_a.id, aws_subnet.private_b.id]
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = true
  }
}
