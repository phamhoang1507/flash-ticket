data "aws_partition" "bootstrap" {}

module "bootstrap_ecr" {
  source = "../../modules/ecr"

  name_prefix           = "flash-ticket-dev"
  service_names         = toset(["mysql-bootstrap"])
  image_tag_prefix      = "dev-"
  image_retention_count = 5
  force_delete          = var.ecr_force_delete
}

resource "aws_iam_role" "mysql_bootstrap_execution" {
  name = "flash-ticket-dev-mysql-bootstrap-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "ecs-tasks.amazonaws.com"
      }
      Action = "sts:AssumeRole"
      Condition = {
        StringEquals = {
          "aws:SourceAccount" = var.aws_account_id
        }
        ArnLike = {
          "aws:SourceArn" = "arn:${data.aws_partition.bootstrap.partition}:ecs:${var.aws_region}:${var.aws_account_id}:*"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "mysql_bootstrap_execution" {
  name = "mysql-bootstrap"
  role = aws_iam_role.mysql_bootstrap_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "EcrAuthentication"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Sid    = "PullBootstrapImage"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
        ]
        Resource = module.bootstrap_ecr.repository_arns["mysql-bootstrap"]
      },
      {
        Sid    = "WriteBootstrapLogs"
        Effect = "Allow"
        Action = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:${data.aws_partition.bootstrap.partition}:logs:${var.aws_region}:${var.aws_account_id}:log-group:/ecs/flash-ticket-dev/mysql-bootstrap:log-stream:*"
      },
      {
        Sid    = "ReadOnlyBootstrapPasswords"
        Effect = "Allow"
        Action = ["ssm:GetParameters"]
        Resource = [
          "arn:${data.aws_partition.bootstrap.partition}:ssm:${var.aws_region}:${var.aws_account_id}:parameter/flash-ticket/dev/data/MYSQL_MASTER_PASSWORD",
          "arn:${data.aws_partition.bootstrap.partition}:ssm:${var.aws_region}:${var.aws_account_id}:parameter/flash-ticket/dev/data/MYSQL_APP_PASSWORD",
        ]
      },
    ]
  })
}

output "mysql_bootstrap_repository_url" {
  value = module.bootstrap_ecr.repository_urls["mysql-bootstrap"]
}

output "mysql_bootstrap_execution_role_arn" {
  value = aws_iam_role.mysql_bootstrap_execution.arn
}
