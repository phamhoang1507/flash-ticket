resource "aws_elasticache_subnet_group" "redis" {
  count = var.enable_redis ? 1 : 0

  name        = "${var.name_prefix}-redis"
  description = "Private data subnets for Flash-Ticket Redis."

  subnet_ids = var.private_data_subnet_ids

  tags = {
    Name = "${var.name_prefix}-redis"
  }
}

resource "aws_elasticache_parameter_group" "redis" {
  count = var.enable_redis ? 1 : 0

  name        = "${var.name_prefix}-redis7"
  family      = "redis7"
  description = "Redis parameters for caching and inventory projections."

  parameter {
    name  = "maxmemory-policy"
    value = "volatile-lru"
  }

  tags = {
    Name = "${var.name_prefix}-redis7"
  }
}

resource "aws_elasticache_replication_group" "redis" {
  count = var.enable_redis ? 1 : 0

  replication_group_id = "${var.name_prefix}-redis"
  description          = "Flash-Ticket cache and inventory projection."

  engine                     = "redis"
  engine_version             = "7.1"
  auto_minor_version_upgrade = true

  node_type = var.redis_node_type
  port      = 6379

  parameter_group_name = aws_elasticache_parameter_group.redis[0].name
  subnet_group_name    = aws_elasticache_subnet_group.redis[0].name

  security_group_ids = [
    var.redis_security_group_id,
  ]

  num_cache_clusters         = var.enable_multi_az_data ? 2 : 1
  automatic_failover_enabled = var.enable_multi_az_data
  multi_az_enabled           = var.enable_multi_az_data

  preferred_cache_cluster_azs = (
    var.enable_multi_az_data
    ? var.redis_availability_zones
    : [var.redis_availability_zones[0]]
  )

  at_rest_encryption_enabled = true

  transit_encryption_enabled = true
  transit_encryption_mode    = "required"

  auth_token_wo              = var.redis_auth_token
  auth_token_wo_version      = var.redis_auth_token_version
  auth_token_update_strategy = "SET"

  snapshot_retention_limit = var.redis_snapshot_retention_days

  maintenance_window = "sun:06:00-sun:07:00"
  apply_immediately  = false

  tags = {
    Name = "${var.name_prefix}-redis"
  }
}