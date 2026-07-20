output "state_bucket_name" {
  value       = aws_s3_bucket.tfstate.id
  description = "Use this exact value as the `bucket` in infra/main/versions.tf's backend \"s3\" block."
}
