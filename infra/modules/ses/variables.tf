variable "sender_email" {
  description = "Email address used to send development tickets."
  type        = string

  validation {
    condition = can(regex(
      "^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}$",
      var.sender_email
    ))
    error_message = "Provide a valid email address."
  }
}