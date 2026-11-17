output "state_bucket_name" {
  description = "S3 bucket used to store Terraform state."
  value       = aws_s3_bucket.terraform_state.id
}

output "state_bucket_region" {
  description = "Region containing the Terraform state bucket."
  value       = var.aws_region
}