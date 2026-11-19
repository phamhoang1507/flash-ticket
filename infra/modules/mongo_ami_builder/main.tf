data "aws_partition" "current" {}

data "aws_ami" "ubuntu" {
  count       = var.enabled ? 1 : 0
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
  count       = var.enabled ? 1 : 0
  name        = "${var.name_prefix}-mongo-ami-builder"
  description = "Temporary MongoDB AMI builder; no inbound access"
  vpc_id      = var.vpc_id

  tags = {
    Name = "${var.name_prefix}-mongo-ami-builder"
    Role = "mongo-ami-builder"
  }
}

resource "aws_vpc_security_group_egress_rule" "https" {
  count             = var.enabled ? 1 : 0
  security_group_id = aws_security_group.builder[0].id
  description       = "HTTPS package downloads and SSM control channels"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_iam_role" "builder" {
  count = var.enabled ? 1 : 0
  name  = "${var.name_prefix}-mongo-ami-builder"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ssm" {
  count      = var.enabled ? 1 : 0
  role       = aws_iam_role.builder[0].name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "builder" {
  count = var.enabled ? 1 : 0
  name  = "${var.name_prefix}-mongo-ami-builder"
  role  = aws_iam_role.builder[0].name
}

resource "aws_instance" "builder" {
  count                       = var.enabled ? 1 : 0
  ami                         = data.aws_ami.ubuntu[0].id
  instance_type               = "t3.micro"
  subnet_id                   = var.public_subnet_id
  associate_public_ip_address = true
  vpc_security_group_ids      = [aws_security_group.builder[0].id]
  iam_instance_profile        = aws_iam_instance_profile.builder[0].name

  instance_initiated_shutdown_behavior = "stop"
  user_data                           = file("${path.module}/templates/bootstrap.sh")
  user_data_replace_on_change          = true

  credit_specification {
    cpu_credits = "standard"
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = 12
    encrypted             = true
    delete_on_termination = true
  }

  tags = {
    Name = "${var.name_prefix}-mongo-ami-builder"
    Role = "mongo-ami-builder"
  }

  depends_on = [
    aws_iam_role_policy_attachment.ssm,
    aws_vpc_security_group_egress_rule.https,
  ]
}
