data "aws_partition" "current" {}

locals {
  service_names = toset(["booking", "catalog", "relay", "worker"])
  http_services = toset(["booking", "catalog"])

  task_security_group_keys = {
    booking = "booking"
    catalog = "catalog"
    relay   = "outbox_relay"
    worker  = "ticket_worker"
  }

  bootstrap_log_group_name = "/ecs/${var.name_prefix}/mysql-bootstrap"
  ssm_prefix               = "/flash-ticket/dev"
  rds_ca_file              = "/app/certs/global-bundle.pem"

  common_environment = {
    AWS_REGION                   = var.aws_region
    AWS_DEFAULT_REGION           = var.aws_region
    AWS_ACCOUNT_ID               = var.aws_account_id
    AWS_EC2_METADATA_DISABLED     = "true"
    PYTHONUNBUFFERED             = "1"
    PYTHONDONTWRITEBYTECODE      = "1"
    LOG_LEVEL                    = "INFO"
  }

  auth_environment = {
    AUTH_PROVIDER        = "cognito"
    COGNITO_REGION       = var.aws_region
    COGNITO_USER_POOL_ID = var.web.cognito_user_pool_id
    COGNITO_CLIENT_ID    = var.web.cognito_client_id
    COGNITO_SCOPE_PREFIX = "flash-ticket"
  }

  service_environment = {
    booking = merge(local.common_environment, local.auth_environment, {
      ASSETS_BUCKET = var.assets_bucket_name
      MYSQL_SSL_CA  = local.rds_ca_file
    })
    catalog = merge(local.common_environment, local.auth_environment, {
      ASSETS_BUCKET          = var.assets_bucket_name
      ASSETS_PUBLIC_BASE_URL = trimsuffix(var.web.frontend_url, "/")
      MONGODB_DATABASE       = "flash_ticket"
    })
    relay = merge(local.common_environment, {
      MYSQL_SSL_CA        = local.rds_ca_file
      QUEUE_NAME          = var.queue_name
      OUTBOX_POLL_SECONDS = "1"
      LEASE_SECONDS       = "120"
    })
    worker = merge(local.common_environment, {
      MYSQL_SSL_CA          = local.rds_ca_file
      QUEUE_NAME            = var.queue_name
      ASSETS_BUCKET         = var.assets_bucket_name
      SES_SENDER            = var.ses_sender_email
      LEASE_SECONDS         = "120"
      SQS_VISIBILITY_SECONDS = "120"
    })
  }

  service_secret_paths = {
    booking = {
      DATABASE_URL = "${local.ssm_prefix}/booking/DATABASE_URL"
    }
    catalog = {
      MONGODB_URL = "${local.ssm_prefix}/catalog/MONGODB_URI"
      REDIS_URL   = "${local.ssm_prefix}/catalog/REDIS_URL"
    }
    relay = {
      DATABASE_URL = "${local.ssm_prefix}/relay/DATABASE_URL"
      REDIS_URL    = "${local.ssm_prefix}/relay/REDIS_URL"
    }
    worker = {
      DATABASE_URL          = "${local.ssm_prefix}/worker/DATABASE_URL"
      TICKET_SIGNING_SECRET = "${local.ssm_prefix}/worker/TICKET_SIGNING_SECRET"
    }
  }

  api_routes = {
    events = {
      key           = "GET /events"
      service       = "catalog"
      authenticated = false
    }
    create_booking = {
      key           = "POST /bookings"
      service       = "booking"
      authenticated = true
    }
    get_booking = {
      key           = "GET /bookings/{booking_id}"
      service       = "booking"
      authenticated = true
    }
    get_ticket = {
      key           = "GET /bookings/{booking_id}/ticket"
      service       = "booking"
      authenticated = true
    }
  }
}

resource "aws_cloudwatch_log_group" "application" {
  for_each = local.service_names

  name              = var.log_group_names[each.key]
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "mysql_bootstrap" {
  name              = local.bootstrap_log_group_name
  retention_in_days = 7
}

resource "aws_ecs_cluster" "this" {
  name = var.name_prefix

  setting {
    name  = "containerInsights"
    value = "disabled"
  }
}

resource "aws_ecs_cluster_capacity_providers" "this" {
  cluster_name       = aws_ecs_cluster.this.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]
}

resource "aws_service_discovery_private_dns_namespace" "this" {
  name        = "${var.name_prefix}.internal"
  description = "Private ECS discovery for API Gateway HTTP API."
  vpc         = var.vpc_id
}

resource "aws_service_discovery_service" "http" {
  for_each = local.http_services

  name = each.key

  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.this.id

    dns_records {
      ttl  = 10
      type = "SRV"
    }

    routing_policy = "MULTIVALUE"
  }

  health_check_custom_config {
    failure_threshold = 1
  }
}

resource "aws_ecs_task_definition" "application" {
  for_each = local.service_names

  family                   = "${var.name_prefix}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = var.execution_role_arns[each.key]
  task_role_arn            = var.task_role_arns[each.key]

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    {
      name      = each.key
      image     = "${var.repository_urls[each.key]}:${var.image_tag}"
      essential = true
      user      = "10001:10001"

      stopTimeout = each.key == "worker" ? 120 : 30

      linuxParameters = {
        initProcessEnabled = true
        capabilities = {
          drop = ["ALL"]
        }
      }

      environment = [
        for name, value in local.service_environment[each.key] :
        { name = name, value = value }
      ]

      secrets = [
        for name, path in local.service_secret_paths[each.key] :
        {
          name = name
          valueFrom = format(
            "arn:%s:ssm:%s:%s:parameter%s",
            data.aws_partition.current.partition,
            var.aws_region,
            var.aws_account_id,
            path
          )
        }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.application[each.key].name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = each.key
          mode                  = "non-blocking"
          max-buffer-size       = "1m"
        }
      }
      portMappings = contains(local.http_services, each.key) ? [{
        name          = "http"
        containerPort = 8000
        hostPort      = 8000
        protocol      = "tcp"
        appProtocol   = "http"
      }] : []

      healthCheck = contains(local.http_services, each.key) ? {
        command = [
          "CMD", "python", "-c",
          "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=3).read()"
        ]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 60
      } : null
    }
  ])
}

resource "aws_ecs_service" "application" {
  for_each = local.service_names

  name            = "${var.name_prefix}-${each.key}"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.application[each.key].arn
  desired_count   = var.desired_count
  platform_version = "1.4.0"

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  enable_ecs_managed_tags             = true
  propagate_tags                      = "SERVICE"

  capacity_provider_strategy {
    capacity_provider = var.use_fargate_spot ? "FARGATE_SPOT" : "FARGATE"
    weight            = 1
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = var.task_subnet_ids
    security_groups  = [var.security_group_ids[local.task_security_group_keys[each.key]]]
    assign_public_ip = var.assign_public_ip
  }

  dynamic "service_registries" {
    for_each = contains(local.http_services, each.key) ? [each.key] : []

    content {
      registry_arn   = aws_service_discovery_service.http[each.key].arn
      container_name = each.key
      container_port = 8000
    }
  }

  depends_on = [aws_ecs_cluster_capacity_providers.this]
}

resource "aws_security_group" "mysql_bootstrap" {
  name_prefix = "${var.name_prefix}-mysql-bootstrap-"
  description = "One-off MySQL migration task; no inbound connections."
  vpc_id      = var.vpc_id

  tags = {
    Name = "${var.name_prefix}-mysql-bootstrap"
  }
}

resource "aws_vpc_security_group_egress_rule" "bootstrap_https" {
  security_group_id = aws_security_group.mysql_bootstrap.id
  description       = "Pull ECR image, read SSM parameters and write logs."
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_egress_rule" "bootstrap_mysql" {
  security_group_id            = aws_security_group.mysql_bootstrap.id
  description                  = "Connect to private RDS using TLS."
  referenced_security_group_id = var.security_group_ids["rds"]
  ip_protocol                  = "tcp"
  from_port                    = 3306
  to_port                      = 3306
}

resource "aws_vpc_security_group_ingress_rule" "mysql_bootstrap" {
  security_group_id            = var.security_group_ids["rds"]
  description                  = "Allow the isolated MySQL migration task."
  referenced_security_group_id = aws_security_group.mysql_bootstrap.id
  ip_protocol                  = "tcp"
  from_port                    = 3306
  to_port                      = 3306
}

resource "aws_ecs_task_definition" "mysql_bootstrap" {
  family                   = "${var.name_prefix}-mysql-bootstrap"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = var.bootstrap_execution_role_arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([{
    name         = "mysql-bootstrap"
    image        = "${var.bootstrap_repository_url}:${var.image_tag}"
    essential    = true
    user         = "10001:10001"
    stopTimeout  = 120

    linuxParameters = {
      initProcessEnabled = true
      capabilities = {
        drop = ["ALL"]
      }
    }

    environment = [
      for name, value in {
        MYSQL_HOST       = var.mysql.address
        MYSQL_PORT       = tostring(var.mysql.port)
        MYSQL_ADMIN_USER = "flash_admin"
        MYSQL_APP_USER   = "flash_app"
        MYSQL_SSL_CA     = local.rds_ca_file
      } : { name = name, value = value }
    ]

    secrets = [
      for name, parameter in {
        MYSQL_ADMIN_PASSWORD = "MYSQL_MASTER_PASSWORD"
        MYSQL_APP_PASSWORD   = "MYSQL_APP_PASSWORD"
      } : {
        name = name
        valueFrom = format(
          "arn:%s:ssm:%s:%s:parameter%s/data/%s",
          data.aws_partition.current.partition,
          var.aws_region,
          var.aws_account_id,
          local.ssm_prefix,
          parameter
        )
      }
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.mysql_bootstrap.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "mysql-bootstrap"
      }
    }
  }])
}

resource "aws_apigatewayv2_api" "this" {
  name          = var.name_prefix
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins = [
      trimsuffix(var.web.frontend_url, "/"),
      "http://localhost:5173",
      "http://localhost:8080",
    ]
    allow_methods = ["GET", "POST", "OPTIONS"]
    allow_headers = ["authorization", "content-type", "idempotency-key"]
    max_age       = 300
  }
}

resource "aws_apigatewayv2_vpc_link" "this" {
  name               = var.name_prefix
  security_group_ids = [var.security_group_ids["vpc_link"]]
  subnet_ids         = var.private_app_subnet_ids
}

resource "aws_apigatewayv2_integration" "http" {
  for_each = local.http_services

  api_id                 = aws_apigatewayv2_api.this.id
  integration_type       = "HTTP_PROXY"
  integration_method     = "ANY"
  integration_uri        = aws_service_discovery_service.http[each.key].arn
  connection_type        = "VPC_LINK"
  connection_id          = aws_apigatewayv2_vpc_link.this.id
  payload_format_version = "1.0"
  timeout_milliseconds   = 30000

  request_parameters = {
    "overwrite:path" = "$request.path"
  }
}

resource "aws_apigatewayv2_authorizer" "cognito" {
  api_id           = aws_apigatewayv2_api.this.id
  name             = "cognito-access-token"
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]

  jwt_configuration {
    audience = [var.web.cognito_client_id]
    issuer   = var.web.cognito_issuer
  }
}

resource "aws_apigatewayv2_route" "this" {
  for_each = local.api_routes

  api_id    = aws_apigatewayv2_api.this.id
  route_key = each.value.key
  target    = "integrations/${aws_apigatewayv2_integration.http[each.value.service].id}"

  authorization_type = each.value.authenticated ? "JWT" : "NONE"
  authorizer_id      = each.value.authenticated ? aws_apigatewayv2_authorizer.cognito.id : null
  authorization_scopes = each.value.authenticated ? ["flash-ticket/bookings:write"] : []
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/apigateway/${var.name_prefix}"
  retention_in_days = 7
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.this.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_burst_limit = 50
    throttling_rate_limit  = 25
  }

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api.arn
    format = jsonencode({
      requestId          = "$context.requestId"
      routeKey           = "$context.routeKey"
      status             = "$context.status"
      responseLatency    = "$context.responseLatency"
      integrationLatency = "$context.integrationLatency"
      integrationStatus  = "$context.integrationStatus"
    })
  }
}
