# Needed the moment any Athena query runs (Block 6's reconciliation
# query, and every later Day 2 dbt-on-Athena run) — Athena requires a
# result output location, and enforcing it at the workgroup level means
# individual queries/dbt profiles don't each have to specify it.
resource "aws_athena_workgroup" "legal_lakehouse" {
  name = "legal-lakehouse"

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true

    result_configuration {
      output_location = "s3://${aws_s3_bucket.data.id}/athena-results/"
    }
  }
}
