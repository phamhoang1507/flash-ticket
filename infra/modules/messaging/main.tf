terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
  }
}

resource "aws_sqs_queue" "dlq" {
  name = "${var.name_prefix}-bookings-dlq"

  fifo_queue                = false
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true

  tags = {
    Name = "${var.name_prefix}-bookings-dlq"
  }
}

resource "aws_sqs_queue" "bookings" {
  name = "${var.name_prefix}-bookings"

  fifo_queue = false

  visibility_timeout_seconds = 120
  receive_wait_time_seconds  = 20
  message_retention_seconds  = 345600

  sqs_managed_sse_enabled = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = var.max_receive_count
  })

  tags = {
    Name = "${var.name_prefix}-bookings"
  }
}

resource "aws_sqs_queue_redrive_allow_policy" "dlq" {
  queue_url = aws_sqs_queue.dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"

    sourceQueueArns = [
      aws_sqs_queue.bookings.arn
    ]
  })
}