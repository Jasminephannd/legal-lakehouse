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

    # GitHub now issues the `sub` claim with IMMUTABLE NUMERIC IDS
    # appended to both the owner and the repository name:
    #
    #   repo:Jasminephannd@57733436/legal-lakehouse@1306134894:ref:refs/heads/main
    #                     ^^^^^^^^^                ^^^^^^^^^^^
    #
    # not the classic, widely-documented shape:
    #
    #   repo:Jasminephannd/legal-lakehouse:ref:refs/heads/main
    #
    # This is invisible from the workflow context — `github.repository`
    # still renders as the plain "owner/repo", so a policy written from
    # the docs (or from the rendered context) silently fails to match and
    # STS returns only "Not authorized to perform
    # sts:AssumeRoleWithWebIdentity". The only way to see it is to decode
    # the issued JWT — see the "Decode the real OIDC token claims" step in
    # .github/workflows/cd.yml.
    #
    # Both forms are listed so the policy keeps working whichever shape
    # GitHub issues. The ID form is actually the STRONGER of the two: the
    # numeric IDs are immutable, so deleting and recreating a repo under
    # the same name produces a different ID and will NOT match — which
    # closes a real (if obscure) name-reuse hole that the classic form
    # leaves open.
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        "repo:${var.github_repo_owner}@${var.github_owner_id}/${var.github_repo_name}@${var.github_repo_id}:ref:refs/heads/main",
        "repo:${var.github_repo}:ref:refs/heads/main",
      ]
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
# THE LESSON THAT SHAPES THIS WHOLE POLICY:
#
# A least-privilege policy for Terraform is NOT "the actions Terraform
# performs". Before applying anything, Terraform REFRESHES — it reads the
# complete current state of every managed resource, including tags,
# bucket policies, lifecycle rules and encryption settings that were never
# explicitly configured. So every resource needs its Get*/List*/Describe*
# actions too, or `terraform plan` dies during refresh having changed
# nothing.
#
# Writing this policy from intent ("it creates a bucket, so it needs
# CreateBucket") produced six separate AccessDenied failures on the first
# real CD run: ecr:ListTagsForResource, glue:GetTags,
# logs:DescribeLogGroups, iam:GetOpenIDConnectProvider,
# s3:GetBucketPolicy, and s3:DeleteObject on the state lock file.
#
# Scoped by ARN prefix wherever the service supports it. Where a statement
# uses "*", there's a comment saying why — that constraint is real, not
# laziness.
data "aws_iam_policy_document" "deploy_permissions" {
  statement {
    sid    = "TerraformStateAccess"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      # DeleteObject is required to RELEASE THE STATE LOCK. With
      # use_lockfile, the lock is an S3 object (.tflock) that Terraform
      # deletes on completion. Without this, every run leaves the state
      # locked and the next one fails until someone force-unlocks by hand.
      "s3:DeleteObject",
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
      # Write/manage
      "s3:CreateBucket",
      "s3:PutBucketVersioning",
      "s3:PutBucketPolicy",
      "s3:PutBucketPublicAccessBlock",
      "s3:PutEncryptionConfiguration",
      "s3:PutLifecycleConfiguration",
      "s3:PutBucketTagging",
      "s3:PutObject",
      "s3:DeleteObject",

      # Read — needed by `terraform refresh`, not by anything this
      # pipeline consciously does. GetBucketPolicy is required even though
      # no bucket policy is ever set: Terraform reads it to confirm it's
      # absent.
      "s3:GetBucketPolicy",
      "s3:GetBucketVersioning",
      "s3:GetBucketPublicAccessBlock",
      "s3:GetEncryptionConfiguration",
      "s3:GetLifecycleConfiguration",
      "s3:GetBucketTagging",
      "s3:GetBucketLocation",
      "s3:GetBucketAcl",
      "s3:GetBucketCORS",
      "s3:GetBucketWebsite",
      "s3:GetBucketLogging",
      "s3:GetBucketObjectLockConfiguration",
      "s3:GetBucketRequestPayment",
      "s3:GetReplicationConfiguration",
      "s3:GetAccelerateConfiguration",
      "s3:GetObject",
      "s3:ListBucket",

      # The bucket notification wires the S3 -> Lambda trigger.
      "s3:GetBucketNotification",
      "s3:PutBucketNotification",

      # Object-level tag reads. default_tags in versions.tf applies tags
      # to the aws_s3_object prefix markers, so refresh reads them back.
      "s3:GetObjectTagging",
      "s3:PutObjectTagging",
      "s3:DeleteObjectTagging",
      "s3:GetObjectVersion",
      "s3:GetObjectVersionTagging",
      "s3:GetObjectAcl",
    ]
    resources = [
      "arn:aws:s3:::legal-lakehouse-data*",
      "arn:aws:s3:::legal-lakehouse-data*/*",
    ]
  }

  # Athena. Two distinct needs in one statement:
  #   1. Terraform manages the workgroup (Get/Create/Update/Delete + tags)
  #   2. `dbt build --target prod` runs queries THROUGH that workgroup,
  #      so the same role needs query-execution permissions.
  # Adding both now rather than discovering (2) in a later failed run.
  statement {
    sid    = "Athena"
    effect = "Allow"
    actions = [
      "athena:GetWorkGroup",
      "athena:CreateWorkGroup",
      "athena:UpdateWorkGroup",
      "athena:DeleteWorkGroup",
      "athena:ListWorkGroups",
      "athena:TagResource",
      "athena:UntagResource",
      "athena:ListTagsForResource",

      # Query execution — needed by dbt.
      "athena:StartQueryExecution",
      "athena:StopQueryExecution",
      "athena:GetQueryExecution",
      "athena:GetQueryResults",
      "athena:GetQueryResultsStream",
      "athena:ListQueryExecutions",
      "athena:BatchGetQueryExecution",
      "athena:GetDataCatalog",
      "athena:ListDataCatalogs",
      "athena:GetDatabase",
      "athena:ListDatabases",
      "athena:GetTableMetadata",
      "athena:ListTableMetadata",
    ]
    resources = ["*"]
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
      # Tag actions: the AWS provider reads tags on every Glue resource
      # during refresh, and default_tags in versions.tf means it writes
      # them too.
      "glue:GetTags",
      "glue:TagResource",
      "glue:UntagResource",

      # --- required by dbt-athena, not by Terraform ---
      # dbt manages tables and views THROUGH the Glue catalog, so it needs
      # a wider set than Terraform does. Table VERSION actions in
      # particular are non-obvious: replacing a view makes dbt call
      # GetTableVersions to find prior versions to clean up, and that call
      # is evaluated against the CATALOG arn
      # (arn:aws:glue:<region>:<account>:catalog), not the table's.
      "glue:GetTableVersion",
      "glue:GetTableVersions",
      "glue:DeleteTableVersion",
      "glue:BatchDeleteTableVersion",
      "glue:BatchDeleteTable",
      "glue:BatchDeletePartition",
      "glue:CreatePartition",
      "glue:UpdatePartition",
      "glue:DeletePartition",
      "glue:BatchUpdatePartition",
      "glue:GetCatalogImportStatus",
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

      # --- the actual push sequence ---
      # Easy to under-scope, because a docker push is not one API call.
      # BatchGetImage and GetDownloadUrlForLayer are READ actions, but a
      # push still needs them: the client issues
      #   HEAD /v2/<repo>/manifests/<tag>
      # to check whether the manifest already exists before uploading,
      # and that maps to ecr:BatchGetImage. Omitting it fails the push at
      # the very last step with a bare "403 Forbidden" — after every layer
      # has already uploaded successfully, which makes it look like a
      # registry problem rather than a permissions one.
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:PutImage",

      # Refresh-time reads.
      "ecr:ListTagsForResource",
      "ecr:TagResource",
      "ecr:UntagResource",
      "ecr:GetLifecyclePolicy",
      "ecr:DescribeImages",
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
      "lambda:UntagResource",
      "lambda:ListTags",

      # Refresh-time reads. ListVersionsByFunction is required even
      # though this project never publishes a version — the provider
      # calls it to determine the function's latest version on every
      # refresh.
      "lambda:ListVersionsByFunction",
      "lambda:GetFunctionConfiguration",
      "lambda:GetFunctionCodeSigningConfig",
      "lambda:GetFunctionConcurrency",
      "lambda:GetFunctionEventInvokeConfig",
      "lambda:ListFunctionEventInvokeConfigs",
      "lambda:GetFunctionUrlConfig",
      "lambda:GetRuntimeManagementConfig",
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
      "iam:ListRoleTags",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:PassRole",
    ]
    resources = ["arn:aws:iam::*:role/legal-lakehouse-*"]
  }

  # The OIDC provider is a separate ARN type from roles, so it needs its
  # own statement. Terraform manages this resource, so it reads it on
  # every refresh — including the run that is itself authenticating
  # through it.
  statement {
    sid    = "IamOidcProvider"
    effect = "Allow"
    actions = [
      "iam:GetOpenIDConnectProvider",
      "iam:CreateOpenIDConnectProvider",
      "iam:UpdateOpenIDConnectProviderThumbprint",
      "iam:AddClientIDToOpenIDConnectProvider",
      "iam:RemoveClientIDFromOpenIDConnectProvider",
      "iam:TagOpenIDConnectProvider",
      "iam:UntagOpenIDConnectProvider",
      "iam:ListOpenIDConnectProviderTags",
    ]
    resources = ["arn:aws:iam::*:oidc-provider/token.actions.githubusercontent.com"]
  }

  statement {
    sid    = "CloudWatchLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:DeleteLogGroup",
      "logs:PutRetentionPolicy",
      "logs:ListTagsForResource",
      "logs:TagResource",
      "logs:UntagResource",
    ]
    resources = ["arn:aws:logs:ap-southeast-2:*:log-group:/aws/lambda/legal-lakehouse-*"]
  }

  # logs:DescribeLogGroups CANNOT be scoped to a specific log group.
  # It's a list operation over the whole region, and AWS evaluates it
  # against `arn:aws:logs:<region>:<account>:log-group::log-stream:` —
  # note the empty log-group segment. Scoping it to
  # "log-group:/aws/lambda/legal-lakehouse-*" therefore never matches, and
  # produces an AccessDenied that names a resource ARN you didn't write.
  statement {
    sid       = "CloudWatchLogsDescribe"
    effect    = "Allow"
    actions   = ["logs:DescribeLogGroups"]
    resources = ["*"]
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
