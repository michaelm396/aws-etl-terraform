from __future__ import annotations
"""Inference Lambda for the Institution Type Classifier.

The handler loads lightweight model and encoder artifacts, accepts query string
input with `domain_type` and `country`, encodes the features, evaluates the
decision tree in pure Python, and returns a decoded `affiliation_category`
prediction.
"""

import json
import pickle
from pathlib import Path
from typing import Any
from urllib.parse import unquote


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCAL_ARTIFACTS_DIR = PROJECT_ROOT / "ml" / "artifacts"
LAMBDA_ARTIFACTS_DIR = Path(__file__).resolve().parent

FEATURE_NAMES = ("domain_type", "country")
MODEL_FILENAME = "model.pkl"
ENCODERS_FILENAME = "encoders.pkl"
LEAF_NODE = -2

_MODEL: dict[str, Any] | None = None
_ENCODERS: dict[str, list[str]] | None = None


def clean_feature_value(value: object) -> str:
    """Normalize null, empty, and whitespace-only values to unknown."""
    if value is None:
        return "unknown"
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned if cleaned else "unknown"
    return str(value)


def resolve_artifact_path(filename: str) -> Path:
    """Locate an artifact from the Lambda package or local ml/artifacts folder."""
    lambda_path = LAMBDA_ARTIFACTS_DIR / filename
    if lambda_path.exists():
        return lambda_path

    local_path = LOCAL_ARTIFACTS_DIR / filename
    if local_path.exists():
        return local_path

    raise FileNotFoundError(f"Could not find artifact: {filename}")


def load_pickle(path: Path) -> Any:
    """Load a pickle artifact from disk."""
    with path.open("rb") as artifact_file:
        return pickle.load(artifact_file)


def load_artifacts() -> tuple[dict[str, Any], dict[str, list[str]]]:
    """Load and cache the model plus encoders for repeated Lambda invokes."""
    global _MODEL, _ENCODERS

    if _MODEL is None:
        model_path = resolve_artifact_path(MODEL_FILENAME)
        print(f"Loading model artifact from {model_path}")
        _MODEL = load_pickle(model_path)

    if _ENCODERS is None:
        encoders_path = resolve_artifact_path(ENCODERS_FILENAME)
        print(f"Loading encoder artifact from {encoders_path}")
        _ENCODERS = load_pickle(encoders_path)

    return _MODEL, _ENCODERS


class RequestValidationError(ValueError):
    """Raised when required GET query parameters are missing."""


def clean_required_query_value(
    query_parameters: dict[str, Any],
    parameter_name: str,
) -> str:
    """Read and URL-decode one required query parameter."""
    raw_value = query_parameters.get(parameter_name)
    if raw_value is None:
        raise RequestValidationError(f"Missing required query parameter: {parameter_name}.")

    cleaned_value = clean_feature_value(unquote(raw_value) if isinstance(raw_value, str) else raw_value)
    if cleaned_value == "unknown":
        raise RequestValidationError(f"Missing required query parameter: {parameter_name}.")

    return cleaned_value


def parse_query_parameters(event: dict[str, Any]) -> dict[str, Any]:
    """Read model inputs from GET query string parameters only."""
    query_parameters = event.get("queryStringParameters")
    if not query_parameters:
        raise RequestValidationError(
            "Missing query parameters. Required: domain_type and country."
        )

    return {
        "domain_type": clean_required_query_value(query_parameters, "domain_type"),
        "country": clean_required_query_value(query_parameters, "country"),
    }


def get_unknown_fallback(classes: list[str]) -> str:
    """Return the unknown class if the encoder supports it."""
    if "unknown" not in classes:
        raise ValueError("Encoder does not contain an 'unknown' fallback class.")
    return "unknown"


def encode_feature_value(feature_name: str, raw_value: object, encoders: dict[str, list[str]]) -> int:
    """Safely encode one feature value, falling back to unknown when needed."""
    classes = encoders[feature_name]
    cleaned_value = clean_feature_value(raw_value)

    if cleaned_value not in classes:
        fallback_value = get_unknown_fallback(classes)
        print(
            f"Unseen value for {feature_name!r}: {cleaned_value!r}. "
            f"Falling back to {fallback_value!r}."
        )
        cleaned_value = fallback_value

    return classes.index(cleaned_value)


def build_feature_vector(payload: dict[str, Any], encoders: dict[str, list[str]]) -> list[int]:
    """Convert the incoming JSON payload into an encoded model feature vector."""
    return [
        encode_feature_value("domain_type", payload.get("domain_type"), encoders),
        encode_feature_value("country", payload.get("country"), encoders),
    ]


def argmax(values: list[float]) -> int:
    """Return the index of the largest class score."""
    best_index = 0
    best_value = values[0]
    for index, value in enumerate(values[1:], start=1):
        if value > best_value:
            best_index = index
            best_value = value
    return best_index


def walk_tree(model: dict[str, Any], encoded_features: list[int]) -> int:
    """Evaluate the saved decision tree without a scikit-learn runtime."""
    children_left = model["children_left"]
    children_right = model["children_right"]
    features = model["feature"]
    thresholds = model["threshold"]
    values = model["value"]

    node_index = 0
    while features[node_index] != LEAF_NODE:
        feature_index = features[node_index]
        threshold = thresholds[node_index]
        if encoded_features[feature_index] <= threshold:
            node_index = children_left[node_index]
        else:
            node_index = children_right[node_index]

    leaf_scores = values[node_index][0]
    return argmax(leaf_scores)


def decode_prediction(prediction: int, encoders: dict[str, list[str]]) -> str:
    """Decode the model output class into a readable affiliation category."""
    target_classes = encoders["affiliation_category"]
    return target_classes[prediction]


def predict_affiliation_category(payload: dict[str, Any]) -> str:
    """Run a single prediction from cleaned request data."""
    model, encoders = load_artifacts()
    encoded_features = build_feature_vector(payload, encoders)
    encoded_prediction = walk_tree(model, encoded_features)
    return decode_prediction(encoded_prediction, encoders)


def success_response(affiliation_category: str) -> dict[str, Any]:
    """Format a successful JSON response body."""
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"affiliation_category": affiliation_category}),
    }


def error_response(message: str, status_code: int = 400) -> dict[str, Any]:
    """Format a safe JSON error response body."""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": message}),
    }


def lambda_handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Handle inference requests for the Institution Type Classifier."""
    try:
        payload = parse_query_parameters(event)
        affiliation_category = predict_affiliation_category(payload)
    except RequestValidationError as exc:
        return error_response(str(exc))
    except FileNotFoundError as exc:
        print(str(exc))
        return error_response("Model artifacts are not available.", status_code=500)
    except Exception as exc:
        print(f"Inference Lambda failed: {exc}")
        return error_response("Inference request failed.", status_code=500)

    return success_response(affiliation_category)


if __name__ == "__main__":
    test_events = [
        {
            "queryStringParameters": {
                "domain_type": "commercial",
                "country": "United%20States",
            },
        },
        {
            "queryStringParameters": {
                "domain_type": "education",
                "country": "United%20States",
            },
        },
        {
            "queryStringParameters": {
                "domain_type": "international",
                "country": "United%20States",
            },
        },
    ]

    for test_event in test_events:
        print(lambda_handler(test_event, None))
