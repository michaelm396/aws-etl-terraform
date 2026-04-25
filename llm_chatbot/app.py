from __future__ import annotations
"""FastAPI chatbot over the transformed RDS dataset.

The first version intentionally does not let the model generate or execute SQL.
Python detects a small set of supported intents, runs predefined queries, and
asks Ollama to turn the query result into a plain-English answer.
"""

import os
import re
from typing import Any

import psycopg2
import psycopg2.extras
import requests
from fastapi import FastAPI
from pydantic import BaseModel, Field


OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/chat")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:0.5b")
QUERY_TIMEOUT_SECONDS = 10
HTTP_TIMEOUT_SECONDS = 45

AFFILIATION_CATEGORIES = ("business", "public_sector", "non_institutional")
DOMAIN_TYPES = (
    "commercial",
    "education",
    "government",
    "organization",
    "personal_provider",
    "international",
    "unknown",
)

app = FastAPI(title="RDS Dataset LLM Chatbot", version="1.0.0")


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1)


class ChatResponse(BaseModel):
    question: str
    answer: str
    query_type: str
    data: Any


def database_config() -> dict[str, str]:
    """Read database connection settings from environment variables."""
    required_names = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")
    missing = [name for name in required_names if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"Missing database environment variables: {', '.join(missing)}")

    return {
        "host": os.environ["DB_HOST"],
        "port": os.environ["DB_PORT"],
        "dbname": os.environ["DB_NAME"],
        "user": os.environ["DB_USER"],
        "password": os.environ["DB_PASSWORD"],
    }


def connect_to_database() -> psycopg2.extensions.connection:
    """Open a short-lived PostgreSQL connection."""
    return psycopg2.connect(
        **database_config(),
        connect_timeout=QUERY_TIMEOUT_SECONDS,
    )


def normalize_question(question: str) -> str:
    """Normalize question text for simple intent matching."""
    return re.sub(r"\s+", " ", question.strip().lower())


def find_known_value(question: str, allowed_values: tuple[str, ...]) -> str | None:
    """Find a known category/domain value in the user's question."""
    normalized = normalize_question(question).replace("-", "_")
    for value in allowed_values:
        spaced_value = value.replace("_", " ")
        if value in normalized or spaced_value in normalized:
            return value
    return None


def find_country(question: str) -> str | None:
    """Extract a country phrase from supported count-by-country questions."""
    patterns = (
        r"\bfrom\s+(.+?)[?.!]*$",
        r"\bin\s+(.+?)[?.!]*$",
        r"\bcountry\s+(.+?)[?.!]*$",
    )
    normalized = question.strip()
    for pattern in patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip(" .?!")
    return None


def detect_intent(question: str) -> tuple[str, dict[str, str]]:
    """Map a natural-language question to one supported query type."""
    normalized = normalize_question(question)

    if any(term in normalized for term in ("summarize", "summary", "overview")):
        return "dataset_summary", {}

    if "missing" in normalized and any(term in normalized for term in ("city", "region", "geo", "geolocation")):
        return "missing_geolocation_counts", {}

    if "top" in normalized and "countr" in normalized:
        return "top_countries", {}

    affiliation_category = find_known_value(question, AFFILIATION_CATEGORIES)
    if affiliation_category:
        return "count_by_affiliation_category", {"affiliation_category": affiliation_category}

    domain_type = find_known_value(question, DOMAIN_TYPES)
    if domain_type:
        return "count_by_domain_type", {"domain_type": domain_type}

    country = find_country(question)
    if country:
        return "count_by_country", {"country": country}

    return "unsupported", {}


def fetch_one(query: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    """Run a predefined query that returns one row."""
    with connect_to_database() as connection:
        with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            cursor.execute(query, params)
            row = cursor.fetchone()
            return dict(row or {})


def fetch_all(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """Run a predefined query that returns multiple rows."""
    with connect_to_database() as connection:
        with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]


def run_controlled_query(query_type: str, slots: dict[str, str]) -> Any:
    """Execute one of the approved SQL queries."""
    if query_type == "count_by_affiliation_category":
        return fetch_one(
            """
            SELECT affiliation_category, COUNT(*)::int AS record_count
            FROM person_records
            WHERE affiliation_category = %s
            GROUP BY affiliation_category
            """,
            (slots["affiliation_category"],),
        )

    if query_type == "count_by_domain_type":
        return fetch_one(
            """
            SELECT domain_type, COUNT(*)::int AS record_count
            FROM person_records
            WHERE domain_type = %s
            GROUP BY domain_type
            """,
            (slots["domain_type"],),
        )

    if query_type == "top_countries":
        return fetch_all(
            """
            SELECT COALESCE(country, 'unknown') AS country, COUNT(*)::int AS record_count
            FROM person_records
            GROUP BY COALESCE(country, 'unknown')
            ORDER BY record_count DESC, country
            LIMIT 10
            """
        )

    if query_type == "count_by_country":
        return fetch_one(
            """
            SELECT country, COUNT(*)::int AS record_count
            FROM person_records
            WHERE lower(country) = lower(%s)
            GROUP BY country
            """,
            (slots["country"],),
        )

    if query_type == "missing_geolocation_counts":
        return fetch_one(
            """
            SELECT
              COUNT(*) FILTER (WHERE city IS NULL OR city = '')::int AS missing_city_count,
              COUNT(*) FILTER (WHERE region IS NULL OR region = '')::int AS missing_region_count,
              COUNT(*) FILTER (
                WHERE city IS NULL OR city = '' OR region IS NULL OR region = ''
              )::int AS missing_city_or_region_count
            FROM person_records
            """
        )

    if query_type == "dataset_summary":
        return fetch_one(
            """
            SELECT
              COUNT(*)::int AS total_records,
              COUNT(DISTINCT domain_type)::int AS domain_type_count,
              COUNT(DISTINCT affiliation_category)::int AS affiliation_category_count,
              COUNT(DISTINCT country)::int AS country_count,
              COUNT(*) FILTER (WHERE country IS NULL OR country = '')::int AS missing_country_count,
              COUNT(*) FILTER (WHERE city IS NULL OR city = '')::int AS missing_city_count,
              COUNT(*) FILTER (WHERE region IS NULL OR region = '')::int AS missing_region_count
            FROM person_records
            """
        )

    return {
        "supported_questions": [
            "How many records are business?",
            "How many records are commercial?",
            "What are the top countries in the dataset?",
            "How many records are from United States?",
            "How many records are missing city or region?",
            "Summarize the dataset.",
        ]
    }


def fallback_answer(query_type: str, data: Any) -> str:
    """Return a deterministic answer if Ollama is unavailable."""
    if query_type == "unsupported":
        return "I can answer only the supported dataset summary and count questions."
    if query_type == "count_by_affiliation_category" and isinstance(data, dict):
        category = data.get("affiliation_category", "that category")
        count = data.get("record_count", 0)
        return f"There are {count} records with affiliation_category '{category}'."
    if query_type == "count_by_domain_type" and isinstance(data, dict):
        domain_type = data.get("domain_type", "that domain type")
        count = data.get("record_count", 0)
        return f"There are {count} records with domain_type '{domain_type}'."
    if query_type == "count_by_country" and isinstance(data, dict):
        country = data.get("country", "that country")
        count = data.get("record_count", 0)
        return f"There are {count} records from {country}."
    if query_type == "top_countries" and isinstance(data, list):
        countries = ", ".join(
            f"{row.get('country')} ({row.get('record_count')})" for row in data[:5]
        )
        return f"The top countries are: {countries}."
    if query_type == "missing_geolocation_counts" and isinstance(data, dict):
        missing = data.get("missing_city_or_region_count", 0)
        return f"There are {missing} records missing city or region."
    if query_type == "dataset_summary" and isinstance(data, dict):
        total = data.get("total_records", 0)
        countries = data.get("country_count", 0)
        domains = data.get("domain_type_count", 0)
        categories = data.get("affiliation_category_count", 0)
        return (
            f"The dataset has {total} records across {countries} countries, "
            f"{domains} domain types, and {categories} affiliation categories."
        )
    return f"Here is the query result: {data}"


def ask_ollama_generate(question: str, query_type: str, data: Any) -> str:
    """Fallback for Ollama versions that do not expose /api/chat."""
    generate_url = OLLAMA_URL.rsplit("/", 1)[0] + "/generate"
    prompt = (
        "Answer using only the supplied data. Be concise and plain-English.\n\n"
        f"Question: {question}\n"
        f"Query type: {query_type}\n"
        f"Query result: {data}\n"
    )
    response = requests.post(
        generate_url,
        json={
            "model": OLLAMA_MODEL,
            "stream": False,
            "prompt": prompt,
        },
        timeout=HTTP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    return payload.get("response", "").strip() or fallback_answer(query_type, data)


def ask_ollama(question: str, query_type: str, data: Any) -> str:
    """Ask local Ollama to summarize controlled SQL results."""
    prompt = (
        "You are answering questions about a transformed RDS dataset. "
        "Use only the provided query result. Be concise and plain-English.\n\n"
        f"Question: {question}\n"
        f"Query type: {query_type}\n"
        f"Query result: {data}\n"
    )
    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "stream": False,
                "messages": [
                    {
                        "role": "system",
                        "content": "Answer using only the supplied data.",
                    },
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        if isinstance(exc, requests.HTTPError) and exc.response is not None:
            if exc.response.status_code == 404:
                try:
                    return ask_ollama_generate(question, query_type, data)
                except requests.RequestException as generate_exc:
                    print(f"Ollama generate fallback failed: {generate_exc}")
                    return fallback_answer(query_type, data)
            print(f"Ollama chat request failed: {exc}. Response body: {exc.response.text}")
            return fallback_answer(query_type, data)
        print(f"Ollama chat request failed: {exc}")
        return fallback_answer(query_type, data)

    payload = response.json()
    return payload.get("message", {}).get("content", "").strip() or fallback_answer(query_type, data)


@app.get("/health")
def health() -> dict[str, str]:
    """Health check for load balancers and reviewers."""
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    """Answer a supported natural-language question about person_records."""
    query_type, slots = detect_intent(request.question)
    try:
        data = run_controlled_query(query_type, slots)
    except Exception as exc:
        return ChatResponse(
            question=request.question,
            answer=f"Database query failed: {exc}",
            query_type=query_type,
            data={"error": str(exc)},
        )

    answer = ask_ollama(request.question, query_type, data)
    return ChatResponse(
        question=request.question,
        answer=answer,
        query_type=query_type,
        data=data,
    )
