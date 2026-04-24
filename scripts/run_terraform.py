from __future__ import annotations
"""Project runner for packaging Lambdas, applying Terraform, and starting ETL."""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
TERRAFORM_DIR = PROJECT_ROOT / "terraform"
EXTRACT_SCRIPT = PROJECT_ROOT / "extract.py"
PACKAGE_LAMBDA_SCRIPT = PROJECT_ROOT / "scripts" / "package_lambda.py"
DEFAULT_VAR_FILE = TERRAFORM_DIR / "terraform.tfvars"
DEFAULT_PLAN_FILE = "tfplan"
DEFAULT_AWS_PROFILE = "default"


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
        "--aws-profile",
        default=os.environ.get("AWS_PROFILE", DEFAULT_AWS_PROFILE),
        help="AWS profile name to use for Terraform commands.",
    )
    return parser


def ensure_prerequisites(var_file: Path) -> None:
    """Fail early if the local machine is missing required files or CLIs."""
    if not TERRAFORM_DIR.exists():
        print(f"Terraform directory not found: {TERRAFORM_DIR}", file=sys.stderr)
        sys.exit(1)

    if shutil.which("terraform") is None:
        print(
            "Terraform CLI is not installed or not available in PATH.",
            file=sys.stderr,
        )
        print("Install it first. On macOS with Homebrew:", file=sys.stderr)
        print("  brew install hashicorp/tap/terraform", file=sys.stderr)
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


def aws_cli_available() -> bool:
    """Return whether the AWS CLI is available on the current machine."""
    return shutil.which("aws") is not None


def validate_aws_identity(profile_name: str) -> bool:
    """Check that the selected AWS profile can call STS successfully."""
    if not aws_cli_available():
        return False

    env = os.environ.copy()
    env["AWS_PROFILE"] = profile_name
    result = subprocess.run(
        ["aws", "sts", "get-caller-identity"],
        text=True,
        capture_output=True,
        env=env,
    )
    return result.returncode == 0


def ensure_aws_access(profile_name: str) -> dict[str, str]:
    """Validate AWS authentication before any Terraform or ETL work begins."""
    env = os.environ.copy()
    env["AWS_PROFILE"] = profile_name

    if validate_aws_identity(profile_name):
        print(f"AWS profile '{profile_name}' is authenticated.")
        return env

    print("\nAWS authentication is required before Terraform can provision resources.")
    print(f"Selected AWS profile: {profile_name}")

    if not aws_cli_available():
        print("AWS CLI is not installed or not available in PATH.", file=sys.stderr)
        print("Install it first. On macOS with Homebrew:", file=sys.stderr)
        print("  brew install awscli", file=sys.stderr)
        sys.exit(1)

    print("Configure AWS authentication, then rerun this script.", file=sys.stderr)
    print(
        f"Recommended: aws configure --profile {profile_name}",
        file=sys.stderr,
    )
    print(
        f"Alternative: aws sso login --profile {profile_name}",
        file=sys.stderr,
    )
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
    run_command(["python3", str(EXTRACT_SCRIPT)], cwd=PROJECT_ROOT, env=env)


def package_lambda(env: dict[str, str]) -> None:
    """Package the Lambda deployment ZIPs before planning or applying."""
    run_command(["python3", str(PACKAGE_LAMBDA_SCRIPT)], cwd=PROJECT_ROOT, env=env)


def print_summary(env: dict[str, str]) -> None:
    """Print the core AWS artifacts created by the deployment flow."""
    bucket_name = terraform_output("bucket_name", env)
    bucket_arn = terraform_output("bucket_arn", env)
    db_endpoint = terraform_output("db_endpoint", env)
    db_name = terraform_output("db_name", env)
    transform_lambda = terraform_output("lambda_function_name", env)
    loader_lambda = terraform_output("loader_lambda_function_name", env)
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
    print(f"RDS endpoint: {db_endpoint}")
    print(f"RDS database: {db_name}")


def main() -> None:
    """Entry point for the project deployment runner."""
    parser = build_parser()
    args = parser.parse_args()

    var_file = Path(args.var_file).expanduser().resolve()
    ensure_prerequisites(var_file)
    terraform_env = os.environ.copy()

    if args.command in {"plan", "apply", "deploy"}:
        terraform_env = ensure_aws_access(args.aws_profile)

    # Keep the branch logic explicit so each CLI mode is easy to follow during
    # handoff and debugging.
    if args.command == "init":
        terraform_init(terraform_env)
    elif args.command == "plan":
        package_lambda(terraform_env)
        terraform_init(terraform_env)
        terraform_plan(var_file, args.plan_file, terraform_env)
    elif args.command == "apply":
        package_lambda(terraform_env)
        terraform_init(terraform_env)
        terraform_apply(args.plan_file, args.auto_approve, terraform_env)
        run_etl(terraform_env)
        print_summary(terraform_env)
    else:
        package_lambda(terraform_env)
        terraform_init(terraform_env)
        terraform_plan(var_file, args.plan_file, terraform_env)
        terraform_apply(args.plan_file, args.auto_approve, terraform_env)
        run_etl(terraform_env)
        print_summary(terraform_env)

    print("Terraform command completed successfully.")


if __name__ == "__main__":
    main()
