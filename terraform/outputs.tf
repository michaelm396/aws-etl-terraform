output "bucket_name" {
  description = "Provisioned S3 bucket name."
  value       = aws_s3_bucket.xlsx_bucket.bucket
}

output "bucket_arn" {
  description = "ARN of the provisioned S3 bucket."
  value       = aws_s3_bucket.xlsx_bucket.arn
}

output "lambda_function_name" {
  description = "Lambda function used for the ETL data transformation stage."
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

output "inference_lambda_function_name" {
  description = "Lambda function used for Institution Type Classifier inference."
  value       = aws_lambda_function.inference.function_name
}

output "inference_lambda_arn" {
  description = "ARN of the Institution Type Classifier inference Lambda."
  value       = aws_lambda_function.inference.arn
}

output "inference_api_url" {
  description = "POST /predict endpoint for the Institution Type Classifier HTTP API."
  value       = "${aws_apigatewayv2_stage.inference_dev.invoke_url}/predict"
}

output "db_endpoint" {
  description = "Endpoint for the PostgreSQL RDS instance."
  value       = aws_db_instance.etl.address
}

output "db_name" {
  description = "Database name for the PostgreSQL RDS instance."
  value       = aws_db_instance.etl.db_name
}
