# One bucket, four prefixes (bronze/silver/gold per the plan, plus
# rejected/ for failed validation records — introduced in Block 4 but the
# bucket permission model needs it from the start). One bucket keeps IAM
# simple; separate prefixes keep lifecycle rules independent.
resource "aws_s3_bucket" "data" {
  bucket = var.data_bucket_name
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket = aws_s3_bucket.data.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Bronze is deliberately cheap and short-lived: it's a re-fetchable cache
# of the HF pull, not a system of record. Silver/gold have no expiration —
# they're the durable layers.
resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    id     = "expire-bronze"
    status = "Enabled"

    filter {
      prefix = "bronze/"
    }

    expiration {
      days = 30
    }
  }

  # Athena query result files (Block 6's reconciliation query, and every
  # later Day 2 dbt-on-Athena query) are throwaway output, not data —
  # matches the plan's Day 2 Block 1 note on this same prefix.
  rule {
    id     = "expire-athena-results"
    status = "Enabled"

    filter {
      prefix = "athena-results/"
    }

    expiration {
      days = 7
    }
  }
}

# Zero-byte marker objects purely so the prefixes are visible in the
# console/CLI before anything has been written to them yet. S3 doesn't
# require these — a prefix exists implicitly the moment an object is
# written under it — so this block is convenience, not necessity.
resource "aws_s3_object" "prefix_markers" {
  for_each = toset(["bronze/", "silver/", "gold/", "rejected/", "athena-results/"])

  bucket       = aws_s3_bucket.data.id
  key          = each.value
  content_type = "application/x-directory"
  content      = "" # empty body — using `content` instead of `source = "/dev/null"` so this applies cleanly on Windows too
}

output "data_bucket_name" {
  value       = aws_s3_bucket.data.id
  description = "Referenced by the ingest script, the parser Lambda, and the Glue table definition in Block 6."
}
