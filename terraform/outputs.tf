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
  value       = var.enable_classifier_api ? aws_lambda_function.inference[0].function_name : "disabled"
}

output "inference_lambda_arn" {
  description = "ARN of the Institution Type Classifier inference Lambda."
  value       = var.enable_classifier_api ? aws_lambda_function.inference[0].arn : "disabled"
}

output "inference_api_url" {
  description = "GET /predict endpoint for the Institution Type Classifier HTTP API."
  value       = var.enable_classifier_api ? "${aws_apigatewayv2_stage.inference_dev[0].invoke_url}/predict" : "disabled"
}

output "db_endpoint" {
  description = "Endpoint for the PostgreSQL RDS instance."
  value       = aws_db_instance.etl.address
}

output "db_name" {
  description = "Database name for the PostgreSQL RDS instance."
  value       = aws_db_instance.etl.db_name
}

output "chatbot_public_dns" {
  description = "Public DNS name of the EC2-hosted Ollama chatbot API."
  value       = var.enable_llm_chatbot ? aws_instance.chatbot[0].public_dns : "disabled"
}

output "chatbot_health_url" {
  description = "Health check URL for the EC2-hosted Ollama chatbot API."
  value       = var.enable_llm_chatbot ? "http://${aws_instance.chatbot[0].public_dns}:8000/health" : "disabled"
}

output "chatbot_api_url" {
  description = "POST /chat endpoint for the EC2-hosted Ollama chatbot API."
  value       = var.enable_llm_chatbot ? "http://${aws_instance.chatbot[0].public_dns}:8000/chat" : "disabled"
}
