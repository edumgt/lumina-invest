# ─────────────────────────────────────────
# VPC
# ─────────────────────────────────────────
resource "aws_vpc" "main" {
  cidr_block           = "172.30.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "final-vpc" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "lumina-igw" }
}

# ─────────────────────────────────────────
# Subnets
# ─────────────────────────────────────────
resource "aws_subnet" "private_a" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "172.30.128.0/17"
  availability_zone = "${var.region}a"

  # ECS uses assignPublicIp=ENABLED to reach AWS services without NAT
  map_public_ip_on_launch = true

  tags = { Name = "subnet-test-a" }
}

resource "aws_subnet" "private_b" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "172.30.32.0/19"
  availability_zone       = "${var.region}d"  # 이 계정 도쿄 AZ: a/c/d (b 없음)
  map_public_ip_on_launch = true

  tags = { Name = "subnet-test-b" }
}

resource "aws_subnet" "public_c" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "172.30.2.0/24"
  availability_zone       = "${var.region}c"
  map_public_ip_on_launch = true

  tags = { Name = "subnet-test-c" }
}

resource "aws_subnet" "private_d" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "172.30.64.0/18"
  availability_zone       = "${var.region}c"  # 도쿄(ap-northeast-1)는 a/b/c 3개 AZ
  map_public_ip_on_launch = true

  tags = { Name = "subnet-test-d" }
}

# ─────────────────────────────────────────
# Route Tables  (all subnets → IGW so ECS assignPublicIp works)
# ─────────────────────────────────────────
resource "aws_route_table" "main" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "lumina-main-rt" }
}

resource "aws_route_table_association" "private_a" {
  subnet_id      = aws_subnet.private_a.id
  route_table_id = aws_route_table.main.id
}

resource "aws_route_table_association" "private_b" {
  subnet_id      = aws_subnet.private_b.id
  route_table_id = aws_route_table.main.id
}

resource "aws_route_table_association" "public_c" {
  subnet_id      = aws_subnet.public_c.id
  route_table_id = aws_route_table.main.id
}

resource "aws_route_table_association" "private_d" {
  subnet_id      = aws_subnet.private_d.id
  route_table_id = aws_route_table.main.id
}

# ─────────────────────────────────────────
# Security Groups
# ─────────────────────────────────────────
resource "aws_security_group" "alb" {
  name        = "lumina-alb-sg"
  description = "ALB: allow HTTP from anywhere"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "lumina-alb-sg" }
}

resource "aws_security_group" "ecs" {
  name        = "lumina-ecs-sg"
  description = "ECS tasks: allow 8000 from ALB"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "lumina-ecs-sg" }
}

resource "aws_security_group" "docdb" {
  name        = "lumina-docdb-sg"
  description = "DocumentDB: allow 27017 from ECS"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 27017
    to_port         = 27017
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "lumina-docdb-sg" }
}

resource "aws_security_group" "ec2_services" {
  name        = "lumina-ec2-services-sg"
  description = "EC2 native services: Redis/Ollama/Qdrant/Neo4j from ECS"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "Redis"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs.id]
  }

  ingress {
    description     = "Ollama"
    from_port       = 11434
    to_port         = 11434
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs.id]
  }

  ingress {
    description     = "Qdrant"
    from_port       = 6333
    to_port         = 6333
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs.id]
  }

  ingress {
    description     = "Neo4j Bolt"
    from_port       = 7687
    to_port         = 7687
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs.id]
  }

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "lumina-ec2-services-sg" }
}
