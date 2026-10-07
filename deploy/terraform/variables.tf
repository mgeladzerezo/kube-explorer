variable "region" {
  type    = string
  default = "eu-central-1"
}

variable "name" {
  description = "Prefix for the ECR repositories and the secret."
  type        = string
  default     = "kube-explorer"
}

variable "environment" {
  type    = string
  default = "prod"
}

variable "untagged_image_retention_days" {
  type    = number
  default = 7
}
