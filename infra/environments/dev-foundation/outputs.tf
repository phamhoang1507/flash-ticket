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