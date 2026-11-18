output "ecr_repository_urls" {
  value = module.ecr.repository_urls
}

output "ecr_repository_arns" {
  value = module.ecr.repository_arns
}

output "ecr_registry_url" {
  value = module.ecr.registry_url
}

output "ecs_execution_role_arns" {
  value = module.iam.execution_role_arns
}

output "ecs_log_group_names" {
  value = module.iam.expected_log_group_names
}

output "assets_bucket_name" {
  value = module.assets.bucket_name
}

output "assets_bucket_arn" {
  value = module.assets.bucket_arn
}

output "assets_bucket_regional_domain_name" {
  value = module.assets.bucket_regional_domain_name
}

output "booking_queue_name" {
  value = module.messaging.queue_name
}

output "booking_queue_url" {
  value = module.messaging.queue_url
}

output "booking_queue_arn" {
  value = module.messaging.queue_arn
}

output "booking_dlq_name" {
  value = module.messaging.dlq_name
}

output "booking_dlq_url" {
  value = module.messaging.dlq_url
}

output "booking_dlq_arn" {
  value = module.messaging.dlq_arn
}