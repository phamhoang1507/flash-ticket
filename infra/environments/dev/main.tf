provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile

  allowed_account_ids = [var.aws_account_id]

  default_tags {
    tags = {
      Project     = "flash-ticket"
      Environment = var.environment
      ManagedBy   = "Terraform"
    }
  }
}

module "vpc" {
  source = "../../modules/vpc"

  name_prefix        = "flash-ticket-${var.environment}"
  vpc_cidr           = var.vpc_cidr
  availability_zones = var.availability_zones
  enable_nat_gateway = var.enable_nat_gateway
}

module "ecr" {
  source = "../../modules/ecr"

  name_prefix           = "flash-ticket-${var.environment}"
  image_tag_prefix      = "${var.environment}-"
  image_retention_count = 5
  force_delete          = var.ecr_force_delete
}