terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # Partial config: `terraform init -backend-config=backend.hcl` (written by
  # `make tf-bootstrap-apply`, git-ignored). Native S3 lock file, no DynamoDB table.
  backend "s3" {}
}

provider "aws" {
  region = var.region
  # Credentials: AWS_PROFILE=puenktlich (`aws login`), never in code.

  default_tags {
    tags = {
      project    = "puenktlich"
      env        = "prod"
      managed_by = "terraform"
      owner      = "piyal21"
    }
  }
}
