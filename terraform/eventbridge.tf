# ─────────────────────────────────────────
# Pipeline periodic trigger (every 5 min)
# ─────────────────────────────────────────
resource "aws_cloudwatch_event_rule" "pipeline_schedule" {
  name                = "lumina-pipeline-every-5min"
  description         = "Trigger lumina-invest-pipeline every 5 minutes"
  schedule_expression = "rate(5 minutes)"
}

resource "aws_cloudwatch_event_target" "pipeline_schedule" {
  rule     = aws_cloudwatch_event_rule.pipeline_schedule.name
  arn      = "arn:aws:codepipeline:${var.region}:${var.account_id}:${aws_codepipeline.main.name}"
  role_arn = aws_iam_role.events_pipeline.arn
}

# ─────────────────────────────────────────
# Pipeline state change → Slack notify
# ─────────────────────────────────────────
resource "aws_cloudwatch_event_rule" "pipeline_state" {
  name        = "lumina-pipeline-state-change"
  description = "Notify Slack when pipeline STARTED / SUCCEEDED / FAILED"

  event_pattern = jsonencode({
    source        = ["aws.codepipeline"]
    "detail-type" = ["CodePipeline Pipeline Execution State Change"]
    detail = {
      pipeline = [aws_codepipeline.main.name]
      state    = ["STARTED", "SUCCEEDED", "FAILED"]
    }
  })
}

resource "aws_cloudwatch_event_target" "pipeline_state_slack" {
  rule = aws_cloudwatch_event_rule.pipeline_state.name
  arn  = aws_lambda_function.slack_notify.arn
}

resource "aws_lambda_permission" "eventbridge_slack" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.slack_notify.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.pipeline_state.arn
}

# ─────────────────────────────────────────
# EventBridge Scheduler: hourly sync
# ─────────────────────────────────────────
resource "aws_scheduler_schedule" "hourly_sync" {
  name       = "lumina-hourly-sync"
  group_name = "default"

  flexible_time_window { mode = "OFF" }

  schedule_expression = "rate(1 hour)"

  target {
    arn      = aws_ecs_cluster.main.arn
    role_arn = aws_iam_role.scheduler.arn

    ecs_parameters {
      task_definition_arn = aws_ecs_task_definition.worker.arn
      launch_type         = "FARGATE"

      network_configuration {
        subnets          = [aws_subnet.private_a.id, aws_subnet.private_b.id]
        security_groups  = [aws_security_group.ecs.id]
        assign_public_ip = true
      }
    }
  }
}

# ─────────────────────────────────────────
# EventBridge Scheduler: daily candles (00:00 UTC)
# ─────────────────────────────────────────
resource "aws_scheduler_schedule" "daily_candles" {
  name       = "lumina-daily-candles"
  group_name = "default"

  flexible_time_window { mode = "OFF" }

  schedule_expression = "cron(0 0 * * ? *)"

  target {
    arn      = aws_ecs_cluster.main.arn
    role_arn = aws_iam_role.scheduler.arn

    ecs_parameters {
      task_definition_arn = aws_ecs_task_definition.worker.arn
      launch_type         = "FARGATE"

      network_configuration {
        subnets          = [aws_subnet.private_a.id, aws_subnet.private_b.id]
        security_groups  = [aws_security_group.ecs.id]
        assign_public_ip = true
      }
    }
  }
}
