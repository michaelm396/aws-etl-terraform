variable "aws_region" {
  description = "AWS region for the ETL pipeline, classifier API, and EC2/Ollama chatbot."
  type        = string
  default     = "us-west-2"
}

variable "project_name" {
  description = "Project name used in tags and naming."
  type        = string
}

variable "environment" {
  description = "Deployment environment."
  type        = string
}

variable "bucket_prefix" {
  description = "Prefix for the globally unique S3 bucket name."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9-]+$", var.bucket_prefix))
    error_message = "bucket_prefix must contain only lowercase letters, numbers, and hyphens."
  }
}

variable "additional_tags" {
  description = "Additional tags to apply to all resources."
  type        = map(string)
  default     = {}
}

variable "lambda_function_name" {
  description = "Name of the Lambda function that performs the ETL data transformation stage."
  type        = string
  default     = "data-transformer"
}

variable "loader_lambda_function_name" {
  description = "Name of the Lambda function that loads processed CSV files into RDS."
  type        = string
  default     = "csv-rds-loader"
}

variable "inference_lambda_function_name" {
  description = "Name of the Lambda function that serves model inference."
  type        = string
  default     = "institution-type-inference"
}

variable "enable_classifier_api" {
  description = "Whether to create the serverless Lambda/API Gateway classifier endpoint."
  type        = bool
  default     = true
}

variable "processed_prefix" {
  description = "S3 prefix used for transformed output files."
  type        = string
  default     = "processed"
}

variable "raw_prefix" {
  description = "S3 prefix used for raw uploaded XLSX files."
  type        = string
  default     = "raw"
}

variable "db_name" {
  description = "Database name for the ETL target RDS instance."
  type        = string
  default     = "etl_app"
}

variable "db_username" {
  description = "Database username for the ETL target RDS instance."
  type        = string
  default     = "etl_admin"
}

variable "transform_pandas_layer_arn" {
  description = "AWS-managed Lambda layer ARN that provides pandas for the transform Lambda."
  type        = string
  default     = "arn:aws:lambda:us-west-2:336392948345:layer:AWSSDKPandas-Python311:28"
}

variable "enable_llm_chatbot" {
  description = "Whether to create the EC2-hosted Ollama/FastAPI chatbot. Set false only as an emergency fallback when AWS account EC2 verification is blocked."
  type        = bool
  default     = true
}

variable "chatbot_instance_type" {
  description = "EC2 instance type for the Ollama/FastAPI chatbot API. Defaults to a 1-vCPU type for new-account EC2 quotas."
  type        = string
  default     = "t2.micro"
}

variable "chatbot_http_cidr" {
  description = "CIDR allowed to reach the chatbot FastAPI demo port."
  type        = string
  default     = "0.0.0.0/0"
}

variable "chatbot_ssh_cidr" {
  description = "CIDR allowed to SSH into the chatbot instance. Default blocks SSH."
  type        = string
  default     = "0.0.0.0/32"
}

variable "chatbot_key_name" {
  description = "Optional EC2 key pair name for SSH access to the chatbot instance."
  type        = string
  default     = null
}
