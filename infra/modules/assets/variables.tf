variable "name_prefix" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "aws_account_id" {
  type = string
}

variable "force_destroy" {
  description = "Allow deletion of all objects when destroying the bucket."
  type        = bool
  default     = false
}

variable "enable_versioning" {
  description = "Enable object versioning for application assets."
  type        = bool
  default     = false
}

variable "demo_banner_path" {
  description = "Local path to the demo banner."
  type        = string

  validation {
    condition     = fileexists(var.demo_banner_path)
    error_message = "The demo banner file does not exist."
  }
}
variable "cloudfront_distribution_arn" {
  description = "CloudFront distribution permitted to read banners through OAC."
  type        = string
  default     = null
}
