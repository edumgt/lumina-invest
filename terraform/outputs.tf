output "cloudfront_domain" {
  description = "CloudFront distribution domain (homepage)"
  value       = "https://${aws_cloudfront_distribution.main.domain_name}"
}

output "cloudfront_distribution_id" {
  value = aws_cloudfront_distribution.main.id
}

output "alb_dns" {
  description = "ALB DNS (internal access)"
  value       = aws_lb.main.dns_name
}

output "docdb_endpoint" {
  description = "DocumentDB cluster endpoint"
  value       = aws_docdb_cluster.main.endpoint
}

output "ecr_lumina_invest" {
  value = aws_ecr_repository.lumina_invest.repository_url
}

output "ecr_auth_service" {
  value = aws_ecr_repository.auth_service.repository_url
}

output "ecr_crawl_service" {
  value = aws_ecr_repository.crawl_service.repository_url
}

output "auth_api_endpoint" {
  description = "Auth API Gateway invoke URL"
  value       = aws_apigatewayv2_stage.auth.invoke_url
}

output "crawl_api_endpoint" {
  description = "Crawl API Gateway invoke URL"
  value       = aws_apigatewayv2_stage.crawl.invoke_url
}

output "bastion_public_ip" {
  description = "EC2 bastion public IP"
  value       = aws_eip.bastion.public_ip
}

output "secret_arn" {
  description = "Secrets Manager ARN for lumina-invest/prod/app"
  value       = aws_secretsmanager_secret.app.arn
  sensitive   = true
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "codepipeline_name" {
  value = aws_codepipeline.main.name
}
