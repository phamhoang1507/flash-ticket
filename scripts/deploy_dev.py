#!/usr/bin/env python3
"""Run one explicit Flash-Ticket AWS dev deployment stage at a time."""

from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
PROFILE = "flash-ticket-terraform"
REGION = "us-east-1"
PREFIX = "/flash-ticket/dev"
DEMO_EVENT_ID = "11111111-1111-4111-8111-111111111111"
DEV = ROOT / "infra/environments/dev"
FOUNDATION = ROOT / "infra/environments/dev-foundation"
BOOTSTRAP = ROOT / "infra/bootstrap"
FLAGS = DEV / "launch.auto.tfvars"
STAGES = ("prepare", "bake", "data", "images", "bootstrap", "start",
          "frontend", "status", "stop", "destroy")
SENSITIVE: set[str] = set()
DEFAULT_FLAGS: dict[str, object] = {
    "enable_data_layer": False,
    "enable_compute": False,
    "enable_redis": False,
    "enable_mongodb": False,
    "enable_mongo_ami_builder": False,
    "enable_nat_gateway": False,
    "enable_multi_az_data": False,
    "use_fargate_spot": True,
    "service_desired_count": 0,
    "application_image_tag": "",
}


class DeployError(RuntimeError):
    pass


def redact(value: str) -> str:
    for secret in sorted(SENSITIVE, key=len, reverse=True):
        if secret:
            value = value.replace(secret, "[REDACTED]")
    return value


def run(command: list[str], *, data: str | None = None,
        capture: bool = False, check: bool = True,
        env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    process_env = dict(os.environ)
    process_env.pop("TF_LOG", None)
    process_env.pop("TF_LOG_PATH", None)
    if env:
        process_env.update(env)
    result = subprocess.run(command, cwd=ROOT, input=data, text=True,
                            capture_output=capture, env=process_env)
    if check and result.returncode:
        detail = redact((result.stderr or result.stdout or "").strip())
        raise DeployError(f"Command failed ({result.returncode}): {command[0]}\n{detail}")
    return result


def aws(*arguments: str, payload: dict | None = None,
        allow_failure: bool = False) -> dict | list | str | None:
    command = ["aws", "--profile", PROFILE, "--region", REGION,
               "--no-cli-pager", *arguments, "--output", "json"]
    try:
        if payload is None:
            result = run(command, capture=True, check=not allow_failure)
        else:
            with tempfile.TemporaryDirectory(prefix="flash-ticket-aws-request-") as directory:
                request_file = Path(directory) / "request.json"
                descriptor = os.open(request_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(payload, stream, ensure_ascii=True, allow_nan=False)
                result = run(
                    [*command, "--cli-input-json", request_file.as_uri()],
                    capture=True, check=not allow_failure,
                )
    except DeployError as error:
        operation = " ".join(arguments[:2])
        raise DeployError(f"AWS {operation}: {error}") from None
    if result.returncode:
        return None
    return json.loads(result.stdout) if result.stdout.strip() else {}


def identity() -> str:
    result = aws("sts", "get-caller-identity")
    if result["Arn"].endswith(":root"):
        raise DeployError("Use the IAM profile flash-ticket-terraform, not root credentials.")
    return result["Account"]


def terraform(root: Path, *arguments: str, runtime: bool = False) -> str:
    command = (["bash", str(ROOT / "scripts/tf-dev.sh")] if runtime
               else ["terraform", f"-chdir={root}"])
    result = run([*command, *arguments])
    return result.stdout or ""


def outputs(root: Path) -> dict:
    result = run(["terraform", f"-chdir={root}", "output", "-json"], capture=True)
    return {key: value["value"] for key, value in json.loads(result.stdout).items()}


def state_addresses(root: Path) -> list[str]:
    result = run(["terraform", f"-chdir={root}", "state", "list"],
                 capture=True, check=False)
    if result.returncode:
        message = (result.stdout + result.stderr).lower()
        if "no state file was found" in message:
            return []
        raise DeployError(redact(result.stderr.strip() or result.stdout.strip()))
    return result.stdout.splitlines()


def apply(root: Path, args: argparse.Namespace, *, runtime: bool = False) -> None:
    options = ["apply"]
    if args.yes:
        options.append("-auto-approve")
    terraform(root, *options, runtime=runtime)


def read_flags() -> dict[str, object]:
    values = dict(DEFAULT_FLAGS)
    if FLAGS.exists():
        for line in FLAGS.read_text().splitlines():
            match = re.fullmatch(r"\s*(\w+)\s*=\s*(.*?)\s*", line)
            if match and match[1] in values:
                values[match[1]] = json.loads(match[2])
    return values


def flags(**updates: object) -> dict[str, object]:
    values = read_flags()
    values.update(updates)
    content = "# Managed by scripts/deploy_dev.py; contains no secrets.\n"
    content += "".join(f"{key} = {json.dumps(value)}\n" for key, value in values.items())
    FLAGS.write_text(content)
    return values


def ensure_account(root: Path, account: str) -> None:
    for path in root.glob("*.tfvars"):
        for configured in re.findall(r'\baws_account_id\s*=\s*"([0-9]{12})"', path.read_text()):
            if configured != account:
                raise DeployError(f"Account mismatch in {path.relative_to(ROOT)}; refusing to overwrite.")
    target = root / "account.auto.tfvars"
    if not target.exists():
        target.write_text(f'aws_account_id = "{account}"\n')


def initialize(root: Path, bucket: str, key: str) -> None:
    backend = root / "backend.hcl"
    expected = (f'bucket = "{bucket}"\nkey = "{key}"\nregion = "{REGION}"\n'
                f'profile = "{PROFILE}"\nencrypt = true\nuse_lockfile = true\n')
    if backend.exists():
        configured = dict(re.findall(r'(\w+)\s*=\s*"([^\"]+)"', backend.read_text()))
        for field, value in {"bucket": bucket, "key": key, "region": REGION, "profile": PROFILE}.items():
            if field in configured and configured[field] != value:
                raise DeployError(f"Review {backend.relative_to(ROOT)}; backend {field} differs.")
    backend.write_text(expected)
    terraform(root, "init", "-reconfigure", "-backend-config=backend.hcl")


def require_outputs(*names: str) -> dict:
    values = outputs(DEV)
    for name in names:
        if not values.get(name):
            raise DeployError(f"Missing runtime output {name}; run the preceding stage first.")
    return values


def require_foundation_outputs(*names: str) -> dict:
    values = outputs(FOUNDATION)
    missing = [name for name in names if not values.get(name)]
    if missing:
        raise DeployError(
            "Foundation state is missing required outputs: " + ", ".join(missing) + ". "
            "Run the prepare stage with your SES sender and wait for it to finish before images. "
            "If prepare already succeeded, inspect infra/environments/dev-foundation/backend.hcl "
            "and the selected Terraform workspace; images must read that same foundation state."
        )
    return values


def get_secret(name: str) -> str:
    value = aws("ssm", "get-parameter", "--name", f"{PREFIX}/{name}",
                "--with-decryption")["Parameter"]["Value"]
    SENSITIVE.update({value, quote(value, safe="")})
    return value


def put_secret(name: str, value: str) -> None:
    SENSITIVE.add(value)
    aws("ssm", "put-parameter", payload={
        "Name": f"{PREFIX}/{name}", "Value": value,
        "Type": "SecureString", "Tier": "Standard", "Overwrite": True,
    })
    print(f"Updated SSM: {PREFIX}/{name}")


def run_python(relative: str, *arguments: str) -> None:
    run([sys.executable, str(ROOT / relative), *arguments])


def helper_options(relative: str) -> set[str]:
    path = ROOT / relative
    if not path.is_file():
        raise DeployError(f"Required existing helper is missing: {relative}")
    tree = ast.parse(path.read_text(), filename=relative)
    options: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr == "add_argument":
            literals = [argument.value for argument in node.args
                        if isinstance(argument, ast.Constant) and isinstance(argument.value, str)]
            required = any(keyword.arg == "required" and isinstance(keyword.value, ast.Constant)
                           and keyword.value.value is True for keyword in node.keywords)
            optional_positional = any(
                keyword.arg == "nargs" and isinstance(keyword.value, ast.Constant)
                and keyword.value.value in {"?", "*"} for keyword in node.keywords)
            if literals and not literals[0].startswith("-") and not optional_positional:
                raise DeployError(f"Unknown required positional argument in {relative}; export helper source for review.")
            if required and not {"--profile", "--region", "--execute"}.intersection(literals):
                raise DeployError(f"Unknown required option in {relative}; export helper source for review.")
            options.update(value for value in literals if value.startswith("--"))
    return options


def helper_preflight() -> None:
    helper_options("scripts/aws_dev_secrets.py")
    mongo_options = helper_options("scripts/provision_mongodb.py")
    if "--execute" not in mongo_options:
        raise DeployError(
            "Cannot verify provision_mongodb.py execution mode statically: expected literal "
            "argparse --execute. Export this helper source for review before creating resources.")
    wrapper = ROOT / "scripts/tf-dev.sh"
    if not wrapper.is_file():
        raise DeployError("Missing existing helper scripts/tf-dev.sh")
    source = wrapper.read_text()
    for token in ("TF_VAR_rds_master_password", "TF_VAR_redis_auth_token", "$@"):
        if token not in source:
            raise DeployError(f"Cannot verify tf-dev.sh contract ({token}); review helper before deploying.")
    for relative in ("scripts/ssh-mongodb.sh", "scripts/mongodb/bootstrap.js"):
        if not (ROOT / relative).is_file():
            raise DeployError(f"Required MongoDB provisioning file is missing: {relative}")
    for executable in ("bash", "ssh", "ssh-keygen"):
        if not shutil.which(executable):
            raise DeployError(f"Required MongoDB provisioning command is missing: {executable}")


def run_existing_helper(relative: str, *, execute: bool = False) -> None:
    options = helper_options(relative)
    arguments = []
    for option, value in (("--profile", PROFILE), ("--region", REGION)):
        if option in options:
            arguments.extend((option, value))
    if execute and "--execute" not in options:
        raise DeployError(f"Cannot verify --execute for existing helper {relative}; no helper was run.")
    if "--execute" in options:
        arguments.append("--execute")
    run_python(relative, *arguments)


def prepare(args: argparse.Namespace) -> None:
    helper_preflight()
    if not args.sender or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", args.sender):
        raise DeployError("prepare requires --sender your-verified-email@example.com")
    account = identity()
    for root in (BOOTSTRAP, FOUNDATION, DEV):
        if not root.is_dir():
            raise DeployError(f"Missing existing project directory: {root.relative_to(ROOT)}")
        ensure_account(root, account)
    for root in (BOOTSTRAP, FOUNDATION):
        (root / "cleanup.auto.tfvars").unlink(missing_ok=True)
    ses_file = FOUNDATION / "ses.auto.tfvars"
    existing_senders = []
    for path in FOUNDATION.glob("*.tfvars"):
        existing_senders.extend(re.findall(r'\bses_sender_email\s*=\s*"([^\"]+)"', path.read_text()))
    if any(sender != args.sender for sender in existing_senders):
        raise DeployError("Existing SES sender differs. Review foundation *.tfvars before continuing.")
    if not existing_senders:
        ses_file.write_text(f"ses_sender_email = {json.dumps(args.sender)}\n")
    terraform(BOOTSTRAP, "init")
    apply(BOOTSTRAP, args)
    bucket = outputs(BOOTSTRAP)["state_bucket_name"]
    initialize(FOUNDATION, bucket, "environments/dev-foundation/terraform.tfstate")
    initialize(DEV, bucket, "environments/dev/terraform.tfstate")
    apply(FOUNDATION, args)
    run_existing_helper("scripts/aws_dev_secrets.py")
    required_parameters = [f"{PREFIX}/{name}" for name in (
        "data/MYSQL_MASTER_PASSWORD", "data/MYSQL_APP_PASSWORD", "data/MONGODB_ROOT_PASSWORD",
        "data/MONGODB_APP_PASSWORD", "data/REDIS_AUTH_TOKEN", "worker/TICKET_SIGNING_SECRET",
    )]
    restored = aws("ssm", "get-parameters", "--names", *required_parameters)
    if restored.get("InvalidParameters"):
        raise DeployError("Secret helper did not restore all required SSM parameters; inspect its source/configuration.")
    print("Foundation ready. Open the SES verification email before the start stage.")
    print("Next: python3 scripts/deploy_dev.py images (build before paid database nodes start)")


def bake(args: argparse.Namespace) -> None:
    addresses = state_addresses(DEV)
    if any("module.data_layer" in address or "module.ecs_runtime" in address
           for address in addresses) or outputs(DEV).get("rds"):
        raise DeployError("bake is only for an empty runtime/data layer. Existing data/compute must be preserved.")
    flags(enable_data_layer=False, enable_compute=False, enable_redis=False,
          enable_mongodb=False, enable_mongo_ami_builder=True, service_desired_count=0)
    apply(DEV, args, runtime=True)
    print("Building MongoDB AMI; this temporary EC2 consumes credits until removed.")
    run_python("scripts/bake_mongo_ami.py")
    flags(enable_mongo_ami_builder=False)
    apply(DEV, args, runtime=True)
    print("MongoDB AMI ready; temporary builder removed. Next: data")


def data_stage(args: argparse.Namespace) -> None:
    current = read_flags()
    if current["enable_compute"] or outputs(DEV).get("ecs"):
        raise DeployError("data is an initial-deployment stage. Stop/destroy deliberately before rebuilding data.")
    if not (DEV / "mongodb-ami.auto.tfvars").exists():
        raise DeployError("MongoDB AMI configuration is missing; run bake first.")
    flags(enable_data_layer=True, enable_redis=True, enable_mongodb=True,
          enable_mongo_ami_builder=False, enable_compute=False, service_desired_count=0)
    apply(DEV, args, runtime=True)
    require_outputs("rds", "redis", "mongodb")
    print("Data nodes are running and consuming credits. Next: bootstrap (publish images first if not done)")


def images(args: argparse.Namespace) -> None:
    foundation = require_foundation_outputs(
        "ecr_repository_urls", "mysql_bootstrap_repository_url", "ecr_registry_url"
    )
    identity()
    dirty = run(["git", "status", "--porcelain", "--untracked-files=normal"], capture=True).stdout
    if dirty.strip():
        raise DeployError("Commit project source before images. The build tag must identify a clean Git commit.")
    sha = run(["git", "rev-parse", "--short=12", "HEAD"], capture=True).stdout.strip()
    tag = f"dev-1.0.0-{sha}"
    repositories = dict(foundation["ecr_repository_urls"])
    repositories["mysql_bootstrap"] = foundation["mysql_bootstrap_repository_url"]
    registry = foundation["ecr_registry_url"]
    login = run(["aws", "--profile", PROFILE, "--region", REGION,
                 "ecr", "get-login-password"], capture=True).stdout
    SENSITIVE.add(login.strip())
    with tempfile.TemporaryDirectory(prefix="flash-ticket-docker-") as directory:
        # An isolated config avoids WSL's Windows credential helper and is deleted afterwards.
        Path(directory, "config.json").write_text(json.dumps({
            "auths": {}, "cliPluginsExtraDirs": [str(Path.home() / ".docker/cli-plugins")],
        }))
        docker = ["docker", "--config", directory]
        run([*docker, "login", "--username", "AWS", "--password-stdin", registry], data=login)
        for name, repository in repositories.items():
            remote = f"{repository}:{tag}"
            repository_name = repository.split("/", 1)[1]
            existing = aws("ecr", "describe-images", "--repository-name", repository_name,
                           "--image-ids", f"imageTag={tag}", allow_failure=True)
            if existing:
                print(f"Already published: {remote}")
                continue
            dockerfile = ("tools/mysql-bootstrap/Dockerfile" if name == "mysql_bootstrap"
                          else f"services/{name}/Dockerfile")
            run([*docker, "build", "--platform", "linux/amd64", "--file", dockerfile,
                 "--tag", remote, "."])
            run([*docker, "push", remote])
    flags(application_image_tag=tag)
    print(f"All five images published with immutable commit tag {tag}. Next for a fresh runtime: bake, then data")


def task(runtime: dict, key: str, command: list[str] | None = None) -> None:
    ecs = runtime["ecs"]
    arn = ecs["task_definition_arns"][key]
    definition = aws("ecs", "describe-task-definition", "--task-definition", arn)["taskDefinition"]
    container = definition["containerDefinitions"][0]["name"]
    network = ecs["task_networks"][key]
    payload = {
        "cluster": ecs["cluster_name"], "taskDefinition": arn,
        "launchType": "FARGATE", "platformVersion": "1.4.0", "count": 1,
        "networkConfiguration": {"awsvpcConfiguration": {
            "subnets": network["subnets"], "securityGroups": network["security_groups"],
            "assignPublicIp": network["assign_public_ip"],
        }},
    }
    if command:
        payload["overrides"] = {"containerOverrides": [{"name": container, "command": command}]}
    launched = aws("ecs", "run-task", payload=payload)
    if launched.get("failures") or len(launched.get("tasks", [])) != 1:
        raise DeployError(f"Cannot launch {key}: {json.dumps(launched.get('failures', []))}")
    task_arn = launched["tasks"][0]["taskArn"]
    print(f"Waiting for one-off task {key}: {task_arn.rsplit('/', 1)[-1]}")
    # Explicit polling has a useful timeout for image pulls/bootstrap; wait does not prove success.
    deadline = time.monotonic() + 1200
    while time.monotonic() < deadline:
        described = aws("ecs", "describe-tasks", "--cluster", ecs["cluster_name"],
                        "--tasks", task_arn)
        if described.get("failures") or not described.get("tasks"):
            raise DeployError(f"Cannot inspect one-off task {key}.")
        stopped = described["tasks"][0]
        if stopped["lastStatus"] == "STOPPED":
            containers = stopped.get("containers", [])
            target = next((item for item in containers if item["name"] == container), {})
            if target.get("exitCode") != 0:
                detail = target.get("reason") or stopped.get("stoppedReason", "No exit code")
                raise DeployError(f"Task {key} failed: exitCode={target.get('exitCode')}; {redact(detail)}. Inspect CloudWatch Logs.")
            print(f"Task {key} completed successfully (exitCode=0).")
            return
        time.sleep(10)
    raise DeployError(f"Task {key} exceeded 20 minutes. Inspect/stop that task before retrying.")


def bootstrap(args: argparse.Namespace) -> None:
    account = identity()
    runtime = require_outputs("rds", "redis", "mongodb")
    current = read_flags()
    if not current["application_image_tag"]:
        raise DeployError("Image tag is missing; run images first.")
    if int(current["service_desired_count"]) != 0:
        raise DeployError("Stop the services before running initial bootstrap again.")
    flags(enable_compute=True, service_desired_count=0)
    apply(DEV, args, runtime=True)
    runtime = require_outputs("ecs", "rds", "redis", "mongodb")
    task(runtime, "mysql_bootstrap")
    app_password = get_secret("data/MYSQL_APP_PASSWORD")
    database = (f"mysql+pymysql://flash_app:{quote(app_password, safe='')}@"
                f"{runtime['rds']['address']}:3306/flash_ticket?charset=utf8mb4")
    for name in ("booking", "relay", "worker"):
        put_secret(f"{name}/DATABASE_URL", database)
    redis_token = get_secret("data/REDIS_AUTH_TOKEN")
    redis_url = (f"rediss://:{quote(redis_token, safe='')}@{runtime['redis']['address']}:6379/0"
                 "?ssl_cert_reqs=required&ssl_check_hostname=true")
    for name in ("catalog", "relay"):
        put_secret(f"{name}/REDIS_URL", redis_url)
    run_existing_helper("scripts/provision_mongodb.py", execute=True)
    get_secret("catalog/MONGODB_URI")
    task(runtime, "catalog", ["python", "-m", "scripts.seed_aws_catalog", "--account-id", account])
    task(runtime, "relay", ["python", "-m", "scripts.reconcile_inventory"])
    print("MySQL, MongoDB and Redis initialized; all services still desired_count=0. Next: start")


def start(args: argparse.Namespace) -> None:
    require_outputs("ecs", "rds", "redis", "mongodb")
    if not read_flags()["enable_compute"]:
        raise DeployError("Compute is disabled; complete bootstrap before start.")
    foundation = outputs(FOUNDATION)
    sender = foundation["ses_sender_email"]
    verified = aws("ses", "get-identity-verification-attributes", "--identities", sender)
    if verified.get("VerificationAttributes", {}).get(sender, {}).get("VerificationStatus") != "Success":
        raise DeployError(f"SES sender {sender} is not verified. Open the verification email first.")
    for parameter in ("booking/DATABASE_URL", "catalog/MONGODB_URI", "catalog/REDIS_URL",
                      "relay/DATABASE_URL", "relay/REDIS_URL", "worker/DATABASE_URL"):
        get_secret(parameter)
    flags(service_desired_count=1)
    apply(DEV, args, runtime=True)
    runtime = require_outputs("ecs", "api_url")
    ecs = runtime["ecs"]
    aws("ecs", "wait", "services-stable", "--cluster", ecs["cluster_name"],
        "--services", *ecs["service_names"].values())
    deadline = time.monotonic() + 180
    last_reason = "No valid API response received"
    count = 0
    while time.monotonic() < deadline:
        try:
            timeout = max(0.1, min(10, deadline - time.monotonic()))
            with urlopen(f"{runtime['api_url'].rstrip('/')}/events", timeout=timeout) as response:
                result = json.load(response)
            events = result if isinstance(result, list) else []
            demo = next((event for event in events if isinstance(event, dict)
                         and event.get("id") == DEMO_EVENT_ID), None)
            if demo is None:
                last_reason = "The fixed demo event is missing from GET /events"
            elif type(demo.get("remaining")) is not int or demo["remaining"] < 0:
                last_reason = "The demo event remaining inventory must be an integer >= 0"
            elif type(demo.get("inventory_version")) is not int or demo["inventory_version"] < 0:
                last_reason = "The demo event inventory_version must be an integer >= 0"
            else:
                count = len(events)
                break
        except (HTTPError, URLError, TimeoutError, ValueError) as error:
            last_reason = str(error)
        time.sleep(min(5, max(0, deadline - time.monotonic())))
    if count == 0:
        raise DeployError(
            "ECS is stable but /events did not pass the demo inventory smoke check within 180s: "
            f"{last_reason}. Inspect Cloud Map, Catalog logs and the reconcile task.")
    print(f"API GET /events succeeded; events={count}. API: {runtime['api_url']}")
    print("Next: frontend")


def frontend(args: argparse.Namespace) -> None:
    runtime = require_outputs("api_url")
    web = outputs(FOUNDATION)["web"]
    source = ROOT / "frontend"
    if not (source / "package-lock.json").is_file():
        raise DeployError("frontend/package-lock.json is required for npm ci; commit the lockfile first.")
    public_env = {
        "VITE_AUTH_PROVIDER": "cognito", "VITE_API_BASE_URL": runtime["api_url"],
        "VITE_COGNITO_AUTHORITY": web["cognito_issuer"],
        "VITE_COGNITO_CLIENT_ID": web["cognito_client_id"],
        "VITE_COGNITO_DOMAIN": web["cognito_domain"],
        "VITE_OIDC_SCOPE": "openid email profile flash-ticket/bookings:write",
    }
    environment_options = [option for name in public_env for option in ("--env", name)]
    run(["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}",
         "--env", "NPM_CONFIG_CACHE=/tmp/npm-cache", *environment_options,
         "--mount", f"type=bind,source={source},target=/work", "--workdir", "/work",
         "node:22-alpine", "sh", "-eu", "-c", "npm ci && npm run build"], env=public_env)
    bucket = web["frontend_bucket_name"]
    base = ["aws", "--profile", PROFILE, "--region", REGION, "--no-cli-pager", "s3"]
    # Keep old hashed assets available to browsers that still have the previous index.
    if (source / "dist/assets").is_dir():
        run([*base, "sync", str(source / "dist/assets"), f"s3://{bucket}/assets/",
             "--cache-control", "public,max-age=31536000,immutable"])
    run([*base, "sync", str(source / "dist"), f"s3://{bucket}/", "--delete",
         "--exclude", "index.html", "--exclude", "assets/*", "--cache-control", "no-cache,max-age=0"])
    run([*base, "cp", str(source / "dist/index.html"), f"s3://{bucket}/index.html",
         "--content-type", "text/html; charset=utf-8", "--cache-control", "no-cache,max-age=0,must-revalidate"])
    aws("cloudfront", "create-invalidation", "--distribution-id", web["distribution_id"],
        "--paths", "/*")
    print(f"Frontend uploaded: {web['frontend_url']}")
    print("CloudFront invalidation can take a few minutes. Sign up using the verified SES email for the first test.")


def status(args: argparse.Namespace) -> None:
    runtime = outputs(DEV)
    foundation = outputs(FOUNDATION)
    if runtime.get("ecs"):
        ecs = runtime["ecs"]
        result = aws("ecs", "describe-services", "--cluster", ecs["cluster_name"],
                     "--services", *ecs["service_names"].values())
        for service in result.get("services", []):
            print(f"{service['serviceName']}: desired={service['desiredCount']} "
                  f"running={service['runningCount']} pending={service['pendingCount']}")
            for event in service.get("events", [])[:2]:
                print(f"  {event['message']}")
    for key in ("rds", "redis", "mongodb", "mongo_ami_builder"):
        print(f"{key}: {'present (may consume credits)' if runtime.get(key) else 'absent'}")
    print(f"API: {runtime.get('api_url') or 'not created'}")
    print(f"Frontend: {foundation.get('web', {}).get('frontend_url', 'not created')}")


def stop(args: argparse.Namespace) -> None:
    runtime = outputs(DEV)
    if not runtime.get("ecs"):
        print("No ECS runtime in state. Nothing to scale down.")
        return
    flags(service_desired_count=0)
    apply(DEV, args, runtime=True)
    ecs = require_outputs("ecs")["ecs"]
    aws("ecs", "wait", "services-stable", "--cluster", ecs["cluster_name"],
        "--services", *ecs["service_names"].values())
    print("ECS desired_count=0. RDS, Redis, EC2, storage and other resources can still consume credits.")
    print("For daily cost cleanup: python3 scripts/deploy_dev.py destroy")


def clean_amis() -> None:
    images = aws("ec2", "describe-images", "--owners", "self", "--filters",
                 "Name=tag:Project,Values=flash-ticket", "Name=tag:Environment,Values=dev")["Images"]
    snapshots: set[str] = set()
    for image in images:
        tags = {item["Key"]: item["Value"] for item in image.get("Tags", [])}
        if tags.get("Role") != "mongodb-ami" and not image.get("Name", "").startswith("flash-ticket-dev-mongo80-"):
            continue
        for mapping in image.get("BlockDeviceMappings", []):
            if mapping.get("Ebs", {}).get("SnapshotId"):
                snapshots.add(mapping["Ebs"]["SnapshotId"])
        aws("ec2", "deregister-image", "--image-id", image["ImageId"])
        print(f"Deregistered project MongoDB AMI: {image['ImageId']}")
    orphaned = aws("ec2", "describe-snapshots", "--owner-ids", "self", "--filters",
                   "Name=tag:Project,Values=flash-ticket", "Name=tag:Environment,Values=dev",
                   "Name=tag:Role,Values=mongodb-ami")["Snapshots"]
    snapshots.update(snapshot["SnapshotId"] for snapshot in orphaned)
    for snapshot_id in sorted(snapshots):
        aws("ec2", "delete-snapshot", "--snapshot-id", snapshot_id)
        print(f"Deleted project AMI snapshot: {snapshot_id}")
    ami_file = DEV / "mongodb-ami.auto.tfvars"
    if ami_file.exists():
        ami_file.unlink()


def destroy_root(root: Path, args: argparse.Namespace, *, runtime: bool = False) -> None:
    if not state_addresses(root):
        print(f"No tracked resources: {root.relative_to(ROOT)}")
        return
    options = ["destroy"]
    if args.yes:
        options.append("-auto-approve")
    terraform(root, *options, runtime=runtime)


def cleanup_targets(root: Path, addresses: list[str]) -> dict[str, str]:
    """Select existing buckets/repositories whose deletion flags need to enter state."""
    if root == FOUNDATION:
        known = {
            'module.assets.aws_s3_bucket.this': 'aws_s3_bucket',
            'module.web_auth.aws_s3_bucket.frontend': 'aws_s3_bucket',
            'module.bootstrap_ecr.aws_ecr_repository.this["mysql-bootstrap"]': 'aws_ecr_repository',
        }
        known.update({
            f'module.ecr.aws_ecr_repository.this["{service}"]': 'aws_ecr_repository'
            for service in ("booking", "catalog", "relay", "worker")
        })
    elif root == BOOTSTRAP:
        known = {'aws_s3_bucket.terraform_state': 'aws_s3_bucket'}
    else:
        raise DeployError("Cleanup flag preparation is only supported for foundation and bootstrap.")
    tracked = set(addresses)
    return {address: resource_type for address, resource_type in known.items() if address in tracked}


def cleanup_has_unknown(value: object) -> bool:
    if isinstance(value, dict):
        return any(cleanup_has_unknown(item) for item in value.values())
    if isinstance(value, list):
        return any(cleanup_has_unknown(item) for item in value)
    return value is True


def validate_cleanup_plan(document: dict, targets: dict[str, str]) -> None:
    """Reject every operation except a known resource's single deletion flag update."""
    changes = document.get("resource_changes")
    if not isinstance(changes, list):
        raise DeployError("Cleanup plan has an invalid resource_changes structure.")
    flags_by_type = {"aws_s3_bucket": "force_destroy", "aws_ecr_repository": "force_delete"}
    seen = set()
    for resource in changes:
        if not isinstance(resource, dict) or not isinstance(resource.get("change"), dict):
            raise DeployError("Cleanup plan contains an invalid resource change.")
        change = resource["change"]
        actions = change.get("actions")
        address = resource.get("address", "<unknown>")
        if actions == ["no-op"] and address not in targets:
            continue
        if actions == ["read"] and resource.get("mode") == "data":
            continue
        resource_type = resource.get("type")
        flag = flags_by_type.get(resource_type)
        before, after = change.get("before"), change.get("after")
        if (
            actions not in (["update"], ["no-op"])
            or resource.get("mode") != "managed"
            or targets.get(address) != resource_type
            or flag is None
            or not isinstance(before, dict)
            or not isinstance(after, dict)
        ):
            raise DeployError(
                f"Cleanup preparation rejected an unrelated or destructive change at {address}. "
                "Only tracked bucket/repository deletion flags may be updated; no apply ran."
            )
        if (
            "after_unknown" not in change
            or after.get(flag) is not True
            or cleanup_has_unknown(change["after_unknown"])
        ):
            raise DeployError(
                f"Cleanup preparation requires a known {flag}=true at {address}; no apply ran."
            )
        before_other = {key: value for key, value in before.items() if key != flag}
        after_other = {key: value for key, value in after.items() if key != flag}
        if before_other != after_other:
            raise DeployError(
                f"Cleanup preparation would change fields other than {flag} at {address}; no apply ran."
            )
        if actions == ["no-op"] and before != after:
            raise DeployError(f"Cleanup plan has an inconsistent no-op at {address}; no apply ran.")
        seen.add(address)
    missing = sorted(set(targets) - seen)
    if missing:
        raise DeployError(
            "Cleanup plan did not confirm deletion flags for: " + ", ".join(missing) + "; no apply ran."
        )


def adopt_cleanup_flags(root: Path, args: argparse.Namespace) -> None:
    """Adopt only deletion flags without planning unrelated infrastructure updates."""
    targets = cleanup_targets(root, state_addresses(root))
    if not targets:
        return
    plan = root / "cleanup.tfplan"
    try:
        # This exceptional targeted plan only persists provider deletion settings.
        # Ordinary destruction follows separately and still uses Terraform's full graph.
        terraform(
            root, "plan", "-refresh=false",
            *(f"-target={address}" for address in sorted(targets)),
            f"-out={plan.name}",
        )
        described = run(["terraform", f"-chdir={root}", "show", "-json", plan.name], capture=True)
        validate_cleanup_plan(json.loads(described.stdout), targets)
        terraform(root, "apply", plan.name)
    finally:
        plan.unlink(missing_ok=True)


def destroy(args: argparse.Namespace) -> None:
    identity()
    if args.bootstrap_bucket and not args.all:
        raise DeployError("--bootstrap-bucket requires --all; remote states must be removed last.")
    # Terraform destroy must still receive the ephemeral SSM passwords while data exists.
    # No apply is run to prepare destruction, so absent resources cannot be recreated.
    flags(enable_data_layer=False, enable_compute=False, enable_redis=False,
          enable_mongodb=False, enable_mongo_ami_builder=False, service_desired_count=0)
    destroy_root(DEV, args, runtime=True)
    if not args.all:
        print("Runtime removed. Foundation, SSM, AMI/EBS snapshots and state bucket retained.")
        print("AMI/ECR/S3/state storage can still consume credits. To remove them: destroy --all")
        return
    clean_amis()
    (FOUNDATION / "cleanup.auto.tfvars").write_text(
        "ecr_force_delete = true\nassets_force_destroy = true\nfrontend_force_destroy = true\n")
    adopt_cleanup_flags(FOUNDATION, args)
    destroy_root(FOUNDATION, args)
    parameters = aws("ssm", "get-parameters-by-path", "--path", PREFIX, "--recursive")["Parameters"]
    names = [parameter["Name"] for parameter in parameters]
    for offset in range(0, len(names), 10):
        aws("ssm", "delete-parameters", "--names", *names[offset:offset + 10])
    if args.bootstrap_bucket:
        (BOOTSTRAP / "cleanup.auto.tfvars").write_text("allow_state_bucket_force_destroy = true\n")
        adopt_cleanup_flags(BOOTSTRAP, args)
        destroy_root(BOOTSTRAP, args)
        print("State bucket destroyed last. Keep the local bootstrap state/backup and .secrets backup.")
    print("Project cleanup finished. IAM login user, AWS Budget, local source and .secrets were retained.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=STAGES)
    parser.add_argument("--sender", help="SES sender email; required by prepare")
    parser.add_argument("--yes", action="store_true", help="Explicitly approve Terraform apply/destroy for this stage")
    parser.add_argument("--all", action="store_true", help="destroy: also delete foundation, SSM and project MongoDB AMIs")
    parser.add_argument("--bootstrap-bucket", action="store_true", help="destroy --all: delete state bucket last")
    args = parser.parse_args()
    if args.stage != "destroy" and (args.all or args.bootstrap_bucket):
        parser.error("--all and --bootstrap-bucket are only valid with destroy")
    if any(key.startswith("AWS_ENDPOINT_URL") and value for key, value in os.environ.items()):
        raise DeployError("Unset LocalStack AWS_ENDPOINT_URL variables before using real AWS deployment.")
    for executable in ("aws", "terraform"):
        if not shutil.which(executable):
            raise DeployError(f"Required command is missing: {executable}")
    if REGION != "us-east-1":
        raise DeployError("This dev tutorial currently uses us-east-1 and the existing helper scripts.")
    os.umask(0o077)
    actions = {"prepare": prepare, "bake": bake, "data": data_stage, "images": images,
               "bootstrap": bootstrap, "start": start, "frontend": frontend,
               "status": status, "stop": stop, "destroy": destroy}
    print(f"Stage={args.stage}; profile={PROFILE}; region={REGION}")
    actions[args.stage](args)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DeployError, OSError, ValueError, KeyError) as error:
        print(f"DEPLOY_FAILED: {redact(str(error))}", file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("Interrupted. Existing AWS resources remain; inspect status before retrying.", file=sys.stderr)
        raise SystemExit(130)

