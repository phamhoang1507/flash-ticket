variable "name_prefix" {
  description = "Prefix used for data layer resource names."
  type        = string
}

variable "private_data_subnet_ids" {
  description = "Private data subnet IDs spanning at least two Availability Zones."
  type        = list(string)

  validation {
    condition     = length(var.private_data_subnet_ids) >= 2
    error_message = "At least two private data subnets are required."
  }
}

variable "rds_security_group_id" {
  description = "Security group controlling connections to MySQL."
  type        = string
}

variable "primary_availability_zone" {
  description = "Availability Zone used for Single-AZ MySQL."
  type        = string
}

variable "enable_multi_az_data" {
  description = "Enable a Multi-AZ RDS deployment."
  type        = bool
  default     = false
}

variable "rds_instance_class" {
  description = "RDS MySQL instance class."
  type        = string
  default     = "db.t3.micro"
}

variable "rds_master_password" {
  description = "Master password supplied only during Terraform execution."
  type        = string
  sensitive   = true
  ephemeral   = true

  validation {
    condition = (
      length(var.rds_master_password) >= 8 &&
      length(var.rds_master_password) <= 41
    )
    error_message = "The MySQL master password must contain 8 to 41 characters."
  }
}

variable "rds_master_password_version" {
  description = "Increment this number when intentionally changing the master password."
  type        = number
  default     = 1

  validation {
    condition = (
      var.rds_master_password_version >= 1 &&
      floor(var.rds_master_password_version) == var.rds_master_password_version
    )
    error_message = "The password version must be a positive integer."
  }
}

variable "rds_backup_retention_days" {
  description = "Number of days to retain automated backups."
  type        = number
  default     = 1

  validation {
    condition = (
      var.rds_backup_retention_days >= 0 &&
      var.rds_backup_retention_days <= 35 &&
      floor(var.rds_backup_retention_days) == var.rds_backup_retention_days
    )
    error_message = "Backup retention must be an integer between 0 and 35."
  }
}

variable "rds_deletion_protection" {
  description = "Prevent accidental deletion of the database."
  type        = bool
  default     = false
}

variable "rds_skip_final_snapshot" {
  description = "Skip the final snapshot when deleting the database."
  type        = bool
  default     = true
}

variable "rds_final_snapshot_identifier" {
  description = "Final snapshot identifier when final snapshots are enabled."
  type        = string
  default     = null
}