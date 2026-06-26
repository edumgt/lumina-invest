locals {
  # Strip "https://" and trailing "/" from API GW invoke URLs to get bare domain
  auth_apigw_domain  = trimsuffix(trimprefix(aws_apigatewayv2_stage.auth.invoke_url, "https://"), "/")
  crawl_apigw_domain = trimsuffix(trimprefix(aws_apigatewayv2_stage.crawl.invoke_url, "https://"), "/")
}

resource "aws_cloudfront_origin_access_control" "main" {
  name                              = "lumina-invest-oac"
  description                       = "OAC for lumina-invest S3 frontend bucket"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "main" {
  enabled             = true
  comment             = "Lumina Invest CDN"
  default_root_object = "login.html"
  price_class         = "PriceClass_200"

  # ── Origins ───────────────────────────
  origin {
    origin_id   = "s3-front"
    domain_name = aws_s3_bucket.front.bucket_regional_domain_name

    origin_access_control_id = aws_cloudfront_origin_access_control.main.id
  }

  origin {
    origin_id   = "alb-api"
    domain_name = aws_lb.main.dns_name

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  origin {
    origin_id   = "apigw-auth"
    domain_name = local.auth_apigw_domain

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  origin {
    origin_id   = "apigw-crawl"
    domain_name = local.crawl_apigw_domain

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  # ── Default behavior: S3 frontend ─────
  default_cache_behavior {
    target_origin_id       = "s3-front"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    forwarded_values {
      query_string = false
      cookies { forward = "none" }
    }

    min_ttl     = 0
    default_ttl = 86400
    max_ttl     = 31536000
  }

  # ── /api/auth/* → Auth API Gateway ────
  ordered_cache_behavior {
    path_pattern           = "/api/auth/*"
    target_origin_id       = "apigw-auth"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    forwarded_values {
      query_string = true
      headers      = ["Authorization", "Content-Type", "Cookie", "Origin"]
      cookies { forward = "all" }
    }

    min_ttl     = 0
    default_ttl = 0
    max_ttl     = 0
  }

  # ── /api/ingest/* → Crawl API Gateway ─
  ordered_cache_behavior {
    path_pattern           = "/api/ingest/*"
    target_origin_id       = "apigw-crawl"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    forwarded_values {
      query_string = true
      headers      = ["Authorization", "Content-Type", "Origin"]
      cookies { forward = "all" }
    }

    min_ttl     = 0
    default_ttl = 0
    max_ttl     = 0
  }

  # ── /api/* → ALB (ECS core API) ───────
  ordered_cache_behavior {
    path_pattern           = "/api/*"
    target_origin_id       = "alb-api"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    forwarded_values {
      query_string = true
      headers      = ["Authorization", "Content-Type", "Cookie", "X-Forwarded-For", "Origin"]
      cookies { forward = "all" }
    }

    min_ttl     = 0
    default_ttl = 0
    max_ttl     = 0
  }

  restrictions {
    geo_restriction { restriction_type = "none" }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }

  tags = { Name = "lumina-invest-cf" }
}
