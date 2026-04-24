from __future__ import annotations
"""Transform Lambda for the ETL pipeline.

This stage reads the raw workbook from S3, normalizes fields, converts the
categorical gender column to a gender_code integer, and writes a processed CSV
back to S3.
"""

import csv
import os
from io import BytesIO, StringIO
from pathlib import PurePosixPath
from urllib.parse import unquote_plus

import boto3
from openpyxl import load_workbook


GENDER_MAP = {
    "Male": 0,
    "Female": 1,
    "Non-binary": 2,
    "Bigender": 3,
    "Genderfluid": 4,
    "Agender": 5,
    "Genderqueer": 6,
    "Polygender": 7,
}

s3_client = boto3.client("s3")
RAW_SAMPLE_ROWS = 3
PROCESSED_SAMPLE_ROWS = 3


def normalize_value(value: object) -> object:
    """Trim string values and convert blank strings to nulls."""
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned if cleaned else None
    return value


def transform_gender(value: object) -> object:
    """Map the normalized gender label to its integer code."""
    if value is None:
        return value

    if value not in GENDER_MAP:
        raise ValueError(f"Unsupported gender value: {value}")

    return GENDER_MAP[value]


def destination_key(source_key: str, processed_prefix: str) -> str:
    """Build the processed CSV key from the raw workbook key."""
    source_path = PurePosixPath(source_key)
    stem = source_path.stem
    return f"{processed_prefix.strip('/')}/{stem}_transformed.csv"


def log_sample_rows(label: str, headers: list[str], rows: list[list[object]]) -> None:
    """Log a small preview of the data flowing through the transform stage."""
    print(f"{label} sample ({len(rows)} row(s)):")
    print(headers)
    for row in rows:
        print(row)


def extract_workbook_rows_from_s3(bucket_name: str, source_key: str) -> list[tuple[object, ...]]:
    """Extract workbook rows from the raw S3 object."""
    response = s3_client.get_object(Bucket=bucket_name, Key=source_key)
    workbook = load_workbook(
        filename=BytesIO(response["Body"].read()),
        read_only=True,
        data_only=True,
    )
    worksheet = workbook.active
    return list(worksheet.iter_rows(values_only=True))


def transform_workbook_rows_to_csv(
    rows: list[tuple[object, ...]],
) -> tuple[str, list[str], list[list[object]], list[list[object]]]:
    """Transform raw workbook rows into a processed CSV payload."""
    if not rows:
        raise ValueError("Uploaded workbook is empty.")

    headers = [str(value).strip() if value is not None else "" for value in rows[0]]
    try:
        gender_index = next(
            index for index, header in enumerate(headers) if header.lower() == "gender"
        )
    except StopIteration as exc:
        raise ValueError("Workbook does not contain a 'gender' column.") from exc

    headers[gender_index] = "gender_code"

    # Capture a tiny before/after sample for CloudWatch verification.
    raw_sample_rows = [
        [normalize_value(value) for value in row]
        for row in rows[1 : 1 + RAW_SAMPLE_ROWS]
    ]

    output_buffer = StringIO()
    writer = csv.writer(output_buffer)
    writer.writerow(headers)
    processed_sample_rows: list[list[object]] = []

    for row in rows[1:]:
        mutable_row = [normalize_value(value) for value in row]
        mutable_row[gender_index] = transform_gender(mutable_row[gender_index])
        writer.writerow(mutable_row)
        if len(processed_sample_rows) < PROCESSED_SAMPLE_ROWS:
            processed_sample_rows.append(mutable_row.copy())

    return output_buffer.getvalue(), headers, raw_sample_rows, processed_sample_rows


def load_transformed_csv_to_s3(
    bucket_name: str,
    transformed_key: str,
    csv_payload: str,
) -> None:
    """Load the processed CSV payload back into S3."""
    s3_client.put_object(
        Bucket=bucket_name,
        Key=transformed_key,
        Body=csv_payload.encode("utf-8"),
        ContentType="text/csv",
    )


def lambda_handler(event: dict, context: object) -> dict:
    """Handle S3-created events for raw workbook uploads."""
    processed_prefix = os.environ.get("PROCESSED_PREFIX", "processed")
    raw_prefix = os.environ.get("RAW_PREFIX", "raw").strip("/")

    for record in event.get("Records", []):
        bucket_name = record["s3"]["bucket"]["name"]
        source_key = unquote_plus(record["s3"]["object"]["key"])
        expected_prefix = f"{raw_prefix}/"

        # Ignore uploads outside the raw prefix so the function stays focused on
        # the extract-to-transform handoff.
        if not source_key.startswith(expected_prefix):
            continue

        print(f"Processing workbook from s3://{bucket_name}/{source_key}")

        rows = extract_workbook_rows_from_s3(bucket_name, source_key)
        (
            csv_payload,
            headers,
            raw_sample_rows,
            processed_sample_rows,
        ) = transform_workbook_rows_to_csv(rows)
        log_sample_rows("Raw workbook", headers, raw_sample_rows)
        log_sample_rows("Processed CSV", headers, processed_sample_rows)

        transformed_key = destination_key(source_key, processed_prefix)
        load_transformed_csv_to_s3(bucket_name, transformed_key, csv_payload)

        print(f"Wrote transformed output to s3://{bucket_name}/{transformed_key}")

    return {
        "statusCode": 200,
        "rawPrefix": raw_prefix,
        "processedPrefix": processed_prefix,
    }
