output "execution_role_arns" {
  description = "Execution role ARNs keyed by service name."

  value = {
    for service, role in aws_iam_role.execution :
    service => role.arn
  }
}

output "expected_log_group_names" {
  description = "Log group names to create with the ECS workload."
  value       = local.log_group_names
}