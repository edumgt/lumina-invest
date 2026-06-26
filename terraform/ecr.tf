resource "aws_ecr_repository" "lumina_invest" {
  name                 = "lumina-invest"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration { scan_on_push = true }
}

resource "aws_ecr_repository" "auth_service" {
  name                 = "auth-service"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration { scan_on_push = true }
}

resource "aws_ecr_repository" "crawl_service" {
  name                 = "crawl-service"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration { scan_on_push = true }
}
