# AWS ETL Terraform Challenge

This project provisions an AWS-native ETL pipeline with Terraform and runs it from a single command-line entrypoint.

## Architecture

The pipeline performs:

1. **Extract**
   - `extract.py` uploads the provided Excel file to S3 under `raw/`

2. **Transform**
   - An S3 event triggers a Lambda function
   - Lambda reads the `.xlsx` file
   - Lambda trims whitespace from string fields
   - Lambda converts blank strings to null values
   - Lambda converts the `gender` column to `gender_code`
   - Lambda writes a transformed CSV to `processed/`

3. **Load**
   - A second S3 event triggers a loader Lambda
   - The loader Lambda reads the processed CSV from S3
   - The loader Lambda connects to PostgreSQL RDS
   - The loader Lambda creates the required tables if needed
   - The loader Lambda maintains a `gender_mapping` lookup table
   - The loader Lambda inserts or updates the transformed rows in the database

## AWS Services Used

- **S3**
  - stores raw and processed files
- **Lambda**
  - `xlsx-gender-transform`: transforms raw Excel data
  - `csv-rds-loader`: loads processed CSV into PostgreSQL RDS
- **RDS PostgreSQL**
  - stores the final transformed dataset
- **Terraform**
  - provisions the infrastructure

## Project Structure

```text
aws_etl_terraform/
├── README.md
├── SRDataEngineerChallenge_DATASET.xlsx
├── extract.py                      # Extract stage entrypoint
├── iam/
│   └── terraform-user-policy.json
├── lambdas/
│   ├── transform/
│   │   ├── handler.py              # Transform stage Lambda
│   │   └── requirements.txt
│   └── load/
│       ├── handler.py              # Load stage Lambda
│       └── requirements.txt
├── requirements.txt
├── scripts/
│   ├── package_lambda.py
│   └── run_terraform.py
└── terraform/
    ├── main.tf
    ├── outputs.tf
    ├── provider.tf
    ├── terraform.tfvars
    └── variables.tf
```

## ETL Stage Ownership

The project is organized so each ETL stage is easy to identify:

- **Extract**
  - File: `extract.py`
  - Key functions:
    - `extract_local_xlsx_to_s3(...)`
    - `run_extract_stage(...)`

- **Transform**
  - File: `lambdas/transform/handler.py`
  - Key functions:
    - `extract_workbook_rows_from_s3(...)`
    - `transform_workbook_rows_to_csv(...)`
    - `load_transformed_csv_to_s3(...)`

- **Load**
  - File: `lambdas/load/handler.py`
  - Key functions:
    - `extract_processed_csv_from_s3(...)`
    - `load_processed_csv_into_rds(...)`
    - `ensure_schema(...)`

## What To Commit

Commit the source code and Terraform configuration, but do not commit local runtime artifacts.

Keep:

- `README.md`
- `extract.py`
- `scripts/`
- `lambdas/`
- `terraform/*.tf`
- `terraform/terraform.tfvars`
- `terraform/.terraform.lock.hcl`
- `iam/terraform-user-policy.json`
- `SRDataEngineerChallenge_DATASET.xlsx`

Do not commit:

- `.terraform/`
- `terraform.tfstate*`
- `tfplan`
- `build/`
- `.venv/`
- `__pycache__/`

## Prerequisites

Install these locally:

- Terraform CLI
- AWS CLI
- Python 3
- Internet access for:
  - Terraform provider downloads
  - Lambda dependency packaging during deployment

On macOS with Homebrew:

```bash
brew install hashicorp/tap/terraform
brew install awscli
```

## AWS Authentication

Authenticate before running the project:

```bash
aws configure
```

Or, if your environment uses SSO:

```bash
aws sso login
```

Verify it:

```bash
aws sts get-caller-identity
```

## IAM Permissions For Terraform

The AWS user or role running Terraform needs permission to manage:

- S3
- Lambda
- IAM roles and inline policies for Lambda
- RDS
- VPC lookups and the S3 VPC endpoint used by the RDS loader Lambda

For this project, a ready-to-attach sandbox policy is included at:

[`iam/terraform-user-policy.json`](/Users/yljlyuad/Desktop/aws_etl_terraform/iam/terraform-user-policy.json)

If you prefer AWS managed policies in your sandbox account, the broadest simple option is to give your Terraform user:

- `AmazonS3FullAccess`
- `AWSLambda_FullAccess`
- `IAMFullAccess`
- `AmazonRDSFullAccess`
- `AmazonVPCFullAccess`

The key networking permission for the AWS-native RDS load flow is:

- `ec2:CreateVpcEndpoint`

That is needed because the loader Lambda runs inside your VPC to reach RDS and also needs a private path back to S3 to read the processed CSV.

## Dependencies

### Local machine

Required:

- Terraform CLI
- AWS CLI
- Python 3

No additional local Python libraries are required for the upload/orchestration step.

No manual RDS credentials are required from the reviewer. Database authentication is handled inside AWS by the loader Lambda.

### Lambda packaging dependencies

These are installed during deployment into the Lambda package, not into your local system Python:

- `openpyxl` for the transform Lambda
- `pg8000` for the RDS loader Lambda

## How To Run

From the project root:

```bash
cd /Users/yljlyuad/Desktop/aws_etl_terraform
python3 scripts/run_terraform.py deploy
```

That command will:

1. Package both Lambda functions
2. Run `terraform init`
3. Run `terraform plan`
4. Run `terraform apply`
5. Upload the provided `.xlsx` file to S3

After the upload:

- S3 triggers the transform Lambda
- The transform Lambda writes the processed CSV to `processed/`
- S3 triggers the loader Lambda
- The loader Lambda inserts the transformed rows into RDS

## Alternate Commands

Initialize only:

```bash
python3 scripts/run_terraform.py init
```

Plan only:

```bash
python3 scripts/run_terraform.py plan
```

Apply an existing plan:

```bash
python3 scripts/run_terraform.py apply
```

Skip Terraform approval prompt:

```bash
python3 scripts/run_terraform.py deploy --auto-approve
```

Use a named AWS profile:

```bash
python3 scripts/run_terraform.py deploy --aws-profile default
```

Run the upload step by itself after infrastructure already exists:

```bash
python3 extract.py
```

## Transform Rules

The transform Lambda applies these rules:

1. Trim whitespace from string values
2. Convert blank strings to null values
3. Convert `gender` values to integer codes
4. Rename the transformed categorical field to `gender_code`

Current gender mapping:

- `Male = 0`
- `Female = 1`
- `Non-binary = 2`
- `Bigender = 3`
- `Genderfluid = 4`
- `Agender = 5`
- `Genderqueer = 6`
- `Polygender = 7`

## Expected S3 Layout

After a successful run:

- Raw file:
  - `s3://<bucket-name>/raw/SRDataEngineerChallenge_DATASET.xlsx`
- Processed file:
  - `s3://<bucket-name>/processed/SRDataEngineerChallenge_DATASET_transformed.csv`

## Expected Database Objects

The loader Lambda creates and populates:

- `gender_mapping`
  - lookup table that stores the mapping between `gender_code` and `gender_label`
- `person_records`
  - main table that stores each record and references `gender_mapping.gender_code`

Example lookup table contents:

- `0 -> Male`
- `1 -> Female`
- `2 -> Non-binary`
- `3 -> Bigender`
- `4 -> Genderfluid`
- `5 -> Agender`
- `6 -> Genderqueer`
- `7 -> Polygender`

## How To Verify Execution

### 1. Check Terraform outputs

```bash
cd terraform
terraform output
```

### 2. Check raw upload in S3

```bash
aws s3 ls s3://$(terraform output -raw bucket_name)/raw/
```

### 3. Check transformed CSV in S3

```bash
aws s3 ls s3://$(terraform output -raw bucket_name)/processed/
```

### 4. Check transform Lambda logs

```bash
aws logs tail /aws/lambda/$(terraform output -raw lambda_function_name) --since 10m --region us-west-1
```

### 5. Check loader Lambda logs

```bash
aws logs tail /aws/lambda/$(terraform output -raw loader_lambda_function_name) --since 10m --region us-west-1
```

You should see lines like:

- `Loading processed CSV from s3://... into RDS`
- `Connected to PostgreSQL successfully`
- `Ensured database schema and gender lookup table`
- `Inserted or updated ... rows`
- `Current RDS row counts: ...`

You should also now see:

- a short raw workbook sample in the transform Lambda logs
- a short processed CSV sample in both Lambda logs
- the full `gender_mapping` lookup table in the loader Lambda logs

## Cleanup

To destroy the infrastructure:

```bash
cd terraform
terraform destroy -var-file=terraform.tfvars
```

## GitHub Handoff Checklist

Before pushing:

1. Make sure local state and build artifacts are ignored by Git
2. Keep `.terraform.lock.hcl` committed for reproducible provider versions
3. Review `terraform/terraform.tfvars` and remove or generalize any personal naming if desired
4. Confirm the README matches the final working flow
5. Push the repo without AWS credentials, local state files, or generated ZIPs
