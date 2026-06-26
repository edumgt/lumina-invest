# ─────────────────────────────────────────
# Shared trust policy documents
# ─────────────────────────────────────────
data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "codebuild_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["codebuild.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "codepipeline_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["codepipeline.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "events_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

# ─────────────────────────────────────────
# ECS Task Execution Role
# ─────────────────────────────────────────
resource "aws_iam_role" "ecs_task_execution" {
  name               = "ecsTaskExecutionRole"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy_attachment" "ecs_task_execution_base" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "ecs_task_execution_secrets" {
  name = "SecretsManagerReadAccess"
  role = aws_iam_role.ecs_task_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
      Resource = ["arn:aws:secretsmanager:${var.region}:${var.account_id}:secret:lumina-invest/prod/app*"]
    }]
  })
}

# ─────────────────────────────────────────
# ECS Task Role (application)
# ─────────────────────────────────────────
resource "aws_iam_role" "lumina_invest_task" {
  name               = "luminaInvestTaskRole"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

# ─────────────────────────────────────────
# EventBridge Scheduler Role
# ─────────────────────────────────────────
resource "aws_iam_role" "scheduler" {
  name               = "luminaSchedulerRole"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

resource "aws_iam_role_policy" "scheduler_ecs" {
  name = "scheduler-ecs-run-task"
  role = aws_iam_role.scheduler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ecs:RunTask"]
        Resource = ["arn:aws:ecs:${var.region}:${var.account_id}:task-definition/lumina-invest-worker:*"]
      },
      {
        Effect = "Allow"
        Action = ["iam:PassRole"]
        Resource = [
          aws_iam_role.ecs_task_execution.arn,
          aws_iam_role.lumina_invest_task.arn,
        ]
      }
    ]
  })
}

# ─────────────────────────────────────────
# Lambda Execution Role (VPC + Secrets)
# ─────────────────────────────────────────
resource "aws_iam_role" "lambda_exec" {
  name               = "lumina-lambda-exec-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "lambda_exec_vpc" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_iam_role_policy" "lambda_exec_secrets" {
  name = "SecretsManagerReadAccess"
  role = aws_iam_role.lambda_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
      Resource = ["arn:aws:secretsmanager:${var.region}:${var.account_id}:secret:lumina-invest/prod/app*"]
    }]
  })
}

# ─────────────────────────────────────────
# Slack Lambda Role (basic execution only)
# ─────────────────────────────────────────
resource "aws_iam_role" "lambda_slack" {
  name               = "lumina-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "lambda_slack_basic" {
  role       = aws_iam_role.lambda_slack.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# ─────────────────────────────────────────
# CodeBuild Role
# ─────────────────────────────────────────
resource "aws_iam_role" "codebuild" {
  name               = "lumina-codebuild-role"
  assume_role_policy = data.aws_iam_policy_document.codebuild_assume.json
}

resource "aws_iam_role_policy" "codebuild_policy" {
  name = "lumina-codebuild-policy"
  role = aws_iam_role.codebuild.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ecr:GetAuthorizationToken",
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
          "ecr:PutImage",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload",
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = ["lambda:UpdateFunctionCode", "lambda:GetFunction"]
        Resource = [
          "arn:aws:lambda:${var.region}:${var.account_id}:function:lumina-auth-service",
          "arn:aws:lambda:${var.region}:${var.account_id}:function:lumina-crawl-service",
        ]
      },
      {
        Effect = "Allow"
        Action = ["ecs:UpdateService", "ecs:DescribeServices"]
        Resource = [
          "arn:aws:ecs:${var.region}:${var.account_id}:service/lumina-prod/lumina-invest-api-svc",
          "arn:aws:ecs:${var.region}:${var.account_id}:service/lumina-prod/lumina-invest-worker-svc",
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject", "s3:GetBucketLocation"]
        Resource = [
          "${aws_s3_bucket.source.arn}/*",
          "${aws_s3_bucket.pipeline_artifacts.arn}/*",
        ]
      },
    ]
  })
}

# ─────────────────────────────────────────
# CodePipeline Role
# ─────────────────────────────────────────
resource "aws_iam_role" "codepipeline" {
  name               = "lumina-codepipeline-role"
  assume_role_policy = data.aws_iam_policy_document.codepipeline_assume.json
}

resource "aws_iam_role_policy" "codepipeline_policy" {
  name = "lumina-codepipeline-policy"
  role = aws_iam_role.codepipeline.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject", "s3:GetObjectVersion",
          "s3:GetBucketVersioning", "s3:PutObject",
        ]
        Resource = [
          aws_s3_bucket.source.arn,
          "${aws_s3_bucket.source.arn}/*",
          aws_s3_bucket.pipeline_artifacts.arn,
          "${aws_s3_bucket.pipeline_artifacts.arn}/*",
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["codebuild:BatchGetBuilds", "codebuild:StartBuild"]
        Resource = "*"
      },
    ]
  })
}

# ─────────────────────────────────────────
# EC2 SSM Role (bastion-host)
# ─────────────────────────────────────────
resource "aws_iam_role" "ec2_ssm" {
  name               = "lumina-ec2-ssm-role"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

resource "aws_iam_role_policy_attachment" "ec2_ssm_core" {
  role       = aws_iam_role.ec2_ssm.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "ec2_s3_deploy" {
  name = "S3DeployAccess"
  role = aws_iam_role.ec2_ssm.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject"]
        Resource = ["${aws_s3_bucket.source.arn}/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["codepipeline:StartPipelineExecution"]
        Resource = ["arn:aws:codepipeline:${var.region}:${var.account_id}:lumina-invest-pipeline"]
      },
    ]
  })
}

resource "aws_iam_instance_profile" "ec2_ssm" {
  name = "lumina-ec2-ssm-profile"
  role = aws_iam_role.ec2_ssm.name
}

# ─────────────────────────────────────────
# EventBridge → CodePipeline Role
# ─────────────────────────────────────────
resource "aws_iam_role" "events_pipeline" {
  name               = "lumina-events-pipeline-role"
  assume_role_policy = data.aws_iam_policy_document.events_assume.json
}

resource "aws_iam_role_policy" "events_pipeline_policy" {
  name = "StartPipelineExecution"
  role = aws_iam_role.events_pipeline.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["codepipeline:StartPipelineExecution"]
      Resource = ["arn:aws:codepipeline:${var.region}:${var.account_id}:lumina-invest-pipeline"]
    }]
  })
}
