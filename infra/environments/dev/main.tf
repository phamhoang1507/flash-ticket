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

module "data_layer" {
  source = "../../modules/data_layer"

  name_prefix = "flash-ticket-dev"

  private_data_subnet_ids = [
    module.vpc.private_data_subnet_ids["a"],
    module.vpc.private_data_subnet_ids["b"],
  ]

  enable_multi_az_data = var.enable_multi_az_data

  rds_security_group_id     = module.vpc.security_group_ids["rds"]
  primary_availability_zone = var.availability_zones[0]
  rds_instance_class        = var.rds_instance_class

  rds_master_password         = var.rds_master_password
  rds_master_password_version = var.rds_master_password_version

  rds_backup_retention_days     = var.rds_backup_retention_days
  rds_deletion_protection       = var.rds_deletion_protection
  rds_skip_final_snapshot       = var.rds_skip_final_snapshot
  rds_final_snapshot_identifier = var.rds_final_snapshot_identifier

  enable_redis             = var.enable_redis
  redis_security_group_id  = module.vpc.security_group_ids["redis"]
  redis_availability_zones = var.availability_zones
  redis_node_type          = var.redis_node_type

  redis_auth_token              = var.redis_auth_token
  redis_auth_token_version      = var.redis_auth_token_version
  redis_snapshot_retention_days = var.redis_snapshot_retention_days
}

module "mongo_ami_builder" {
  source = "../../modules/mongo_ami_builder"

  enabled     = var.enable_mongo_ami_builder
  name_prefix = "flash-ticket-dev"

  vpc_id           = module.vpc.vpc_id
  public_subnet_id = module.vpc.public_subnet_ids["a"]

  depends_on = [
    module.vpc,
  ]
}