from __future__ import annotations
"""Build the deployment ZIP for the inference Lambda.

The inference runtime uses lightweight plain-Python artifacts, so the Lambda
package only needs the handler and model files.
"""

import shutil
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
INFERENCE_ROOT = PROJECT_ROOT / "lambdas" / "inference"
MODEL_ARTIFACTS_ROOT = PROJECT_ROOT / "ml" / "artifacts"
BUILD_ROOT = PROJECT_ROOT / "build"
BUILD_DIR = BUILD_ROOT / "inference"
FUNCTION_DIR = BUILD_DIR / "function"

FUNCTION_ZIP_PATH = BUILD_ROOT / "inference_lambda.zip"
STALE_ZIP_PATHS = [
    BUILD_ROOT / "inference_core_layer.zip",
    BUILD_ROOT / "inference_scipy_layer.zip",
]

HANDLER_FILE = INFERENCE_ROOT / "handler.py"
REQUIREMENTS_FILE = INFERENCE_ROOT / "requirements.txt"
MODEL_FILE = MODEL_ARTIFACTS_ROOT / "model.pkl"
ENCODERS_FILE = MODEL_ARTIFACTS_ROOT / "encoders.pkl"
UNNEEDED_GLOBS = ["__pycache__"]


def ensure_inputs_exist() -> None:
    """Fail early if the handler, requirements, or model artifacts are missing."""
    required_paths = [
        HANDLER_FILE,
        REQUIREMENTS_FILE,
        MODEL_FILE,
        ENCODERS_FILE,
    ]
    for path in required_paths:
        if not path.exists():
            print(f"Required inference packaging input not found: {path}", file=sys.stderr)
            sys.exit(1)


def prune_directory(root: Path) -> None:
    """Remove runtime-irrelevant files from an artifact tree."""
    for pattern in UNNEEDED_GLOBS:
        for path in root.rglob(pattern):
            if not path.exists():
                continue
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()


def make_zip(zip_path: Path, root_dir: Path) -> None:
    """Create a ZIP file from a root directory, replacing any previous build."""
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()

    shutil.make_archive(
        base_name=str(zip_path.with_suffix("")),
        format="zip",
        root_dir=str(root_dir),
    )


def main() -> None:
    """Package the lightweight inference Lambda into build/inference_lambda.zip."""
    ensure_inputs_exist()

    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    for stale_zip_path in STALE_ZIP_PATHS:
        if stale_zip_path.exists():
            stale_zip_path.unlink()

    FUNCTION_DIR.mkdir(parents=True, exist_ok=True)

    shutil.copy2(HANDLER_FILE, FUNCTION_DIR / "handler.py")
    shutil.copy2(MODEL_FILE, FUNCTION_DIR / "model.pkl")
    shutil.copy2(ENCODERS_FILE, FUNCTION_DIR / "encoders.pkl")
    prune_directory(FUNCTION_DIR)
    make_zip(FUNCTION_ZIP_PATH, FUNCTION_DIR)
    print(f"Inference Lambda package created: {FUNCTION_ZIP_PATH}")


if __name__ == "__main__":
    main()
