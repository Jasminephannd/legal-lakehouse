variable "github_repo" {
  description = "GitHub \"org/repo\" allowed to assume the deploy role via OIDC."
  type        = string
  default     = "Jasminephannd/legal-lakehouse"
}

variable "state_bucket_name" {
  description = "Name of the bucket created in infra/bootstrap. Must match the literal value hardcoded in this file's backend block — used here only to scope the deploy role's IAM permissions to that bucket."
  type        = string
  default     = "legal-lakehouse-tfstate-jasminephannd"
}

variable "data_bucket_name" {
  description = "Globally-unique name for the single data lake bucket (bronze/silver/gold/rejected prefixes live inside it)."
  type        = string
  default     = "legal-lakehouse-data-jasminephannd"
}
