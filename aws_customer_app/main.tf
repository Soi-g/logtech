# ============================================================
# Customer App Stack
#
# 별도 VPC에 AWS 고객 앱을 시뮬레이션하는 스택.
# OTel Collector Agent가 플랫폼 NLB(var.otel_gateway_endpoint, :4317)로 데이터를 전송.
#
# EC2 #1 (frontend): Flask(5001) + Thymeleaf(8090) + loadgenerator + OTel Collector
# EC2 #2 (backend):  Spring Boot(8080) + OTel Collector
# RDS:               Postgres 16 (db.t3.micro)
#
# 배포:
#   cd aws_customer_app
#   terraform init
#   terraform apply
#
# 삭제:
#   terraform destroy
# ============================================================

terraform {
  backend "s3" {}
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# Session Manager 접속용 (키 페어 없음)
resource "aws_iam_role" "customer_ec2" {
  name = "${var.project_name}-customer-ec2-ssm"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })

  tags = { Name = "${var.project_name}-customer-ec2-ssm-role" }
}

resource "aws_iam_role_policy_attachment" "customer_ec2_ssm" {
  role       = aws_iam_role.customer_ec2.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "customer_ec2" {
  name = "${var.project_name}-customer-ec2-ssm"
  role = aws_iam_role.customer_ec2.name
}

# ─── VPC ────────────────────────────────────────────────────────────────────

resource "aws_vpc" "customer" {
  cidr_block           = "10.1.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags = { Name = "${var.project_name}-customer-vpc" }
}

resource "aws_subnet" "app" {
  vpc_id                  = aws_vpc.customer.id
  cidr_block              = "10.1.1.0/24"
  availability_zone       = "${var.aws_region}a"
  map_public_ip_on_launch = true
  tags = { Name = "${var.project_name}-customer-app-subnet" }
}

# RDS DB Subnet Group은 2개 AZ 필요
resource "aws_subnet" "db" {
  vpc_id            = aws_vpc.customer.id
  cidr_block        = "10.1.2.0/24"
  availability_zone = "${var.aws_region}c"
  tags = { Name = "${var.project_name}-customer-db-subnet" }
}

resource "aws_internet_gateway" "customer" {
  vpc_id = aws_vpc.customer.id
  tags   = { Name = "${var.project_name}-customer-igw" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.customer.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.customer.id
  }
  tags = { Name = "${var.project_name}-customer-public-rt" }
}

resource "aws_route_table_association" "app" {
  subnet_id      = aws_subnet.app.id
  route_table_id = aws_route_table.public.id
}

# ─── Security Groups ─────────────────────────────────────────────────────────

resource "aws_security_group" "frontend" {
  name        = "${var.project_name}-customer-frontend-sg"
  description = "Customer frontend EC2 - Flask and Thymeleaf"
  vpc_id      = aws_vpc.customer.id

  ingress {
    description = "Thymeleaf UI"
    from_port   = 8090
    to_port     = 8090
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    description = "Flask UI"
    from_port   = 5001
    to_port     = 5001
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "All outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project_name}-customer-frontend-sg" }
}

resource "aws_security_group" "backend" {
  name        = "${var.project_name}-customer-backend-sg"
  description = "Customer backend EC2 - Spring Boot"
  vpc_id      = aws_vpc.customer.id

  ingress {
    description     = "Spring Boot from frontend SG only"
    from_port       = 8080
    to_port         = 8080
    protocol        = "tcp"
    security_groups = [aws_security_group.frontend.id]
  }

  egress {
    description = "All outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project_name}-customer-backend-sg" }
}

resource "aws_security_group" "rds" {
  name        = "${var.project_name}-customer-rds-sg"
  description = "Customer RDS Postgres - backend EC2 only"
  vpc_id      = aws_vpc.customer.id

  ingress {
    description     = "Postgres from backend SG only"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.backend.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project_name}-customer-rds-sg" }
}

# ─── RDS ─────────────────────────────────────────────────────────────────────

resource "aws_db_subnet_group" "customer" {
  name       = "${var.project_name}-customer-db-subnet-group"
  subnet_ids = [aws_subnet.app.id, aws_subnet.db.id]
  tags = { Name = "${var.project_name}-customer-db-subnet-group" }
}

resource "aws_db_instance" "postgres" {
  identifier        = "${var.project_name}-customer-postgres"
  engine            = "postgres"
  engine_version    = "16"
  instance_class    = "db.t3.micro"
  allocated_storage = 20
  storage_type      = "gp2"

  db_name  = "mydb"
  username = "matthias"
  password = "password"

  db_subnet_group_name   = aws_db_subnet_group.customer.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  publicly_accessible    = false
  multi_az               = false
  skip_final_snapshot    = true
  deletion_protection    = false

  tags = { Name = "${var.project_name}-customer-postgres" }
}

# ─── EC2 #2 — Backend (Spring Boot) ──────────────────────────────────────────
# Frontend user_data에서 backend private IP를 사용하므로 먼저 생성

resource "aws_instance" "backend" {
  ami                    = var.ec2_ami_id
  instance_type          = "t3.micro"
  subnet_id              = aws_subnet.app.id
  vpc_security_group_ids = [aws_security_group.backend.id]
  iam_instance_profile   = aws_iam_instance_profile.customer_ec2.name

  root_block_device {
    volume_size = 10
    volume_type = "gp3"
  }

  user_data = templatefile("${path.module}/templates/user_data_backend.sh.tpl", {
    gateway_endpoint = var.otel_gateway_endpoint
    rds_endpoint     = aws_db_instance.postgres.address
    environment      = var.environment
    instance_name    = "${var.project_name}-customer-backend"
  })

  tags = { Name = "${var.project_name}-customer-backend" }

  depends_on = [aws_db_instance.postgres]
}

# ─── EC2 #1 — Frontend (Flask + Thymeleaf + loadgenerator) ───────────────────

resource "aws_instance" "frontend" {
  ami                    = var.ec2_ami_id
  instance_type          = "t3.micro"
  subnet_id              = aws_subnet.app.id
  vpc_security_group_ids = [aws_security_group.frontend.id]
  iam_instance_profile   = aws_iam_instance_profile.customer_ec2.name

  root_block_device {
    volume_size = 10
    volume_type = "gp3"
  }

  user_data = templatefile("${path.module}/templates/user_data_frontend.sh.tpl", {
    gateway_endpoint   = var.otel_gateway_endpoint
    backend_private_ip = aws_instance.backend.private_ip
    environment        = var.environment
    instance_name      = "${var.project_name}-customer-frontend"
  })

  tags = { Name = "${var.project_name}-customer-frontend" }

  depends_on = [aws_instance.backend]
}
