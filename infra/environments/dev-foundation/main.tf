locals {
  service_names = toset([
    "booking",
    "catalog",
    "relay",
    "worker"
  ])
}

provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile

  allowed_account_ids = [var.aws_account_id]

  default_tags {
    tags = {
      Project     = "flash-ticket"
      Environment = "dev"
      ManagedBy   = "Terraform"
    }
  }
}

module "ecr" {
  source = "../../modules/ecr"

  name_prefix           = "flash-ticket-dev"
  service_names         = local.service_names
  image_tag_prefix      = "dev-"
  image_retention_count = 5
  force_delete          = var.ecr_force_delete
}

module "iam" {
  source = "../../modules/iam"

  name_prefix    = "flash-ticket-dev"
  aws_region     = var.aws_region
  aws_account_id = var.aws_account_id

  ecr_repository_arns = module.ecr.repository_arns

  ssm_parameter_prefix = "/flash-ticket/dev"
  log_group_prefix     = "/ecs/flash-ticket-dev"
}

module "assets" {
  source = "../../modules/assets"

  name_prefix    = "flash-ticket-dev"
  aws_region     = var.aws_region
  aws_account_id = var.aws_account_id

  cloudfront_distribution_arn = module.web_auth.distribution_arn

  force_destroy     = var.assets_force_destroy
  enable_versioning = false

  demo_banner_path = abspath(
    "${path.module}/../../../assets/demo-banner.svg"
  )
}

module "messaging" {
  source = "../../modules/messaging"

  name_prefix       = "flash-ticket-dev"
  max_receive_count = 5
}

module "ses" {
  source = "../../modules/ses"

  sender_email = var.ses_sender_email
}

module "task_roles" {
  source = "../../modules/task_roles"

  name_prefix    = "flash-ticket-dev"
  aws_region     = var.aws_region
  aws_account_id = var.aws_account_id

  assets_bucket_arn = module.assets.bucket_arn
  booking_queue_arn = module.messaging.queue_arn

  ses_identity_arn = module.ses.identity_arn
  ses_sender_email = module.ses.sender_email
}