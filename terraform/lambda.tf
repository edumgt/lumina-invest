# ─────────────────────────────────────────
# Auth Lambda  (container image via ECR)
# Note: ECR image must exist before first apply; CodePipeline updates it thereafter
# ─────────────────────────────────────────
resource "aws_lambda_function" "auth" {
  function_name = "lumina-auth-service"
  role          = aws_iam_role.lambda_exec.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.auth_service.repository_url}:latest"
  memory_size   = 512
  timeout       = 30

  vpc_config {
    subnet_ids         = [aws_subnet.private_a.id, aws_subnet.private_b.id]
    security_group_ids = [aws_security_group.ecs.id]
  }

  environment {
    variables = {
      SECRET_NAME = aws_secretsmanager_secret.app.name
      AWS_REGION  = var.region
    }
  }

  # CodePipeline manages image updates
  lifecycle {
    ignore_changes = [image_uri]
  }
}

resource "aws_cloudwatch_log_group" "lambda_auth" {
  name              = "/aws/lambda/lumina-auth-service"
  retention_in_days = 14
}

# ─────────────────────────────────────────
# Crawl Lambda  (container image via ECR)
# ─────────────────────────────────────────
resource "aws_lambda_function" "crawl" {
  function_name = "lumina-crawl-service"
  role          = aws_iam_role.lambda_exec.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.crawl_service.repository_url}:latest"
  memory_size   = 1024
  timeout       = 300

  vpc_config {
    subnet_ids         = [aws_subnet.private_a.id, aws_subnet.private_b.id]
    security_group_ids = [aws_security_group.ecs.id]
  }

  environment {
    variables = {
      SECRET_NAME = aws_secretsmanager_secret.app.name
      AWS_REGION  = var.region
    }
  }

  lifecycle {
    ignore_changes = [image_uri]
  }
}

resource "aws_cloudwatch_log_group" "lambda_crawl" {
  name              = "/aws/lambda/lumina-crawl-service"
  retention_in_days = 14
}

# ─────────────────────────────────────────
# Slack Notify Lambda  (Python 3.12 zip)
# ─────────────────────────────────────────
data "archive_file" "slack_notify" {
  type        = "zip"
  output_path = "${path.module}/.terraform/tmp/lambda_slack_notify.zip"

  source {
    filename = "lambda_function.py"
    content  = <<-PYTHON
      import json, os, urllib.request

      def handler(event, context):
          detail   = event.get("detail", {})
          state    = detail.get("state", "UNKNOWN")
          pipeline = detail.get("pipeline", "lumina-invest-pipeline")

          color_map = {"SUCCEEDED": "#36a64f", "FAILED": "#d00000", "STARTED": "#439FE0"}
          color = color_map.get(state, "#888888")

          region     = os.environ.get("AWS_REGION", "ap-northeast-1")
          console    = f"https://console.aws.amazon.com/codesuite/codepipeline/pipelines/{pipeline}/view?region={region}"
          homepage   = os.environ.get("HOMEPAGE_URL", "https://d3ls3wdarllhnf.cloudfront.net")

          payload = {
              "attachments": [{
                  "color":  color,
                  "title":  f"[{pipeline}] {state}",
                  "fields": [
                      {"title": "상태",       "value": state,    "short": True},
                      {"title": "파이프라인", "value": pipeline, "short": True},
                  ],
                  "actions": [
                      {"type": "button", "text": "CodePipeline", "url": console},
                      {"type": "button", "text": "홈페이지",     "url": homepage},
                  ],
              }]
          }

          req = urllib.request.Request(
              os.environ["SLACK_WEBHOOK_URL"],
              data=json.dumps(payload).encode(),
              headers={"Content-Type": "application/json"},
          )
          urllib.request.urlopen(req)
          return {"statusCode": 200}
    PYTHON
  }
}

resource "aws_lambda_function" "slack_notify" {
  function_name    = "lumina-slack-notify"
  role             = aws_iam_role.lambda_slack.arn
  handler          = "lambda_function.handler"
  runtime          = "python3.12"
  filename         = data.archive_file.slack_notify.output_path
  source_code_hash = data.archive_file.slack_notify.output_base64sha256
  memory_size      = 128
  timeout          = 10

  environment {
    variables = {
      SLACK_WEBHOOK_URL = var.slack_webhook_url
      HOMEPAGE_URL      = "https://${aws_cloudfront_distribution.main.domain_name}"
    }
  }
}

resource "aws_cloudwatch_log_group" "lambda_slack" {
  name              = "/aws/lambda/lumina-slack-notify"
  retention_in_days = 14
}
