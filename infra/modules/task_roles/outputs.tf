output "task_role_arns" {
  description = "ECS task role ARNs indexed by service name."

  value = {
    for service, role in aws_iam_role.task :
    service => role.arn
  }
}