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
brew tap hashicorp/tap
brew install hashicorp/tap/terraform
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

The deployment runner checks for Terraform and AWS CLI before provisioning.
On macOS with Homebrew or Windows with `winget`, it asks before installing
missing system tools. It does not silently change your machine.

## AWS Authentication

Authenticate before running the project:

```bash
aws configure
```

You can also use environment variables:

```bash
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=us-west-2
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

For the EC2/Ollama chatbot, attach the EC2/security-group permissions separately from:

[`iam/terraform-user-ec2-chatbot-policy.json`](/Users/yljlyuad/Desktop/aws_etl_terraform/iam/terraform-user-ec2-chatbot-policy.json)

If you prefer AWS managed policies in your sandbox account, the broadest simple option is to give your Terraform user:

- `AmazonS3FullAccess`
- `AWSLambda_FullAccess`
- `AmazonAPIGatewayAdministrator`
- `AmazonEC2FullAccess`
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

The script also checks for Terraform and AWS CLI. If either is missing and a
supported installer is available, it asks:

```text
Install missing system dependencies now? [y/N]
```

On macOS this uses Homebrew. On Windows this uses `winget`.

No manual RDS credentials are required from the reviewer. Database authentication is handled inside AWS by the loader Lambda.

### Lambda packaging dependencies

These are installed during deployment into the Lambda package, not into your local system Python:

- `openpyxl` for the transform Lambda
- `maxminddb-geolite2` for offline IP geolocation in the transform Lambda
- `pg8000` for the RDS loader Lambda

The transform Lambda uses the AWS-managed pandas layer for Python 3.11 in
`us-west-2`, so `pandas` is available at runtime without inflating the ZIP
package beyond Lambda's direct upload size limit.

### Default AWS Region

This project defaults to `us-west-2`. New AWS accounts commonly have EC2
available in `us-west-2`, which makes the full deployment work out of the box
for both the Lambda classifier API and the EC2/Ollama chatbot API.

Changing AWS regions creates a separate Terraform stack with separate AWS
resources. If you previously deployed this project in `us-west-1`, destroy the
old `us-west-1` resources when you no longer need them.

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
9. Print the final classifier API URL, chatbot API URL, and ready-to-use curl examples

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
https://<api-id>.execute-api.us-west-2.amazonaws.com/dev/predict?domain_type={domain_type}&country={country}
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
curl "https://<api-id>.execute-api.us-west-2.amazonaws.com/dev/predict?domain_type=commercial&country=United%20States"
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
https://<api-id>.execute-api.us-west-2.amazonaws.com/dev/predict
```

Parts of the URL:

- `https://` is the secure protocol used by API Gateway.
- `<api-id>` is the unique API Gateway identifier AWS creates for your HTTP API.
- `execute-api` is the AWS-managed API Gateway domain.
- `us-west-2` is the AWS region where the API is deployed.
- `/dev` is the API stage name.
- `/predict` is the route path mapped to the inference Lambda.

## Model Serving Paths

This project deploys two model-serving paths side-by-side by default:

- **Serverless ML classifier on Lambda**
  - Endpoint: API Gateway `GET /predict`
  - Purpose: lightweight structured prediction from `domain_type` and `country`
  - Terraform flag: `enable_classifier_api = true`
- **EC2/Ollama chatbot**
  - Endpoint: FastAPI `POST /chat`
  - Purpose: natural-language Q&A over the transformed RDS dataset
  - Terraform flag: `enable_llm_chatbot = true`

The classifier is serverless and cost-efficient, so it remains independent from
the chatbot. Enabling or disabling the EC2 chatbot does not affect the Lambda
classifier API.

The chatbot runs on EC2 because Ollama and LLM serving need a long-running
runtime and more control over compute than a short Lambda request is designed
to provide.

The EC2/Ollama chatbot requires an AWS account that is verified for EC2
launches. If AWS blocks EC2 with an account verification error, that is an AWS
account status issue rather than a Terraform issue. Sign in as the AWS root
account owner, verify billing/contact/payment information, and open an AWS
Support account verification case.

`enable_llm_chatbot = false` is available only as an emergency fallback when
you intentionally want to deploy the non-EC2 parts of the system while EC2
verification is being resolved.

When both serving paths are enabled, deployment prints examples for both:

The deployment runner also waits for the deployed endpoints to return JSON
before printing the final summary. Use the exact `curl` commands printed by the
script; they include the current API Gateway URL and current EC2 public DNS, so
you do not need to set shell variables like `CHATBOT_URL`.

```bash
curl "$(terraform output -raw inference_api_url)?domain_type=commercial&country=United%20States"
```

```bash
curl -X POST "$(terraform output -raw chatbot_api_url)" \
  -H "Content-Type: application/json" \
  -d '{"question":"How many records are business?"}'
```

## LLM Chatbot API

The `llm` branch adds an EC2-hosted chatbot API after the RDS load step. It
lets a client ask natural-language questions about the transformed
`person_records` table using `curl`. It is enabled by default for the full
deployment.

The chatbot runs:

- Ollama
- `qwen2.5:0.5b`
- FastAPI
- controlled predefined SQL queries against RDS

The first version does not let the LLM generate or execute arbitrary SQL.
Python detects supported question intents, runs fixed SQL, and passes the query
result to Ollama for a concise plain-English answer.

### Model Choice

The chatbot uses:

```text
qwen2.5:0.5b
```

This model was chosen because it is small, efficient, and suitable for
summarizing structured query results on a modest EC2 instance.

### Endpoints

Health check:

```text
http://<ec2-public-dns>:8000/health
```

Chat endpoint:

```text
http://<ec2-public-dns>:8000/chat
```

Terraform outputs the exact URLs:

```bash
cd terraform
terraform output -raw chatbot_health_url
terraform output -raw chatbot_api_url
```

The easiest path is still the deployment runner:

```bash
python3 scripts/run_terraform.py deploy
```

At the end, it prints tested, ready-to-run `curl --max-time ...` commands and
the verified JSON responses.

### Example Request

```bash
curl -X POST "http://<ec2-public-dns>:8000/chat" \
  -H "Content-Type: application/json" \
  -d '{"question":"How many records are business?"}'
```

Example response:

```json
{
  "question": "How many records are business?",
  "answer": "There are 42 business records.",
  "query_type": "count_by_affiliation_category",
  "data": {
    "affiliation_category": "business",
    "record_count": 42
  }
}
```

### Supported Questions

- `How many records are business?`
- `How many records are commercial?`
- `What are the top countries in the dataset?`
- `How many records are from United States?`
- `How many records are missing city or region?`
- `Summarize the dataset.`

Additional examples:

```bash
curl -X POST "http://<ec2-public-dns>:8000/chat" \
  -H "Content-Type: application/json" \
  -d '{"question":"What are the top countries in the dataset?"}'
```

```bash
curl -X POST "http://<ec2-public-dns>:8000/chat" \
  -H "Content-Type: application/json" \
  -d '{"question":"Summarize the dataset."}'
```

### Chatbot Security Notes

- RDS remains private.
- EC2 connects to RDS through security-group access on the database port.
- FastAPI port `8000` is open for demo by default through `chatbot_http_cidr`.
- SSH is blocked by default through `chatbot_ssh_cidr = "0.0.0.0/32"` unless you configure a trusted CIDR and key pair.

Optional Terraform variables:

```hcl
enable_classifier_api = true
enable_llm_chatbot   = true
chatbot_instance_type = "t2.micro"
chatbot_http_cidr     = "203.0.113.10/32"
chatbot_ssh_cidr      = "203.0.113.10/32"
chatbot_key_name      = "my-key-pair"
```

If `enable_llm_chatbot = true` fails with an EC2 `UnsupportedOperation` or
`account-verification` error, complete AWS account verification for EC2. This
does not disable or destroy the classifier API. Set `enable_llm_chatbot = false`
only as an emergency fallback if you want to deploy the ETL pipeline and
classifier while EC2 verification is pending.

The chatbot defaults to `t2.micro` because new AWS accounts may have a 1 vCPU
EC2 quota. If you change `chatbot_instance_type` to a larger instance and see
`VcpuLimitExceeded`, request an EC2 quota increase or switch back to `t2.micro`.

### Local Chatbot Testing

You can run the chatbot locally when Ollama is installed and you have network
access to the RDS database.

Pull the model:

```bash
ollama pull qwen2.5:0.5b
```

Start Ollama:

```bash
ollama serve
```

Set database environment variables:

```bash
export DB_HOST="<rds-endpoint>"
export DB_PORT="5432"
export DB_NAME="<database-name>"
export DB_USER="<database-user>"
export DB_PASSWORD="<database-password>"
```

Run FastAPI:

```bash
cd llm_chatbot
python3 -m pip install -r requirements.txt
python3 -m uvicorn app:app --host 0.0.0.0 --port 8000
```

Test health:

```bash
curl "http://localhost:8000/health"
```

Test chat:

```bash
curl -X POST "http://localhost:8000/chat" \
  -H "Content-Type: application/json" \
  -d '{"question":"How many records are business?"}'
```

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
aws logs tail /aws/lambda/$(terraform output -raw lambda_function_name) --since 10m --region us-west-2
```

### 5. Check loader Lambda logs

```bash
aws logs tail /aws/lambda/$(terraform output -raw loader_lambda_function_name) --since 10m --region us-west-2
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
python3 scripts/run_terraform.py destroy
```

The deployment runner empties versioned S3 objects and delete markers before
running `terraform destroy`, which prevents `BucketNotEmpty` errors during
cleanup.

## GitHub Handoff Checklist

Before pushing:

1. Make sure local state and build artifacts are ignored by Git
2. Keep `.terraform.lock.hcl` committed for reproducible provider versions
3. Review `terraform/terraform.tfvars` and remove or generalize any personal naming if desired
4. Confirm the README matches the final working flow
5. Push the repo without AWS credentials, local state files, or generated ZIPs
