aws_region    = "us-west-2"
project_name  = "aws-etl"
environment   = "dev"
bucket_prefix = "aws-etl-xlsx-storage"
db_name       = "etl_app"
db_username   = "etl_admin"

enable_classifier_api = true
enable_llm_chatbot    = true
chatbot_instance_type = "t2.micro"

additional_tags = {
  Owner       = "data-platform"
  Application = "xlsx-ingestion"
}
