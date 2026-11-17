variable "aws_region" {
  description = "AWS region used for the Terraform state bucket."
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "AWS profile used by Terraform."
  type        = string
  default     = "flash-ticket-terraform"
}

variable "aws_account_id" {
  description = "Expected AWS account ID."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "aws_account_id must contain exactly 12 digits."
  }
}

variable "allow_state_bucket_force_destroy" {
  description = "Allow deletion of all objects and versions when destroying the state bucket."
  type        = bool
  default     = false
}