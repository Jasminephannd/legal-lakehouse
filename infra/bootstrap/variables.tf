variable "state_bucket_name" {
  description = "Globally-unique S3 bucket name for Terraform remote state, consumed by infra/main's backend \"s3\" block."
  type        = string
  default     = "legal-lakehouse-tfstate-jasminephannd"
}
