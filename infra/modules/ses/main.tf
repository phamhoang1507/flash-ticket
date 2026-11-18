terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
  }
}

resource "aws_sesv2_email_identity" "this" {
  email_identity = var.sender_email
}