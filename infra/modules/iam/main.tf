terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
  }
}

locals {
  log_group_names = {
    for service, repository_arn in var.ecr_repository_arns :
    service => "${trimsuffix(var.log_group_prefix, "/")}/${service}"
  }

  ssm_parameter_prefix = trimsuffix(
    var.ssm_parameter_prefix,
    "/"
  )
}

data "aws_iam_policy_document" "ecs_tasks_trust" {
  statement {
    sid     = "AllowECSTasks"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.aws_account_id]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"

      values = [
        "arn:aws:ecs:${var.aws_region}:${var.aws_account_id}:*"
      ]
    }
  }
}

resource "aws_iam_role" "execution" {
  for_each = var.ecr_repository_arns

  name = "${var.name_prefix}-${var.aws_region}-${each.key}-execution"

  description = "ECS task execution role for ${each.key}"

  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json

  tags = {
    Name    = "${var.name_prefix}-${each.key}-execution"
    Service = each.key
    Purpose = "ecs-task-execution"
  }
}

data "aws_iam_policy_document" "execution" {
  for_each = var.ecr_repository_arns

  statement {
    sid       = "AuthenticateToECR"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "PullServiceImage"
    effect = "Allow"

    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage"
    ]

    resources = [each.value]
  }

  statement {
    sid    = "WriteServiceLogs"
    effect = "Allow"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents"
    ]

    resources = [
      "arn:aws:logs:${var.aws_region}:${var.aws_account_id}:log-group:${local.log_group_names[each.key]}:log-stream:*"
    ]
  }

  statement {
    sid     = "ReadServiceParameters"
    effect  = "Allow"
    actions = ["ssm:GetParameters"]

    resources = [
      "arn:aws:ssm:${var.aws_region}:${var.aws_account_id}:parameter${local.ssm_parameter_prefix}/${each.key}/*"
    ]
  }
}

resource "aws_iam_role_policy" "execution" {
  for_each = var.ecr_repository_arns

  name   = "${each.key}-execution-permissions"
  role   = aws_iam_role.execution[each.key].id
  policy = data.aws_iam_policy_document.execution[each.key].json
}