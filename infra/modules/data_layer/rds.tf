resource "aws_db_subnet_group" "mysql" {
  name        = "${var.name_prefix}-mysql"
  description = "Private data subnets for Flash-Ticket MySQL."

  subnet_ids = var.private_data_subnet_ids

  tags = {
    Name = "${var.name_prefix}-mysql"
  }
}

resource "aws_db_parameter_group" "mysql" {
  name_prefix = "${var.name_prefix}-mysql-"
  family      = "mysql8.4"

  description = "MySQL parameters requiring encrypted client connections."

  parameter {
    name         = "require_secure_transport"
    value        = "1"
    apply_method = "immediate"
  }

  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Name = "${var.name_prefix}-mysql"
  }
}

resource "aws_db_instance" "mysql" {
  identifier = "${var.name_prefix}-mysql"

  engine                      = "mysql"
  engine_version              = "8.4"
  auto_minor_version_upgrade  = true
  allow_major_version_upgrade = false

  instance_class = var.rds_instance_class

  db_name  = "flash_ticket"
  username = "flash_admin"
  port     = 3306

  password_wo         = var.rds_master_password
  password_wo_version = var.rds_master_password_version

  allocated_storage     = 20
  max_allocated_storage = 0
  storage_type          = "gp3"
  storage_encrypted     = true

  db_subnet_group_name   = aws_db_subnet_group.mysql.name
  vpc_security_group_ids = [var.rds_security_group_id]
  parameter_group_name   = aws_db_parameter_group.mysql.name

  publicly_accessible = false

  multi_az = var.enable_multi_az_data

  availability_zone = (
    var.enable_multi_az_data
    ? null
    : var.primary_availability_zone
  )

  backup_retention_period  = var.rds_backup_retention_days
  delete_automated_backups = true
  copy_tags_to_snapshot    = true

  deletion_protection       = var.rds_deletion_protection
  skip_final_snapshot       = var.rds_skip_final_snapshot
  final_snapshot_identifier = var.rds_final_snapshot_identifier

  monitoring_interval          = 0
  performance_insights_enabled = false

  apply_immediately = false

  lifecycle {
    precondition {
      condition = (
        var.rds_skip_final_snapshot ||
        try(length(trimspace(var.rds_final_snapshot_identifier)) > 0, false)
      )

      error_message = "A final snapshot identifier is required when skip_final_snapshot is false."
    }
  }

  tags = {
    Name = "${var.name_prefix}-mysql"
  }
}