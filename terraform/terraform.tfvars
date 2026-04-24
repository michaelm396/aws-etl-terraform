aws_region    = "us-west-1"
project_name  = "aws-etl"
environment   = "dev"
bucket_prefix = "aws-etl-xlsx-storage"
db_name       = "etl_app"
db_username   = "etl_admin"

additional_tags = {
  Owner       = "data-platform"
  Application = "xlsx-ingestion"
}
