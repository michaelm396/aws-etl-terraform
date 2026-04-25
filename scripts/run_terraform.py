from __future__ import annotations
"""Project runner for packaging Lambdas, applying Terraform, and starting ETL."""

import argparse
import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
TERRAFORM_DIR = PROJECT_ROOT / "terraform"
EXTRACT_SCRIPT = PROJECT_ROOT / "extract.py"
PACKAGE_LAMBDA_SCRIPT = PROJECT_ROOT / "scripts" / "package_lambda.py"
PACKAGE_INFERENCE_LAMBDA_SCRIPT = PROJECT_ROOT / "scripts" / "package_inference_lambda.py"
DEFAULT_VAR_FILE = TERRAFORM_DIR / "terraform.tfvars"
REQUIREMENTS_FILE = PROJECT_ROOT / "requirements.txt"
DEFAULT_PLAN_FILE = "tfplan"
READINESS_ATTEMPTS = 30
READINESS_DELAY_SECONDS = 10
EC2_ACCOUNT_BLOCK_MESSAGE = (
    "AWS account is blocked from launching EC2 instances. This is an AWS "
    "account verification issue, not a Terraform issue. To deploy the Ollama "
    "chatbot, sign in as the AWS root account owner, verify billing/contact/"
    "payment information, and open an AWS Support account verification case."
)
EC2_VCPU_LIMIT_MESSAGE = (
    "AWS EC2 vCPU quota is too low for the requested chatbot instance type. "
    "This project defaults to t2.micro because it uses 1 vCPU. If you choose a "
    "larger chatbot_instance_type, request an EC2 vCPU quota increase or switch "
    "back to chatbot_instance_type = \"t2.micro\"."
)
LAST_COMMAND_STDERR = ""


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser for the deployment runner."""
    parser = argparse.ArgumentParser(
        description="Run Terraform commands for the S3 bucket project."
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="deploy",
        choices=["init", "plan", "apply", "deploy", "destroy"],
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
                print("  brew tap hashicorp/tap", file=sys.stderr)
                print("  brew install hashicorp/tap/terraform", file=sys.stderr)
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


def system_install_commands(missing_commands: list[str]) -> list[list[str]]:
    """Return safe OS/package-manager install commands for missing tools."""
    system_name = platform.system().lower()
    commands: list[list[str]] = []

    if system_name == "darwin" and shutil.which("brew"):
        if "terraform" in missing_commands:
            commands.append(["brew", "tap", "hashicorp/tap"])
            commands.append(["brew", "install", "hashicorp/tap/terraform"])
        if "aws" in missing_commands:
            commands.append(["brew", "install", "awscli"])
    elif system_name == "windows" and shutil.which("winget"):
        if "terraform" in missing_commands:
            commands.append(["winget", "install", "Hashicorp.Terraform"])
        if "aws" in missing_commands:
            commands.append(["winget", "install", "Amazon.AWSCLI"])

    return commands


def prompt_yes_no(question: str) -> bool:
    """Return True when the user explicitly answers yes."""
    try:
        answer = input(question)
    except EOFError:
        answer = ""
    return answer.strip().lower() in {"y", "yes"}


def offer_system_dependency_install(missing_commands: list[str]) -> None:
    """Ask before installing supported missing system dependencies."""
    install_commands = system_install_commands(missing_commands)
    if not install_commands:
        print_system_install_instructions(missing_commands)
        sys.exit(1)

    print(
        "Missing required system command(s): "
        f"{', '.join(missing_commands)}"
    )
    print("The deployment runner can install these with:")
    for command in install_commands:
        print(f"  {' '.join(command)}")

    if not prompt_yes_no("Install missing system dependencies now? [y/N] "):
        print_system_install_instructions(missing_commands)
        sys.exit(1)

    for command in install_commands:
        run_command(command, cwd=PROJECT_ROOT)

    still_missing = [
        command for command in missing_commands if shutil.which(command) is None
    ]
    if still_missing:
        print(
            "Installation completed, but these commands are still not available "
            f"on PATH: {', '.join(still_missing)}",
            file=sys.stderr,
        )
        print(
            "Open a new terminal or update your PATH, then rerun this script.",
            file=sys.stderr,
        )
        sys.exit(1)


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
    if prompt_yes_no("Python dependencies are missing. Install them now? [y/N] "):
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
        offer_system_dependency_install(missing_commands)

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


def parse_tfvars_bool(var_file: Path, variable_name: str, default: bool) -> bool:
    """Read a simple bool value from a Terraform tfvars file."""
    pattern = re.compile(rf"^\s*{re.escape(variable_name)}\s*=\s*(true|false)\b")
    for raw_line in var_file.read_text().splitlines():
        line = raw_line.split("#", 1)[0].strip()
        match = pattern.match(line)
        if match:
            return match.group(1) == "true"
    return default


def parse_tfvars_string(var_file: Path, variable_name: str, default: str) -> str:
    """Read a simple quoted string value from a Terraform tfvars file."""
    pattern = re.compile(rf'^\s*{re.escape(variable_name)}\s*=\s*"([^"]+)"')
    for raw_line in var_file.read_text().splitlines():
        line = raw_line.split("#", 1)[0].strip()
        match = pattern.match(line)
        if match:
            return match.group(1)
    return default


def run_command(
    command: list[str],
    *,
    cwd: Path = TERRAFORM_DIR,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess and print its captured output in a readable way."""
    global LAST_COMMAND_STDERR
    LAST_COMMAND_STDERR = ""
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
        LAST_COMMAND_STDERR = exc.stderr or ""
        if exc.stdout:
            print(exc.stdout)
        if exc.stderr:
            print(exc.stderr, file=sys.stderr)
            if ec2_account_block_detected(exc.stderr):
                print(f"\n{EC2_ACCOUNT_BLOCK_MESSAGE}", file=sys.stderr)
            if ec2_vcpu_limit_detected(exc.stderr):
                print(f"\n{EC2_VCPU_LIMIT_MESSAGE}", file=sys.stderr)
            print_decoded_authorization_message(exc.stderr, env)
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


def ec2_account_block_detected(message: str) -> bool:
    """Return True when AWS reports account verification or EC2 block status."""
    lowered = message.lower()
    return any(
        marker in lowered
        for marker in (
            "account-verification",
            "account is currently blocked",
            "currently blocked and not recognized as a valid account",
            "not recognized as a valid account",
        )
    )


def ec2_vcpu_limit_detected(message: str) -> bool:
    """Return True when AWS reports the account's EC2 vCPU quota is too low."""
    lowered = message.lower()
    return "vcpulimitexceeded" in lowered or "vcpu limit" in lowered


def encoded_authorization_message(message: str) -> str | None:
    """Extract an AWS encoded authorization failure message from stderr."""
    patterns = (
        r"Encoded authorization failure message:\s*([A-Za-z0-9+/=_-]+)",
        r"encoded authorization failure message:\s*([A-Za-z0-9+/=_-]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, message)
        if match:
            return match.group(1)
    return None


def print_decoded_authorization_message(
    message: str,
    env: dict[str, str] | None,
) -> None:
    """Decode AWS authorization failures when the caller has STS permission."""
    encoded_message = encoded_authorization_message(message)
    if not encoded_message:
        return

    result = subprocess.run(
        [
            "aws",
            "sts",
            "decode-authorization-message",
            "--encoded-message",
            encoded_message,
            "--query",
            "DecodedMessage",
            "--output",
            "text",
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        env=env,
    )
    if result.returncode == 0 and result.stdout.strip():
        print("\nDecoded AWS authorization failure:", file=sys.stderr)
        print(result.stdout.strip(), file=sys.stderr)
        return

    print(
        "\nAWS returned an encoded authorization failure. To decode the exact "
        "missing permission, grant sts:DecodeAuthorizationMessage to this user "
        "or run:",
        file=sys.stderr,
    )
    print(
        "  aws sts decode-authorization-message --encoded-message "
        f"{encoded_message}",
        file=sys.stderr,
    )


def run_aws_command(
    command: list[str],
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    """Run an AWS CLI command for preflight checks without generic exit handling."""
    return subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        env=env,
    )


def fail_for_ec2_account_block() -> None:
    """Print the required EC2 account verification message and stop deployment."""
    print(f"\n{EC2_ACCOUNT_BLOCK_MESSAGE}", file=sys.stderr)
    sys.exit(1)


def ensure_ec2_launch_allowed(var_file: Path, env: dict[str, str]) -> None:
    """Dry-run an EC2 launch before Terraform creates the full chatbot stack."""
    if not parse_tfvars_bool(var_file, "enable_llm_chatbot", default=True):
        print("LLM chatbot deployment is disabled with enable_llm_chatbot = false.")
        return

    region = parse_tfvars_string(var_file, "aws_region", default="us-west-2")
    instance_type = parse_tfvars_string(
        var_file,
        "chatbot_instance_type",
        default="t2.micro",
    )
    print(f"Checking EC2 launch access for the Ollama chatbot in {region}...")

    describe_images = run_aws_command(
        [
            "aws",
            "ec2",
            "describe-images",
            "--region",
            region,
            "--owners",
            "amazon",
            "--filters",
            "Name=name,Values=al2023-ami-2023.*-x86_64",
            "Name=architecture,Values=x86_64",
            "Name=virtualization-type,Values=hvm",
            "--query",
            "sort_by(Images,&CreationDate)[-1].ImageId",
            "--output",
            "text",
        ],
        env,
    )
    describe_message = f"{describe_images.stdout}\n{describe_images.stderr}"
    if describe_images.returncode != 0:
        if ec2_account_block_detected(describe_message):
            fail_for_ec2_account_block()
        print(describe_message, file=sys.stderr)
        print(
            "Unable to verify EC2 AMI access for the chatbot preflight check.",
            file=sys.stderr,
        )
        sys.exit(describe_images.returncode)

    ami_id = describe_images.stdout.strip()
    if not ami_id or ami_id == "None":
        print(
            "Unable to find an Amazon Linux 2023 AMI for the chatbot preflight check.",
            file=sys.stderr,
        )
        sys.exit(1)

    dry_run = run_aws_command(
        [
            "aws",
            "ec2",
            "run-instances",
            "--region",
            region,
            "--image-id",
            ami_id,
            "--instance-type",
            instance_type,
            "--count",
            "1",
            "--dry-run",
        ],
        env,
    )
    dry_run_message = f"{dry_run.stdout}\n{dry_run.stderr}"
    if "DryRunOperation" in dry_run_message:
        print("EC2 launch dry-run succeeded for the Ollama chatbot.")
        return
    if ec2_account_block_detected(dry_run_message):
        fail_for_ec2_account_block()
    if ec2_vcpu_limit_detected(dry_run_message):
        print(f"\n{EC2_VCPU_LIMIT_MESSAGE}", file=sys.stderr)
        sys.exit(dry_run.returncode or 1)

    print(dry_run_message, file=sys.stderr)
    print(
        "EC2 launch dry-run failed. Fix the AWS EC2 permission or quota issue, then rerun deployment.",
        file=sys.stderr,
    )
    sys.exit(dry_run.returncode or 1)


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
    print("  AWS_DEFAULT_REGION=us-west-2", file=sys.stderr)
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


def terraform_destroy(
    var_file: Path,
    auto_approve: bool,
    env: dict[str, str],
) -> None:
    """Destroy Terraform-managed infrastructure."""
    command = ["terraform", "destroy", f"-var-file={var_file}"]
    if auto_approve:
        command.append("-auto-approve")
    try:
        run_command(command, env=env)
    except SystemExit as exc:
        if not last_command_failed_with_bucket_not_empty():
            raise
        print("\nTerraform hit BucketNotEmpty. Emptying S3 bucket and retrying destroy.")
        empty_managed_s3_bucket_before_destroy(env)
        run_command(command, env=env)
        if isinstance(exc.code, int) and exc.code == 0:
            return


def terraform_output(name: str, env: dict[str, str]) -> str:
    """Read a single Terraform output value."""
    completed = run_command(
        ["terraform", "output", "-raw", name],
        cwd=TERRAFORM_DIR,
        env=env,
    )
    return completed.stdout.strip()


def terraform_output_optional(name: str, env: dict[str, str]) -> str | None:
    """Read a Terraform output without exiting when state is partially destroyed."""
    result = subprocess.run(
        ["terraform", "output", "-raw", name],
        cwd=TERRAFORM_DIR,
        text=True,
        capture_output=True,
        env=env,
    )
    if result.returncode != 0:
        return None
    output = result.stdout.strip()
    return output or None


def valid_s3_bucket_name(value: str | None) -> bool:
    """Return True when a string is shaped like a normal S3 bucket name."""
    if not value:
        return False
    if "\n" in value or "\r" in value:
        return False
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", value))


def last_command_failed_with_bucket_not_empty() -> bool:
    """Return True when the most recent command failed on a non-empty S3 bucket."""
    return "BucketNotEmpty" in LAST_COMMAND_STDERR


def terraform_state_bucket_name(env: dict[str, str]) -> str | None:
    """Find the managed S3 bucket name from Terraform state."""
    result = subprocess.run(
        ["terraform", "state", "show", "aws_s3_bucket.xlsx_bucket"],
        cwd=TERRAFORM_DIR,
        text=True,
        capture_output=True,
        env=env,
    )
    if result.returncode != 0:
        return None

    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        match = re.match(r'^(bucket|id)\s+=\s+"?([^"\s]+)"?$', line)
        if match:
            return match.group(2)
    return None


def delete_s3_objects(bucket_name: str, objects: list[dict[str, str]], env: dict[str, str]) -> None:
    """Delete up to 1000 versioned S3 objects using a temporary AWS CLI payload."""
    if not objects:
        return

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as temp_file:
        json.dump({"Objects": objects, "Quiet": True}, temp_file)
        temp_file_path = temp_file.name

    try:
        run_command(
            [
                "aws",
                "s3api",
                "delete-objects",
                "--bucket",
                bucket_name,
                "--delete",
                f"file://{temp_file_path}",
            ],
            cwd=PROJECT_ROOT,
            env=env,
        )
    finally:
        Path(temp_file_path).unlink(missing_ok=True)


def empty_versioned_s3_bucket(bucket_name: str, env: dict[str, str]) -> None:
    """Remove all object versions and delete markers from a versioned S3 bucket."""
    print(f"\nEmptying versioned S3 bucket before destroy: {bucket_name}")
    key_marker: str | None = None
    version_marker: str | None = None
    deleted_count = 0

    while True:
        command = [
            "aws",
            "s3api",
            "list-object-versions",
            "--bucket",
            bucket_name,
            "--output",
            "json",
        ]
        if key_marker:
            command.extend(["--key-marker", key_marker])
        if version_marker:
            command.extend(["--version-id-marker", version_marker])

        listed = run_command(command, cwd=PROJECT_ROOT, env=env)
        payload = json.loads(listed.stdout or "{}")
        objects = [
            {"Key": item["Key"], "VersionId": item["VersionId"]}
            for item in payload.get("Versions", [])
        ]
        objects.extend(
            {"Key": item["Key"], "VersionId": item["VersionId"]}
            for item in payload.get("DeleteMarkers", [])
        )

        for index in range(0, len(objects), 1000):
            batch = objects[index : index + 1000]
            delete_s3_objects(bucket_name, batch, env)
            deleted_count += len(batch)

        if not payload.get("IsTruncated"):
            break
        key_marker = payload.get("NextKeyMarker")
        version_marker = payload.get("NextVersionIdMarker")

    print(f"Deleted {deleted_count} S3 object version(s) and delete marker(s).")


def empty_managed_s3_bucket_before_destroy(env: dict[str, str]) -> None:
    """Best-effort cleanup for the Terraform-managed versioned ETL bucket."""
    bucket_name = terraform_output_optional("bucket_name", env)
    if not valid_s3_bucket_name(bucket_name) or bucket_name == "disabled":
        bucket_name = terraform_state_bucket_name(env)
    if not valid_s3_bucket_name(bucket_name) or bucket_name == "disabled":
        print("No Terraform bucket output found; skipping S3 pre-destroy cleanup.")
        return
    empty_versioned_s3_bucket(bucket_name, env)


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


def request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, str] | None = None,
    timeout_seconds: int = 15,
) -> dict[str, object]:
    """Call a JSON HTTP endpoint using only the standard library."""
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        response_body = response.read().decode("utf-8")
    return json.loads(response_body)


def wait_for_json_endpoint(
    label: str,
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, str] | None = None,
    is_ready: object | None = None,
    attempts: int = READINESS_ATTEMPTS,
    delay_seconds: int = READINESS_DELAY_SECONDS,
) -> dict[str, object] | None:
    """Wait for an endpoint to return JSON, then return that response."""
    print(f"\nWaiting for {label} to be ready...")
    last_error = ""
    for attempt in range(1, attempts + 1):
        try:
            response = request_json(url, method=method, payload=payload)
            if callable(is_ready) and not is_ready(response):
                raise RuntimeError(f"Unexpected response: {response}")
            print(f"{label} is ready.")
            return response
        except (
            TimeoutError,
            urllib.error.URLError,
            urllib.error.HTTPError,
            json.JSONDecodeError,
            RuntimeError,
        ) as exc:
            last_error = str(exc)
            if attempt == attempts:
                break
            print(
                f"{label} not ready yet "
                f"({attempt}/{attempts}): {last_error}"
            )
            time.sleep(delay_seconds)

    print(
        f"{label} did not return a usable JSON response after "
        f"{attempts * delay_seconds} seconds."
    )
    print(f"Last error: {last_error}")
    return None


def wait_for_deployed_apis(env: dict[str, str]) -> dict[str, dict[str, object]]:
    """Wait for deployed APIs and capture example responses for the final summary."""
    responses: dict[str, dict[str, object]] = {}

    inference_api_url = terraform_output("inference_api_url", env)
    if inference_api_url != "disabled":
        response = wait_for_json_endpoint(
            "Institution Type Classifier API",
            f"{inference_api_url}?domain_type=commercial&country=United%20States",
            is_ready=lambda body: body.get("affiliation_category") == "business",
            attempts=12,
            delay_seconds=5,
        )
        if response is not None:
            responses["classifier"] = response

    chatbot_health_url = terraform_output("chatbot_health_url", env)
    chatbot_api_url = terraform_output("chatbot_api_url", env)
    if chatbot_health_url != "disabled" and chatbot_api_url != "disabled":
        health_response = wait_for_json_endpoint(
            "LLM Chatbot health endpoint",
            chatbot_health_url,
        )
        if health_response is not None:
            responses["chatbot_health"] = health_response

        chat_response = wait_for_json_endpoint(
            "LLM Chatbot chat endpoint and RDS dataset",
            chatbot_api_url,
            method="POST",
            payload={"question": "Summarize the dataset."},
            is_ready=lambda body: (
                isinstance(body.get("data"), dict)
                and "error" not in body.get("data", {})
                and body.get("query_type") == "dataset_summary"
            ),
        )
        if chat_response is not None:
            responses["chatbot_chat"] = chat_response

    return responses


def print_summary(
    env: dict[str, str],
    readiness_responses: dict[str, dict[str, object]] | None = None,
) -> None:
    """Print the core AWS artifacts created by the deployment flow."""
    readiness_responses = readiness_responses or {}
    bucket_name = terraform_output("bucket_name", env)
    bucket_arn = terraform_output("bucket_arn", env)
    db_endpoint = terraform_output("db_endpoint", env)
    db_name = terraform_output("db_name", env)
    transform_lambda = terraform_output("lambda_function_name", env)
    loader_lambda = terraform_output("loader_lambda_function_name", env)
    inference_lambda = terraform_output("inference_lambda_function_name", env)
    inference_api_url = terraform_output("inference_api_url", env)
    chatbot_public_dns = terraform_output("chatbot_public_dns", env)
    chatbot_health_url = terraform_output("chatbot_health_url", env)
    chatbot_api_url = terraform_output("chatbot_api_url", env)
    inference_api_get_format = "disabled"
    commercial_example_command = "disabled"
    if inference_api_url != "disabled":
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
    print(f"LLM Chatbot public DNS: {chatbot_public_dns}")
    print(f"LLM Chatbot health URL: {chatbot_health_url}")
    print(f"LLM Chatbot chat URL: {chatbot_api_url}")
    print(f"RDS endpoint: {db_endpoint}")
    print(f"RDS database: {db_name}")

    print("\nDeployment complete.")
    print("\nAvailable APIs:")

    print("\n1. Institution Type Classifier API")
    print("Description:")
    print("Classifies a record by domain_type and country.")
    if inference_api_url == "disabled":
        print("Disabled. Set enable_classifier_api = true to create this endpoint.")
    else:
        print("\nEndpoint format:")
        print(inference_api_get_format)
        print("\nExample:")
        print(commercial_example_command)
        print("\nExpected response:")
        classifier_response = readiness_responses.get("classifier")
        if classifier_response:
            print(json.dumps(classifier_response, separators=(",", ":")))
        else:
            print('{"affiliation_category":"business"}')
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

    print("\n2. LLM Chatbot API")
    print("Description:")
    print("Allows users to ask natural-language questions about the transformed RDS dataset.")
    if chatbot_api_url == "disabled":
        print(
            "Disabled. enable_llm_chatbot = false should be used only as an emergency "
            "fallback when EC2 account verification is blocked."
        )
    else:
        print("\nHealth check:")
        print(f"curl --max-time 10 \"{chatbot_health_url}\"")
        health_response = readiness_responses.get("chatbot_health")
        if health_response:
            print("\nVerified health response:")
            print(json.dumps(health_response, separators=(",", ":")))
        print("\nExample:")
        print(
            f"curl --max-time 60 -X POST \"{chatbot_api_url}\" \\\n"
            "  -H \"Content-Type: application/json\" \\\n"
            "  -d '{\"question\":\"Summarize the dataset.\"}'"
        )
        chatbot_response = readiness_responses.get("chatbot_chat")
        if chatbot_response:
            print("\nVerified chat response:")
            print(json.dumps(chatbot_response, separators=(",", ":")))
        else:
            print("\nExpected response:")
            print(
                "A JSON object containing the original question, a natural-language "
                "answer, query_type, and supporting data."
            )


def main() -> None:
    """Entry point for the project deployment runner."""
    parser = build_parser()
    args = parser.parse_args()

    var_file = Path(args.var_file).expanduser().resolve()
    ensure_prerequisites(var_file)
    terraform_env = os.environ.copy()

    if args.command in {"plan", "apply", "deploy", "destroy"}:
        terraform_env = ensure_aws_access(args.profile)
        if args.command in {"plan", "apply", "deploy"}:
            ensure_ec2_launch_allowed(var_file, terraform_env)

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
        readiness_responses = wait_for_deployed_apis(terraform_env)
        print_summary(terraform_env, readiness_responses)
    elif args.command == "destroy":
        terraform_init(terraform_env)
        empty_managed_s3_bucket_before_destroy(terraform_env)
        terraform_destroy(var_file, args.auto_approve, terraform_env)
    else:
        package_lambdas(terraform_env)
        terraform_init(terraform_env)
        terraform_plan(var_file, args.plan_file, terraform_env)
        terraform_apply(args.plan_file, args.auto_approve, terraform_env)
        run_etl(terraform_env)
        readiness_responses = wait_for_deployed_apis(terraform_env)
        print_summary(terraform_env, readiness_responses)

    print("Terraform command completed successfully.")


if __name__ == "__main__":
    main()
