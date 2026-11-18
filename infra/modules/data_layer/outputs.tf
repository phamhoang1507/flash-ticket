output "mysql" {
  description = "Non-secret MySQL connection information."

  value = {
    identifier     = aws_db_instance.mysql.identifier
    address        = aws_db_instance.mysql.address
    port           = aws_db_instance.mysql.port
    database       = aws_db_instance.mysql.db_name
    engine_version = aws_db_instance.mysql.engine_version_actual
  }
}

output "redis" {
  description = "Non-secret Redis connection information."

  value = var.enable_redis ? {
    replication_group_id = aws_elasticache_replication_group.redis[0].replication_group_id
    address              = aws_elasticache_replication_group.redis[0].primary_endpoint_address
    port                 = aws_elasticache_replication_group.redis[0].port
    engine_version       = aws_elasticache_replication_group.redis[0].engine_version_actual
    node_type            = aws_elasticache_replication_group.redis[0].node_type
  } : null
}

output "mongodb" {
  description = "Private MongoDB instance and management endpoint."

  value = var.enable_mongodb ? {
    instance_id                  = aws_instance.mongodb[0].id
    private_ip                   = aws_instance.mongodb[0].private_ip
    private_dns                  = aws_instance.mongodb[0].private_dns
    availability_zone            = aws_instance.mongodb[0].availability_zone
    instance_connect_endpoint_id = aws_ec2_instance_connect_endpoint.mongodb[0].id
  } : null
}