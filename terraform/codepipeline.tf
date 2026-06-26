resource "aws_cloudwatch_log_group" "codebuild" {
  name              = "/codebuild/lumina-build"
  retention_in_days = 14
}

resource "aws_codebuild_project" "main" {
  name          = "lumina-build"
  description   = "Lumina Invest CI/CD: build & deploy ECR images, Lambda, ECS"
  build_timeout = 60
  service_role  = aws_iam_role.codebuild.arn

  artifacts {
    type = "CODEPIPELINE"
  }

  environment {
    compute_type                = "BUILD_GENERAL1_SMALL"
    image                       = "aws/codebuild/standard:7.0"
    type                        = "LINUX_CONTAINER"
    privileged_mode             = true
    image_pull_credentials_type = "CODEBUILD"

    environment_variable {
      name  = "REGION"
      value = var.region
    }
    environment_variable {
      name  = "ACCOUNT_ID"
      value = var.account_id
    }
    environment_variable {
      name  = "CLUSTER_NAME"
      value = aws_ecs_cluster.main.name
    }
  }

  source {
    type = "CODEPIPELINE"
    buildspec = yamlencode({
      version = "0.2"
      phases = {
        pre_build = {
          commands = [
            "aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com"
          ]
        }
        build = {
          commands = [
            # auth-service
            "docker build --platform linux/amd64 --provenance=false -t auth-service:latest -f services/auth/Dockerfile services/auth/",
            "docker tag auth-service:latest $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/auth-service:latest",
            "docker push $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/auth-service:latest",

            # crawl-service
            "docker build --platform linux/amd64 --provenance=false -t crawl-service:latest -f services/crawl/Dockerfile services/crawl/",
            "docker tag crawl-service:latest $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/crawl-service:latest",
            "docker push $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/crawl-service:latest",

            # core image
            "docker build --platform linux/amd64 --provenance=false -t lumina-invest:latest .",
            "docker tag lumina-invest:latest $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/lumina-invest:latest",
            "docker push $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/lumina-invest:latest",

            # Lambda updates
            "aws lambda update-function-code --function-name lumina-auth-service  --image-uri $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/auth-service:latest  --region $REGION",
            "aws lambda update-function-code --function-name lumina-crawl-service --image-uri $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/crawl-service:latest --region $REGION",

            # ECS rolling deploys
            "aws ecs update-service --cluster $CLUSTER_NAME --service lumina-invest-api-svc    --force-new-deployment --region $REGION",
            "aws ecs update-service --cluster $CLUSTER_NAME --service lumina-invest-worker-svc --force-new-deployment --region $REGION",
          ]
        }
      }
    })
  }

  logs_config {
    cloudwatch_logs {
      group_name  = aws_cloudwatch_log_group.codebuild.name
      stream_name = "build"
    }
  }
}

resource "aws_codepipeline" "main" {
  name     = "lumina-invest-pipeline"
  role_arn = aws_iam_role.codepipeline.arn

  artifact_store {
    location = aws_s3_bucket.pipeline_artifacts.bucket
    type     = "S3"
  }

  stage {
    name = "Source"

    action {
      name             = "Source"
      category         = "Source"
      owner            = "AWS"
      provider         = "S3"
      version          = "1"
      output_artifacts = ["source_output"]

      configuration = {
        S3Bucket             = aws_s3_bucket.source.bucket
        S3ObjectKey          = "source.zip"
        PollForSourceChanges = "true"
      }
    }
  }

  stage {
    name = "Build"

    action {
      name            = "Build"
      category        = "Build"
      owner           = "AWS"
      provider        = "CodeBuild"
      version         = "1"
      input_artifacts = ["source_output"]

      configuration = {
        ProjectName = aws_codebuild_project.main.name
      }
    }
  }
}
