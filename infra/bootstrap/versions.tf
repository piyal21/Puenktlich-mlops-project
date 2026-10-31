# Bootstrap: applied once from the laptop, with LOCAL state (it creates the remote state bucket).
# No GitHub OIDC provider / CI roles here: an AWS-managed SCP on this account denies them
# (see docs/adr/0002-aws-account-type.md).

terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
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

variable "region" {
  description = "AWS region (fixed by the account type)."
  type        = string
  default     = "eu-north-1"
}
