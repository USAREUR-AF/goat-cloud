# Hunterkiller Module
#
# Automated compliance enforcement with 24-hour grace period.
# Three-layer safety model: notifier/executor split, DynamoDB approval gate,
# terraform-gated ephemeral permissions.
#
# Flow:
#   1. Compliance scanner (nightly) detects non-compliant resources
#   2. Notifier Lambda sends 24-hr warning emails, writes DynamoDB state,
#      schedules a deferred enforcement invocation
#   3. When executor_armed=true: Executor Lambda re-reads fresh compliance report,
#      checks approval_status, and tears down approved resources (unless dry_run)
#   4. Sends confirmation emails with results
#
# Safety:
#   - executor_armed = false by default (no destructive Lambda or IAM exists)
#   - enabled = false by default (EventBridge rules disabled)
#   - dry_run = true by default (teardowns logged but not executed)
#   - DynamoDB approval gate (admin must approve each resource)
#   - Tag-based IAM conditions prevent touching Protected/prod resources
#   - Production-protected resources (Environment=prod, Protected=true) are exempt

locals {
  name_prefix = "${var.project}-${var.environment}"
  table_name  = "${local.name_prefix}-hunterkiller-state"

  # The protected tag key and the protected environment names default to the
  # contract's (contract.generated.tf, generated from contract/contract.yaml).
  environment_tag_key    = local.generated_contract.keys["environment"]
  protected_tag_key      = coalesce(var.protected_tag_key, local.generated_contract.keys["protected"])
  protected_environments = var.protected_environments == null ? local.generated_contract.protected_values["environment"] : var.protected_environments

  # Contacts taken from tags may be in these mail domains; by default, the
  # domains of the admin addresses.
  contact_allowed_domains = var.contact_allowed_email_domains != null ? var.contact_allowed_email_domains : distinct([
    for address in var.ses_admin_emails : lower(element(split("@", address), length(split("@", address)) - 1))
    if length(split("@", address)) > 1
  ])

  shared_schema_path   = "${path.module}/../shared/compliance_schema.py"
  shared_contract_path = "${path.module}/../shared/compliance_contract.py"

  bedrock_profile_prefix   = try(regex("^(eu|us|apac|global)\\.", var.bedrock_summary_model_id)[0], "")
  bedrock_foundation_model = local.bedrock_profile_prefix != "" ? trimprefix(var.bedrock_summary_model_id, "${local.bedrock_profile_prefix}.") : var.bedrock_summary_model_id
  bedrock_summary_resources = concat(
    local.bedrock_profile_prefix != "" ? ["arn:aws:bedrock:*:${data.aws_caller_identity.current.account_id}:inference-profile/${var.bedrock_summary_model_id}"] : [],
    ["arn:aws:bedrock:*::foundation-model/${local.bedrock_foundation_model}"],
  )
  lambda_source_hash = base64sha256(join("", concat(
    [
      for f in sort(fileset("${path.module}/lambda", "**"))
      : filesha256("${path.module}/lambda/${f}")
      if length(regexall("__pycache__|\\.pyc$|\\.pytest_cache", f)) == 0
    ],
    [filesha256(local.shared_schema_path)],
    [filesha256(local.shared_contract_path)],
  )))
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# =============================================================================
# DynamoDB — Notification State Tracking
# =============================================================================

resource "aws_dynamodb_table" "state" {
  name         = local.table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "resource_arn"

  attribute {
    name = "resource_arn"
    type = "S"
  }

  ttl {
    attribute_name = "ttl_expires"
    enabled        = true
  }

  server_side_encryption {
    enabled = true
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = merge(var.tags, {
    Name = local.table_name
  })
}

# =============================================================================
# IAM — Notifier Role (always deployed, non-destructive only)
# =============================================================================

data "aws_iam_policy_document" "notifier_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "notifier" {
  name               = "${local.name_prefix}-hunterkiller-notifier"
  assume_role_policy = data.aws_iam_policy_document.notifier_assume_role.json

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-hunterkiller-notifier"
  })
}

resource "aws_iam_role_policy_attachment" "notifier_read_only" {
  role       = aws_iam_role.notifier.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

data "aws_iam_policy_document" "notifier_permissions" {
  # CloudWatch Logs
  statement {
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents"
    ]
    resources = ["arn:aws:logs:*:*:*"]
  }

  # DynamoDB — notification state CRUD
  statement {
    actions = [
      "dynamodb:PutItem",
      "dynamodb:GetItem",
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem",
      "dynamodb:Query",
      "dynamodb:Scan"
    ]
    resources = [aws_dynamodb_table.state.arn]
  }

  # Read compliance reports from S3
  statement {
    actions = ["s3:GetObject", "s3:ListBucket"]
    resources = [
      "arn:aws:s3:::${var.compliance_reports_bucket}",
      "arn:aws:s3:::${var.compliance_reports_bucket}/*"
    ]
  }

  # Send notification emails via SES
  dynamic "statement" {
    for_each = var.ses_sender_email != "" ? [1] : []
    content {
      actions   = ["ses:SendRawEmail"]
      resources = ["*"]
      condition {
        test     = "StringEquals"
        variable = "ses:FromAddress"
        values   = [var.ses_sender_email]
      }
    }
  }

  # IAM user tag lookup (for ManagedBy → email resolution)
  statement {
    actions   = ["iam:ListUserTags", "iam:GetUser"]
    resources = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:user/*"]
  }

  # EventBridge Scheduler — create/delete one-shot schedules for deferred enforcement
  statement {
    actions = [
      "scheduler:CreateSchedule",
      "scheduler:DeleteSchedule",
      "scheduler:GetSchedule"
    ]
    resources = ["arn:aws:scheduler:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:schedule/default/${local.name_prefix}-hk-enforce-*"]
  }

  # Pass the scheduler role (whose only permission is invoking the executor)
  # to EventBridge Scheduler. No role is passable when the executor is not armed.
  dynamic "statement" {
    for_each = var.executor_armed ? [1] : []
    content {
      actions   = ["iam:PassRole"]
      resources = [aws_iam_role.scheduler[0].arn]
      condition {
        test     = "StringEquals"
        variable = "iam:PassedToService"
        values   = ["scheduler.amazonaws.com"]
      }
    }
  }

  # Look up executor Lambda ARN for scheduling enforcement
  dynamic "statement" {
    for_each = var.executor_armed ? [1] : []
    content {
      actions   = ["lambda:GetFunction"]
      resources = [aws_lambda_function.executor[0].arn]
    }
  }

  # SSM — write job execution status for dashboard visibility
  statement {
    actions   = ["ssm:PutParameter"]
    resources = ["arn:aws:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter/${var.project}/${var.environment}/job-status/*"]
  }
}

resource "aws_iam_role_policy" "notifier_permissions" {
  name   = "${local.name_prefix}-hunterkiller-notifier-permissions"
  role   = aws_iam_role.notifier.id
  policy = data.aws_iam_policy_document.notifier_permissions.json
}

# =============================================================================
# IAM — Executor Role (conditional, only when executor_armed=true)
# =============================================================================

data "aws_iam_policy_document" "executor_assume_role" {
  count = var.executor_armed ? 1 : 0

  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# =============================================================================
# IAM — Scheduler Role (conditional): the deferred enforcement schedule runs
# as this role. It can invoke the executor function and nothing else, so a
# schedule cannot call any other API with the executor's permissions.
# =============================================================================

data "aws_iam_policy_document" "scheduler_assume_role" {
  count = var.executor_armed ? 1 : 0

  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  count = var.executor_armed ? 1 : 0

  name               = "${local.name_prefix}-hunterkiller-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume_role[0].json

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-hunterkiller-scheduler"
  })
}

data "aws_iam_policy_document" "scheduler_permissions" {
  count = var.executor_armed ? 1 : 0

  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.executor[0].arn, "${aws_lambda_function.executor[0].arn}:*"]
  }
}

resource "aws_iam_role_policy" "scheduler_permissions" {
  count = var.executor_armed ? 1 : 0

  name   = "${local.name_prefix}-hunterkiller-scheduler-invoke"
  role   = aws_iam_role.scheduler[0].id
  policy = data.aws_iam_policy_document.scheduler_permissions[0].json
}

resource "aws_iam_role" "executor" {
  count = var.executor_armed ? 1 : 0

  name               = "${local.name_prefix}-hunterkiller-executor"
  assume_role_policy = data.aws_iam_policy_document.executor_assume_role[0].json

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-hunterkiller-executor"
  })
}

data "aws_iam_policy_document" "executor_permissions" {
  count = var.executor_armed ? 1 : 0

  # CloudWatch Logs
  statement {
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents"
    ]
    resources = ["arn:aws:logs:*:*:*"]
  }

  # DynamoDB — read state + update results
  statement {
    actions = [
      "dynamodb:GetItem",
      "dynamodb:UpdateItem",
      "dynamodb:Query",
      "dynamodb:Scan"
    ]
    resources = [aws_dynamodb_table.state.arn]
  }

  # Read compliance reports from S3
  statement {
    actions = ["s3:GetObject", "s3:ListBucket"]
    resources = [
      "arn:aws:s3:::${var.compliance_reports_bucket}",
      "arn:aws:s3:::${var.compliance_reports_bucket}/*"
    ]
  }

  # Send enforcement report emails via SES
  dynamic "statement" {
    for_each = var.ses_sender_email != "" ? [1] : []
    content {
      actions   = ["ses:SendRawEmail"]
      resources = ["*"]
      condition {
        test     = "StringEquals"
        variable = "ses:FromAddress"
        values   = [var.ses_sender_email]
      }
    }
  }

  # Reads the executor makes, and no others: the protected-resources list,
  # live tags for the protection check, and the lookups its handlers need
  # before a removal (volumes to snapshot, existing snapshots, an instance's
  # cluster membership, a cache cluster's engine).
  dynamic "statement" {
    for_each = var.protected_resources_ssm_name != "" ? [1] : []
    content {
      actions   = ["ssm:GetParameter"]
      resources = ["arn:aws:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter/${trimprefix(var.protected_resources_ssm_name, "/")}"]
    }
  }
  statement {
    sid = "TeardownReads"
    actions = [
      "tag:GetResources",
      "iam:ListRoleTags",
      "iam:ListUserTags",
      "iam:ListPolicyTags",
      "ec2:DescribeInstances",
      "ec2:DescribeSnapshots",
      "rds:DescribeDBInstances",
      "elasticache:DescribeCacheClusters",
    ]
    resources = ["*"]
  }

  # Re-invoke compliance scanner after teardown for fresh post-teardown report
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = ["arn:aws:lambda:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:function:${var.compliance_scanner_function_name}"]
  }

  # Bedrock — optional LLM-written summary in enforcement emails. Granted only
  # when a model id is set. An id with a geography prefix (eu., us., apac.,
  # global.) is a cross-region inference profile, which needs both the profile
  # and the foundation model behind it.
  dynamic "statement" {
    for_each = var.bedrock_summary_model_id != "" ? [1] : []
    content {
      actions   = ["bedrock:InvokeModel"]
      resources = local.bedrock_summary_resources
    }
  }

  # === DESTRUCTIVE PERMISSIONS — tag-scoped (ABAC) ===

  # EC2 teardown — only resources NOT tagged Protected=true AND NOT prod
  statement {
    actions = [
      "ec2:TerminateInstances",
      "ec2:DeleteVolume",
      "ec2:DeleteSecurityGroup",
      "ec2:ReleaseAddress",
      "ec2:DeleteNatGateway"
    ]
    resources = ["*"]
    condition {
      test     = "StringNotEqualsIgnoreCase"
      variable = "aws:ResourceTag/${local.protected_tag_key}"
      values   = ["true", "yes", "1"]
    }
    condition {
      test     = "StringNotEqualsIgnoreCase"
      variable = "aws:ResourceTag/${local.environment_tag_key}"
      values   = local.protected_environments
    }
  }

  # RDS teardown
  statement {
    actions = [
      "rds:DeleteDBInstance"
    ]
    resources = ["*"]
    condition {
      test     = "StringNotEqualsIgnoreCase"
      variable = "aws:ResourceTag/${local.protected_tag_key}"
      values   = ["true", "yes", "1"]
    }
    condition {
      test     = "StringNotEqualsIgnoreCase"
      variable = "aws:ResourceTag/${local.environment_tag_key}"
      values   = local.protected_environments
    }
  }

  # ECS teardown
  statement {
    actions = [
      "ecs:UpdateService",
      "ecs:DeleteService"
    ]
    resources = ["*"]
    condition {
      test     = "StringNotEqualsIgnoreCase"
      variable = "aws:ResourceTag/${local.protected_tag_key}"
      values   = ["true", "yes", "1"]
    }
    condition {
      test     = "StringNotEqualsIgnoreCase"
      variable = "aws:ResourceTag/${local.environment_tag_key}"
      values   = local.protected_environments
    }
  }

  # Lambda teardown
  statement {
    actions   = ["lambda:DeleteFunction"]
    resources = ["*"]
    condition {
      test     = "StringNotEqualsIgnoreCase"
      variable = "aws:ResourceTag/${local.protected_tag_key}"
      values   = ["true", "yes", "1"]
    }
    condition {
      test     = "StringNotEqualsIgnoreCase"
      variable = "aws:ResourceTag/${local.environment_tag_key}"
      values   = local.protected_environments
    }
  }

  # ElastiCache teardown
  statement {
    actions   = ["elasticache:DeleteCacheCluster"]
    resources = ["*"]
    condition {
      test     = "StringNotEqualsIgnoreCase"
      variable = "aws:ResourceTag/${local.protected_tag_key}"
      values   = ["true", "yes", "1"]
    }
    condition {
      test     = "StringNotEqualsIgnoreCase"
      variable = "aws:ResourceTag/${local.environment_tag_key}"
      values   = local.protected_environments
    }
  }

  # ELB teardown
  statement {
    actions   = ["elasticloadbalancing:DeleteLoadBalancer"]
    resources = ["*"]
    condition {
      test     = "StringNotEqualsIgnoreCase"
      variable = "aws:ResourceTag/${local.protected_tag_key}"
      values   = ["true", "yes", "1"]
    }
    condition {
      test     = "StringNotEqualsIgnoreCase"
      variable = "aws:ResourceTag/${local.environment_tag_key}"
      values   = local.protected_environments
    }
  }

  # Recovery points taken before a removal: volume snapshots (tagged at
  # creation), and the final snapshot an RDS instance or cache cluster takes
  # as part of its deletion.
  statement {
    sid       = "FinalSnapshots"
    actions   = ["ec2:CreateSnapshot", "rds:CreateDBSnapshot", "elasticache:CreateSnapshot"]
    resources = ["*"]
  }

  statement {
    sid       = "TagFinalSnapshotAtCreation"
    actions   = ["ec2:CreateTags"]
    resources = ["arn:aws:ec2:*::snapshot/*"]
    condition {
      test     = "StringEquals"
      variable = "ec2:CreateAction"
      values   = ["CreateSnapshot"]
    }
  }

  # S3 teardown — Allow (ARN-scoped deny below blocks infrastructure buckets).
  # An empty bucket can be deleted; objects can be deleted only when
  # unrecoverable_teardown is set.
  statement {
    actions = concat(
      ["s3:DeleteBucket", "s3:ListBucket", "s3:ListBucketVersions"],
      var.unrecoverable_teardown ? ["s3:DeleteObject", "s3:DeleteObjectVersion"] : [],
    )
    resources = ["*"]
    condition {
      test     = "StringNotEqualsIgnoreCase"
      variable = "aws:ResourceTag/${local.protected_tag_key}"
      values   = ["true", "yes", "1"]
    }
    condition {
      test     = "StringNotEqualsIgnoreCase"
      variable = "aws:ResourceTag/${local.environment_tag_key}"
      values   = local.protected_environments
    }
  }

  # S3 — Deny destructive actions on known infrastructure buckets
  # (s3:DeleteBucket supports no resource-tag condition, so the buckets are named)
  statement {
    effect = "Deny"
    actions = [
      "s3:DeleteBucket",
      "s3:DeleteObject",
      "s3:DeleteObjectVersion"
    ]
    resources = [
      "arn:aws:s3:::${var.project}-*-tf-state",
      "arn:aws:s3:::${var.project}-*-tf-state/*",
      "arn:aws:s3:::${var.project}-*-compliance-reports",
      "arn:aws:s3:::${var.project}-*-compliance-reports/*",
    ]
  }

  # EC2 additions — SG rules, flow logs, snapshots (ABAC)
  statement {
    actions = [
      "ec2:RevokeSecurityGroupIngress",
      "ec2:RevokeSecurityGroupEgress",
      "ec2:DeleteFlowLogs"
    ]
    resources = ["*"]
    condition {
      test     = "StringNotEqualsIgnoreCase"
      variable = "aws:ResourceTag/${local.protected_tag_key}"
      values   = ["true", "yes", "1"]
    }
    condition {
      test     = "StringNotEqualsIgnoreCase"
      variable = "aws:ResourceTag/${local.environment_tag_key}"
      values   = local.protected_environments
    }
  }

  # EC2 read — needed by SG rule handler to look up rule direction
  statement {
    actions   = ["ec2:DescribeSecurityGroupRules"]
    resources = ["*"]
  }

  # SSM Parameter teardown (ABAC)
  statement {
    actions   = ["ssm:DeleteParameter"]
    resources = ["*"]
    condition {
      test     = "StringNotEqualsIgnoreCase"
      variable = "aws:ResourceTag/${local.protected_tag_key}"
      values   = ["true", "yes", "1"]
    }
    condition {
      test     = "StringNotEqualsIgnoreCase"
      variable = "aws:ResourceTag/${local.environment_tag_key}"
      values   = local.protected_environments
    }
  }

  # ECR repository teardown (ABAC)
  statement {
    actions = [
      "ecr:DeleteRepository"
    ]
    resources = ["*"]
    condition {
      test     = "StringNotEqualsIgnoreCase"
      variable = "aws:ResourceTag/${local.protected_tag_key}"
      values   = ["true", "yes", "1"]
    }
    condition {
      test     = "StringNotEqualsIgnoreCase"
      variable = "aws:ResourceTag/${local.environment_tag_key}"
      values   = local.protected_environments
    }
  }

  # EventBridge teardown (ABAC)
  statement {
    actions = [
      "events:ListTargetsByRule",
      "events:RemoveTargets",
      "events:DeleteRule"
    ]
    resources = ["*"]
    condition {
      test     = "StringNotEqualsIgnoreCase"
      variable = "aws:ResourceTag/${local.protected_tag_key}"
      values   = ["true", "yes", "1"]
    }
    condition {
      test     = "StringNotEqualsIgnoreCase"
      variable = "aws:ResourceTag/${local.environment_tag_key}"
      values   = local.protected_environments
    }
  }

  # CloudWatch Logs teardown (ABAC)
  statement {
    actions   = ["logs:DeleteLogGroup"]
    resources = ["*"]
    condition {
      test     = "StringNotEqualsIgnoreCase"
      variable = "aws:ResourceTag/${local.protected_tag_key}"
      values   = ["true", "yes", "1"]
    }
    condition {
      test     = "StringNotEqualsIgnoreCase"
      variable = "aws:ResourceTag/${local.environment_tag_key}"
      values   = local.protected_environments
    }
  }

  # Secrets Manager teardown (ABAC)
  statement {
    actions   = ["secretsmanager:DeleteSecret"]
    resources = ["*"]
    condition {
      test     = "StringNotEqualsIgnoreCase"
      variable = "aws:ResourceTag/${local.protected_tag_key}"
      values   = ["true", "yes", "1"]
    }
    condition {
      test     = "StringNotEqualsIgnoreCase"
      variable = "aws:ResourceTag/${local.environment_tag_key}"
      values   = local.protected_environments
    }
  }

  # KMS teardown (ABAC)
  statement {
    actions   = ["kms:ScheduleKeyDeletion"]
    resources = ["*"]
    condition {
      test     = "StringNotEqualsIgnoreCase"
      variable = "aws:ResourceTag/${local.protected_tag_key}"
      values   = ["true", "yes", "1"]
    }
    condition {
      test     = "StringNotEqualsIgnoreCase"
      variable = "aws:ResourceTag/${local.environment_tag_key}"
      values   = local.protected_environments
    }
  }

  # IAM teardown — no ABAC support, software-level protection applies
  statement {
    actions = [
      "iam:DetachRolePolicy",
      "iam:DeleteRolePolicy",
      "iam:ListRolePolicies",
      "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole",
      "iam:RemoveRoleFromInstanceProfile",
      "iam:DeleteRole",
      "iam:ListAccessKeys",
      "iam:DeleteAccessKey",
      "iam:ListAttachedUserPolicies",
      "iam:DetachUserPolicy",
      "iam:ListUserPolicies",
      "iam:DeleteUserPolicy",
      "iam:ListGroupsForUser",
      "iam:RemoveUserFromGroup",
      "iam:DeleteLoginProfile",
      "iam:GetLoginProfile",
      "iam:ListMFADevices",
      "iam:DeactivateMFADevice",
      "iam:DeleteVirtualMFADevice",
      "iam:DeleteUser",
      "iam:ListEntitiesForPolicy",
      "iam:DetachGroupPolicy",
      "iam:ListPolicyVersions",
      "iam:DeletePolicyVersion",
      "iam:DeletePolicy"
    ]
    resources = ["*"]
  }

  # Route53 teardown — no ABAC, software-level protection applies
  statement {
    actions = [
      "route53:ListResourceRecordSets",
      "route53:ChangeResourceRecordSets",
      "route53:DeleteHostedZone"
    ]
    resources = ["*"]
  }

  # RDS additions — parameter groups
  statement {
    actions = [
      "rds:DeleteDBClusterParameterGroup"
    ]
    resources = ["*"]
  }

  # The role holds a delete permission only for a resource type that
  # lambda/executor.py has a handler for. Add the permission with the handler.

  # IAM principals the executor never removes, whatever a record says:
  # service-linked roles, roles that IAM Identity Center reserves, anything
  # under a protected path, and anything tagged as protected or in a protected
  # environment (IAM supports the tag condition on roles, users and policies).
  statement {
    sid    = "DenyReservedIam"
    effect = "Deny"
    actions = [
      "iam:DeleteRole",
      "iam:DeleteRolePolicy",
      "iam:DetachRolePolicy",
      "iam:RemoveRoleFromInstanceProfile",
      "iam:DeleteUser",
      "iam:DeleteUserPolicy",
      "iam:DetachUserPolicy",
      "iam:DeleteAccessKey",
      "iam:DeleteLoginProfile",
      "iam:RemoveUserFromGroup",
      "iam:DeactivateMFADevice",
      "iam:DeletePolicy",
      "iam:DeletePolicyVersion",
    ]
    resources = concat(
      [
        "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/aws-service-role/*",
        "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/aws-reserved/*",
      ],
      flatten([
        for path in var.protected_iam_paths : [
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role${path}*",
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:user${path}*",
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:policy${path}*",
        ]
      ]),
    )
  }

  statement {
    sid    = "DenyProtectedTaggedIam"
    effect = "Deny"
    actions = [
      "iam:DeleteRole",
      "iam:DeleteRolePolicy",
      "iam:DetachRolePolicy",
      "iam:DeleteUser",
      "iam:DeleteUserPolicy",
      "iam:DetachUserPolicy",
      "iam:DeletePolicy",
    ]
    resources = ["*"]
    condition {
      test     = "StringEqualsIgnoreCase"
      variable = "aws:ResourceTag/${local.protected_tag_key}"
      values   = ["true", "yes", "1"]
    }
  }

  statement {
    sid    = "DenyProtectedEnvironmentIam"
    effect = "Deny"
    actions = [
      "iam:DeleteRole",
      "iam:DeleteRolePolicy",
      "iam:DetachRolePolicy",
      "iam:DeleteUser",
      "iam:DeleteUserPolicy",
      "iam:DetachUserPolicy",
      "iam:DeletePolicy",
    ]
    resources = ["*"]
    condition {
      test     = "StringEqualsIgnoreCase"
      variable = "aws:ResourceTag/${local.environment_tag_key}"
      values   = local.protected_environments
    }
  }

  # The enforcement pipeline never removes its own parts, whatever a record says.
  statement {
    sid    = "DenySelfTeardown"
    effect = "Deny"
    actions = [
      "iam:DeleteRole",
      "iam:DeleteRolePolicy",
      "iam:DetachRolePolicy",
      "lambda:DeleteFunction",
      "logs:DeleteLogGroup",
      "dynamodb:DeleteTable",
    ]
    resources = [
      aws_iam_role.executor[0].arn,
      aws_iam_role.notifier.arn,
      aws_iam_role.scheduler[0].arn,
      "arn:aws:lambda:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:function:${local.name_prefix}-hunterkiller",
      "arn:aws:lambda:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:function:${local.name_prefix}-hunterkiller-executor",
      "arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${local.name_prefix}-hunterkiller*",
      aws_dynamodb_table.state.arn,
    ]
  }
}

resource "aws_iam_role_policy" "executor_permissions" {
  count = var.executor_armed ? 1 : 0

  name   = "${local.name_prefix}-hunterkiller-executor-permissions"
  role   = aws_iam_role.executor[0].id
  policy = data.aws_iam_policy_document.executor_permissions[0].json
}

# =============================================================================
# Lambda — Notifier Function (always deployed)
# =============================================================================

resource "null_resource" "lambda_zip" {
  triggers = {
    source_hash = local.lambda_source_hash
  }

  provisioner "local-exec" {
    command = "cp '${path.module}/../shared/compliance_schema.py' '${path.module}/lambda/compliance_schema.py' && cp '${path.module}/../shared/compliance_contract.py' '${path.module}/lambda/compliance_contract.py' && cd '${path.module}/lambda' && rm -f ../lambda.zip && zip -qr ../lambda.zip . -x '*__pycache__*' '*.pyc' '.pytest_cache/*'"
  }
}

resource "aws_lambda_function" "notifier" {
  function_name = "${local.name_prefix}-hunterkiller"
  role          = aws_iam_role.notifier.arn
  handler       = "index.handler"
  runtime       = "python3.12"
  timeout       = 900
  memory_size   = 512

  depends_on       = [null_resource.lambda_zip]
  filename         = "${path.module}/lambda.zip"
  source_code_hash = local.lambda_source_hash

  tracing_config {
    mode = "PassThrough"
  }

  environment {
    variables = {
      PROJECT                  = var.project
      ENVIRONMENT              = var.environment
      DRY_RUN                  = tostring(var.dry_run)
      GRACE_PERIOD_HOURS       = tostring(var.grace_period_hours)
      REPORT_MAX_AGE_HOURS     = tostring(var.destroy_now_report_max_age_hours)
      STATE_TABLE              = aws_dynamodb_table.state.name
      REPORTS_BUCKET           = var.compliance_reports_bucket
      SES_SENDER_EMAIL         = var.ses_sender_email
      SES_ADMIN_EMAILS         = join(",", var.ses_admin_emails)
      PROTECTED_ENVIRONMENTS   = join(",", local.protected_environments)
      PROTECTED_TAG_KEY        = local.protected_tag_key
      PROTECTED_RESOURCE_TYPES = join(",", var.protected_resource_types)
      CONTACT_TAG_KEYS         = join(",", var.contact_tag_keys)
      CONTACT_ALLOWED_DOMAINS  = join(",", local.contact_allowed_domains)
      STATE_TTL_DAYS           = tostring(var.state_ttl_days)
      LAMBDA_FUNCTION_NAME     = "${local.name_prefix}-hunterkiller"
      EXECUTOR_FUNCTION_NAME   = var.executor_armed ? "${local.name_prefix}-hunterkiller-executor" : ""
      SCHEDULER_ROLE_ARN       = var.executor_armed ? aws_iam_role.scheduler[0].arn : ""
      LAMBDA_FUNCTION_ARN      = ""
      DASHBOARD_URL            = var.dashboard_url
      EXECUTOR_ARMED           = tostring(var.executor_armed)
      AUTO_APPROVE_EXPIRED     = tostring(var.auto_approve_expired)
      PROTECTED_RESOURCES_SSM  = var.protected_resources_ssm_name
      JOB_STATUS_SSM_PREFIX    = "/${var.project}/${var.environment}/job-status"
    }
  }

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-hunterkiller"
  })
}

resource "aws_lambda_function_event_invoke_config" "notifier" {
  function_name = aws_lambda_function.notifier.function_name
}

resource "aws_cloudwatch_log_group" "notifier" {
  name              = "/aws/lambda/${aws_lambda_function.notifier.function_name}"
  retention_in_days = 30

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-hunterkiller-notifier-logs"
  })
}

# =============================================================================
# Lambda — Executor Function (conditional, only when executor_armed=true)
# =============================================================================

resource "aws_lambda_function" "executor" {
  count = var.executor_armed ? 1 : 0

  function_name = "${local.name_prefix}-hunterkiller-executor"
  role          = aws_iam_role.executor[0].arn
  handler       = "executor_index.handler"
  runtime       = "python3.12"
  timeout       = 900
  memory_size   = 512

  depends_on       = [null_resource.lambda_zip]
  filename         = "${path.module}/lambda.zip"
  source_code_hash = local.lambda_source_hash

  tracing_config {
    mode = "PassThrough"
  }

  environment {
    variables = {
      PROJECT                  = var.project
      ENVIRONMENT              = var.environment
      DRY_RUN                  = tostring(var.dry_run)
      UNRECOVERABLE_TEARDOWN   = tostring(var.unrecoverable_teardown)
      GRACE_PERIOD_HOURS       = tostring(var.grace_period_hours)
      REPORT_MAX_AGE_HOURS     = tostring(var.destroy_now_report_max_age_hours)
      STATE_TABLE              = aws_dynamodb_table.state.name
      REPORTS_BUCKET           = var.compliance_reports_bucket
      SES_SENDER_EMAIL         = var.ses_sender_email
      SES_ADMIN_EMAILS         = join(",", var.ses_admin_emails)
      PROTECTED_ENVIRONMENTS   = join(",", local.protected_environments)
      PROTECTED_TAG_KEY        = local.protected_tag_key
      PROTECTED_RESOURCE_TYPES = join(",", var.protected_resource_types)
      PROTECTED_RESOURCES_SSM  = var.protected_resources_ssm_name
      STATE_TTL_DAYS           = tostring(var.state_ttl_days)
      DASHBOARD_URL            = var.dashboard_url
      SCANNER_FUNCTION_NAME    = var.compliance_scanner_function_name
      BEDROCK_SUMMARY_MODEL_ID = var.bedrock_summary_model_id
      BEDROCK_SUMMARY_REGION   = var.bedrock_summary_region
    }
  }

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-hunterkiller-executor"
  })
}

resource "aws_cloudwatch_log_group" "executor" {
  count = var.executor_armed ? 1 : 0

  name              = "/aws/lambda/${aws_lambda_function.executor[0].function_name}"
  retention_in_days = 30

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-hunterkiller-executor-logs"
  })
}

# =============================================================================
# EventBridge — Allow Scanner to Invoke Notifier
# =============================================================================

# Allow EventBridge Scheduler to invoke the executor Lambda
# (for deferred enforcement after 24-hour grace period)
resource "aws_lambda_permission" "allow_scheduler_executor" {
  count = var.executor_armed ? 1 : 0

  statement_id   = "AllowSchedulerInvoke"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.executor[0].function_name
  principal      = "scheduler.amazonaws.com"
  source_account = data.aws_caller_identity.current.account_id
}

# Allow the compliance scanner Lambda to invoke the notifier directly
resource "aws_lambda_permission" "allow_scanner_invoke" {
  statement_id  = "AllowScannerInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.notifier.function_name
  principal     = "lambda.amazonaws.com"
  source_arn    = "arn:aws:lambda:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:function:${var.compliance_scanner_function_name}"
}

# =============================================================================
# Self-monitoring — alarms on this module's own functions (optional)
# =============================================================================
# Created only when alarm_sns_topic_arn is set.

resource "aws_cloudwatch_metric_alarm" "notifier_errors" {
  count = var.alarm_sns_topic_arn != "" ? 1 : 0

  alarm_name          = "${aws_lambda_function.notifier.function_name}-errors"
  alarm_description   = "The enforcement notifier function ended with an error or timed out."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.notifier.function_name
  }

  alarm_actions = [var.alarm_sns_topic_arn]
  ok_actions    = [var.alarm_sns_topic_arn]
  tags          = var.tags
}

resource "aws_cloudwatch_metric_alarm" "executor_errors" {
  count = var.alarm_sns_topic_arn != "" && var.executor_armed ? 1 : 0

  alarm_name          = "${aws_lambda_function.executor[0].function_name}-errors"
  alarm_description   = "The enforcement executor function ended with an error or timed out."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.executor[0].function_name
  }

  alarm_actions = [var.alarm_sns_topic_arn]
  ok_actions    = [var.alarm_sns_topic_arn]
  tags          = var.tags
}
