# Stack Reaper Module
#
# Planned IaC stack teardown at expiry. Separate from hunterkiller (reactive
# compliance enforcement) — this is lifecycle management for temporary stacks.
#
# Two Lambdas:
#   1. Reconciler (always deployed): reads each IaC registry entry and the
#      tags of its live resources, creates/updates/deletes EventBridge
#      Scheduler entries per stack.
#   2. Reaper (conditional on var.armed): fires at scheduled time, performs
#      JIT precheck, dispatches teardown via GitHub workflow_dispatch.
#
# Safety:
#   - armed = false by default (no reaper Lambda or dispatch permissions)
#   - dry_run = true by default (teardowns logged but not dispatched)
#   - Only a registry entry with lifecycle temporary or experiment and an
#     expires_at is ever scheduled; resource tags can only block or delay it
#     (a resource without ExpiresAt, with Lifecycle persistent, or with an
#     expiry that disagrees blocks; a later expiry delays)
#   - JIT precheck: reaper decides again from the entry and live tags before
#     dispatch
#   - All teardowns via GitHub workflow_dispatch (never direct AWS destroy APIs)

locals {
  name_prefix    = "${var.project}-${var.environment}"
  table_name     = "${local.name_prefix}-stack-reaper-state"
  schedule_group = "${local.name_prefix}-stack-reaper"

  # compliance_schema.py, compliance_contract.py and iac_registry.py are copied
  # in from ../shared at build time; hash the sources, not the copies, so the
  # hash is the same before and after a build.
  shared_schema_path   = "${path.module}/../shared/compliance_schema.py"
  shared_contract_path = "${path.module}/../shared/compliance_contract.py"
  shared_registry_path = "${path.module}/../shared/iac_registry.py"
  lambda_source_hash = base64sha256(join("", concat(
    [
      for f in sort(fileset("${path.module}/lambda", "**"))
      : filesha256("${path.module}/lambda/${f}")
      if length(regexall("__pycache__|\\.pyc$|\\.pytest_cache|^(compliance_schema|compliance_contract|iac_registry)\\.py$", f)) == 0
    ],
    [filesha256(local.shared_schema_path)],
    [filesha256(local.shared_contract_path)],
    [filesha256(local.shared_registry_path)],
  )))
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# =============================================================================
# DynamoDB — Stack Reaper State
# =============================================================================

resource "aws_dynamodb_table" "state" {
  name         = local.table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "stack_id"

  attribute {
    name = "stack_id"
    type = "S"
  }

  ttl {
    attribute_name = "ttl_epoch"
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
# Lambda Zip — Shared by both reconciler and reaper
# =============================================================================

resource "null_resource" "lambda_zip" {
  triggers = {
    source_hash = local.lambda_source_hash
  }

  provisioner "local-exec" {
    command = "cp '${path.module}/../shared/compliance_schema.py' '${path.module}/lambda/compliance_schema.py' && cp '${path.module}/../shared/compliance_contract.py' '${path.module}/lambda/compliance_contract.py' && cp '${path.module}/../shared/iac_registry.py' '${path.module}/lambda/iac_registry.py' && cd '${path.module}/lambda' && rm -f ../lambda.zip && zip -qr ../lambda.zip . -x '*__pycache__*' '*.pyc' '.pytest_cache/*'"
  }
}

# =============================================================================
# EventBridge Scheduler Group — All reaper schedules live here
# =============================================================================

resource "aws_scheduler_schedule_group" "reaper" {
  name = local.schedule_group

  tags = merge(var.tags, {
    Name = local.schedule_group
  })
}

# =============================================================================
# IAM — Reconciler Role (always deployed, non-destructive)
# =============================================================================

data "aws_iam_policy_document" "reconciler_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "reconciler" {
  name               = "${local.name_prefix}-stack-reaper-reconciler"
  assume_role_policy = data.aws_iam_policy_document.reconciler_assume_role.json

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-stack-reaper-reconciler"
  })
}

data "aws_iam_policy_document" "reconciler_permissions" {
  # CloudWatch Logs
  statement {
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents"
    ]
    resources = ["arn:aws:logs:*:*:*"]
  }

  # DynamoDB — state CRUD
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

  # SSM — read IaC registry
  statement {
    actions   = ["ssm:GetParametersByPath"]
    resources = ["arn:aws:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter${var.iac_registry_prefix}/*"]
  }

  # Resource Groups Tagging — read ExpiresAt tags from resources
  statement {
    actions   = ["tag:GetResources"]
    resources = ["*"]
  }

  # EventBridge Scheduler — create/update/delete schedules (scoped to group)
  statement {
    actions = [
      "scheduler:CreateSchedule",
      "scheduler:UpdateSchedule",
      "scheduler:DeleteSchedule",
      "scheduler:GetSchedule"
    ]
    resources = [
      "arn:aws:scheduler:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:schedule/${local.schedule_group}/*"
    ]
  }

  # EventBridge Scheduler — list schedules (requires broader resource scope)
  statement {
    actions   = ["scheduler:ListSchedules"]
    resources = ["*"]
  }

  # SSM — write job execution status for dashboard visibility
  statement {
    actions   = ["ssm:PutParameter"]
    resources = ["arn:aws:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter/${var.project}/${var.environment}/job-status/*"]
  }

  # Pass scheduler execution role to EventBridge Scheduler
  statement {
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.scheduler_execution.arn]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "reconciler_permissions" {
  name   = "${local.name_prefix}-stack-reaper-reconciler-permissions"
  role   = aws_iam_role.reconciler.id
  policy = data.aws_iam_policy_document.reconciler_permissions.json
}

# =============================================================================
# IAM — Scheduler Execution Role (EventBridge Scheduler → Reaper Lambda)
# =============================================================================

data "aws_iam_policy_document" "scheduler_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scheduler_execution" {
  name               = "${local.name_prefix}-stack-reaper-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume_role.json

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-stack-reaper-scheduler"
  })
}

data "aws_iam_policy_document" "scheduler_permissions" {
  # Invoke the reaper Lambda (or a placeholder ARN when not armed)
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = var.armed ? [aws_lambda_function.reaper[0].arn] : ["arn:aws:lambda:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:function:${local.name_prefix}-stack-reaper"]
  }
}

resource "aws_iam_role_policy" "scheduler_permissions" {
  name   = "${local.name_prefix}-stack-reaper-scheduler-permissions"
  role   = aws_iam_role.scheduler_execution.id
  policy = data.aws_iam_policy_document.scheduler_permissions.json
}

# =============================================================================
# IAM — Reaper Role (conditional, only when armed=true)
# =============================================================================

data "aws_iam_policy_document" "reaper_assume_role" {
  count = var.armed ? 1 : 0

  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "reaper" {
  count = var.armed ? 1 : 0

  name               = "${local.name_prefix}-stack-reaper"
  assume_role_policy = data.aws_iam_policy_document.reaper_assume_role[0].json

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-stack-reaper"
  })
}

data "aws_iam_policy_document" "reaper_permissions" {
  count = var.armed ? 1 : 0

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
      "dynamodb:PutItem",
      "dynamodb:Query",
      "dynamodb:Scan"
    ]
    resources = [aws_dynamodb_table.state.arn]
  }

  # SSM — read IaC registry (for repo_url lookup)
  statement {
    actions   = ["ssm:GetParametersByPath"]
    resources = ["arn:aws:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter${var.iac_registry_prefix}/*"]
  }

  # Resource Groups Tagging — JIT precheck of ExpiresAt tags
  statement {
    actions   = ["tag:GetResources"]
    resources = ["*"]
  }

  # Secrets Manager — GitHub App credentials for workflow_dispatch
  dynamic "statement" {
    for_each = var.github_app_secret_arn != "" ? [1] : []
    content {
      actions   = ["secretsmanager:GetSecretValue"]
      resources = [var.github_app_secret_arn]
    }
  }

  # SES — send teardown notification emails
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

  # EventBridge Scheduler — delete one-shot schedules after successful dispatch
  statement {
    actions = [
      "scheduler:DeleteSchedule",
      "scheduler:GetSchedule"
    ]
    resources = [
      "arn:aws:scheduler:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:schedule/${local.schedule_group}/*"
    ]
  }
}

resource "aws_iam_role_policy" "reaper_permissions" {
  count = var.armed ? 1 : 0

  name   = "${local.name_prefix}-stack-reaper-permissions"
  role   = aws_iam_role.reaper[0].id
  policy = data.aws_iam_policy_document.reaper_permissions[0].json
}

# =============================================================================
# Lambda — Reconciler Function (always deployed)
# =============================================================================

resource "aws_lambda_function" "reconciler" {
  function_name = "${local.name_prefix}-stack-reaper-reconciler"
  role          = aws_iam_role.reconciler.arn
  handler       = "reconciler_index.handler"
  runtime       = "python3.12"
  timeout       = 900
  memory_size   = 256

  depends_on       = [null_resource.lambda_zip]
  filename         = "${path.module}/lambda.zip"
  source_code_hash = local.lambda_source_hash

  tracing_config {
    mode = "PassThrough"
  }

  environment {
    variables = {
      PROJECT               = var.project
      ENVIRONMENT           = var.environment
      IAC_REGISTRY_PREFIX   = var.iac_registry_prefix
      REPOSITORY_URL        = var.repository_url
      STATE_TABLE           = aws_dynamodb_table.state.name
      SCHEDULE_GROUP        = local.schedule_group
      SCHEDULER_ROLE_ARN    = aws_iam_role.scheduler_execution.arn
      REAPER_FUNCTION_ARN   = var.armed ? aws_lambda_function.reaper[0].arn : ""
      DRY_RUN               = tostring(var.dry_run)
      STATE_TTL_DAYS        = tostring(var.state_ttl_days)
      JOB_STATUS_SSM_PREFIX = "/${var.project}/${var.environment}/job-status"
    }
  }

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-stack-reaper-reconciler"
  })
}

resource "aws_cloudwatch_log_group" "reconciler" {
  name              = "/aws/lambda/${aws_lambda_function.reconciler.function_name}"
  retention_in_days = 30

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-stack-reaper-reconciler-logs"
  })
}

# EventBridge Rule — periodic reconciler invocation
resource "aws_cloudwatch_event_rule" "reconciler_schedule" {
  name                = "${local.name_prefix}-stack-reaper-reconciler"
  description         = "Periodic scan of IaC registry for expiring stacks"
  schedule_expression = var.reconciler_schedule

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-stack-reaper-reconciler-schedule"
  })
}

resource "aws_cloudwatch_event_target" "reconciler_schedule" {
  rule = aws_cloudwatch_event_rule.reconciler_schedule.name
  arn  = aws_lambda_function.reconciler.arn
}

resource "aws_lambda_permission" "allow_eventbridge_reconciler" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.reconciler.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.reconciler_schedule.arn
}

# =============================================================================
# Lambda — Reaper Function (conditional, only when armed=true)
# =============================================================================

resource "aws_lambda_function" "reaper" {
  count = var.armed ? 1 : 0

  function_name = "${local.name_prefix}-stack-reaper"
  role          = aws_iam_role.reaper[0].arn
  handler       = "reaper_index.handler"
  runtime       = "python3.12"
  timeout       = 900
  memory_size   = 256

  depends_on       = [null_resource.lambda_zip]
  filename         = "${path.module}/lambda.zip"
  source_code_hash = local.lambda_source_hash

  tracing_config {
    mode = "PassThrough"
  }

  environment {
    variables = {
      PROJECT               = var.project
      ENVIRONMENT           = var.environment
      IAC_REGISTRY_PREFIX   = var.iac_registry_prefix
      STATE_TABLE           = aws_dynamodb_table.state.name
      SCHEDULE_GROUP        = local.schedule_group
      DRY_RUN               = tostring(var.dry_run)
      GITHUB_APP_SECRET_ARN = var.github_app_secret_arn
      GITHUB_ORG            = var.github_org
      SES_SENDER_EMAIL      = var.ses_sender_email
      SES_ADMIN_EMAILS      = join(",", var.ses_admin_emails)
      STATE_TTL_DAYS        = tostring(var.state_ttl_days)
    }
  }

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-stack-reaper"
  })
}

resource "aws_cloudwatch_log_group" "reaper" {
  count = var.armed ? 1 : 0

  name              = "/aws/lambda/${aws_lambda_function.reaper[0].function_name}"
  retention_in_days = 30

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-stack-reaper-logs"
  })
}

# Allow EventBridge Scheduler to invoke the reaper Lambda
resource "aws_lambda_permission" "allow_scheduler_reaper" {
  count = var.armed ? 1 : 0

  statement_id   = "AllowSchedulerInvoke"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.reaper[0].function_name
  principal      = "scheduler.amazonaws.com"
  source_account = data.aws_caller_identity.current.account_id
}

# =============================================================================
# Self-monitoring — alarms on this module's own functions (optional)
# =============================================================================
# Created only when alarm_sns_topic_arn is set.

resource "aws_cloudwatch_metric_alarm" "reconciler_errors" {
  count = var.alarm_sns_topic_arn != "" ? 1 : 0

  alarm_name          = "${aws_lambda_function.reconciler.function_name}-errors"
  alarm_description   = "The expiry schedule reconciler function ended with an error or timed out."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.reconciler.function_name
  }

  alarm_actions = [var.alarm_sns_topic_arn]
  ok_actions    = [var.alarm_sns_topic_arn]
  tags          = var.tags
}

resource "aws_cloudwatch_metric_alarm" "reaper_errors" {
  count = var.alarm_sns_topic_arn != "" && var.armed ? 1 : 0

  alarm_name          = "${aws_lambda_function.reaper[0].function_name}-errors"
  alarm_description   = "The expiry teardown dispatcher function ended with an error or timed out."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.reaper[0].function_name
  }

  alarm_actions = [var.alarm_sns_topic_arn]
  ok_actions    = [var.alarm_sns_topic_arn]
  tags          = var.tags
}
