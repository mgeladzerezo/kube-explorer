terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.70"
    }
  }

  # State backend is configured per environment:
  #   terraform init -backend-config=bucket=... -backend-config=key=kube-explorer.tfstate
  backend "s3" {}
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "kube-explorer"
      ManagedBy = "terraform"
    }
  }
}
