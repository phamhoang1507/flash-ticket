output "vpc_id" {
  value = aws_vpc.this.id
}

output "availability_zones" {
  value = {
    for key, zone in local.zones :
    key => zone.name
  }
}

output "public_subnet_ids" {
  value = {
    for key, subnet in aws_subnet.public :
    key => subnet.id
  }
}

output "private_app_subnet_ids" {
  value = {
    for key, subnet in aws_subnet.private_app :
    key => subnet.id
  }
}

output "private_data_subnet_ids" {
  value = {
    for key, subnet in aws_subnet.private_data :
    key => subnet.id
  }
}

output "nat_gateway_ids" {
  value = {
    for key, gateway in aws_nat_gateway.this :
    key => gateway.id
  }
}