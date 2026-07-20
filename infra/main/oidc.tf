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

  # GitHub's two real intermediate-CA thumbprints. Both are listed
  # because GitHub's servers can return either intermediate certificate,
  # and pinning only one is a documented cause of intermittent failures.
  #
  # AWS's docs say it has verified this provider against its trusted root
  # CA library since July 2023 and no longer needs the thumbprint. An
  # earlier version of this file therefore used a randomly generated
  # placeholder — which was wrong. Whatever the documented behaviour,
  # supplying real values costs nothing and removes the only
  # non-authentic element from the configuration.
  thumbprint_list = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "1c58a3a8518e8759bf075b76b750d4f2df264fcd",
  ]
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

# NOTE THE NAME: it deliberately does NOT contain the string "github".
#
# aws-actions/configure-aws-credentials has an open bug
# (https://github.com/aws-actions/configure-aws-credentials/issues/1093,
# and #953) where a role whose NAME contains "github" fails to assume
# with the completely non-specific error:
#
#   Could not assume role with OIDC:
#   Not authorized to perform sts:AssumeRoleWithWebIdentity
#
# The suspected cause is GitHub Actions' automatic secret-masking
# interfering with the role name inside the action before the STS call is
# made — so the ARN that actually reaches AWS is not the one configured.
#
# This cost several hours to find, because every value on the AWS side
# (provider URL, audience, trust policy sub, the decoded token claims)
# verifies as correct. The fault is in the action, not the configuration.
# The original name here was "legal-lakehouse-github-deploy".
resource "aws_iam_role" "github_deploy" {
  name               = "legal-lakehouse-ci-deploy"
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
  description = "Store as the AWS_DEPLOY_ROLE_ARN repository secret. NOTE: the role name must not contain the string 'github' — see the comment on aws_iam_role.github_deploy."
}
