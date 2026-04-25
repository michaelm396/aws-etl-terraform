from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INFERENCE_ROOT = PROJECT_ROOT / "lambdas" / "inference"
sys.path.insert(0, str(INFERENCE_ROOT))

import handler  # noqa: E402


def invoke(query_parameters: dict[str, object] | None) -> dict[str, object]:
    return handler.lambda_handler({"queryStringParameters": query_parameters}, None)


def response_body(response: dict[str, object]) -> dict[str, object]:
    return json.loads(str(response["body"]))


class InferenceHandlerTests(unittest.TestCase):
    def test_valid_commercial_request(self) -> None:
        response = invoke(
            {"domain_type": "commercial", "country": "United%20States"}
        )

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(response_body(response)["affiliation_category"], "business")

    def test_valid_education_request(self) -> None:
        response = invoke(
            {"domain_type": "education", "country": "United%20States"}
        )

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(
            response_body(response)["affiliation_category"],
            "public_sector",
        )

    def test_valid_international_request(self) -> None:
        response = invoke(
            {"domain_type": "international", "country": "United%20States"}
        )

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(
            response_body(response)["affiliation_category"],
            "non_institutional",
        )

    def test_missing_domain_type(self) -> None:
        response = invoke({"country": "United%20States"})

        self.assertEqual(response["statusCode"], 400)
        self.assertEqual(
            response_body(response)["error"],
            "Missing required query parameter: domain_type.",
        )

    def test_missing_country(self) -> None:
        response = invoke({"domain_type": "commercial"})

        self.assertEqual(response["statusCode"], 400)
        self.assertEqual(
            response_body(response)["error"],
            "Missing required query parameter: country.",
        )

    def test_missing_query_string_parameters(self) -> None:
        response = invoke(None)

        self.assertEqual(response["statusCode"], 400)
        self.assertEqual(
            response_body(response)["error"],
            "Missing query parameters. Required: domain_type and country.",
        )


if __name__ == "__main__":
    unittest.main()
