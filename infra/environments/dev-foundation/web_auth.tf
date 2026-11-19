variable "frontend_force_destroy" {
  description = "Allow development teardown to delete all uploaded frontend build files."
  type        = bool
  default     = false
}

variable "cognito_allow_localhost_callbacks" {
  description = "Allow localhost ports 8080 and 5173 to log in through the development Cognito pool."
  type        = bool
  default     = false
}

module "web_auth" {
  source = "../../modules/web_auth"

  name_prefix    = "flash-ticket-dev"
  aws_region     = var.aws_region
  aws_account_id = var.aws_account_id

  assets_bucket_arn                  = module.assets.bucket_arn
  assets_bucket_regional_domain_name = module.assets.bucket_regional_domain_name
  force_destroy                     = var.frontend_force_destroy
  allow_localhost_callbacks         = var.cognito_allow_localhost_callbacks
}

output "web" {
  value = module.web_auth.web
}
