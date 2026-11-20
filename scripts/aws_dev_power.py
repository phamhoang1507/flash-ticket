#!/usr/bin/env python3
"""Internal controller for aws-dev-up.sh and aws-dev-down.sh."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time

if __package__:
    from . import deploy_dev as deploy
else:
    import deploy_dev as deploy


DATA_NAMES = ("rds", "redis", "mongodb")
SERVICE_NAMES = set(deploy.SERVICE_NAMES)
IMAGE_INPUTS = (
    ".dockerignore", "shared",
    *(f"services/{service}/{part}" for service in deploy.SERVICE_NAMES
      for part in ("Dockerfile", "requirements.txt", "app")),
    "tools/mysql-bootstrap/Dockerfile", "tools/mysql-bootstrap/requirements.txt",
    "tools/mysql-bootstrap/bootstrap.py", ":(glob)database/[0-9][0-9][0-9]_*.sql",
    "assets/demo-banner.svg", "scripts/__init__.py", "scripts/local_token.py",
    "scripts/seed_catalog.py", "scripts/seed_aws_catalog.py", "scripts/init_queue.py",
    "scripts/reconcile_inventory.py", "scripts/init_assets.py",
)
QUEUE_COUNTS = (
    "ApproximateNumberOfMessages",
    "ApproximateNumberOfMessagesNotVisible",
    "ApproximateNumberOfMessagesDelayed",
)


def announce(message: str) -> None:
    print("[AWS DEV] " + message, flush=True)


def preflight(command: str) -> None:
    deploy.require_default_workspace()
    if any(name.startswith("AWS_ENDPOINT_URL") and value
           for name, value in os.environ.items()):
        raise deploy.DeployError("Bỏ các biến AWS_ENDPOINT_URL của LocalStack trước khi dùng AWS thật.")
    required = ["aws", "terraform", "bash"]
    if command == "up":
        required.extend(("docker", "git", "ssh", "ssh-keygen"))
        deploy.helper_preflight()
        for relative in ("scripts/bake_mongo_ami.py", "scripts/repair_mongodb_bootstrap.py"):
            if not (deploy.ROOT / relative).is_file():
                raise deploy.DeployError(f"Thiếu source cần thiết: {relative}; pull đầy đủ branch dự án.")
    for executable in required:
        if not shutil.which(executable):
            raise deploy.DeployError(f"Chưa cài lệnh bắt buộc: {executable}")


def sender_email(explicit: str | None) -> str:
    if explicit:
        value = explicit.strip()
    else:
        values: set[str] = set()
        for path in deploy.FOUNDATION.glob("*.tfvars"):
            values.update(re.findall(
                r'(?m)^\s*ses_sender_email\s*=\s*"([^"\n]+)"', path.read_text()))
        if len(values) != 1:
            raise deploy.DeployError(
                "Chưa xác định được một SES sender duy nhất. Chạy "
                "bash scripts/aws-dev-up.sh --sender EMAIL_CUA_BAN ở lần đầu.")
        value = values.pop()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        raise deploy.DeployError("SES sender không đúng định dạng email.")
    return value


def require_ses(foundation: dict) -> None:
    sender = foundation["ses_sender_email"]
    result = deploy.aws("ses", "get-identity-verification-attributes", "--identities", sender)
    status = result.get("VerificationAttributes", {}).get(sender, {}).get("VerificationStatus")
    if status != "Success":
        raise deploy.DeployError(
            "SES sender chưa verified. Mở email xác minh AWS SES rồi chạy lại aws-dev-up.sh. "
            "Script dừng trước khi tạo database/EC2 builder.")
    announce("SES sender đã verified; có thể tiếp tục dựng runtime.")


def service_activity(runtime: dict) -> bool:
    ecs = runtime.get("ecs")
    if not ecs:
        return False
    names = ecs.get("service_names", {})
    cluster = ecs.get("cluster_name")
    if not cluster or not isinstance(names, dict) or not names:
        raise deploy.DeployError("ECS output chưa hoàn chỉnh; kiểm tra Terraform state trước khi bootstrap.")
    described = deploy.aws("ecs", "describe-services", "--cluster", cluster,
                           "--services", *names.values())
    for failure in described.get("failures", []):
        if failure.get("reason") != "MISSING":
            raise deploy.DeployError("Không xác minh được ECS service: " + str(failure.get("reason", "unknown")))
    active = False
    for service in described.get("services", []):
        counts = [int(service.get(key, 0)) for key in ("desiredCount", "runningCount", "pendingCount")]
        if any(count < 0 for count in counts):
            raise deploy.DeployError("ECS trả về số lượng task không hợp lệ.")
        announce(f"{service['serviceName']}: desired={counts[0]}, running={counts[1]}, pending={counts[2]}")
        active = active or any(counts)
    return active


def repositories(foundation: dict) -> dict[str, str]:
    result = dict(foundation["ecr_repository_urls"])
    if set(result) != SERVICE_NAMES:
        raise deploy.DeployError("Foundation phải có đủ bốn ECR repository ứng dụng.")
    result["mysql_bootstrap"] = foundation["mysql_bootstrap_repository_url"]
    return result


def image_inputs_changed(tag: str) -> bool:
    source_commit = tag.rsplit("-", 1)[-1]
    resolved = deploy.run(["git", "rev-parse", "--verify", f"{source_commit}^{{commit}}"],
                          capture=True, check=False)
    if resolved.returncode:
        announce("Không có Git commit của image cũ trong local history; build lại để xác minh source đang deploy.")
        return True
    changed = deploy.run(["git", "diff", "--quiet", resolved.stdout.strip(), "--", *IMAGE_INPUTS],
                         capture=True, check=False)
    if changed.returncode not in (0, 1):
        raise deploy.DeployError("Không so sánh được Docker input với commit của image đã lưu.")
    untracked = deploy.run(["git", "ls-files", "--others", "--exclude-standard", "--", *IMAGE_INPUTS],
                           capture=True).stdout.strip()
    if changed.returncode == 1 or untracked:
        announce("Backend/Docker input đã thay đổi so với image đã lưu; cần build tag mới từ source đã commit.")
        return True
    return False


def saved_images_exist(foundation: dict) -> bool:
    tag = deploy.read_flags()["application_image_tag"]
    if not isinstance(tag, str) or not re.fullmatch(r"dev-\d+\.\d+\.\d+-[0-9a-f]{7,40}", tag):
        return False
    if image_inputs_changed(tag):
        return False
    complete = True
    for repository in repositories(foundation).values():
        exists = deploy.ecr_image_exists(repository, tag)
        complete = complete and exists
    if complete:
        announce(f"Dùng lại đủ năm image đã publish với tag {tag}; không build Docker lại.")
    return complete


def check_fresh_queues(foundation: dict, purge: bool) -> None:
    queue_urls = [foundation["booking_queue_url"], foundation["booking_dlq_url"]]
    nonempty = False
    for index, url in enumerate(queue_urls):
        result = deploy.aws("sqs", "get-queue-attributes", "--queue-url", url,
                            "--attribute-names", *QUEUE_COUNTS)
        attributes = result.get("Attributes", {})
        if any(name not in attributes for name in QUEUE_COUNTS):
            raise deploy.DeployError("SQS không trả đủ queue attributes; không tự bỏ qua bước kiểm tra message cũ.")
        counts = [int(attributes[name]) for name in QUEUE_COUNTS]
        if any(count < 0 for count in counts):
            raise deploy.DeployError("SQS trả về message count không hợp lệ.")
        label = "Booking queue" if index == 0 else "DLQ"
        announce(f"{label}: available={counts[0]}, in-flight={counts[1]}, delayed={counts[2]}")
        nonempty = nonempty or any(counts)
    if nonempty and not purge:
        raise deploy.DeployError(
            "Database sẽ tạo mới nhưng SQS/DLQ còn message của lượt test trước. "
            "Chỉ khi chấp nhận xóa toàn bộ message cũ, chạy lại aws-dev-up.sh --purge-queues. "
            "Script chưa tạo database mới và chưa purge queue.")
    if not purge:
        return
    announce("--purge-queues: xóa có chủ ý toàn bộ message trong Booking queue và DLQ trước khi tạo database mới.")
    for url in queue_urls:
        deploy.aws("sqs", "purge-queue", "--queue-url", url)
    for elapsed in range(0, 60, 10):
        announce(f"Đợi SQS hoàn tất purge: {elapsed}/60 giây.")
        time.sleep(10)


def available_saved_ami(account: str) -> bool:
    path = deploy.DEV / "mongodb-ami.auto.tfvars"
    if not path.is_file():
        return False
    values = re.findall(r'(?m)^\s*mongodb_ami_id\s*=\s*"(ami-[0-9a-f]{8,17})"', path.read_text())
    if len(values) != 1:
        raise deploy.DeployError("mongodb-ami.auto.tfvars không có đúng một mongodb_ami_id hợp lệ.")
    result = deploy.aws("ec2", "describe-images", "--owners", "self", "--filters",
                        f"Name=image-id,Values={values[0]}", "Name=state,Values=available")
    images = result.get("Images", [])
    if not images:
        announce("AMI đã lưu không còn available; cần bake lại khi runtime trống.")
        return False
    image = images[0]
    if len(images) != 1 or image.get("OwnerId") != account or image.get("Architecture") != "x86_64":
        raise deploy.DeployError("AMI MongoDB không thuộc account hiện tại hoặc không dùng kiến trúc x86_64.")
    tags = {item["Key"]: item["Value"] for item in image.get("Tags", [])}
    expected = {"Project": "flash-ticket", "Environment": "dev", "Role": "mongodb-ami"}
    if any(tags.get(key) != value for key, value in expected.items()):
        raise deploy.DeployError(
            "AMI đã lưu không có đủ project/environment/role tags của MongoDB dev. "
            "Kiểm tra AMI trước khi dùng; script không tự thay bằng một AMI owned khác.")
    announce(f"Dùng lại AMI MongoDB đang available: {values[0]}")
    return True


def wait_and_repair_mongodb() -> None:
    announce("Chờ cloud-init của MongoDB hoàn tất trước khi kiểm tra/repair lỗi bootstrap đã biết.")
    ssh = deploy.ROOT / "scripts/ssh-mongodb.sh"
    deadline = time.monotonic() + 90
    environment = dict(os.environ)
    environment.pop("TF_LOG", None)
    environment.pop("TF_LOG_PATH", None)
    environment["AWS_PAGER"] = ""
    environment["AWS_CLI_AUTO_PROMPT"] = "off"
    while True:
        try:
            result = subprocess.run(
                ["bash", str(ssh), "sudo -n cloud-init status --wait --long"],
                cwd=deploy.ROOT, env=environment, text=True, capture_output=True,
                timeout=330, check=False,
            )
        except subprocess.TimeoutExpired:
            raise deploy.DeployError(
                "Chờ SSH/cloud-init quá 330 giây. Kiểm tra node hiện tại; script không tự replace MongoDB.") from None
        if result.returncode in (0, 1, 2) and re.search(r"(?m)^status:\s*", result.stdout):
            break
        if result.returncode != 255 or time.monotonic() >= deadline:
            details = "\n".join(part.strip() for part in (result.stdout[-4000:], result.stderr[-4000:]) if part.strip())
            raise deploy.DeployError(
                f"Không chờ được cloud-init qua SSH (exit {result.returncode}). " + deploy.redact(details))
        announce("SSH chưa sẵn sàng; thử lại sau tối đa 5 giây.")
        time.sleep(min(5, max(0, deadline - time.monotonic())))
    announce("Kiểm tra MongoDB bằng repair helper có guard; không xóa data hay thay EC2.")
    deploy.run_python("scripts/repair_mongodb_bootstrap.py")


def ensure_data_nodes_running(runtime: dict) -> None:
    identifier = runtime["rds"]["identifier"]
    described = deploy.aws("rds", "describe-db-instances", "--db-instance-identifier", identifier)
    databases = described.get("DBInstances", [])
    if len(databases) != 1 or databases[0].get("DBInstanceIdentifier") != identifier:
        raise deploy.DeployError("Không xác minh được đúng RDS instance đã lưu trong Terraform output.")
    state = databases[0].get("DBInstanceStatus")
    if state == "stopped":
        announce("RDS đang stopped; start lại đúng instance hiện tại, giữ nguyên dữ liệu.")
        deploy.aws("rds", "start-db-instance", "--db-instance-identifier", identifier)
    elif state not in ("available", "starting"):
        raise deploy.DeployError(
            f"RDS đang ở trạng thái {state}; kiểm tra/chờ instance trước khi chạy lại up. "
            "Script không tự tạo lại database.")
    if state != "available":
        announce("Chờ RDS hiện tại chuyển sang available.")
        deploy.aws("rds", "wait", "db-instance-available", "--db-instance-identifier", identifier)

    instance_id = runtime["mongodb"]["instance_id"]
    described = deploy.aws("ec2", "describe-instances", "--instance-ids", instance_id)
    instances = [instance for reservation in described.get("Reservations", [])
                 for instance in reservation.get("Instances", [])]
    if len(instances) != 1 or instances[0].get("InstanceId") != instance_id:
        raise deploy.DeployError("Không xác minh được đúng EC2 MongoDB đã lưu trong Terraform output.")
    state = instances[0].get("State", {}).get("Name")
    if state == "stopped":
        announce("EC2 MongoDB đang stopped; start lại đúng instance hiện tại, giữ nguyên EBS/data.")
        deploy.aws("ec2", "start-instances", "--instance-ids", instance_id)
    elif state not in ("running", "pending"):
        raise deploy.DeployError(
            f"EC2 MongoDB đang ở trạng thái {state}; kiểm tra instance trước khi chạy lại up. "
            "Script không tự replace hoặc tạo lại MongoDB.")
    if state != "running":
        announce("Chờ EC2 MongoDB hiện tại chuyển sang running.")
        deploy.aws("ec2", "wait", "instance-running", "--instance-ids", instance_id)


def up(args: argparse.Namespace) -> None:
    args.sender = sender_email(args.sender)
    account = deploy.identity()
    announce(f"Account={account}; region={deploy.REGION}; profile={deploy.PROFILE}; workspace=default.")
    announce("Khôi phục/đồng bộ foundation và SSM. Terraform vẫn hỏi yes trừ khi bạn truyền --yes.")
    deploy.prepare(args)
    foundation = deploy.require_foundation_outputs(
        "ecr_repository_urls", "mysql_bootstrap_repository_url", "ecr_registry_url",
        "ses_sender_email", "booking_queue_url", "booking_dlq_url", "web",
    )
    require_ses(foundation)
    runtime = deploy.outputs(deploy.DEV)
    addresses = deploy.state_addresses(deploy.DEV)
    has_data_state = any("module.data_layer" in address for address in addresses)
    has_compute_state = any("module.ecs_runtime" in address for address in addresses)
    active = service_activity(runtime)
    complete_data = all(runtime.get(name) for name in DATA_NAMES)
    fresh_data = not has_data_state and not has_compute_state and not runtime.get("ecs")
    fresh_data = fresh_data and not any(runtime.get(name) for name in DATA_NAMES)
    if args.purge_queues and (not fresh_data or active):
        raise deploy.DeployError(
            "--purge-queues chỉ dùng khi data/runtime đã được xóa và không có ECS app hoạt động. "
            "Script không purge queue khi đang giữ database hiện tại.")
    if active and not complete_data:
        raise deploy.DeployError(
            "ECS còn desired/running/pending nhưng data outputs chưa đầy đủ. "
            "Kiểm tra state và service hiện tại; script không reset database hoặc tắt compute tự động.")
    if has_data_state and not complete_data:
        raise deploy.DeployError(
            "Data layer đang tạo/xóa dở, chưa đủ RDS/Redis/MongoDB outputs. "
            "Kiểm tra Terraform plan rồi resume stage data/bootstrap thủ công. "
            "Script không tự tạo lại MySQL khi SQS có thể còn job của database cũ.")
    if has_compute_state and not runtime.get("ecs"):
        raise deploy.DeployError(
            "Compute state tồn tại nhưng thiếu ECS output để xác minh task hiện tại. "
            "Kiểm tra/resume Terraform trước; script chưa bootstrap hoặc tắt service.")
    current = deploy.read_flags()
    if not complete_data and (current["enable_compute"] or has_compute_state or runtime.get("ecs")):
        raise deploy.DeployError(
            "Runtime đang ở trạng thái tạo dở: compute tồn tại nhưng data chưa đầy đủ. "
            "Resume Terraform/bootstrap sau khi kiểm tra plan; không tự disable compute để dựng lại data.")
    if fresh_data:
        check_fresh_queues(foundation, args.purge_queues)
    if args.rebuild_images or not saved_images_exist(foundation):
        announce("Build/push năm image theo Git commit hiện tại; working tree phải sạch.")
        deploy.images(args)
    if not complete_data:
        if runtime.get("mongodb"):
            announce("Giữ nguyên EC2 MongoDB hiện tại và AMI configuration của node.")
        elif not available_saved_ami(account):
            if has_data_state:
                raise deploy.DeployError(
                    "Data layer tạo dở và không có AMI saved available. Cần xử lý AMI trước; "
                    "không chạy bake vì có thể ảnh hưởng data đang được giữ.")
            announce("Chưa có AMI available; bake MongoDB AMI rồi tự dọn EC2 builder khi thành công.")
            deploy.bake(args)
        deploy.data_stage(args)
        runtime = deploy.require_outputs(*DATA_NAMES)
    ensure_data_nodes_running(runtime)
    if active:
        announce("ECS đang hoạt động: giữ nguyên dữ liệu, bỏ qua bootstrap và seed.")
    else:
        # An old local desired_count=1 is not evidence of an active service.
        # Recheck actual tasks before applying the explicitly stopped bootstrap configuration.
        if service_activity(runtime):
            raise deploy.DeployError("ECS vừa bắt đầu hoạt động; chạy lại up để đánh giá lại, chưa bootstrap.")
        deploy.flags(service_desired_count=0)
        wait_and_repair_mongodb()
        announce("Bootstrap idempotent khi toàn bộ ECS app đã về 0; không reset inventory đang có.")
        deploy.bootstrap(args)
    deploy.start(args)
    deploy.frontend(args)
    deploy.status(args)
    announce("Hạ tầng/API/frontend đã qua các bước kiểm tra startup. Việc gửi email cần kiểm tra riêng trên booking thực tế.")


def down(args: argparse.Namespace) -> None:
    account = deploy.identity()
    announce(f"Account={account}; region={deploy.REGION}; profile={deploy.PROFILE}; workspace=default.")
    if args.mode == "stop":
        announce("Chỉ scale bốn ECS service về 0; RDS, Redis, MongoDB và storage vẫn có thể tính phí.")
        deploy.stop(args)
        announce("ECS đã về 0. Không chạy Terraform apply và không xóa/stop database.")
        deploy.status(args)
        return
    args.all = args.mode == "all"
    args.bootstrap_bucket = args.all
    if args.all:
        announce("Xóa runtime/database, AMI/snapshot, foundation, SSM và state bucket sau cùng.")
    else:
        announce("Xóa runtime và database; giữ foundation, SQS/DLQ, SSM, image, AMI và state bucket.")
    deploy.destroy(args)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("up", help="Dựng/bật AWS dev và publish frontend")
    start.add_argument("--sender", help="SES sender; tự đọc *.tfvars khi đã lưu trước đó")
    start.add_argument("--yes", action="store_true", help="Tự approve Terraform cho lần chạy này")
    start.add_argument("--rebuild-images", action="store_true", help="Build image từ Git commit hiện tại")
    start.add_argument("--purge-queues", action="store_true", help="Xóa message cũ khi tạo mới toàn bộ data layer")
    stop = commands.add_parser("down", help="Chọn rõ mức tắt hoặc xóa tài nguyên")
    stop.add_argument("mode", choices=("stop", "runtime", "all"))
    stop.add_argument("--yes", action="store_true", help="Tự approve Terraform cho lần chạy này")
    args = parser.parse_args()
    os.umask(0o077)
    preflight(args.command)
    if args.command == "up":
        up(args)
    else:
        down(args)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (deploy.DeployError, OSError, ValueError, KeyError, TypeError) as error:
        print("AWS_DEV_POWER_FAILED: " + deploy.redact(str(error)), file=sys.stderr)
        print(
            "Tài nguyên đã tạo có thể vẫn còn. Kiểm tra trạng thái trước khi thử lại. "
            "Dùng bash scripts/aws-dev-down.sh stop | runtime | all theo mức dọn cần thiết.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    except KeyboardInterrupt:
        print(
            "Đã ngắt lệnh; AWS không tự dọn tài nguyên. Dùng aws-dev-down.sh với mode phù hợp.",
            file=sys.stderr,
        )
        raise SystemExit(130)
