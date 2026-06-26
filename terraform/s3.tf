# ─────────────────────────────────────────
# Frontend static bucket
# ─────────────────────────────────────────
resource "aws_s3_bucket" "front" {
  bucket = "lumina-invest-front-${var.account_id}-${var.region}"
  tags   = { Name = "lumina-invest-front" }
}

resource "aws_s3_bucket_public_access_block" "front" {
  bucket                  = aws_s3_bucket.front.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Bucket policy is applied after CloudFront distribution is created
resource "aws_s3_bucket_policy" "front" {
  bucket     = aws_s3_bucket.front.id
  depends_on = [aws_cloudfront_distribution.main]

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "AllowCloudFrontOAC"
      Effect = "Allow"
      Principal = { Service = "cloudfront.amazonaws.com" }
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.front.arn}/*"
      Condition = {
        StringEquals = {
          "AWS:SourceArn" = aws_cloudfront_distribution.main.arn
        }
      }
    }]
  })
}

# ─────────────────────────────────────────
# CodePipeline source bucket
# ─────────────────────────────────────────
resource "aws_s3_bucket" "source" {
  bucket = "lumina-source-${var.account_id}-${var.region}"
  tags   = { Name = "lumina-source" }
}

resource "aws_s3_bucket_versioning" "source" {
  bucket = aws_s3_bucket.source.id
  versioning_configuration { status = "Enabled" }
}

# ─────────────────────────────────────────
# CodePipeline artifacts bucket
# ─────────────────────────────────────────
resource "aws_s3_bucket" "pipeline_artifacts" {
  bucket = "lumina-pipeline-artifacts-${var.account_id}-${var.region}"
  tags   = { Name = "lumina-pipeline-artifacts" }
}
