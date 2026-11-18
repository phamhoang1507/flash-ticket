variable "enabled" {
  description = "Create the temporary MongoDB AMI builder."
  type        = bool
  default     = false
}

variable "name_prefix" {
  description = "Prefix used for builder resource names."
  type        = string
}

variable "vpc_id" {
  description = "VPC containing the builder."
  type        = string
}

variable "public_subnet_id" {
  description = "Public subnet with a route to an Internet Gateway."
  type        = string
}