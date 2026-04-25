from __future__ import annotations
"""Extract stage entrypoint for the ETL pipeline.

This script uploads the source workbook to S3 and relies on the AWS-side
pipeline to continue the transform and load stages.
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
TERRAFORM_DIR = PROJECT_ROOT / "terraform"
DEFAULT_FILE = PROJECT_ROOT / "SRDataEngineerChallenge_DATASET.xlsx"
DEFAULT_S3_KEY_PREFIX = "raw"
DEFAULT_PROCESSED_PREFIX = "processed"
DEFAULT_REGION = "us-west-2"
XLSX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser for the extract stage."""
    parser = argparse.ArgumentParser(
        description=(
            "Upload the source XLSX file to S3 and let the AWS-side pipeline "
            "handle transformation and loading into RDS."
        )
    )
    parser.add_argument(
        "--bucket",
        default=os.environ.get("S3_BUCKET_NAME"),
        help=(
            "Target S3 bucket name. Can also be provided via S3_BUCKET_NAME. "
            "If omitted, the script reads terraform output bucket_name."
        ),
    )
    parser.add_argument(
        "--file",
        default=str(DEFAULT_FILE),
        help="Path to the local XLSX file to upload.",
    )
    parser.add_argument(
        "--key-prefix",
        default=DEFAULT_S3_KEY_PREFIX,
        help="S3 prefix used for uploaded raw files.",
    )
    parser.add_argument(
        "--processed-prefix",
        default=DEFAULT_PROCESSED_PREFIX,
        help="S3 prefix where Lambda writes transformed CSV files.",
    )
    parser.add_argument(
        "--aws-profile",
        default=os.environ.get("AWS_PROFILE"),
        help="Optional AWS profile name to use for AWS CLI calls.",
    )
    parser.add_argument(
        "--aws-region",
        default=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION"),
        help=(
            "AWS region for S3 calls. Defaults to AWS_REGION/AWS_DEFAULT_REGION, "
            "then terraform.tfvars aws_region, then us-west-2."
        ),
    )
    return parser


def parse_tfvars_string(name: str, default: str) -> str:
    """Read a simple quoted string value from terraform.tfvars."""
    tfvars_path = TERRAFORM_DIR / "terraform.tfvars"
    if not tfvars_path.exists():
        return default

    pattern = re.compile(rf'^\s*{re.escape(name)}\s*=\s*"([^"]+)"\s*$')
    for line in tfvars_path.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match:
            return match.group(1)
    return default


def resolve_aws_region(explicit_region: str | None) -> str:
    """Prefer explicit/env region, otherwise use Terraform's configured region."""
    if explicit_region:
        return explicit_region
    return parse_tfvars_string("aws_region", DEFAULT_REGION)


def terraform_output(name: str) -> str:
    """Read a Terraform output value from the local state."""
    try:
        completed = subprocess.run(
            ["terraform", "output", "-raw", name],
            cwd=TERRAFORM_DIR,
            check=True,
            text=True,
            capture_output=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Terraform CLI is required for ETL orchestration.") from exc
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        raise RuntimeError(
            f"Could not resolve terraform output '{name}'. Details: {message}"
        ) from exc

    value = completed.stdout.strip()
    if not value:
        raise RuntimeError(f"Terraform output '{name}' was empty.")
    return value


def resolve_bucket_name(explicit_bucket: str | None) -> str:
    """Prefer an explicit bucket name, otherwise read it from Terraform."""
    if explicit_bucket:
        return explicit_bucket
    return terraform_output("bucket_name")


def resolve_bucket_arn() -> str:
    """Read the provisioned bucket ARN from Terraform."""
    return terraform_output("bucket_arn")


def extract_local_xlsx_to_s3(
    bucket_name: str,
    file_path: Path,
    key_prefix: str,
    profile_name: str | None,
    region: str,
) -> str:
    """Upload the local XLSX file into the raw S3 landing zone."""
    if not file_path.exists():
        raise FileNotFoundError(f"Source file not found: {file_path}")

    s3_key = f"{key_prefix.strip('/')}/{file_path.name}" if key_prefix else file_path.name
    destination = f"s3://{bucket_name}/{s3_key}"

    command = [
        "aws",
        "s3",
        "cp",
        str(file_path),
        destination,
        "--content-type",
        XLSX_CONTENT_TYPE,
        "--region",
        region,
    ]
    if profile_name:
        command.extend(["--profile", profile_name])

    try:
        subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("AWS CLI is required to upload the XLSX file.") from exc
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        raise RuntimeError(f"AWS CLI upload failed. Details: {message}") from exc

    return s3_key


def verify_extracted_s3_object(
    bucket_name: str,
    s3_key: str,
    profile_name: str | None,
    region: str,
) -> None:
    """Confirm the raw object exists before the AWS-side pipeline continues."""
    command = [
        "aws",
        "s3api",
        "head-object",
        "--bucket",
        bucket_name,
        "--key",
        s3_key,
        "--region",
        region,
    ]
    if profile_name:
        command.extend(["--profile", profile_name])

    try:
        subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("AWS CLI is required to verify the uploaded file.") from exc
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        raise RuntimeError(
            "The XLSX upload command finished, but the object was not found in S3. "
            f"Details: {message}"
        ) from exc


def processed_key(source_key: str, processed_prefix: str) -> str:
    """Derive the processed CSV key from the uploaded raw object key."""
    source_name = Path(source_key).stem
    return f"{processed_prefix.strip('/')}/{source_name}_transformed.csv"


def run_extract_stage(
    bucket_name: str,
    file_path: Path,
    key_prefix: str,
    profile_name: str | None,
    region: str,
) -> str:
    """Run the full extract stage: upload the workbook and verify it exists."""
    raw_s3_key = extract_local_xlsx_to_s3(
        bucket_name=bucket_name,
        file_path=file_path,
        key_prefix=key_prefix,
        profile_name=profile_name,
        region=region,
    )
    verify_extracted_s3_object(
        bucket_name=bucket_name,
        s3_key=raw_s3_key,
        profile_name=profile_name,
        region=region,
    )
    return raw_s3_key


def main() -> None:
    """Run the extract stage and print the downstream AWS artifact locations."""
    parser = build_parser()
    args = parser.parse_args()

    file_path = Path(args.file).expanduser().resolve()

    try:
        region = resolve_aws_region(args.aws_region)
        bucket_name = resolve_bucket_name(args.bucket)
        bucket_arn = resolve_bucket_arn()
        raw_s3_key = run_extract_stage(
            bucket_name=bucket_name,
            file_path=file_path,
            key_prefix=args.key_prefix,
            profile_name=args.aws_profile,
            region=region,
        )
        transformed_s3_key = processed_key(raw_s3_key, args.processed_prefix)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"ETL upload failed: {exc}", file=sys.stderr)
        sys.exit(1)

    print("Upload completed successfully.")
    print(f"Bucket: {bucket_name}")
    print(f"Bucket ARN: {bucket_arn}")
    print(f"AWS Region: {region}")
    print(f"Raw S3 URI: s3://{bucket_name}/{raw_s3_key}")
    print(f"Expected processed S3 URI: s3://{bucket_name}/{transformed_s3_key}")
    print("Lambda transform and RDS load continue inside AWS.")


if __name__ == "__main__":
    main()
