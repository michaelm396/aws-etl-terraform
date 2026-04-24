from __future__ import annotations
"""Load Lambda for the ETL pipeline.

This stage reads the processed CSV from S3, maintains the gender lookup table,
and upserts the transformed records into PostgreSQL RDS, including the IP
geolocation enrichment columns added during the transform stage.
"""

import csv
import os
import traceback
from io import StringIO

import boto3
import pg8000


GENDER_LOOKUP = {
    0: "Male",
    1: "Female",
    2: "Non-binary",
    3: "Bigender",
    4: "Genderfluid",
    5: "Agender",
    6: "Genderqueer",
    7: "Polygender",
}

s3_client = boto3.client("s3")
CSV_SAMPLE_ROWS = 3


def get_db_credentials() -> dict[str, str]:
    """Read the database connection details from Lambda environment variables."""
    return {
        "host": os.environ["DB_HOST"],
        "port": os.environ["DB_PORT"],
        "dbname": os.environ["DB_NAME"],
        "username": os.environ["DB_USER"],
        "password": os.environ["DB_PASSWORD"],
    }


def ensure_schema(connection: pg8000.dbapi.Connection) -> None:
    """Create or update the tables needed by the load stage."""
    cursor = connection.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS gender_mapping (
            gender_code SMALLINT PRIMARY KEY,
            gender_label TEXT NOT NULL UNIQUE
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS person_records (
            id INTEGER PRIMARY KEY,
            first_name TEXT,
            last_name TEXT,
            email TEXT,
            gender_code SMALLINT REFERENCES gender_mapping(gender_code),
            ip_address TEXT,
            country TEXT,
            region TEXT,
            city TEXT,
            latitude DOUBLE PRECISION,
            longitude DOUBLE PRECISION,
            timezone TEXT
        )
        """
    )
    cursor.execute(
        """
        ALTER TABLE person_records
        ADD COLUMN IF NOT EXISTS gender_code SMALLINT
        """
    )
    cursor.execute(
        """
        ALTER TABLE person_records
        ADD COLUMN IF NOT EXISTS country TEXT
        """
    )
    cursor.execute(
        """
        ALTER TABLE person_records
        ADD COLUMN IF NOT EXISTS region TEXT
        """
    )
    cursor.execute(
        """
        ALTER TABLE person_records
        ADD COLUMN IF NOT EXISTS city TEXT
        """
    )
    cursor.execute(
        """
        ALTER TABLE person_records
        ADD COLUMN IF NOT EXISTS latitude DOUBLE PRECISION
        """
    )
    cursor.execute(
        """
        ALTER TABLE person_records
        ADD COLUMN IF NOT EXISTS longitude DOUBLE PRECISION
        """
    )
    cursor.execute(
        """
        ALTER TABLE person_records
        ADD COLUMN IF NOT EXISTS timezone TEXT
        """
    )
    cursor.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'person_records'
                  AND column_name = 'gender'
            ) THEN
                EXECUTE '
                    UPDATE person_records
                    SET gender_code = COALESCE(gender_code, gender)
                ';
            END IF;
        END $$;
        """
    )
    # Keep the lookup table synchronized with the transform mapping.
    for code, label in GENDER_LOOKUP.items():
        cursor.execute(
            """
            INSERT INTO gender_mapping (gender_code, gender_label)
            VALUES (%s, %s)
            ON CONFLICT (gender_code) DO UPDATE
            SET gender_label = EXCLUDED.gender_label
            """,
            (code, label),
        )
    connection.commit()


def load_rows(
    connection: pg8000.dbapi.Connection,
    csv_payload: str,
) -> int:
    """Upsert processed CSV rows into the person_records table."""
    cursor = connection.cursor()
    reader = csv.DictReader(StringIO(csv_payload))
    row_count = 0

    for row in reader:
        cursor.execute(
            """
            INSERT INTO person_records (
                id,
                first_name,
                last_name,
                email,
                gender_code,
                ip_address,
                country,
                region,
                city,
                latitude,
                longitude,
                timezone
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                first_name = EXCLUDED.first_name,
                last_name = EXCLUDED.last_name,
                email = EXCLUDED.email,
                gender_code = EXCLUDED.gender_code,
                ip_address = EXCLUDED.ip_address,
                country = EXCLUDED.country,
                region = EXCLUDED.region,
                city = EXCLUDED.city,
                latitude = EXCLUDED.latitude,
                longitude = EXCLUDED.longitude,
                timezone = EXCLUDED.timezone
            """,
            (
                int(row["id"]) if row["id"] else None,
                row["first_name"] or None,
                row["last_name"] or None,
                row["email"] or None,
                int(row["gender_code"]) if row["gender_code"] else None,
                row["ip_address"] or None,
                row["country"] or None,
                row["region"] or None,
                row["city"] or None,
                float(row["latitude"]) if row["latitude"] else None,
                float(row["longitude"]) if row["longitude"] else None,
                row["timezone"] or None,
            ),
        )
        row_count += 1

    connection.commit()
    return row_count


def get_table_counts(connection: pg8000.dbapi.Connection) -> dict[str, int]:
    """Return row counts for the lookup and fact tables."""
    cursor = connection.cursor()
    cursor.execute("SELECT COUNT(*) FROM gender_mapping")
    gender_mapping_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM person_records")
    person_records_count = cursor.fetchone()[0]
    return {
        "gender_mapping": gender_mapping_count,
        "person_records": person_records_count,
    }


def get_gender_lookup_rows(connection: pg8000.dbapi.Connection) -> list[tuple[int, str]]:
    """Fetch the full gender lookup table for logging and verification."""
    cursor = connection.cursor()
    cursor.execute(
        """
        SELECT gender_code, gender_label
        FROM gender_mapping
        ORDER BY gender_code
        """
    )
    return [(int(code), str(label)) for code, label in cursor.fetchall()]


def log_processed_csv_sample(csv_payload: str) -> None:
    """Log a small preview of the processed CSV before loading it into RDS."""
    reader = csv.reader(StringIO(csv_payload))
    rows = []
    for index, row in enumerate(reader):
        rows.append(row)
        if index >= CSV_SAMPLE_ROWS:
            break

    print(f"Processed CSV preview ({len(rows)} line(s)):")
    for row in rows:
        print(row)


def extract_processed_csv_from_s3(bucket_name: str, object_key: str) -> str:
    """Extract the processed CSV payload from S3."""
    response = s3_client.get_object(Bucket=bucket_name, Key=object_key)
    return response["Body"].read().decode("utf-8")


def load_processed_csv_into_rds(
    csv_payload: str,
    credentials: dict[str, str],
) -> tuple[int, dict[str, int], list[tuple[int, str]]]:
    """Load the processed CSV payload into PostgreSQL and return diagnostics."""
    connection = pg8000.connect(
        host=credentials["host"],
        port=int(credentials["port"]),
        database=credentials["dbname"],
        user=credentials["username"],
        password=credentials["password"],
        timeout=10,
    )
    print("Connected to PostgreSQL successfully")
    try:
        ensure_schema(connection)
        print("Ensured database schema and gender lookup table")
        row_count = load_rows(connection, csv_payload)
        print(f"Inserted or updated {row_count} rows")
        table_counts = get_table_counts(connection)
        gender_lookup_rows = get_gender_lookup_rows(connection)
    finally:
        connection.close()

    return row_count, table_counts, gender_lookup_rows


def lambda_handler(event: dict, context: object) -> dict:
    """Handle S3-created events for processed CSV uploads."""
    credentials = get_db_credentials()
    processed = []

    for record in event.get("Records", []):
        bucket_name = record["s3"]["bucket"]["name"]
        object_key = record["s3"]["object"]["key"]
        print(f"Loading processed CSV from s3://{bucket_name}/{object_key} into RDS")

        csv_payload = extract_processed_csv_from_s3(bucket_name, object_key)
        log_processed_csv_sample(csv_payload)
        print(
            "Connecting to PostgreSQL "
            f"{credentials['host']}:{credentials['port']}/{credentials['dbname']}"
        )

        try:
            (
                row_count,
                table_counts,
                gender_lookup_rows,
            ) = load_processed_csv_into_rds(
                csv_payload=csv_payload,
                credentials=credentials,
            )
        except Exception:
            # Print the full traceback so CloudWatch contains actionable details
            # if the load stage fails in AWS.
            print("RDS loader Lambda failed during database processing")
            print(traceback.format_exc())
            raise

        processed.append(
            {
                "key": object_key,
                "rows_loaded": row_count,
                "table_counts": table_counts,
            }
        )
        print(f"Loaded {row_count} rows from {object_key}")
        print(
            "Current RDS row counts: "
            f"gender_mapping={table_counts['gender_mapping']}, "
            f"person_records={table_counts['person_records']}"
        )
        print("Gender lookup table rows:")
        for code, label in gender_lookup_rows:
            print(f"{code} -> {label}")

    return {"statusCode": 200, "processed": processed}
