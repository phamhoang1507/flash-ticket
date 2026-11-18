output "queue_name" {
  value = aws_sqs_queue.bookings.name
}

output "queue_url" {
  value = aws_sqs_queue.bookings.id
}

output "queue_arn" {
  value = aws_sqs_queue.bookings.arn
}

output "dlq_name" {
  value = aws_sqs_queue.dlq.name
}

output "dlq_url" {
  value = aws_sqs_queue.dlq.id
}

output "dlq_arn" {
  value = aws_sqs_queue.dlq.arn
}