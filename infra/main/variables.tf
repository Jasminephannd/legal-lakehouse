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

variable "parser_image_tag" {
  description = "Git SHA tag of the parser image already built and pushed to ECR (see src/parser/Dockerfile). No default on purpose: this must reference an image that actually exists in the legal-lakehouse-parser repo before you can apply — build and push first, then pass this in with -var or TF_VAR_parser_image_tag."
  type        = string
}
