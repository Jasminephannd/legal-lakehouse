# --- GitHub OIDC provider ---------------------------------------------
#
# AWS has verified GitHub's OIDC endpoint against its own trusted root CAs
# since July 2023 and no longer actually checks this thumbprint — but the
# Terraform aws_iam_openid_connect_provider resource still requires a
# non-empty thumbprint_list. The value below is a placeholder; AWS ignores
# it in practice. (Flagging this because the source plan says "no
# thumbprint required," which is true of AWS's validation but not of this
# Terraform resource's schema.)
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]

  thumbprint_list = ["7ff779a415f7a1e932e21fae2f39af8798cd1d05"]
}

# --- Trust policy: only main-branch pushes on this exact repo ----------
#
# Scoping `sub` to ref:refs/heads/main (not a wildcard) means a PR from a
# fork can't assume this role. The `aud` condition pins the audience to
# the AWS STS endpoint GitHub's action targets.
data "aws_iam_policy_document" "github_oidc_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo}:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  name               = "legal-lakehouse-github-deploy"
  assume_role_policy = data.aws_iam_policy_document.github_oidc_trust.json
}

# --- Deploy permissions --------------------------------------------------
#
# Scoped to this project's resources by name/ARN prefix wherever the
# service supports it (S3, Lambda, IAM, CloudWatch Logs). Glue and ECR
# don't offer resource-level ARNs for the catalog/repo-management actions
# used here, so those two statements are action-scoped only.
#
# This covers Day 1 (S3, Glue, ECR, Lambda, IAM). Day 2 adds Step
# Functions, SNS, SQS, and Athena permissions — extend this file then
# rather than over-granting now.
data "aws_iam_policy_document" "deploy_permissions" {
  statement {
    sid    = "TerraformStateAccess"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:ListBucket",
    ]
    resources = [
      "arn:aws:s3:::${var.state_bucket_name}",
      "arn:aws:s3:::${var.state_bucket_name}/*",
    ]
  }

  statement {
    sid    = "DataLakeBucket"
    effect = "Allow"
    actions = [
      "s3:CreateBucket",
      "s3:PutBucketVersioning",
      "s3:PutBucketPolicy",
      "s3:PutBucketPublicAccessBlock",
      "s3:PutEncryptionConfiguration",
      "s3:PutLifecycleConfiguration",
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:ListBucket",
    ]
    resources = [
      "arn:aws:s3:::legal-lakehouse-data*",
      "arn:aws:s3:::legal-lakehouse-data*/*",
    ]
  }

  statement {
    sid    = "Glue"
    effect = "Allow"
    actions = [
      "glue:CreateDatabase",
      "glue:DeleteDatabase",
      "glue:GetDatabase",
      "glue:GetDatabases",
      "glue:UpdateDatabase",
      "glue:CreateTable",
      "glue:DeleteTable",
      "glue:GetTable",
      "glue:GetTables",
      "glue:UpdateTable",
      "glue:BatchCreatePartition",
      "glue:GetPartition",
      "glue:GetPartitions",
      "glue:BatchGetPartition",
    ]
    resources = ["*"] # Glue Data Catalog actions don't support resource-level ARNs here
  }

  statement {
    sid    = "ECR"
    effect = "Allow"
    actions = [
      "ecr:CreateRepository",
      "ecr:DeleteRepository",
      "ecr:DescribeRepositories",
      "ecr:PutLifecyclePolicy",
      "ecr:SetRepositoryPolicy",
      "ecr:GetRepositoryPolicy",
      "ecr:GetAuthorizationToken",
      "ecr:BatchCheckLayerAvailability",
      "ecr:PutImage",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
    ]
    resources = ["*"] # ecr:GetAuthorizationToken in particular only works against "*"
  }

  statement {
    sid    = "Lambda"
    effect = "Allow"
    actions = [
      "lambda:CreateFunction",
      "lambda:DeleteFunction",
      "lambda:GetFunction",
      "lambda:UpdateFunctionCode",
      "lambda:UpdateFunctionConfiguration",
      "lambda:AddPermission",
      "lambda:RemovePermission",
      "lambda:GetPolicy",
      "lambda:TagResource",
      "lambda:ListTags",
    ]
    resources = ["arn:aws:lambda:ap-southeast-2:*:function:legal-lakehouse-*"]
  }

  statement {
    sid    = "IamForProjectRoles"
    effect = "Allow"
    actions = [
      "iam:CreateRole",
      "iam:DeleteRole",
      "iam:GetRole",
      "iam:PutRolePolicy",
      "iam:DeleteRolePolicy",
      "iam:GetRolePolicy",
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
      "iam:ListRolePolicies",
      "iam:ListAttachedRolePolicies",
      "iam:TagRole",
      "iam:PassRole",
    ]
    resources = ["arn:aws:iam::*:role/legal-lakehouse-*"]
  }

  statement {
    sid    = "CloudWatchLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:DeleteLogGroup",
      "logs:PutRetentionPolicy",
      "logs:DescribeLogGroups",
      "logs:TagResource",
    ]
    resources = ["arn:aws:logs:ap-southeast-2:*:log-group:/aws/lambda/legal-lakehouse-*"]
  }
}

resource "aws_iam_role_policy" "github_deploy" {
  name   = "legal-lakehouse-deploy-permissions"
  role   = aws_iam_role.github_deploy.id
  policy = data.aws_iam_policy_document.deploy_permissions.json
}

output "github_deploy_role_arn" {
  value       = aws_iam_role.github_deploy.arn
  description = "Set this as the `role-to-assume` input for aws-actions/configure-aws-credentials in cd.yml (Day 2)."
}
