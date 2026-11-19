variable "name_prefix" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "aws_account_id" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "private_app_subnet_ids" {
  type = list(string)
}

variable "task_subnet_ids" {
  type = list(string)
}

variable "assign_public_ip" {
  type = bool
}

variable "security_group_ids" {
  type = map(string)
}

variable "repository_urls" {
  type = map(string)
}

variable "execution_role_arns" {
  type = map(string)
}

variable "task_role_arns" {
  type = map(string)
}

variable "log_group_names" {
  type = map(string)
}

variable "bootstrap_repository_url" {
  type = string
}

variable "bootstrap_execution_role_arn" {
  type = string
}

variable "image_tag" {
  type = string
}

variable "desired_count" {
  type = number

  validation {
    condition = (
      var.desired_count >= 0 &&
      floor(var.desired_count) == var.desired_count
    )
    error_message = "desired_count must be a non-negative integer."
  }
}

variable "use_fargate_spot" {
  type = bool
}

variable "mysql" {
  type = object({
    address  = string
    port     = number
    database = string
  })
}

variable "assets_bucket_name" {
  type = string
}

variable "queue_name" {
  type = string
}

variable "ses_sender_email" {
  type = string
}

variable "web" {
  type = object({
    frontend_url           = string
    frontend_bucket_name   = string
    distribution_id        = string
    cognito_user_pool_id   = string
    cognito_client_id      = string
    cognito_issuer         = string
    cognito_domain         = string
  })
}
