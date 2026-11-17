variable "name_prefix" {
  description = "Prefix used for resource names."
  type        = string
}

variable "vpc_cidr" {
  description = "IPv4 /16 CIDR for the VPC."
  type        = string

  validation {
    condition = (
      can(cidrnetmask(var.vpc_cidr)) &&
      endswith(var.vpc_cidr, "/16")
    )
    error_message = "vpc_cidr must be a valid IPv4 /16 CIDR."
  }
}

variable "availability_zones" {
  description = "Exactly two distinct availability zones."
  type        = list(string)

  validation {
    condition = (
      length(var.availability_zones) == 2 &&
      length(distinct(var.availability_zones)) == 2
    )
    error_message = "Provide exactly two distinct availability zones."
  }
}

variable "enable_nat_gateway" {
  description = "Create one NAT Gateway per AZ when enabled."
  type        = bool
  default     = false
}