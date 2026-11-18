variable "name_prefix" {
  description = "Prefix for IAM role names."
  type        = string
}

variable "aws_region" {
  description = "Region where ECS tasks will run."
  type        = string
}

variable "aws_account_id" {
  description = "AWS account containing the resources."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "aws_account_id must contain exactly 12 digits."
  }
}

variable "ecr_repository_arns" {
  description = "ECR repository ARNs keyed by service name."
  type        = map(string)

  validation {
    condition     = length(var.ecr_repository_arns) > 0
    error_message = "Provide at least one ECR repository ARN."
  }
}

variable "ssm_parameter_prefix" {
  description = "Parameter Store namespace for this environment."
  type        = string

  validation {
    condition = (
      startswith(var.ssm_parameter_prefix, "/") &&
      length(trim(var.ssm_parameter_prefix, "/")) > 0
    )
    error_message = "Provide an absolute non-root SSM path."
  }
}

variable "log_group_prefix" {
  description = "Prefix for the workload CloudWatch log groups."
  type        = string

  validation {
    condition     = startswith(var.log_group_prefix, "/")
    error_message = "log_group_prefix must start with a slash."
  }
}