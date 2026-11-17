output "repository_urls" {
  description = "Repository URLs keyed by service name."

  value = {
    for name, repository in aws_ecr_repository.this :
    name => repository.repository_url
  }
}

output "repository_arns" {
  description = "Repository ARNs used by IAM policies."

  value = {
    for name, repository in aws_ecr_repository.this :
    name => repository.arn
  }
}

output "registry_url" {
  description = "Private registry hostname."

  value = try(
    split(
      "/",
      values(aws_ecr_repository.this)[0].repository_url
    )[0],
    null
  )
}