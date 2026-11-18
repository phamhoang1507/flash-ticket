output "sender_email" {
  value = aws_sesv2_email_identity.this.email_identity
}

output "identity_arn" {
  value = aws_sesv2_email_identity.this.arn
}