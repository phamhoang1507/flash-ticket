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