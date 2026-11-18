data "aws_ami" "ubuntu" {
  count = var.enabled ? 1 : 0

  most_recent = true
  owners      = ["099720109477"]

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }

  filter {
    name   = "architecture"
    values = ["x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }

  filter {
    name   = "root-device-type"
    values = ["ebs"]
  }
}

resource "aws_security_group" "builder" {
  count = var.enabled ? 1 : 0

  name_prefix = "${var.name_prefix}-mongo-builder-"
  description = "No inbound access for the temporary MongoDB AMI builder."
  vpc_id      = var.vpc_id

  tags = {
    Name = "${var.name_prefix}-mongo-builder"
  }
}

resource "aws_vpc_security_group_egress_rule" "https" {
  count = var.enabled ? 1 : 0

  security_group_id = aws_security_group.builder[0].id

  description = "HTTPS access to Ubuntu and MongoDB repositories."
  ip_protocol = "tcp"
  from_port   = 443
  to_port     = 443
  cidr_ipv4   = "0.0.0.0/0"
}

resource "aws_instance" "builder" {
  count = var.enabled ? 1 : 0

  ami           = data.aws_ami.ubuntu[0].id
  instance_type = "t3.micro"

  subnet_id                   = var.public_subnet_id
  associate_public_ip_address = true

  vpc_security_group_ids = [
    aws_security_group.builder[0].id,
  ]

  instance_initiated_shutdown_behavior = "stop"

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  root_block_device {
    volume_size           = 12
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
  }

  user_data = file("${path.module}/templates/bootstrap.sh")

  user_data_replace_on_change = true

  lifecycle {
    ignore_changes = [ami]
  }

  depends_on = [
    aws_vpc_security_group_egress_rule.https,
  ]

  tags = {
    Name = "${var.name_prefix}-mongo-ami-builder"
    Role = "mongo-ami-builder"
  }

  volume_tags = {
    Name        = "${var.name_prefix}-mongo-builder-root"
    Project     = "flash-ticket"
    Environment = "dev"
    ManagedBy   = "Terraform"
  }
}