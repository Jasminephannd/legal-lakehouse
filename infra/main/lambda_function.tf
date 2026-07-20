# Container image Lambda (not a zip) — pyarrow alone blows past the
# 250MB unzipped package limit. Image must already exist in ECR at
# var.parser_image_tag before this can apply; build/push happens outside
# Terraform (manually today per the plan, automated by cd.yml on Day 2).
resource "aws_cloudwatch_log_group" "parser_lambda" {
  name              = "/aws/lambda/legal-lakehouse-parser"
  retention_in_days = 14
}

resource "aws_lambda_function" "parser" {
  function_name = "legal-lakehouse-parser"
  role          = aws_iam_role.parser_lambda.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.parser.repository_url}:${var.parser_image_tag}"

  architectures = ["arm64"] # cheaper and faster than x86_64; must match the image's build --platform
  memory_size   = 2048      # billing is per GB-second and CPU scales with memory — a job that
  timeout       = 300       # finishes 3x faster at 2x memory often costs less overall, not more

  depends_on = [aws_cloudwatch_log_group.parser_lambda]
}

resource "aws_lambda_permission" "allow_s3_invoke" {
  statement_id  = "AllowS3Invoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.parser.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.data.arn
}

# Triggers only on the actual data batches — the .jsonl.gz suffix filter
# excludes manifest.json (also written under bronze/) and the prefix
# marker objects from s3_data_lake.tf.
resource "aws_s3_bucket_notification" "bronze_trigger" {
  bucket = aws_s3_bucket.data.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.parser.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "bronze/"
    filter_suffix       = ".jsonl.gz"
  }

  depends_on = [aws_lambda_permission.allow_s3_invoke]
}

output "parser_lambda_function_name" {
  value       = aws_lambda_function.parser.function_name
  description = "For viewing logs / manual invocation during Block 6 verification."
}
