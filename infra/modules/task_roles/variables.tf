variable "name_prefix" {
  description = "Prefix used for ECS task role names."
  type        = string

  validation {
    condition = (
      length(var.name_prefix) >= 1 &&
      length(var.name_prefix) <= 32 &&
      can(regex("^[A-Za-z0-9-]+$", var.name_prefix))
    )
    error_message = "name_prefix must contain 1 to 32 letters, digits, or hyphens."
  }
}

variable "aws_region" {
  description = "AWS Region containing the ECS services."
  type        = string
}

variable "aws_account_id" {
  description = "AWS account allowed to assume the task roles through ECS."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "aws_account_id must contain exactly 12 digits."
  }
}

variable "assets_bucket_arn" {
  description = "ARN of the private S3 assets bucket."
  type        = string
}

variable "booking_queue_arn" {
  description = "ARN of the main booking SQS queue."
  type        = string
}

variable "ses_identity_arn" {
  description = "ARN of the SES identity used to send ticket emails."
  type        = string
}

variable "ses_sender_email" {
  description = "Verified SES sender email address."
  type        = string
}