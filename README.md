# AWS ETL Terraform Challenge

This project provisions an AWS-native ETL pipeline with Terraform and runs it from a single command-line entrypoint.

## Architecture

The pipeline performs:

1. **Extract**
   - `extract.py` uploads the provided Excel file to S3 under `raw/`

2. **Transform**
  - An S3 event triggers a Lambda function
  - Lambda reads the `.xlsx` file
  - Lambda uses pandas for dataframe-based transformation
  - Lambda trims whitespace from string fields
  - Lambda converts blank strings to null values
  - Lambda engineers `email_domain`, `domain_type`, and `affiliation_category`
  - Lambda preserves `gender` and adds `gender_mapped`
  - Lambda enriches `ip_address` values with geolocation metadata
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
  - `data-transformer`: transforms raw Excel data into cleaned, enriched output
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
    - `extract_workbook_dataframe_from_s3(...)`
    - `transform_workbook_dataframe_to_csv(...)`
    - `clean_null(...)`
    - `get_email_domain(...)`
    - `get_domain_type(...)`
    - `get_affiliation_category(...)`
    - `geolocate_ip_address(...)`
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

- Python 3
- Terraform CLI
- AWS CLI
- Internet access for:
  - Terraform provider downloads
  - Lambda dependency packaging during deployment

### macOS

If Homebrew is available:

```bash
brew install terraform
brew install awscli
```

If Homebrew is not available, install Terraform and AWS CLI from their official installers.

### Windows

If `winget` is available:

```powershell
winget install Hashicorp.Terraform
winget install Amazon.AWSCLI
```

If `winget` is not available, install Terraform and AWS CLI from their official Windows installers.

### Linux

Install Terraform from HashiCorp's official repository or package instructions.

Install AWS CLI from the AWS official Linux installer.

## AWS Authentication

Authenticate before running the project:

```bash
aws configure
```

You can also use environment variables:

```bash
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=us-west-1
```

Verify it:

```bash
aws sts get-caller-identity
```

If you use a named profile, pass it to the deployment script:

```bash
python scripts/run_terraform.py --profile my-profile
```

## IAM Permissions For Terraform

The AWS user or role running Terraform needs permission to manage:

- S3
- Lambda
- API Gateway HTTP APIs for the inference endpoint
- IAM roles and inline policies for Lambda
- RDS
- VPC lookups and the S3 VPC endpoint used by the RDS loader Lambda

For this project, a ready-to-attach sandbox policy is included at:

[`iam/terraform-user-policy.json`](/Users/yljlyuad/Desktop/aws_etl_terraform/iam/terraform-user-policy.json)

If you attach policies as inline user policies and hit AWS's inline policy size limit, attach the API Gateway permissions separately from:

[`iam/terraform-user-apigateway-policy.json`](/Users/yljlyuad/Desktop/aws_etl_terraform/iam/terraform-user-apigateway-policy.json)

If you prefer AWS managed policies in your sandbox account, the broadest simple option is to give your Terraform user:

- `AmazonS3FullAccess`
- `AWSLambda_FullAccess`
- `AmazonAPIGatewayAdministrator`
- `IAMFullAccess`
- `AmazonRDSFullAccess`
- `AmazonVPCFullAccess`

The key networking permission for the AWS-native RDS load flow is:

- `ec2:CreateVpcEndpoint`

That is needed because the loader Lambda runs inside your VPC to reach RDS and also needs a private path back to S3 to read the processed CSV.

## Dependencies

### Local Machine

Required:

- Terraform CLI
- AWS CLI
- Python 3

When you run `scripts/run_terraform.py`, the script checks `requirements.txt`.
If Python dependencies are missing, it asks:

```text
Python dependencies are missing. Install them now? [y/N]
```

If you answer `y`, dependencies are installed into the same Python environment
running the script with:

```bash
python -m pip install -r requirements.txt
```

No manual RDS credentials are required from the reviewer. Database authentication is handled inside AWS by the loader Lambda.

### Lambda packaging dependencies

These are installed during deployment into the Lambda package, not into your local system Python:

- `openpyxl` for the transform Lambda
- `maxminddb-geolite2` for offline IP geolocation in the transform Lambda
- `pg8000` for the RDS loader Lambda

The transform Lambda uses the AWS-managed pandas layer for Python 3.11 in
`us-west-1`, so `pandas` is available at runtime without inflating the ZIP
package beyond Lambda's direct upload size limit.

## How To Run

From the project root:

```bash
cd /Users/yljlyuad/Desktop/aws_etl_terraform
python scripts/run_terraform.py
```

Depending on your system, `python3` may be the right command:

```bash
python3 scripts/run_terraform.py deploy
```

That command will:

1. Check Python dependencies and offer to install missing packages
2. Check that Terraform and AWS CLI are installed
3. Validate AWS credentials with `aws sts get-caller-identity`
4. Package the transform, load, and inference Lambda functions
5. Run `terraform init`
6. Generate a fresh Terraform plan
7. Run `terraform apply`
8. Upload the provided `.xlsx` file to S3
9. Print the final inference API URL and a ready-to-use GET request example

The default command is `deploy`, so these are equivalent:

```bash
python scripts/run_terraform.py
python scripts/run_terraform.py deploy
python3 scripts/run_terraform.py
python3 scripts/run_terraform.py deploy
```

After the upload:

- S3 triggers the transform Lambda
- The transform Lambda writes the processed CSV to `processed/`
- S3 triggers the loader Lambda
- The loader Lambda inserts the transformed rows into RDS

## Using the Inference API

The inference API uses a GET request with query parameters. After deployment,
no Python code is required for a client to use the model; a browser, `curl`,
Postman, or any HTTP client can call the endpoint directly.

GET endpoint URL format:

```text
https://<api-id>.execute-api.us-west-1.amazonaws.com/dev/predict?domain_type={domain_type}&country={country}
```

Required parameters:

- `domain_type`
  - Description: engineered domain classification used by the model
  - Examples: `commercial`, `education`, `government`, `organization`, `personal_provider`, `international`, `unknown`
- `country`
  - Description: country associated with the record
  - Example: `United States`
  - Note: spaces in URLs should be encoded as `%20`, so `United States` becomes `United%20States`

`%20` is URL encoding for a space, so `United%20States` is interpreted by the API as `United States`.

Example request:

```bash
curl "https://<api-id>.execute-api.us-west-1.amazonaws.com/dev/predict?domain_type=commercial&country=United%20States"
```

Example response:

```json
{
  "affiliation_category": "business"
}
```

Additional GET examples:

```text
GET /predict?domain_type=education&country=United%20States
```

Response:

```json
{
  "affiliation_category": "public_sector"
}
```

```text
GET /predict?domain_type=international&country=United%20States
```

Response:

```json
{
  "affiliation_category": "non_institutional"
}
```

Possible outputs:

- `business`
- `public_sector`
- `non_institutional`

## Alternate Commands

Initialize only:

```bash
python scripts/run_terraform.py init
```

Plan only:

```bash
python scripts/run_terraform.py plan
```

Apply with a freshly generated plan:

```bash
python scripts/run_terraform.py apply
```

Skip Terraform approval prompt:

```bash
python scripts/run_terraform.py deploy --auto-approve
```

Use a named AWS profile:

```bash
python scripts/run_terraform.py deploy --profile my-profile
```

Run the upload step by itself after infrastructure already exists:

```bash
python extract.py
```

## Transform Rules

The transform Lambda applies these rules:

1. Trim whitespace from string values
2. Convert blank strings to null values
3. Preserve the original `gender` column
4. Create `gender_mapped` by converting `gender` values to integer codes
5. Create `email_domain` from the portion of `email` after `@`
6. Create `domain_type`:
   - `education` for domains ending in `.edu`
   - `government` for domains ending in `.gov`
   - `organization` for domains ending in `.org`
   - `personal_provider` for `gmail.com`, `yahoo.com`, `hotmail.com`, `outlook.com`, `icloud.com`, `msn.com`, `live.com`, and `aol.com`
   - `commercial` for domains ending in `.com`, `.net`, `.io`, or `.co`
   - `international` for domains ending in `.uk`, `.au`, `.jp`, `.cn`, `.ru`, `.br`, `.de`, `.fr`, `.it`, `.nl`, or `.ca`
   - `unknown` when a domain is missing or cannot be classified
7. Create `affiliation_category`:
   - `business` when `domain_type = commercial`
   - `public_sector` when `domain_type` is `education` or `government`
   - `non_institutional` for all other categories
8. Enrich public IP addresses with:
   - `country`
   - `region`
   - `city`
   - `latitude`
   - `longitude`
   - `timezone`

Invalid, missing, private, local, loopback, and otherwise unsupported IP
addresses do not fail the transform. They are logged and written with null
geolocation fields instead.

Missing or invalid emails also do not fail the transform. They are logged with
`email_domain = null`, `domain_type = unknown`, and
`affiliation_category = non_institutional`.

Nullable categorical fields such as `email_domain`, `country`,
`region`, `city`, and `timezone` are standardized to nulls so the dataset stays
RDS-safe and ready for later ML feature handling.

The geolocation enrichment is performed with the offline `maxminddb-geolite2`
dataset packaged into the transform Lambda, so the transform does not depend on
an external paid API.

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
  - lookup table that stores the mapping between `gender_mapped` and `gender_label`
- `person_records`
  - main table that stores each record and references `gender_mapping.gender_code`
  - also stores domain-based email features and geolocation fields derived from `ip_address`

Example lookup table contents:

- `0 -> Male`
- `1 -> Female`
- `2 -> Non-binary`
- `3 -> Bigender`
- `4 -> Genderfluid`
- `5 -> Agender`
- `6 -> Genderqueer`
- `7 -> Polygender`

Example transformed output columns:

- `id`
- `first_name`
- `last_name`
- `email`
- `gender`
- `gender_mapped`
- `email_domain`
- `domain_type`
- `affiliation_category`
- `ip_address`
- `country`
- `region`
- `city`
- `latitude`
- `longitude`
- `timezone`

## Local ML Training

The local training script at `ml/train_contact_type_model.py` trains the
Institution Type Classifier with a DecisionTreeClassifier using:

- features: `domain_type`, `country`
- target: `affiliation_category`

Before training, missing feature values are standardized to `unknown` locally
for the model only. The ETL transform itself continues to preserve nulls except
for `domain_type`, which is intentionally classified as `unknown` when a
domain cannot be categorized.

## Inference Lambda

The inference Lambda lives at:

- `lambdas/inference/handler.py`

It loads these packaged model artifacts:

- `model.pkl`
- `encoders.pkl`

It accepts JSON input with:

- `domain_type`
- `country`

It returns:

- `affiliation_category`

Package it with:

```bash
python scripts/package_inference_lambda.py
```

That creates:

```bash
build/inference_lambda.zip
```

The inference package stays very small because local training exports a
lightweight pure-Python decision tree artifact for Lambda inference. That keeps
deployment under AWS Lambda's direct ZIP upload limit without needing S3-based
code deployment or heavyweight ML runtime layers.

Terraform now provisions an inference Lambda only. It does not create:

- Lambda Function URL
- RDS access
- VPC configuration

Relevant Terraform outputs:

- `inference_lambda_function_name`
- `inference_lambda_arn`
- `inference_api_url`

### Test In AWS Console

After packaging and running `terraform apply`, open the inference Lambda in the
AWS Console and create a test event with:

```json
{
  "queryStringParameters": {
    "domain_type": "commercial",
    "country": "United%20States"
  }
}
```

Expected response body:

```json
{
  "affiliation_category": "business"
}
```

### Test The HTTP API

After `terraform apply`, retrieve the API URL:

```bash
cd terraform
terraform output -raw inference_api_url
```

Call the deployed `GET /predict` endpoint with:

```bash
curl "$(terraform output -raw inference_api_url)?domain_type=commercial&country=United%20States"
```

Expected response:

```json
{
  "affiliation_category": "business"
}
```

Additional examples:

```bash
curl "$(terraform output -raw inference_api_url)?domain_type=education&country=United%20States"
```

Expected response:

```json
{
  "affiliation_category": "public_sector"
}
```

```bash
curl "$(terraform output -raw inference_api_url)?domain_type=international&country=United%20States"
```

Expected response:

```json
{
  "affiliation_category": "non_institutional"
}
```

### API URL Structure

The final inference API URL looks like:

```text
https://<api-id>.execute-api.us-west-1.amazonaws.com/dev/predict
```

Parts of the URL:

- `https://` is the secure protocol used by API Gateway.
- `<api-id>` is the unique API Gateway identifier AWS creates for your HTTP API.
- `execute-api` is the AWS-managed API Gateway domain.
- `us-west-1` is the AWS region where the API is deployed.
- `/dev` is the API stage name.
- `/predict` is the route path mapped to the inference Lambda.

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
