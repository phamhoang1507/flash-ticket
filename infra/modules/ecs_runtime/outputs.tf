output "ecs" {
  value = {
    cluster_name = aws_ecs_cluster.this.name

    service_names = {
      for name, service in aws_ecs_service.application : name => service.name
    }

    task_definition_arns = merge(
      {
        for name, task in aws_ecs_task_definition.application : name => task.arn
      },
      {
        mysql_bootstrap = aws_ecs_task_definition.mysql_bootstrap.arn
      }
    )

    task_networks = merge(
      {
        for name in local.service_names : name => {
          subnets = var.task_subnet_ids
          security_groups = [
            var.security_group_ids[local.task_security_group_keys[name]]
          ]
          assign_public_ip = var.assign_public_ip ? "ENABLED" : "DISABLED"
        }
      },
      {
        mysql_bootstrap = {
          subnets          = var.task_subnet_ids
          security_groups  = [aws_security_group.mysql_bootstrap.id]
          assign_public_ip = var.assign_public_ip ? "ENABLED" : "DISABLED"
        }
      }
    )
  }
}

output "api_url" {
  value = aws_apigatewayv2_api.this.api_endpoint
}
