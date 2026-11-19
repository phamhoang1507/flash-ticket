output "distribution_arn" {
  description = "Distribution allowed to read banners by the existing assets bucket policy."
  value       = aws_cloudfront_distribution.this.arn
}

output "web" {
  description = "Public deployment configuration consumed by the runtime stack and the frontend build."
  value = {
    frontend_url         = local.frontend_url
    frontend_bucket_name = aws_s3_bucket.frontend.id
    distribution_id      = aws_cloudfront_distribution.this.id
    cognito_user_pool_id = aws_cognito_user_pool.this.id
    cognito_client_id    = aws_cognito_user_pool_client.frontend.id
    cognito_issuer       = local.cognito_issuer
    cognito_domain       = "https://${aws_cognito_user_pool_domain.this.domain}.auth.${var.aws_region}.amazoncognito.com"
  }
}
