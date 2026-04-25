from __future__ import annotations
"""Build deployment ZIPs for the Lambda-based transform and load stages."""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
LAMBDA_ROOT = PROJECT_ROOT / "lambdas"
BUILD_ROOT = PROJECT_ROOT / "build"
PACKAGES = [
    {
        "source_dir": LAMBDA_ROOT / "transform",
        "handler": "handler.py",
        "requirements": "requirements.txt",
        "zip_name": "gender_transform_lambda.zip",
        "exclude_requirements": ["pandas"],
    },
    {
        "source_dir": LAMBDA_ROOT / "load",
        "handler": "handler.py",
        "requirements": "requirements.txt",
        "zip_name": "rds_loader_lambda.zip",
    },
]


def run_command(command: list[str]) -> None:
    """Run a build command and surface stdout/stderr clearly on failure."""
    try:
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
    except FileNotFoundError:
        print(f"Command not found: {command[0]}", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as exc:
        if exc.stdout:
            print(exc.stdout)
        if exc.stderr:
            print(exc.stderr, file=sys.stderr)
        print(
            f"Command failed with exit code {exc.returncode}: {' '.join(command)}",
            file=sys.stderr,
        )
        sys.exit(exc.returncode)

    if completed.stdout:
        print(completed.stdout)
    if completed.stderr:
        print(completed.stderr, file=sys.stderr)


def package_lambda(definition: dict[str, str]) -> None:
    """Install dependencies into an isolated build folder and zip the Lambda."""
    source_dir = Path(definition["source_dir"])
    handler_file = source_dir / definition["handler"]
    requirements_file = source_dir / definition["requirements"]
    build_dir = BUILD_ROOT / source_dir.name
    package_dir = build_dir / "package"
    zip_path = BUILD_ROOT / definition["zip_name"]

    if not handler_file.exists():
        print(f"Lambda handler not found: {handler_file}", file=sys.stderr)
        sys.exit(1)

    if not requirements_file.exists():
        print(f"Lambda requirements not found: {requirements_file}", file=sys.stderr)
        sys.exit(1)

    if build_dir.exists():
        shutil.rmtree(build_dir)

    package_dir.mkdir(parents=True, exist_ok=True)

    requirements_source = requirements_file
    excluded_packages = set(definition.get("exclude_requirements", []))
    temp_requirements_file: Path | None = None

    if excluded_packages:
        filtered_lines = []
        for raw_line in requirements_file.read_text().splitlines():
            stripped = raw_line.strip()
            package_name = stripped.split("==")[0].strip().lower() if stripped else ""
            if package_name and package_name not in excluded_packages:
                filtered_lines.append(raw_line)

        temp_file = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".txt",
            delete=False,
        )
        temp_file.write("\n".join(filtered_lines) + "\n")
        temp_file.close()
        temp_requirements_file = Path(temp_file.name)
        requirements_source = temp_requirements_file

    # Package dependencies into the deployment directory instead of the local
    # Python environment so the repo stays lightweight for handoff.
    run_command(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "-r",
            str(requirements_source),
            "--target",
            str(package_dir),
        ]
    )

    if temp_requirements_file is not None and temp_requirements_file.exists():
        temp_requirements_file.unlink()

    shutil.copy2(handler_file, package_dir / "handler.py")

    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()

    shutil.make_archive(
        base_name=str(zip_path.with_suffix("")),
        format="zip",
        root_dir=str(package_dir),
    )

    print(f"Lambda package created: {zip_path}")


def main() -> None:
    """Package every Lambda defined in PACKAGES."""
    for definition in PACKAGES:
        package_lambda(definition)


if __name__ == "__main__":
    main()
