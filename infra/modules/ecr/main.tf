terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
  }
}

resource "aws_ecr_repository" "this" {
  for_each = var.service_names

  name                 = "${var.name_prefix}/${each.key}"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = var.force_delete

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = {
    Name    = "${var.name_prefix}/${each.key}"
    Service = each.key
  }
}

resource "aws_ecr_lifecycle_policy" "this" {
  for_each = var.service_names

  repository = aws_ecr_repository.this[each.key].name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged images after one day"

        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 1
        }

        action = {
          type = "expire"
        }
      },
      {
        rulePriority = 2
        description  = "Retain recent environment images"

        selection = {
          tagStatus     = "tagged"
          tagPrefixList = [var.image_tag_prefix]
          countType     = "imageCountMoreThan"
          countNumber   = var.image_retention_count
        }

        action = {
          type = "expire"
        }
      }
    ]
  })
}