terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Local state on purpose: this bootstrap stack creates the bucket that
  # infra/main's remote backend depends on, so it can't use that backend
  # itself. Apply once from your machine, keep the resulting
  # terraform.tfstate file safe (never commit it — see .gitignore), and
  # treat this stack as documented-manual rather than something CI touches.
}

provider "aws" {
  region = "ap-southeast-2"

  default_tags {
    tags = {
      Project   = "legal-lakehouse"
      ManagedBy = "terraform-bootstrap"
    }
  }
}
