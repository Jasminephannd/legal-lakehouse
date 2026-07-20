variable "github_repo" {
  description = "GitHub \"owner/repo\" allowed to assume the deploy role via OIDC (classic sub claim form)."
  type        = string
  default     = "Jasminephannd/legal-lakehouse"
}

# --- Immutable-ID form of the OIDC sub claim -----------------------------
#
# GitHub issues the sub claim with numeric IDs appended to the owner and
# repo names. These four variables build that form. To find the values for
# a different repo, decode the OIDC token in a workflow run (see the
# "Decode the real OIDC token claims" step in .github/workflows/cd.yml) —
# they are NOT visible in the rendered `github.*` workflow context.
#
# Alternatively:
#   owner id: curl -s https://api.github.com/users/<owner> | jq .id
#   repo id:  curl -s https://api.github.com/repos/<owner>/<repo> | jq .id

variable "github_repo_owner" {
  description = "GitHub account/org name, e.g. Jasminephannd."
  type        = string
  default     = "Jasminephannd"
}

variable "github_owner_id" {
  description = "Immutable numeric GitHub account ID, as it appears in the OIDC sub claim."
  type        = string
  default     = "57733436"
}

variable "github_repo_name" {
  description = "Repository name without the owner prefix."
  type        = string
  default     = "legal-lakehouse"
}

variable "github_repo_id" {
  description = "Immutable numeric GitHub repository ID, as it appears in the OIDC sub claim."
  type        = string
  default     = "1306134894"
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
