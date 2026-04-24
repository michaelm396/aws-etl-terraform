variable "aws_region" {
  description = "AWS region for the S3 bucket."
  type        = string
  default     = "us-west-1"
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
  description = "Name of the Lambda function that transforms uploaded XLSX files."
  type        = string
  default     = "xlsx-gender-transform"
}

variable "loader_lambda_function_name" {
  description = "Name of the Lambda function that loads processed CSV files into RDS."
  type        = string
  default     = "csv-rds-loader"
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
