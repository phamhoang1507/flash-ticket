data "aws_ami" "mongodb" {
  count = var.enable_mongodb ? 1 : 0

  owners = ["self"]

  filter {
    name   = "image-id"
    values = [var.mongodb_ami_id]
  }

  filter {
    name   = "architecture"
    values = ["x86_64"]
  }

  filter {
    name   = "tag:Project"
    values = ["flash-ticket"]
  }

  filter {
    name   = "tag:Role"
    values = ["mongodb-ami"]
  }
}

resource "aws_security_group" "mongodb_management" {
  count = var.enable_mongodb ? 1 : 0

  name_prefix = "${var.name_prefix}-mongo-management-"
  description = "SSH access to MongoDB through EC2 Instance Connect Endpoint."
  vpc_id      = var.vpc_id

  tags = {
    Name = "${var.name_prefix}-mongo-management"
  }
}

resource "aws_security_group" "mongodb_endpoint" {
  count = var.enable_mongodb ? 1 : 0

  name_prefix = "${var.name_prefix}-mongo-endpoint-"
  description = "Outbound SSH from EC2 Instance Connect Endpoint."
  vpc_id      = var.vpc_id

  tags = {
    Name = "${var.name_prefix}-mongo-endpoint"
  }
}

resource "aws_vpc_security_group_ingress_rule" "mongodb_ssh" {
  count = var.enable_mongodb ? 1 : 0

  security_group_id = aws_security_group.mongodb_management[0].id

  description = "SSH from the management endpoint."

  referenced_security_group_id = aws_security_group.mongodb_endpoint[0].id

  ip_protocol = "tcp"
  from_port   = 22
  to_port     = 22
}

resource "aws_vpc_security_group_egress_rule" "mongodb_endpoint_ssh" {
  count = var.enable_mongodb ? 1 : 0

  security_group_id = aws_security_group.mongodb_endpoint[0].id

  description = "SSH to the MongoDB management security group."

  referenced_security_group_id = aws_security_group.mongodb_management[0].id

  ip_protocol = "tcp"
  from_port   = 22
  to_port     = 22
}

resource "aws_ec2_instance_connect_endpoint" "mongodb" {
  count = var.enable_mongodb ? 1 : 0

  subnet_id = var.mongodb_management_subnet_id

  security_group_ids = [
    aws_security_group.mongodb_endpoint[0].id,
  ]

  preserve_client_ip = false

  depends_on = [
    aws_vpc_security_group_egress_rule.mongodb_endpoint_ssh,
  ]

  tags = {
    Name = "${var.name_prefix}-mongo-management"
  }
}

resource "aws_instance" "mongodb" {
  count = var.enable_mongodb ? 1 : 0

  ami           = data.aws_ami.mongodb[0].id
  instance_type = var.mongodb_instance_type

  subnet_id                   = var.mongodb_subnet_id
  associate_public_ip_address = false

  vpc_security_group_ids = [
    var.mongodb_security_group_id,
    aws_security_group.mongodb_management[0].id,
  ]

  instance_initiated_shutdown_behavior = "stop"

  credit_specification {
    cpu_credits = "standard"
  }

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

  user_data = file("${path.module}/templates/mongodb-node.sh")

  user_data_replace_on_change = true

  depends_on = [
    aws_vpc_security_group_ingress_rule.mongodb_ssh,
  ]

  tags = {
    Name = "${var.name_prefix}-mongodb"
    Role = "mongodb"
  }

  volume_tags = {
    Name        = "${var.name_prefix}-mongodb-root"
    Project     = "flash-ticket"
    Environment = "dev"
    ManagedBy   = "Terraform"
  }
}