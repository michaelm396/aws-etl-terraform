from __future__ import annotations
"""Transform Lambda for the ETL pipeline.

This stage reads the raw workbook from S3 into a pandas DataFrame, normalizes
fields, engineers domain-based email features, preserves the original gender
column while adding a mapped gender integer, enriches public IP addresses with
offline geolocation metadata, and writes a processed CSV back to S3.
"""

import ipaddress
import os
from io import BytesIO
from pathlib import PurePosixPath
from urllib.parse import unquote_plus

import boto3
import pandas as pd
from geolite2 import geolite2


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
INTERNATIONAL_DOMAIN_SUFFIXES = (
    ".uk",
    ".au",
    ".jp",
    ".cn",
    ".ru",
    ".br",
    ".de",
    ".fr",
    ".it",
    ".nl",
    ".ca",
)
COMMERCIAL_DOMAIN_SUFFIXES = (".com", ".net", ".io", ".co")
PERSONAL_PROVIDER_DOMAINS = {
    "gmail.com",
    "yahoo.com",
    "hotmail.com",
    "outlook.com",
    "icloud.com",
    "msn.com",
    "live.com",
    "aol.com",
}
NULLABLE_CATEGORICAL_COLUMNS = [
    "email_domain",
    "country",
    "region",
    "city",
    "timezone",
]
GEOLOCATION_HEADERS = [
    "country",
    "region",
    "city",
    "latitude",
    "longitude",
    "timezone",
]
RAW_SAMPLE_ROWS = 3
PROCESSED_SAMPLE_ROWS = 3

s3_client = boto3.client("s3")
geoip_reader = geolite2.reader()


def normalize_value(value: object) -> object:
    """Trim string values and convert blank strings to nulls."""
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned if cleaned else None
    return value


def clean_null(value: object) -> object:
    """Convert empty or invalid placeholder values to nulls."""
    if value is None:
        return None

    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        return cleaned

    if pd.isna(value):
        return None

    return value


def normalize_dataframe(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Normalize workbook values after reading them into pandas."""
    normalized = dataframe.copy()
    normalized.columns = [
        str(column).strip() if column is not None else ""
        for column in normalized.columns
    ]
    return normalized.apply(lambda column: column.map(normalize_value))


def transform_gender(value: object) -> object:
    """Map the normalized gender label to its integer code."""
    if value is None:
        return value

    if value not in GENDER_MAP:
        raise ValueError(f"Unsupported gender value: {value}")

    return GENDER_MAP[value]


def get_email_domain(email_value: object) -> str | None:
    """Extract a lowercased email domain or return null when parsing fails."""
    if email_value is None:
        return None

    email_text = str(email_value).strip()
    local_part, separator, domain = email_text.partition("@")
    if (
        not separator
        or not local_part
        or not domain
        or "@" in domain
        or "." not in domain
        or any(character.isspace() for character in email_text)
    ):
        print(f"Email parsing skipped for invalid email value: {email_value}")
        return None

    return domain.lower()


def get_domain_type(email_domain: object) -> str:
    """Categorize an email domain into a readable engineered feature.

    The domain_type feature is derived only from the email domain and is kept
    separate from any IP-derived geolocation features such as country.
    """
    if email_domain is None:
        return "unknown"

    domain = str(email_domain).lower()
    if domain.endswith(".edu"):
        return "education"
    if domain.endswith(".gov"):
        return "government"
    if domain.endswith(".org"):
        return "organization"
    if domain in PERSONAL_PROVIDER_DOMAINS:
        return "personal_provider"
    if domain.endswith(COMMERCIAL_DOMAIN_SUFFIXES):
        return "commercial"
    if domain.endswith(INTERNATIONAL_DOMAIN_SUFFIXES):
        return "international"
    return "unknown"


def get_affiliation_category(domain_type: object) -> str:
    """Create the ML target label from the engineered domain_type feature."""
    if domain_type == "commercial":
        return "business"
    if domain_type in {"education", "government"}:
        return "public_sector"
    return "non_institutional"


def null_geolocation_fields() -> list[object]:
    """Return an empty geolocation payload for failed lookups."""
    return [None] * len(GEOLOCATION_HEADERS)


def validate_public_ip(ip_value: object) -> object | None:
    """Return a parsed public IP address or None for missing/private/local values."""
    if ip_value is None:
        return None

    try:
        parsed_ip = ipaddress.ip_address(str(ip_value))
    except ValueError:
        print(f"Geolocation skipped for invalid IP address: {ip_value}")
        return None

    if (
        parsed_ip.is_private
        or parsed_ip.is_loopback
        or parsed_ip.is_multicast
        or parsed_ip.is_reserved
        or parsed_ip.is_link_local
        or parsed_ip.is_unspecified
    ):
        print(f"Geolocation skipped for non-public IP address: {ip_value}")
        return None

    return parsed_ip


def safe_nested_get(value: dict | list | None, *keys: object) -> object | None:
    """Safely walk nested dict/list values from the GeoLite2 lookup payload."""
    current: object | None = value
    for key in keys:
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(key)
        elif isinstance(current, list) and isinstance(key, int):
            if key >= len(current):
                return None
            current = current[key]
        else:
            return None
    return current


def geolocate_ip_address(ip_value: object) -> list[object]:
    """Return geolocation fields for a public IP, or nulls when lookup fails."""
    parsed_ip = validate_public_ip(ip_value)
    if parsed_ip is None:
        return null_geolocation_fields()

    try:
        geo_record = geoip_reader.get(str(parsed_ip))
    except Exception as exc:
        print(f"Geolocation lookup failed for IP {ip_value}: {exc}")
        return null_geolocation_fields()

    if not geo_record:
        print(f"Geolocation lookup returned no data for IP {ip_value}")
        return null_geolocation_fields()

    return [
        safe_nested_get(geo_record, "country", "names", "en"),
        safe_nested_get(geo_record, "subdivisions", 0, "names", "en"),
        safe_nested_get(geo_record, "city", "names", "en"),
        safe_nested_get(geo_record, "location", "latitude"),
        safe_nested_get(geo_record, "location", "longitude"),
        safe_nested_get(geo_record, "location", "time_zone"),
    ]


def standardize_nullable_columns(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Normalize nullable categorical fields for RDS-safe and ML-safe output."""
    standardized = dataframe.copy()

    for column_name in NULLABLE_CATEGORICAL_COLUMNS:
        if column_name not in standardized.columns:
            continue

        original_values = standardized[column_name].tolist()
        standardized[column_name] = standardized[column_name].apply(clean_null)

        for original_value, cleaned_value in zip(original_values, standardized[column_name].tolist()):
            if original_value != cleaned_value and cleaned_value is None:
                print(
                    f"Standardized missing value in column '{column_name}': "
                    f"{original_value!r} -> None"
                )

    return standardized


def build_output_columns(raw_headers: list[str]) -> list[str]:
    """Build a stable transformed output column order."""
    processed_headers = raw_headers.copy()

    if "email" in processed_headers:
        email_index = processed_headers.index("email")
        processed_headers[email_index + 1 : email_index + 1] = [
            "email_domain",
            "domain_type",
            "affiliation_category",
        ]

    if "gender" in processed_headers:
        gender_index = processed_headers.index("gender")
        processed_headers.insert(gender_index + 1, "gender_mapped")

    processed_headers.extend(GEOLOCATION_HEADERS)
    return processed_headers


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


def extract_workbook_dataframe_from_s3(
    bucket_name: str,
    source_key: str,
) -> pd.DataFrame:
    """Extract the workbook into a pandas DataFrame from the raw S3 object."""
    response = s3_client.get_object(Bucket=bucket_name, Key=source_key)
    return pd.read_excel(BytesIO(response["Body"].read()))


def transform_workbook_dataframe_to_csv(
    dataframe: pd.DataFrame,
) -> tuple[str, list[str], list[str], list[list[object]], list[list[object]]]:
    """Transform the raw workbook DataFrame into a processed CSV payload."""
    if dataframe.empty:
        raise ValueError("Uploaded workbook is empty.")

    normalized = normalize_dataframe(dataframe)
    required_columns = {"gender", "ip_address", "email"}
    missing_columns = sorted(required_columns.difference(normalized.columns))
    if missing_columns:
        raise ValueError(
            "Workbook is missing required column(s): "
            + ", ".join(missing_columns)
        )

    raw_headers = list(normalized.columns)
    raw_sample_rows = normalized.head(RAW_SAMPLE_ROWS).values.tolist()

    transformed = normalized.copy()
    # email_domain is extracted from the raw email address and domain_type is a
    # human-readable engineered feature derived from that domain alone.
    transformed["email_domain"] = transformed["email"].apply(get_email_domain)
    transformed["domain_type"] = transformed["email_domain"].apply(get_domain_type)
    # affiliation_category is the model training label derived from domain_type.
    transformed["affiliation_category"] = transformed["domain_type"].apply(
        get_affiliation_category
    )
    transformed["gender_mapped"] = transformed["gender"].apply(transform_gender)

    # country and the other location fields come from IP-based geolocation and
    # intentionally remain separate signals from the email-derived features.
    geolocation_frame = pd.DataFrame(
        transformed["ip_address"].apply(geolocate_ip_address).tolist(),
        columns=GEOLOCATION_HEADERS,
        index=transformed.index,
    )
    transformed = pd.concat([transformed, geolocation_frame], axis=1)
    transformed = standardize_nullable_columns(transformed)

    output_columns = build_output_columns(raw_headers)
    transformed = transformed[output_columns]
    processed_sample_rows = transformed.head(PROCESSED_SAMPLE_ROWS).values.tolist()

    return (
        transformed.to_csv(index=False),
        raw_headers,
        output_columns,
        raw_sample_rows,
        processed_sample_rows,
    )


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

        dataframe = extract_workbook_dataframe_from_s3(bucket_name, source_key)
        (
            csv_payload,
            raw_headers,
            processed_headers,
            raw_sample_rows,
            processed_sample_rows,
        ) = transform_workbook_dataframe_to_csv(dataframe)
        log_sample_rows("Raw workbook", raw_headers, raw_sample_rows)
        log_sample_rows("Processed CSV", processed_headers, processed_sample_rows)

        transformed_key = destination_key(source_key, processed_prefix)
        load_transformed_csv_to_s3(bucket_name, transformed_key, csv_payload)

        print(f"Wrote transformed output to s3://{bucket_name}/{transformed_key}")

    return {
        "statusCode": 200,
        "rawPrefix": raw_prefix,
        "processedPrefix": processed_prefix,
    }
