# Execution role for the parser Lambda (Block 5). Written by hand rather
# than attaching AmazonS3FullAccess or similar managed policies — the
# plan's own note applies here: least privilege is a talking point later.
data "aws_iam_policy_document" "parser_lambda_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "parser_lambda" {
  name               = "legal-lakehouse-parser-lambda"
  assume_role_policy = data.aws_iam_policy_document.parser_lambda_trust.json
}

data "aws_iam_policy_document" "parser_lambda_permissions" {
  statement {
    sid       = "ReadBronze"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.data.arn}/bronze/*"]
  }

  statement {
    sid    = "WriteSilverAndRejected"
    effect = "Allow"
    actions = [
      "s3:PutObject",
    ]
    resources = [
      "${aws_s3_bucket.data.arn}/silver/*",
      "${aws_s3_bucket.data.arn}/rejected/*",
    ]
  }

  statement {
    sid    = "Logs"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["arn:aws:logs:ap-southeast-2:*:log-group:/aws/lambda/legal-lakehouse-*:*"]
  }
}

resource "aws_iam_role_policy" "parser_lambda" {
  name   = "legal-lakehouse-parser-lambda-permissions"
  role   = aws_iam_role.parser_lambda.id
  policy = data.aws_iam_policy_document.parser_lambda_permissions.json
}

output "parser_lambda_role_arn" {
  value       = aws_iam_role.parser_lambda.arn
  description = "Used by the aws_lambda_function resource in Block 5."
}
