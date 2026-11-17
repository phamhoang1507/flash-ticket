variable "name_prefix" {
  description = "Prefix for ECR repository names."
  type        = string
}

variable "service_names" {
  description = "Backend services requiring private repositories."
  type        = set(string)

  default = [
    "booking",
    "catalog",
    "relay",
    "worker"
  ]

  validation {
    condition = (
      length(var.service_names) > 0 &&
      alltrue([
        for name in var.service_names :
        can(regex("^[a-z][a-z0-9_-]*$", name))
      ])
    )
    error_message = "Provide valid lowercase service names."
  }
}

variable "image_tag_prefix" {
  description = "Image tag prefix managed by the retention policy."
  type        = string
  default     = "dev-"

  validation {
    condition     = length(var.image_tag_prefix) > 0
    error_message = "image_tag_prefix must not be empty."
  }
}

variable "image_retention_count" {
  description = "Number of tagged images retained per repository."
  type        = number
  default     = 5

  validation {
    condition = (
      var.image_retention_count >= 1 &&
      floor(var.image_retention_count) == var.image_retention_count
    )
    error_message = "image_retention_count must be a positive integer."
  }
}

variable "force_delete" {
  description = "Allow repository deletion even when images remain."
  type        = bool
  default     = false
}