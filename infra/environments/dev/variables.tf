variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "aws_profile" {
  type    = string
  default = "flash-ticket-terraform"
}

variable "aws_account_id" {
  type = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "aws_account_id must contain exactly 12 digits."
  }
}

variable "environment" {
  type    = string
  default = "dev"

  validation {
    condition     = var.environment == "dev"
    error_message = "This root configuration is reserved for dev."
  }
}

variable "vpc_cidr" {
  type    = string
  default = "10.42.0.0/16"
}

variable "availability_zones" {
  type    = list(string)
  default = ["us-east-1a", "us-east-1b"]
}

variable "enable_nat_gateway" {
  type    = bool
  default = false
}

variable "enable_multi_az_data" {
  description = "Enable Multi-AZ deployment for supported data services."
  type        = bool
  default     = false
}

variable "rds_instance_class" {
  description = "RDS MySQL instance class."
  type        = string
  default     = "db.t3.micro"
}

variable "rds_master_password" {
  description = "Ephemeral RDS master password loaded from SSM."
  type        = string
  sensitive   = true
  ephemeral   = true
}

variable "rds_master_password_version" {
  description = "Version counter for intentional RDS password changes."
  type        = number
  default     = 1
}

variable "rds_backup_retention_days" {
  description = "Automated backup retention in days."
  type        = number
  default     = 1
}

variable "rds_deletion_protection" {
  description = "Prevent deletion of the RDS instance."
  type        = bool
  default     = false
}

variable "rds_skip_final_snapshot" {
  description = "Skip the final snapshot when deleting RDS."
  type        = bool
  default     = true
}

variable "rds_final_snapshot_identifier" {
  description = "Final snapshot identifier when required."
  type        = string
  default     = null
}