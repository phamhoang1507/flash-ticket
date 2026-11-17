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

variable "ecr_force_delete" {
  description = "Allow deletion of dev repositories containing images."
  type        = bool
  default     = false
}