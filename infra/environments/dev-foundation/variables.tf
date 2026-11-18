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

variable "ecr_force_delete" {
  description = "Allow deletion of repositories containing images."
  type        = bool
  default     = false
}

variable "assets_force_destroy" {
  description = "Allow deletion of all objects in the assets bucket."
  type        = bool
  default     = false
}

variable "ses_sender_email" {
  description = "Verified email used for development ticket delivery."
  type        = string
}