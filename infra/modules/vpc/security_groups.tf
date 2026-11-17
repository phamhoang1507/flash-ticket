locals {
  security_group_descriptions = {
    vpc_link      = "API Gateway VPC Link ENIs"
    catalog       = "Event Catalog ECS tasks"
    booking       = "Booking ECS tasks"
    outbox_relay  = "Outbox Relay ECS tasks"
    ticket_worker = "Ticket Worker ECS tasks"
    rds           = "RDS MySQL instances"
    redis         = "ElastiCache nodes"
    mongodb       = "MongoDB EC2 instances"
  }

  private_flows = {
    vpc_link_to_catalog = {
      source      = "vpc_link"
      destination = "catalog"
      port        = 8000
    }

    vpc_link_to_booking = {
      source      = "vpc_link"
      destination = "booking"
      port        = 8000
    }

    booking_to_rds = {
      source      = "booking"
      destination = "rds"
      port        = 3306
    }

    relay_to_rds = {
      source      = "outbox_relay"
      destination = "rds"
      port        = 3306
    }

    worker_to_rds = {
      source      = "ticket_worker"
      destination = "rds"
      port        = 3306
    }

    catalog_to_redis = {
      source      = "catalog"
      destination = "redis"
      port        = 6379
    }

    relay_to_redis = {
      source      = "outbox_relay"
      destination = "redis"
      port        = 6379
    }

    catalog_to_mongodb = {
      source      = "catalog"
      destination = "mongodb"
      port        = 27017
    }

    redis_internal = {
      source      = "redis"
      destination = "redis"
      port        = 6379
    }

    mongodb_internal = {
      source      = "mongodb"
      destination = "mongodb"
      port        = 27017
    }
  }

  https_clients = toset([
    "catalog",
    "booking",
    "outbox_relay",
    "ticket_worker"
  ])
}

resource "aws_security_group" "this" {
  for_each = local.security_group_descriptions

  name_prefix = "${var.name_prefix}-${replace(each.key, "_", "-")}-"
  description = each.value
  vpc_id      = aws_vpc.this.id

  tags = {
    Name = "${var.name_prefix}-${replace(each.key, "_", "-")}"
    Role = each.key
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "private" {
  for_each = local.private_flows

  security_group_id = aws_security_group.this[
    each.value.destination
  ].id

  referenced_security_group_id = aws_security_group.this[
    each.value.source
  ].id

  ip_protocol = "tcp"
  from_port   = each.value.port
  to_port     = each.value.port

  description = "${each.value.source} to ${each.value.destination}"

  tags = {
    Name = "${var.name_prefix}-${each.key}-in"
  }
}

resource "aws_vpc_security_group_egress_rule" "private" {
  for_each = local.private_flows

  security_group_id = aws_security_group.this[
    each.value.source
  ].id

  referenced_security_group_id = aws_security_group.this[
    each.value.destination
  ].id

  ip_protocol = "tcp"
  from_port   = each.value.port
  to_port     = each.value.port

  description = "${each.value.source} to ${each.value.destination}"

  tags = {
    Name = "${var.name_prefix}-${each.key}-out"
  }
}

resource "aws_vpc_security_group_egress_rule" "https" {
  for_each = local.https_clients

  security_group_id = aws_security_group.this[each.key].id

  cidr_ipv4   = "0.0.0.0/0"
  ip_protocol = "tcp"
  from_port   = 443
  to_port     = 443

  description = "Outbound HTTPS for AWS APIs and authentication"

  tags = {
    Name = "${var.name_prefix}-${each.key}-https"
  }
}