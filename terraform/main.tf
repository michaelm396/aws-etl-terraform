locals {
  bucket_name          = "${var.bucket_prefix}-${random_id.bucket_suffix.hex}"
  transform_lambda_zip = "${path.module}/../build/gender_transform_lambda.zip"
  loader_lambda_zip    = "${path.module}/../build/rds_loader_lambda.zip"

  common_tags = merge(
    {
      Name        = local.bucket_name
      Environment = var.environment
      ManagedBy   = "Terraform"
      Project     = var.project_name
    },
    var.additional_tags
  )
}

resource "random_id" "bucket_suffix" {
  byte_length = 4
}

resource "aws_s3_bucket" "xlsx_bucket" {
  bucket = local.bucket_name

  tags = local.common_tags
}

resource "aws_s3_bucket_versioning" "xlsx_bucket" {
  bucket = aws_s3_bucket.xlsx_bucket.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "xlsx_bucket" {
  bucket = aws_s3_bucket.xlsx_bucket.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "xlsx_bucket" {
  bucket = aws_s3_bucket.xlsx_bucket.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "random_password" "db_password" {
  length  = 20
  special = true
}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

data "aws_route_tables" "default" {
  vpc_id = data.aws_vpc.default.id
}

data "aws_security_group" "default" {
  name   = "default"
  vpc_id = data.aws_vpc.default.id
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = data.aws_vpc.default.id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = data.aws_route_tables.default.ids

  tags = local.common_tags
}

resource "aws_db_subnet_group" "default" {
  name       = "${var.project_name}-${var.environment}-db-subnets"
  subnet_ids = data.aws_subnets.default.ids

  tags = local.common_tags
}

resource "aws_db_instance" "etl" {
  identifier              = "${var.project_name}-${var.environment}-etl-db"
  engine                  = "postgres"
  instance_class          = "db.t3.micro"
  allocated_storage       = 20
  storage_type            = "gp2"
  db_name                 = var.db_name
  username                = var.db_username
  password                = random_password.db_password.result
  db_subnet_group_name    = aws_db_subnet_group.default.name
  vpc_security_group_ids  = [data.aws_security_group.default.id]
  publicly_accessible     = false
  skip_final_snapshot     = true
  deletion_protection     = false
  backup_retention_period = 0

  tags = local.common_tags
}

data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "lambda_s3_access" {
  statement {
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["arn:aws:logs:*:*:*"]
  }

  statement {
    actions = [
      "s3:GetObject",
      "s3:PutObject",
    ]
    resources = ["${aws_s3_bucket.xlsx_bucket.arn}/*"]
  }
}

resource "aws_iam_role" "gender_transform_lambda" {
  name               = "${var.project_name}-${var.environment}-gender-transform-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = local.common_tags
}

resource "aws_iam_role_policy" "gender_transform_lambda" {
  name   = "${var.project_name}-${var.environment}-gender-transform-policy"
  role   = aws_iam_role.gender_transform_lambda.id
  policy = data.aws_iam_policy_document.lambda_s3_access.json
}

resource "aws_lambda_function" "gender_transform" {
  function_name = "${var.project_name}-${var.environment}-${var.lambda_function_name}"
  role          = aws_iam_role.gender_transform_lambda.arn
  handler       = "handler.lambda_handler"
  runtime       = "python3.11"
  timeout       = 30
  memory_size   = 256
  filename      = local.transform_lambda_zip

  source_code_hash = filebase64sha256(local.transform_lambda_zip)

  environment {
    variables = {
      PROCESSED_PREFIX = var.processed_prefix
      RAW_PREFIX       = var.raw_prefix
    }
  }

  tags = local.common_tags
}

data "aws_iam_policy_document" "rds_loader_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "rds_loader_access" {
  statement {
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["arn:aws:logs:*:*:*"]
  }

  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.xlsx_bucket.arn}/*"]
  }

  statement {
    actions = [
      "ec2:CreateNetworkInterface",
      "ec2:DescribeNetworkInterfaces",
      "ec2:DeleteNetworkInterface",
      "ec2:AssignPrivateIpAddresses",
      "ec2:UnassignPrivateIpAddresses",
      "ec2:DescribeSubnets",
      "ec2:DescribeSecurityGroups",
      "ec2:DescribeVpcs",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role" "rds_loader_lambda" {
  name               = "${var.project_name}-${var.environment}-rds-loader-role"
  assume_role_policy = data.aws_iam_policy_document.rds_loader_assume_role.json

  tags = local.common_tags
}

resource "aws_iam_role_policy" "rds_loader_lambda" {
  name   = "${var.project_name}-${var.environment}-rds-loader-policy"
  role   = aws_iam_role.rds_loader_lambda.id
  policy = data.aws_iam_policy_document.rds_loader_access.json
}

resource "aws_lambda_function" "rds_loader" {
  function_name = "${var.project_name}-${var.environment}-${var.loader_lambda_function_name}"
  role          = aws_iam_role.rds_loader_lambda.arn
  handler       = "handler.lambda_handler"
  runtime       = "python3.11"
  timeout       = 60
  memory_size   = 256
  filename      = local.loader_lambda_zip

  source_code_hash = filebase64sha256(local.loader_lambda_zip)

  vpc_config {
    subnet_ids         = data.aws_subnets.default.ids
    security_group_ids = [data.aws_security_group.default.id]
  }

  environment {
    variables = {
      DB_HOST     = aws_db_instance.etl.address
      DB_PORT     = aws_db_instance.etl.port
      DB_NAME     = aws_db_instance.etl.db_name
      DB_USER     = aws_db_instance.etl.username
      DB_PASSWORD = random_password.db_password.result
    }
  }

  tags = local.common_tags
}

resource "aws_lambda_permission" "allow_s3_invoke" {
  statement_id  = "AllowS3InvokeGenderTransform"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.gender_transform.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.xlsx_bucket.arn
}

resource "aws_lambda_permission" "allow_s3_invoke_loader" {
  statement_id  = "AllowS3InvokeRdsLoader"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.rds_loader.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.xlsx_bucket.arn
}

resource "aws_s3_bucket_notification" "xlsx_bucket" {
  bucket = aws_s3_bucket.xlsx_bucket.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.gender_transform.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "${var.raw_prefix}/"
    filter_suffix       = ".xlsx"
  }

  lambda_function {
    lambda_function_arn = aws_lambda_function.rds_loader.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "${var.processed_prefix}/"
    filter_suffix       = ".csv"
  }

  depends_on = [
    aws_lambda_permission.allow_s3_invoke,
    aws_lambda_permission.allow_s3_invoke_loader,
  ]
}
