# ─────────────────────────────────────────
# Auth HTTP API Gateway
# ─────────────────────────────────────────
resource "aws_apigatewayv2_api" "auth" {
  name          = "lumina-auth-api"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "auth" {
  api_id                 = aws_apigatewayv2_api.auth.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.auth.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "auth" {
  api_id    = aws_apigatewayv2_api.auth.id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.auth.id}"
}

resource "aws_apigatewayv2_stage" "auth" {
  api_id      = aws_apigatewayv2_api.auth.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_lambda_permission" "apigw_auth" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.auth.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.auth.execution_arn}/*/*"
}

# ─────────────────────────────────────────
# Crawl HTTP API Gateway
# ─────────────────────────────────────────
resource "aws_apigatewayv2_api" "crawl" {
  name          = "lumina-crawl-api"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "crawl" {
  api_id                 = aws_apigatewayv2_api.crawl.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.crawl.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "crawl" {
  api_id    = aws_apigatewayv2_api.crawl.id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.crawl.id}"
}

resource "aws_apigatewayv2_stage" "crawl" {
  api_id      = aws_apigatewayv2_api.crawl.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_lambda_permission" "apigw_crawl" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.crawl.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.crawl.execution_arn}/*/*"
}
