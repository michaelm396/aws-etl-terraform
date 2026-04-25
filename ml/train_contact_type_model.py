from __future__ import annotations
"""Train the Institution Type Classifier locally with scikit-learn.

The script expects a pandas DataFrame containing at least `domain_type` and
`country`, derives the `affiliation_category` label from the same business
rules used in the ETL transform, encodes the categorical features, trains a
DecisionTreeClassifier, and saves lightweight inference artifacts that can run
in AWS Lambda without bundling the full scikit-learn runtime.
"""

import argparse
import pickle
from pathlib import Path

import pandas as pd
from sklearn.preprocessing import LabelEncoder
from sklearn.tree import DecisionTreeClassifier


ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"
MODEL_PATH = ARTIFACTS_DIR / "model.pkl"
ENCODERS_PATH = ARTIFACTS_DIR / "encoders.pkl"
REQUIRED_COLUMNS = {"domain_type", "country"}


def clean_missing_feature(value: object) -> str:
    """Standardize feature nulls to the shared unknown category for training."""
    if value is None or pd.isna(value):
        return "unknown"
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned if cleaned else "unknown"
    return str(value)


def build_affiliation_category(domain_type: str) -> str:
    """Create the training label from the engineered domain_type feature."""
    if domain_type == "commercial":
        return "business"
    if domain_type in {"education", "government"}:
        return "public_sector"
    return "non_institutional"


def fill_missing_features(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Replace null feature values with the shared unknown category."""
    cleaned = dataframe.copy()
    cleaned.loc[:, "domain_type"] = cleaned["domain_type"].apply(clean_missing_feature)
    cleaned.loc[:, "country"] = cleaned["country"].apply(clean_missing_feature)
    return cleaned


def prepare_training_dataframe(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Validate features, standardize nulls, and derive the target column."""
    missing_columns = REQUIRED_COLUMNS.difference(dataframe.columns)
    if missing_columns:
        missing_display = ", ".join(sorted(missing_columns))
        raise ValueError(f"Training dataframe is missing required column(s): {missing_display}")

    # domain_type is engineered from email_domain in the ETL transform, while
    # country comes from IP-derived geolocation. affiliation_category is the
    # model's readable prediction target.
    prepared = fill_missing_features(dataframe[["domain_type", "country"]])
    prepared.loc[:, "affiliation_category"] = prepared["domain_type"].apply(
        build_affiliation_category
    )
    return prepared


def fit_label_encoders(dataframe: pd.DataFrame) -> dict[str, LabelEncoder]:
    """Fit one LabelEncoder per categorical feature and for the target."""
    encoders = {
        "domain_type": LabelEncoder(),
        "country": LabelEncoder(),
        "affiliation_category": LabelEncoder(),
    }
    encoders["domain_type"].fit(dataframe["domain_type"])
    encoders["country"].fit(dataframe["country"])
    encoders["affiliation_category"].fit(dataframe["affiliation_category"])
    return encoders


def encode_training_data(
    dataframe: pd.DataFrame,
    encoders: dict[str, LabelEncoder],
) -> tuple[pd.DataFrame, pd.Series]:
    """Encode the feature columns and target column for model training."""
    feature_frame = pd.DataFrame(
        {
            "domain_type": encoders["domain_type"].transform(dataframe["domain_type"]),
            "country": encoders["country"].transform(dataframe["country"]),
        }
    )
    target = pd.Series(
        encoders["affiliation_category"].transform(dataframe["affiliation_category"]),
        name="affiliation_category",
    )
    return feature_frame, target


def train_model(features: pd.DataFrame, target: pd.Series) -> DecisionTreeClassifier:
    """Fit the decision tree classifier."""
    model = DecisionTreeClassifier(random_state=42)
    model.fit(features, target)
    return model


def predict_affiliation_category(
    model: DecisionTreeClassifier,
    encoders: dict[str, LabelEncoder],
    domain_type: str,
    country: str,
) -> str:
    """Run a sample prediction and decode the label output."""
    encoded_features = pd.DataFrame(
        {
            "domain_type": [
                encoders["domain_type"].transform([clean_missing_feature(domain_type)])[0]
            ],
            "country": [encoders["country"].transform([clean_missing_feature(country)])[0]],
        }
    )
    encoded_prediction = model.predict(encoded_features)[0]
    return str(
        encoders["affiliation_category"].inverse_transform([encoded_prediction])[0]
    )


def save_artifacts(
    model: DecisionTreeClassifier,
    encoders: dict[str, LabelEncoder],
) -> None:
    """Persist lightweight inference artifacts to pickle files.

    The local training step still uses scikit-learn, but the saved artifacts are
    plain Python structures so Lambda inference can stay compact and portable.
    """
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    model_artifact = {
        "feature_names": ["domain_type", "country"],
        "children_left": model.tree_.children_left.tolist(),
        "children_right": model.tree_.children_right.tolist(),
        "feature": model.tree_.feature.tolist(),
        "threshold": model.tree_.threshold.tolist(),
        "value": model.tree_.value.tolist(),
    }
    encoders_artifact = {
        name: encoder.classes_.tolist() for name, encoder in encoders.items()
    }
    with MODEL_PATH.open("wb") as model_file:
        pickle.dump(model_artifact, model_file)
    with ENCODERS_PATH.open("wb") as encoders_file:
        pickle.dump(encoders_artifact, encoders_file)


def load_training_dataframe(csv_path: Path | None) -> pd.DataFrame:
    """Load a training dataframe from CSV or fall back to a small demo dataset."""
    if csv_path is not None:
        return pd.read_csv(csv_path)

    return pd.DataFrame(
        [
            {"domain_type": "commercial", "country": "United States"},
            {"domain_type": "education", "country": "United States"},
            {"domain_type": "government", "country": "Canada"},
            {"domain_type": "organization", "country": "Germany"},
            {"domain_type": "international", "country": None},
            {"domain_type": None, "country": "France"},
            {"domain_type": "personal_provider", "country": "United States"},
        ]
    )


def parse_args() -> argparse.Namespace:
    """Parse optional CLI arguments for local training."""
    parser = argparse.ArgumentParser(
        description="Train the Institution Type Classifier."
    )
    parser.add_argument(
        "--input-csv",
        type=Path,
        default=None,
        help="Optional CSV file containing at least domain_type and country columns.",
    )
    return parser.parse_args()


def main() -> None:
    """Train the model, test the sample prediction, and save artifacts."""
    args = parse_args()
    raw_dataframe = load_training_dataframe(args.input_csv)
    training_dataframe = prepare_training_dataframe(raw_dataframe)
    encoders = fit_label_encoders(training_dataframe)
    features, target = encode_training_data(training_dataframe, encoders)
    model = train_model(features, target)

    test_cases = [
        {"domain_type": "commercial", "country": "United States"},
        {"domain_type": "education", "country": "United States"},
        {"domain_type": "international", "country": "United States"},
    ]
    for test_case in test_cases:
        prediction = predict_affiliation_category(
            model=model,
            encoders=encoders,
            domain_type=str(test_case["domain_type"]),
            country=str(test_case["country"]),
        )
        print(
            "Prediction "
            f"{test_case} -> "
            f'{{"affiliation_category": "{prediction}"}}'
        )

    save_artifacts(model, encoders)
    print(f"Saved model to {MODEL_PATH}")
    print(f"Saved encoders to {ENCODERS_PATH}")


if __name__ == "__main__":
    main()
