output "builder" {
  description = "Temporary MongoDB AMI builder information."

  value = var.enabled ? {
    instance_id   = aws_instance.builder[0].id
    instance_type = aws_instance.builder[0].instance_type
    base_ami_id   = aws_instance.builder[0].ami
  } : null
}