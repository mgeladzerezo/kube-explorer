# The app is installed into an existing cluster with Helm, so AWS only has to provide
# somewhere to push images and somewhere to keep the two secrets the backend needs.

locals {
  components = toset(["backend", "frontend"])
}

resource "aws_ecr_repository" "this" {
  for_each = local.components

  name                 = "${var.name}-${each.key}"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "this" {
  for_each = aws_ecr_repository.this

  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Expire untagged images"
      selection = {
        tagStatus   = "untagged"
        countType   = "sinceImagePushed"
        countUnit   = "days"
        countNumber = var.untagged_image_retention_days
      }
      action = { type = "expire" }
    }]
  })
}

# Terraform creates the secret but never its value, so the API key and database URL
# stay out of state. Set them once with:
#   aws secretsmanager put-secret-value --secret-id kube-explorer/prod \
#     --secret-string '{"anthropic_api_key":"...","database_url":"postgresql+psycopg://..."}'
resource "aws_secretsmanager_secret" "app" {
  name                    = "${var.name}/${var.environment}"
  description             = "anthropic_api_key and database_url for kube-explorer"
  recovery_window_in_days = 7
}

# Attach this to the IAM role used by the External Secrets Operator.
data "aws_iam_policy_document" "read_secret" {
  statement {
    actions   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
    resources = [aws_secretsmanager_secret.app.arn]
  }
}

resource "aws_iam_policy" "read_secret" {
  name   = "${var.name}-${var.environment}-read-secret"
  policy = data.aws_iam_policy_document.read_secret.json
}
