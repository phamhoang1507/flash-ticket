locals {
  service_names = toset([
    "booking",
    "catalog",
    "relay",
    "worker",
  ])
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
        "arn:aws:ecs:${var.aws_region}:${var.aws_account_id}:*",
      ]
    }
  }
}

resource "aws_iam_role" "task" {
  for_each = local.service_names

  name = "${var.name_prefix}-${var.aws_region}-${each.key}-task"

  description = "AWS API permissions for the ${each.key} application."

  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json

  tags = {
    Name    = "${var.name_prefix}-${each.key}-task"
    Service = each.key
    Purpose = "ecs-task"
  }
}