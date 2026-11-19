variable "enable_compute" {
  description = "Create ECS tasks/services and HTTP API; leave false while building the MongoDB AMI."
  type        = bool
  default     = false
}

variable "service_desired_count" {
  description = "Keep at zero until MySQL/MongoDB bootstrap and Redis reconciliation succeed."
  type        = number
  default     = 0

  validation {
    condition = (
      var.service_desired_count >= 0 &&
      floor(var.service_desired_count) == var.service_desired_count
    )
    error_message = "service_desired_count must be a non-negative integer."
  }
}

variable "use_fargate_spot" {
  type    = bool
  default = true
}

variable "application_image_tag" {
  description = "Immutable tag shared by the four application images and the MySQL bootstrap image."
  type        = string
  default     = ""

  validation {
    condition = (
      !var.enable_compute ||
      can(regex("^dev-[0-9]+\\.[0-9]+\\.[0-9]+-[0-9a-f]{7,40}$", var.application_image_tag))
    )
    error_message = "When compute is enabled, use a tag such as dev-1.0.0-a1b2c3d4e5f6."
  }
}

data "terraform_remote_state" "foundation" {
  count   = var.enable_compute ? 1 : 0
  backend = "s3"

  config = {
    bucket  = "flash-ticket-tfstate-${var.aws_account_id}-${var.aws_region}"
    key     = "environments/dev-foundation/terraform.tfstate"
    region  = var.aws_region
    profile = var.aws_profile
    encrypt = true
  }
}

resource "terraform_data" "compute_requirements" {
  count = var.enable_compute ? 1 : 0

  lifecycle {
    precondition {
      condition     = var.enable_data_layer && var.enable_redis && var.enable_mongodb
      error_message = "Compute requires enable_data_layer=true, enable_redis=true and enable_mongodb=true. Initialize databases before setting service_desired_count above zero."
    }
  }
}

module "ecs_runtime" {
  count  = var.enable_compute ? 1 : 0
  source = "../../modules/ecs_runtime"

  name_prefix    = "flash-ticket-dev"
  aws_region     = var.aws_region
  aws_account_id = var.aws_account_id
  vpc_id         = module.vpc.vpc_id

  private_app_subnet_ids = [
    module.vpc.private_app_subnet_ids["a"],
    module.vpc.private_app_subnet_ids["b"],
  ]

  task_subnet_ids = var.enable_nat_gateway ? [
    module.vpc.private_app_subnet_ids["a"],
    module.vpc.private_app_subnet_ids["b"],
    ] : [
    module.vpc.public_subnet_ids["a"],
    module.vpc.public_subnet_ids["b"],
  ]

  assign_public_ip   = !var.enable_nat_gateway
  security_group_ids = module.vpc.security_group_ids

  repository_urls     = data.terraform_remote_state.foundation[0].outputs.ecr_repository_urls
  execution_role_arns = data.terraform_remote_state.foundation[0].outputs.ecs_execution_role_arns
  task_role_arns      = data.terraform_remote_state.foundation[0].outputs.ecs_task_role_arns
  log_group_names     = data.terraform_remote_state.foundation[0].outputs.ecs_log_group_names

  bootstrap_repository_url     = data.terraform_remote_state.foundation[0].outputs.mysql_bootstrap_repository_url
  bootstrap_execution_role_arn = data.terraform_remote_state.foundation[0].outputs.mysql_bootstrap_execution_role_arn

  image_tag        = var.application_image_tag
  desired_count    = var.service_desired_count
  use_fargate_spot = var.use_fargate_spot

  mysql = try(module.data_layer[0].mysql, null)

  assets_bucket_name = data.terraform_remote_state.foundation[0].outputs.assets_bucket_name
  queue_name         = data.terraform_remote_state.foundation[0].outputs.booking_queue_name
  ses_sender_email   = data.terraform_remote_state.foundation[0].outputs.ses_sender_email
  web                = data.terraform_remote_state.foundation[0].outputs.web

  depends_on = [terraform_data.compute_requirements]
}

output "ecs" {
  description = "Service/task identifiers and per-task networking for deployment scripts."
  value       = try(module.ecs_runtime[0].ecs, null)
}

output "api_url" {
  value = try(module.ecs_runtime[0].api_url, null)
}
