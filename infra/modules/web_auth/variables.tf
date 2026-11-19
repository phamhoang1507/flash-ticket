variable "name_prefix" {
  description = "Lowercase prefix shared by this environment's resources."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,29}$", var.name_prefix))
    error_message = "name_prefix must contain 3-30 lowercase letters, digits or hyphens and start with a letter."
  }
}

variable "aws_region" {
  type = string
}

variable "aws_account_id" {
  type = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "aws_account_id must contain exactly 12 digits."
  }
}

variable "assets_bucket_arn" {
  description = "Existing private assets bucket ARN; its policy remains owned by the assets module."
  type        = string

  validation {
    condition     = can(regex("^arn:aws:s3:::[a-z0-9][a-z0-9.-]+$", var.assets_bucket_arn))
    error_message = "assets_bucket_arn must be an S3 bucket ARN in the AWS commercial partition."
  }
}

variable "assets_bucket_regional_domain_name" {
  description = "Regional REST endpoint of the existing assets bucket, without a URL scheme."
  type        = string
}

variable "force_destroy" {
  description = "Allow Terraform to delete uploaded frontend objects when destroying the frontend bucket."
  type        = bool
  default     = false
}

variable "allow_localhost_callbacks" {
  description = "Additionally allow the existing local frontend ports to use this development Cognito client."
  type        = bool
  default     = false
}
