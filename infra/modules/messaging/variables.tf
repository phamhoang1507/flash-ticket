variable "name_prefix" {
  type = string
}

variable "max_receive_count" {
  description = "Receive attempts before moving a message to the DLQ."
  type        = number
  default     = 5

  validation {
    condition = (
      var.max_receive_count >= 1 &&
      var.max_receive_count <= 1000 &&
      floor(var.max_receive_count) == var.max_receive_count
    )
    error_message = "max_receive_count must be an integer from 1 to 1000."
  }
}