terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
  }
}

data "aws_availability_zones" "available" {
  state = "available"

  filter {
    name   = "zone-type"
    values = ["availability-zone"]
  }
}

locals {
  zones = {
    a = {
      name   = var.availability_zones[0]
      number = 0
    }

    b = {
      name   = var.availability_zones[1]
      number = 1
    }
  }

  nat_zones = var.enable_nat_gateway ? local.zones : {}
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "${var.name_prefix}-vpc"
  }

  lifecycle {
    precondition {
      condition = alltrue([
        for zone in var.availability_zones :
        contains(data.aws_availability_zones.available.names, zone)
      ])
      error_message = "One or more requested availability zones are unavailable."
    }
  }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id

  tags = {
    Name = "${var.name_prefix}-igw"
  }
}

resource "aws_subnet" "public" {
  for_each = local.zones

  vpc_id                  = aws_vpc.this.id
  availability_zone       = each.value.name
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, each.value.number)
  map_public_ip_on_launch = false

  tags = {
    Name = "${var.name_prefix}-public-${each.key}"
    Tier = "public"
  }
}

resource "aws_subnet" "private_app" {
  for_each = local.zones

  vpc_id                  = aws_vpc.this.id
  availability_zone       = each.value.name
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, 10 + each.value.number)
  map_public_ip_on_launch = false

  tags = {
    Name = "${var.name_prefix}-private-app-${each.key}"
    Tier = "private-app"
  }
}

resource "aws_subnet" "private_data" {
  for_each = local.zones

  vpc_id                  = aws_vpc.this.id
  availability_zone       = each.value.name
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, 20 + each.value.number)
  map_public_ip_on_launch = false

  tags = {
    Name = "${var.name_prefix}-private-data-${each.key}"
    Tier = "private-data"
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  tags = {
    Name = "${var.name_prefix}-public"
  }
}

resource "aws_route" "public_internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.this.id
}

resource "aws_route_table_association" "public" {
  for_each = local.zones

  subnet_id      = aws_subnet.public[each.key].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private_app" {
  for_each = local.zones

  vpc_id = aws_vpc.this.id

  tags = {
    Name = "${var.name_prefix}-private-app-${each.key}"
  }
}

resource "aws_route_table_association" "private_app" {
  for_each = local.zones

  subnet_id      = aws_subnet.private_app[each.key].id
  route_table_id = aws_route_table.private_app[each.key].id
}

resource "aws_route_table" "private_data" {
  for_each = local.zones

  vpc_id = aws_vpc.this.id

  tags = {
    Name = "${var.name_prefix}-private-data-${each.key}"
  }
}

resource "aws_route_table_association" "private_data" {
  for_each = local.zones

  subnet_id      = aws_subnet.private_data[each.key].id
  route_table_id = aws_route_table.private_data[each.key].id
}

resource "aws_eip" "nat" {
  for_each = local.nat_zones

  domain = "vpc"

  tags = {
    Name = "${var.name_prefix}-nat-${each.key}"
  }
}

resource "aws_nat_gateway" "this" {
  for_each = local.nat_zones

  allocation_id     = aws_eip.nat[each.key].id
  subnet_id         = aws_subnet.public[each.key].id
  connectivity_type = "public"

  tags = {
    Name = "${var.name_prefix}-nat-${each.key}"
  }

  depends_on = [aws_internet_gateway.this]
}

resource "aws_route" "private_app_internet" {
  for_each = local.nat_zones

  route_table_id         = aws_route_table.private_app[each.key].id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.this[each.key].id
}