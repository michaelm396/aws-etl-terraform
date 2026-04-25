locals {
  bucket_name          = "${var.bucket_prefix}-${random_id.bucket_suffix.hex}"
  transform_lambda_zip = "${path.module}/../build/gender_transform_lambda.zip"
  loader_lambda_zip    = "${path.module}/../build/rds_loader_lambda.zip"
  inference_lambda_zip = "${path.module}/../build/inference_lambda.zip"
  chatbot_app_dir      = "${path.module}/../llm_chatbot"

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
  bucket        = local.bucket_name
  force_destroy = true

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

data "aws_ami" "amazon_linux_2023" {
  count = var.enable_llm_chatbot ? 1 : 0

  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-x86_64"]
  }

  filter {
    name   = "architecture"
    values = ["x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
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
  architectures = ["x86_64"]
  timeout       = 30
  memory_size   = 256
  filename      = local.transform_lambda_zip
  layers        = [var.transform_pandas_layer_arn]

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

resource "aws_iam_role" "inference_lambda" {
  count = var.enable_classifier_api ? 1 : 0

  name               = "${var.project_name}-${var.environment}-inference-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = local.common_tags
}

resource "aws_iam_role_policy" "inference_lambda" {
  count = var.enable_classifier_api ? 1 : 0

  name = "${var.project_name}-${var.environment}-inference-policy"
  role = aws_iam_role.inference_lambda[0].id
  policy = jsonencode(
    {
      Version = "2012-10-17"
      Statement = [
        {
          Effect = "Allow"
          Action = [
            "logs:CreateLogGroup",
            "logs:CreateLogStream",
            "logs:PutLogEvents",
          ]
          Resource = "arn:aws:logs:*:*:*"
        },
      ]
    }
  )
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

resource "aws_lambda_function" "inference" {
  count = var.enable_classifier_api ? 1 : 0

  function_name = "${var.project_name}-${var.environment}-${var.inference_lambda_function_name}"
  role          = aws_iam_role.inference_lambda[0].arn
  handler       = "handler.lambda_handler"
  runtime       = "python3.11"
  timeout       = 30
  memory_size   = 512
  filename      = local.inference_lambda_zip

  source_code_hash = filebase64sha256(local.inference_lambda_zip)

  tags = local.common_tags
}

resource "aws_apigatewayv2_api" "inference" {
  count = var.enable_classifier_api ? 1 : 0

  name          = "${var.project_name}-${var.environment}-institution-classifier-api"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "inference_lambda" {
  count = var.enable_classifier_api ? 1 : 0

  api_id                 = aws_apigatewayv2_api.inference[0].id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.inference[0].invoke_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "inference_predict_get" {
  count = var.enable_classifier_api ? 1 : 0

  api_id    = aws_apigatewayv2_api.inference[0].id
  route_key = "GET /predict"
  target    = "integrations/${aws_apigatewayv2_integration.inference_lambda[0].id}"
}

resource "aws_apigatewayv2_stage" "inference_dev" {
  count = var.enable_classifier_api ? 1 : 0

  api_id      = aws_apigatewayv2_api.inference[0].id
  name        = var.environment
  auto_deploy = true
}

resource "aws_lambda_permission" "allow_apigateway_invoke_inference" {
  count = var.enable_classifier_api ? 1 : 0

  statement_id  = "AllowApiGatewayInvokeInference"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.inference[0].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.inference[0].execution_arn}/*/*"
}

resource "aws_security_group" "chatbot" {
  count = var.enable_llm_chatbot ? 1 : 0

  name        = "${var.project_name}-${var.environment}-llm-chatbot-sg"
  description = "Allow HTTP demo access and optional SSH for the LLM chatbot EC2 instance."
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "FastAPI chatbot demo port"
    from_port   = 8000
    to_port     = 8000
    protocol    = "tcp"
    cidr_blocks = [var.chatbot_http_cidr]
  }

  ingress {
    description = "Optional SSH access"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.chatbot_ssh_cidr]
  }

  egress {
    description = "Outbound access for package/model downloads and RDS"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, { Name = "${var.project_name}-${var.environment}-llm-chatbot-sg" })
}

resource "aws_security_group_rule" "allow_chatbot_to_rds" {
  count = var.enable_llm_chatbot ? 1 : 0

  type                     = "ingress"
  from_port                = aws_db_instance.etl.port
  to_port                  = aws_db_instance.etl.port
  protocol                 = "tcp"
  security_group_id        = data.aws_security_group.default.id
  source_security_group_id = aws_security_group.chatbot[0].id
  description              = "Allow chatbot EC2 to connect to PostgreSQL RDS"
}

resource "aws_instance" "chatbot" {
  count = var.enable_llm_chatbot ? 1 : 0

  ami                         = data.aws_ami.amazon_linux_2023[0].id
  instance_type               = var.chatbot_instance_type
  subnet_id                   = data.aws_subnets.default.ids[0]
  vpc_security_group_ids      = [aws_security_group.chatbot[0].id]
  associate_public_ip_address = true
  key_name                    = var.chatbot_key_name

  user_data_replace_on_change = true
  user_data                   = <<-EOF
    #!/bin/bash
    exec > >(tee /var/log/llm-chatbot-user-data.log | logger -t llm-chatbot-user-data -s 2>/dev/console) 2>&1
    set -euxo pipefail

    dnf install -y python3 python3-pip git

    if ! command -v curl >/dev/null 2>&1; then
      dnf install -y curl-minimal
    fi

    if [ ! -f /swapfile ]; then
      fallocate -l 2G /swapfile
      chmod 600 /swapfile
      mkswap /swapfile
      swapon /swapfile
      echo '/swapfile none swap sw 0 0' >> /etc/fstab
    else
      swapon /swapfile || true
    fi

    mkdir -p /opt/llm_chatbot
    cd /opt/llm_chatbot

    cat > /opt/llm_chatbot/app.py.gz.b64 <<'APP_B64'
    ${base64gzip(file("${local.chatbot_app_dir}/app.py"))}
    APP_B64
    base64 -d /opt/llm_chatbot/app.py.gz.b64 | gunzip > /opt/llm_chatbot/app.py

    cat > /opt/llm_chatbot/requirements.txt.b64 <<'REQ_B64'
    ${filebase64("${local.chatbot_app_dir}/requirements.txt")}
    REQ_B64
    base64 -d /opt/llm_chatbot/requirements.txt.b64 > /opt/llm_chatbot/requirements.txt

    python3 -m venv /opt/llm_chatbot/venv

    cat > /opt/llm_chatbot/start-chatbot.sh <<'START'
    #!/bin/bash
    set -euxo pipefail
    cd /opt/llm_chatbot
    /opt/llm_chatbot/venv/bin/python -m pip install --no-cache-dir --upgrade pip
    /opt/llm_chatbot/venv/bin/python -m pip install --no-cache-dir -r /opt/llm_chatbot/requirements.txt
    exec /opt/llm_chatbot/venv/bin/python -m uvicorn app:app --host 0.0.0.0 --port 8000
    START
    chmod +x /opt/llm_chatbot/start-chatbot.sh

    cat > /etc/systemd/system/llm-chatbot.service <<'SERVICE'
    [Unit]
    Description=FastAPI LLM Chatbot over RDS
    After=network-online.target
    Wants=network-online.target

    [Service]
    Type=simple
    WorkingDirectory=/opt/llm_chatbot
    Environment=DB_HOST=${aws_db_instance.etl.address}
    Environment=DB_PORT=${aws_db_instance.etl.port}
    Environment=DB_NAME=${aws_db_instance.etl.db_name}
    Environment=DB_USER=${aws_db_instance.etl.username}
    Environment=DB_PASSWORD=${random_password.db_password.result}
    Environment=OLLAMA_URL=http://localhost:11434/api/chat
    Environment=OLLAMA_MODEL=qwen2.5:0.5b
    ExecStart=/opt/llm_chatbot/start-chatbot.sh
    Restart=always
    RestartSec=10
    StandardOutput=journal+console
    StandardError=journal+console

    [Install]
    WantedBy=multi-user.target
    SERVICE

    systemctl daemon-reload
    systemctl enable llm-chatbot
    systemctl start llm-chatbot

    if ! command -v ollama >/dev/null 2>&1; then
      curl -fsSL https://ollama.com/install.sh | sh
    fi

    systemctl enable ollama
    systemctl start ollama

    for attempt in $(seq 1 60); do
      if curl -fsS http://localhost:11434/api/tags >/dev/null 2>&1; then
        break
      fi
      sleep 2
    done

    ollama pull qwen2.5:0.5b || true
  EOF

  root_block_device {
    volume_size = 20
    volume_type = "gp3"
  }

  tags = merge(local.common_tags, { Name = "${var.project_name}-${var.environment}-llm-chatbot" })

  depends_on = [
    aws_db_instance.etl,
    aws_security_group_rule.allow_chatbot_to_rds,
  ]
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
