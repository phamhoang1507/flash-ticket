output "mysql" {
  description = "Non-secret MySQL connection information."

  value = {
    identifier     = aws_db_instance.mysql.identifier
    address        = aws_db_instance.mysql.address
    port           = aws_db_instance.mysql.port
    database       = aws_db_instance.mysql.db_name
    engine_version = aws_db_instance.mysql.engine_version_actual
  }
}