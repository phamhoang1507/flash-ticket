output "vpc_id" {
  value = module.vpc.vpc_id
}

output "availability_zones" {
  value = module.vpc.availability_zones
}

output "public_subnet_ids" {
  value = module.vpc.public_subnet_ids
}

output "private_app_subnet_ids" {
  value = module.vpc.private_app_subnet_ids
}

output "private_data_subnet_ids" {
  value = module.vpc.private_data_subnet_ids
}

output "nat_gateway_ids" {
  value = module.vpc.nat_gateway_ids
}

output "security_group_ids" {
  value = module.vpc.security_group_ids
}

output "rds" {
  description = "Non-secret connection information for development MySQL."
  value       = module.data_layer.mysql
}

output "redis" {
  description = "Non-secret Redis connection information."
  value       = module.data_layer.redis
}

output "mongo_ami_builder" {
  description = "Temporary MongoDB AMI builder."
  value       = module.mongo_ami_builder.builder
}