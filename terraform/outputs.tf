output "bucket_name" {
  description = "Provisioned S3 bucket name."
  value       = aws_s3_bucket.xlsx_bucket.bucket
}

output "bucket_arn" {
  description = "ARN of the provisioned S3 bucket."
  value       = aws_s3_bucket.xlsx_bucket.arn
}

output "lambda_function_name" {
  description = "Lambda function used for XLSX gender transformation."
  value       = aws_lambda_function.gender_transform.function_name
}

output "processed_prefix" {
  description = "S3 prefix where transformed files are written."
  value       = var.processed_prefix
}

output "raw_prefix" {
  description = "S3 prefix where raw files are uploaded."
  value       = var.raw_prefix
}

output "loader_lambda_function_name" {
  description = "Lambda function used to load transformed CSV data into RDS."
  value       = aws_lambda_function.rds_loader.function_name
}

output "db_endpoint" {
  description = "Endpoint for the PostgreSQL RDS instance."
  value       = aws_db_instance.etl.address
}

output "db_name" {
  description = "Database name for the PostgreSQL RDS instance."
  value       = aws_db_instance.etl.db_name
}
