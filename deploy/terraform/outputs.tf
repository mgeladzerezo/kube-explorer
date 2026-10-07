output "repository_urls" {
  description = "Set image.backend.repository and image.frontend.repository in the Helm values to these."
  value       = { for name, repo in aws_ecr_repository.this : name => repo.repository_url }
}

output "secret_name" {
  description = "Value for secret.externalSecret.remoteKey in the Helm values."
  value       = aws_secretsmanager_secret.app.name
}

output "read_secret_policy_arn" {
  value = aws_iam_policy.read_secret.arn
}
