data "aws_iam_policy_document" "booking" {
  statement {
    sid     = "ReadTicketObjects"
    effect  = "Allow"
    actions = ["s3:GetObject"]

    resources = [
      "${var.assets_bucket_arn}/tickets/*",
    ]
  }
}

data "aws_iam_policy_document" "catalog" {
  statement {
    sid     = "ReadBannerObjects"
    effect  = "Allow"
    actions = ["s3:GetObject"]

    resources = [
      "${var.assets_bucket_arn}/banners/*",
    ]
  }
}

data "aws_iam_policy_document" "relay" {
  statement {
    sid    = "PublishBookingMessages"
    effect = "Allow"

    actions = [
      "sqs:GetQueueUrl",
      "sqs:SendMessage",
    ]

    resources = [
      var.booking_queue_arn,
    ]
  }
}

data "aws_iam_policy_document" "worker" {
  statement {
    sid    = "ConsumeBookingMessages"
    effect = "Allow"

    actions = [
      "sqs:GetQueueUrl",
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:ChangeMessageVisibility",
    ]

    resources = [
      var.booking_queue_arn,
    ]
  }

  statement {
    sid    = "WriteAndReadTicketObjects"
    effect = "Allow"

    actions = [
      "s3:PutObject",
      "s3:GetObject",
    ]

    resources = [
      "${var.assets_bucket_arn}/tickets/*",
    ]
  }

  statement {
    sid     = "SendTicketEmails"
    effect  = "Allow"
    actions = ["ses:SendEmail"]

    resources = [
      var.ses_identity_arn,
    ]

    condition {
      test     = "StringEquals"
      variable = "ses:FromAddress"
      values   = [var.ses_sender_email]
    }
  }
}

locals {
  task_policy_documents = {
    booking = data.aws_iam_policy_document.booking.json
    catalog = data.aws_iam_policy_document.catalog.json
    relay   = data.aws_iam_policy_document.relay.json
    worker  = data.aws_iam_policy_document.worker.json
  }
}

resource "aws_iam_role_policy" "task" {
  for_each = local.task_policy_documents

  name   = "${each.key}-task-permissions"
  role   = aws_iam_role.task[each.key].id
  policy = each.value
}