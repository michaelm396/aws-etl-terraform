from __future__ import annotations
"""Project runner for packaging Lambdas, applying Terraform, and starting ETL."""

import argparse
import importlib.metadata
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
TERRAFORM_DIR = PROJECT_ROOT / "terraform"
EXTRACT_SCRIPT = PROJECT_ROOT / "extract.py"
PACKAGE_LAMBDA_SCRIPT = PROJECT_ROOT / "scripts" / "package_lambda.py"
PACKAGE_INFERENCE_LAMBDA_SCRIPT = PROJECT_ROOT / "scripts" / "package_inference_lambda.py"
DEFAULT_VAR_FILE = TERRAFORM_DIR / "terraform.tfvars"
REQUIREMENTS_FILE = PROJECT_ROOT / "requirements.txt"
DEFAULT_PLAN_FILE = "tfplan"


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser for the deployment runner."""
    parser = argparse.ArgumentParser(
        description="Run Terraform commands for the S3 bucket project."
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="deploy",
        choices=["init", "plan", "apply", "deploy"],
        help=(
            "Terraform action to run. Defaults to 'deploy', which runs "
            "init, plan, and apply in sequence."
        ),
    )
    parser.add_argument(
        "--var-file",
        default=str(DEFAULT_VAR_FILE),
        help="Path to the Terraform variable file.",
    )
    parser.add_argument(
        "--plan-file",
        default=DEFAULT_PLAN_FILE,
        help="Path to the Terraform plan output file.",
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="Skip the approval prompt during terraform apply.",
    )
    parser.add_argument(
        "--profile",
        "--aws-profile",
        dest="profile",
        default=os.environ.get("AWS_PROFILE"),
        help="Optional AWS profile name to use for Terraform and AWS CLI commands.",
    )
    return parser


def print_system_install_instructions(missing_commands: list[str]) -> None:
    """Print OS-specific install guidance for missing system commands."""
    print(
        "Missing required system command(s): "
        f"{', '.join(missing_commands)}",
        file=sys.stderr,
    )
    print("Install the missing tools, then rerun this script.", file=sys.stderr)

    system_name = platform.system().lower()
    if system_name == "darwin":
        if shutil.which("brew"):
            print("\nmacOS with Homebrew:", file=sys.stderr)
            if "terraform" in missing_commands:
                print("  brew install terraform", file=sys.stderr)
            if "aws" in missing_commands:
                print("  brew install awscli", file=sys.stderr)
        else:
            print(
                "\nmacOS: install Terraform and AWS CLI from their official installers.",
                file=sys.stderr,
            )
    elif system_name == "windows":
        print("\nWindows with winget:", file=sys.stderr)
        if "terraform" in missing_commands:
            print("  winget install Hashicorp.Terraform", file=sys.stderr)
        if "aws" in missing_commands:
            print("  winget install Amazon.AWSCLI", file=sys.stderr)
        print(
            "If winget is unavailable, use the official Terraform and AWS CLI installers.",
            file=sys.stderr,
        )
    elif system_name == "linux":
        print(
            "\nLinux: install Terraform from HashiCorp's official repository or "
            "package instructions.",
            file=sys.stderr,
        )
        print(
            "Linux: install AWS CLI from the AWS official Linux installer.",
            file=sys.stderr,
        )
    else:
        print(
            "\nInstall Terraform and AWS CLI from their official installers.",
            file=sys.stderr,
        )


def parse_requirement_name(raw_line: str) -> str | None:
    """Extract a package name from a simple requirements.txt line."""
    stripped = raw_line.strip()
    if not stripped or stripped.startswith("#") or stripped.startswith("-"):
        return None

    package_name = stripped
    for separator in ("==", ">=", "<=", "~=", "!=", ">", "<"):
        package_name = package_name.split(separator, 1)[0]
    package_name = package_name.split("[", 1)[0].strip()
    return package_name or None


def missing_python_dependencies(requirements_file: Path) -> list[str]:
    """Return missing top-level Python dependencies from requirements.txt."""
    if not requirements_file.exists():
        return []

    missing = []
    for raw_line in requirements_file.read_text().splitlines():
        package_name = parse_requirement_name(raw_line)
        if package_name is None:
            continue
        try:
            importlib.metadata.version(package_name)
        except importlib.metadata.PackageNotFoundError:
            missing.append(package_name)

    return missing


def install_python_dependencies(requirements_file: Path) -> None:
    """Install project Python dependencies into this interpreter environment."""
    run_command(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "-r",
            str(requirements_file),
        ],
        cwd=PROJECT_ROOT,
    )


def ensure_python_dependencies() -> None:
    """Offer to install missing Python dependencies into the active interpreter."""
    missing = missing_python_dependencies(REQUIREMENTS_FILE)
    if not missing:
        return

    print(f"Missing Python dependencies: {', '.join(missing)}")
    try:
        answer = input("Python dependencies are missing. Install them now? [y/N] ")
    except EOFError:
        answer = ""
    if answer.strip().lower() in {"y", "yes"}:
        install_python_dependencies(REQUIREMENTS_FILE)
        return

    print(
        "Python dependencies were not installed. Install them with:\n"
        f"  {sys.executable} -m pip install -r {REQUIREMENTS_FILE}",
        file=sys.stderr,
    )
    sys.exit(1)


def ensure_prerequisites(var_file: Path) -> None:
    """Fail early if the local machine is missing required files or CLIs."""
    if not TERRAFORM_DIR.exists():
        print(f"Terraform directory not found: {TERRAFORM_DIR}", file=sys.stderr)
        sys.exit(1)

    missing_commands = [
        command for command in ("terraform", "aws") if shutil.which(command) is None
    ]
    if missing_commands:
        print_system_install_instructions(missing_commands)
        sys.exit(1)

    if not var_file.exists():
        print(f"Terraform variable file not found: {var_file}", file=sys.stderr)
        sys.exit(1)

    if not EXTRACT_SCRIPT.exists():
        print(f"Extract script not found: {EXTRACT_SCRIPT}", file=sys.stderr)
        sys.exit(1)

    if not PACKAGE_LAMBDA_SCRIPT.exists():
        print(
            f"Lambda packaging script not found: {PACKAGE_LAMBDA_SCRIPT}",
            file=sys.stderr,
        )
        sys.exit(1)

    ensure_python_dependencies()

    if not PACKAGE_INFERENCE_LAMBDA_SCRIPT.exists():
        print(
            f"Inference Lambda packaging script not found: {PACKAGE_INFERENCE_LAMBDA_SCRIPT}",
            file=sys.stderr,
        )
        sys.exit(1)


def run_command(
    command: list[str],
    *,
    cwd: Path = TERRAFORM_DIR,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess and print its captured output in a readable way."""
    print(f"\n==> Running: {' '.join(command)}")

    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            check=True,
            text=True,
            capture_output=True,
            env=env,
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

    return completed


def validate_aws_identity(profile_name: str | None) -> bool:
    """Check that the selected AWS credentials can call STS successfully."""
    env = os.environ.copy()
    if profile_name:
        env["AWS_PROFILE"] = profile_name
    result = subprocess.run(
        ["aws", "sts", "get-caller-identity"],
        text=True,
        capture_output=True,
        env=env,
    )
    return result.returncode == 0


def ensure_aws_access(profile_name: str | None) -> dict[str, str]:
    """Validate AWS authentication before any Terraform or ETL work begins."""
    env = os.environ.copy()
    if profile_name:
        env["AWS_PROFILE"] = profile_name

    if validate_aws_identity(profile_name):
        if profile_name:
            print(f"AWS profile '{profile_name}' is authenticated.")
        else:
            print("AWS credentials are authenticated.")
        return env

    print("\nAWS authentication is required before Terraform can provision resources.")
    if profile_name:
        print(f"Selected AWS profile: {profile_name}")

    print("Configure AWS authentication, then rerun this script.", file=sys.stderr)
    if profile_name:
        print(f"  aws configure --profile {profile_name}", file=sys.stderr)
    else:
        print("  aws configure", file=sys.stderr)
    print("Or set environment variables:", file=sys.stderr)
    print("  AWS_ACCESS_KEY_ID", file=sys.stderr)
    print("  AWS_SECRET_ACCESS_KEY", file=sys.stderr)
    print("  AWS_DEFAULT_REGION", file=sys.stderr)
    sys.exit(1)


def terraform_init(env: dict[str, str]) -> None:
    """Initialize the Terraform working directory."""
    run_command(["terraform", "init"], env=env)


def terraform_plan(var_file: Path, plan_file: str, env: dict[str, str]) -> None:
    """Create a Terraform plan file using the selected tfvars file."""
    run_command(
        [
            "terraform",
            "plan",
            f"-var-file={var_file}",
            f"-out={plan_file}",
        ],
        env=env,
    )


def terraform_apply(
    plan_file: str,
    auto_approve: bool,
    env: dict[str, str],
) -> None:
    """Apply a previously-created Terraform plan."""
    command = ["terraform", "apply"]
    if auto_approve:
        command.append("-auto-approve")
    command.append(plan_file)
    run_command(command, env=env)


def terraform_output(name: str, env: dict[str, str]) -> str:
    """Read a single Terraform output value."""
    completed = run_command(
        ["terraform", "output", "-raw", name],
        cwd=TERRAFORM_DIR,
        env=env,
    )
    return completed.stdout.strip()


def run_etl(env: dict[str, str]) -> None:
    """Run the extract stage that uploads the workbook to S3."""
    run_command([sys.executable, str(EXTRACT_SCRIPT)], cwd=PROJECT_ROOT, env=env)


def package_lambda(env: dict[str, str]) -> None:
    """Package the Lambda deployment ZIPs before planning or applying."""
    run_command([sys.executable, str(PACKAGE_LAMBDA_SCRIPT)], cwd=PROJECT_ROOT, env=env)


def package_inference_lambda(env: dict[str, str]) -> None:
    """Package the inference Lambda deployment ZIP before Terraform runs."""
    run_command(
        [sys.executable, str(PACKAGE_INFERENCE_LAMBDA_SCRIPT)],
        cwd=PROJECT_ROOT,
        env=env,
    )


def package_lambdas(env: dict[str, str]) -> None:
    """Package all Lambda deployment ZIPs required by Terraform."""
    package_lambda(env)
    package_inference_lambda(env)


def print_summary(env: dict[str, str]) -> None:
    """Print the core AWS artifacts created by the deployment flow."""
    bucket_name = terraform_output("bucket_name", env)
    bucket_arn = terraform_output("bucket_arn", env)
    db_endpoint = terraform_output("db_endpoint", env)
    db_name = terraform_output("db_name", env)
    transform_lambda = terraform_output("lambda_function_name", env)
    loader_lambda = terraform_output("loader_lambda_function_name", env)
    inference_lambda = terraform_output("inference_lambda_function_name", env)
    inference_api_url = terraform_output("inference_api_url", env)
    inference_api_get_format = (
        f"{inference_api_url}?domain_type={{domain_type}}&country={{country}}"
    )
    commercial_example_command = (
        f'curl "{inference_api_url}?domain_type=commercial&country=United%20States"'
    )
    education_example_path = "/predict?domain_type=education&country=United%20States"
    international_example_path = (
        "/predict?domain_type=international&country=United%20States"
    )
    raw_uri = f"s3://{bucket_name}/raw/SRDataEngineerChallenge_DATASET.xlsx"
    processed_uri = (
        f"s3://{bucket_name}/processed/"
        "SRDataEngineerChallenge_DATASET_transformed.csv"
    )

    print("\nProvisioning summary:")
    print(f"Bucket: {bucket_name}")
    print(f"Bucket ARN: {bucket_arn}")
    print(f"Raw file: {raw_uri}")
    print(f"Processed file: {processed_uri}")
    print(f"Transform Lambda: {transform_lambda}")
    print(f"Loader Lambda: {loader_lambda}")
    print(f"Inference Lambda: {inference_lambda}")
    print(f"Inference API URL: {inference_api_url}")
    print(f"RDS endpoint: {db_endpoint}")
    print(f"RDS database: {db_name}")

    print("\nInference API GET endpoint:")
    print(inference_api_url)
    print("\nInference API endpoint format:")
    print(inference_api_get_format)
    print("\nRequired parameters:")
    print(
        "- domain_type: commercial, education, government, organization, "
        "personal_provider, international, or unknown"
    )
    print(
        "- country: country name, URL-encoded if it contains spaces, "
        "e.g. United%20States"
    )
    print("\nReady-to-use curl command:")
    print(commercial_example_command)
    print("\nExpected response:")
    print('{\n  "affiliation_category": "business"\n}')
    print("\nAdditional examples:")
    print(
        f"- {education_example_path} -> "
        '{"affiliation_category":"public_sector"}'
    )
    print(
        f"- {international_example_path} -> "
        '{"affiliation_category":"non_institutional"}'
    )
    print("\nNote: %20 is URL encoding for a space.")
    print("United%20States is interpreted as United States.")


def main() -> None:
    """Entry point for the project deployment runner."""
    parser = build_parser()
    args = parser.parse_args()

    var_file = Path(args.var_file).expanduser().resolve()
    ensure_prerequisites(var_file)
    terraform_env = os.environ.copy()

    if args.command in {"plan", "apply", "deploy"}:
        terraform_env = ensure_aws_access(args.profile)

    # Keep the branch logic explicit so each CLI mode is easy to follow during
    # handoff and debugging.
    if args.command == "init":
        terraform_init(terraform_env)
    elif args.command == "plan":
        package_lambdas(terraform_env)
        terraform_init(terraform_env)
        terraform_plan(var_file, args.plan_file, terraform_env)
    elif args.command == "apply":
        package_lambdas(terraform_env)
        terraform_init(terraform_env)
        terraform_plan(var_file, args.plan_file, terraform_env)
        terraform_apply(args.plan_file, args.auto_approve, terraform_env)
        run_etl(terraform_env)
        print_summary(terraform_env)
    else:
        package_lambdas(terraform_env)
        terraform_init(terraform_env)
        terraform_plan(var_file, args.plan_file, terraform_env)
        terraform_apply(args.plan_file, args.auto_approve, terraform_env)
        run_etl(terraform_env)
        print_summary(terraform_env)

    print("Terraform command completed successfully.")


if __name__ == "__main__":
    main()
