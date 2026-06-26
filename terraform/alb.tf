# ─────────────────────────────────────────
# Application Load Balancer
# ─────────────────────────────────────────
resource "aws_lb" "main" {
  name               = "lumina-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets = [
    aws_subnet.private_a.id,
    aws_subnet.private_b.id,
    aws_subnet.private_d.id,
  ]

  tags = { Name = "lumina-alb" }
}

# ─────────────────────────────────────────
# Target Groups
# ─────────────────────────────────────────
resource "aws_lb_target_group" "api" {
  name        = "lumina-api-tg"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {
    path                = "/api/health"
    interval            = 30
    timeout             = 10
    healthy_threshold   = 2
    unhealthy_threshold = 3
    matcher             = "200"
  }

  tags = { Name = "lumina-api-tg" }
}

resource "aws_lb_target_group" "auth_lambda" {
  name        = "lumina-auth-lambda-tg"
  target_type = "lambda"

  tags = { Name = "lumina-auth-lambda-tg" }
}

resource "aws_lambda_permission" "alb_auth" {
  statement_id  = "AllowALBInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.auth.function_name
  principal     = "elasticloadbalancing.amazonaws.com"
  source_arn    = aws_lb_target_group.auth_lambda.arn
}

resource "aws_lb_target_group_attachment" "auth_lambda" {
  target_group_arn = aws_lb_target_group.auth_lambda.arn
  target_id        = aws_lambda_function.auth.arn
  depends_on       = [aws_lambda_permission.alb_auth]
}

# ─────────────────────────────────────────
# Listener & Rules
# ─────────────────────────────────────────
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

resource "aws_lb_listener_rule" "auth" {
  listener_arn = aws_lb_listener.http.arn
  priority     = 10

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.auth_lambda.arn
  }

  condition {
    path_pattern {
      values = ["/api/auth/*", "/api/me", "/api/sessions", "/api/sessions/*"]
    }
  }
}
