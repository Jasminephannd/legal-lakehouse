terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Native S3 state locking (use_lockfile) needs Terraform >= 1.10.
  # Check with `terraform version` before first init. If you're on an
  # older version, drop use_lockfile and add a DynamoDB lock table instead
  # (dynamodb_table = "legal-lakehouse-tflock" + a matching resource in
  # infra/bootstrap).
  backend "s3" {
    bucket       = "legal-lakehouse-tfstate-jasminephannd" # must match infra/bootstrap's state_bucket_name output, literally
    key          = "main/terraform.tfstate"
    region       = "ap-southeast-2"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = "ap-southeast-2"

  default_tags {
    tags = {
      Project = "legal-lakehouse"
    }
  }
}
