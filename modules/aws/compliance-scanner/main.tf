# Compliance Scanner Module
#
# Account-wide compliance scanner that discovers all AWS resources,
# checks tag compliance, detects Terraform state drift (click-ops),
# and flags lifecycle/schedule violations. Report-only — no destructive actions.

locals {
  # Namespace of the scan outcome metrics the function writes.
  scan_metric_namespace = "ComplianceScanner"

  name_prefix = "${var.project}-${var.environment}"
  bucket_name = "${local.name_prefix}-compliance-reports"

  # Hash only real source files — excludes __pycache__/.pyc so the hash is
  # stable regardless of whether someone ran tests or imports locally.
  shared_schema_path   = "${path.module}/../shared/compliance_schema.py"
  shared_contract_path = "${path.module}/../shared/compliance_contract.py"
  shared_delta_path    = "${path.module}/../shared/delta.py"
  shared_registry_path = "${path.module}/../shared/iac_registry.py"
  lambda_source_hash = base64sha256(join("", concat(
    [
      for f in sort(fileset("${path.module}/lambda", "**"))
      : filesha256("${path.module}/lambda/${f}")
      if length(regexall("__pycache__|\\.pyc$|\\.pytest_cache", f)) == 0
    ],
    [filesha256(local.shared_schema_path)],
    [filesha256(local.shared_contract_path)],
    [filesha256(local.shared_delta_path)],
    [filesha256(local.shared_registry_path)],
  )))
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# =============================================================================
# S3 Bucket for Compliance Reports
# =============================================================================

resource "aws_s3_bucket" "reports" {
  bucket = local.bucket_name

  tags = merge(var.tags, {
    Name = local.bucket_name
  })
}

resource "aws_s3_bucket_versioning" "reports" {
  bucket = aws_s3_bucket.reports.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "reports" {
  bucket = aws_s3_bucket.reports.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "reports" {
  bucket = aws_s3_bucket.reports.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "reports" {
  bucket = aws_s3_bucket.reports.id

  rule {
    id     = "expire-old-reports"
    status = "Enabled"
    filter {}

    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }

    expiration {
      days = var.report_retention_days
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# Requests to the reports bucket must use TLS.
data "aws_iam_policy_document" "reports_tls_only" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.reports.arn,
      "${aws_s3_bucket.reports.arn}/*",
    ]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "reports" {
  count  = var.manage_reports_bucket_policy ? 1 : 0
  bucket = aws_s3_bucket.reports.id
  policy = data.aws_iam_policy_document.reports_tls_only.json

  depends_on = [aws_s3_bucket_public_access_block.reports]
}

# =============================================================================
# DynamoDB Table for Report Data
# =============================================================================

resource "aws_dynamodb_table" "report_data" {
  name         = "${local.name_prefix}-compliance-report-data"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
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
    Name = "${local.name_prefix}-compliance-report-data"
  })
}

# =============================================================================
# IAM Role for Lambda
# =============================================================================

data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${local.name_prefix}-compliance-scanner"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-compliance-scanner"
  })
}

# Read-only discovery — see everything in the account
resource "aws_iam_role_policy_attachment" "read_only" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

# Write permissions — only reports bucket, TF state read, SNS publish, logs
data "aws_iam_policy_document" "lambda_write" {
  # CloudWatch Logs
  statement {
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents"
    ]
    resources = ["arn:aws:logs:*:*:*"]
  }

  # Write compliance reports to S3
  statement {
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.reports.arn}/*"]
  }

  # Read Terraform state for drift detection (this repo + any registered cross-repo states)
  statement {
    actions = ["s3:GetObject"]
    resources = concat(
      ["arn:aws:s3:::${var.tf_state_bucket}/${var.tf_state_key}"],
      [for b in var.iac_registry_state_buckets : "arn:aws:s3:::${b}/*"]
    )
  }

  # Send HTML email via SES
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

  # Invoke hunterkiller Lambda in warn mode after scan (if configured)
  dynamic "statement" {
    for_each = var.hunterkiller_function_name != "" ? [1] : []
    content {
      actions   = ["lambda:InvokeFunction"]
      resources = ["arn:aws:lambda:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:function:${var.hunterkiller_function_name}"]
    }
  }

  # Read dashboard overrides table (false positives / exemptions)
  dynamic "statement" {
    for_each = var.overrides_table_arn != "" ? [1] : []
    content {
      actions   = ["dynamodb:Scan", "dynamodb:GetItem"]
      resources = [var.overrides_table_arn]
    }
  }

  # Write compliance report data to DynamoDB
  statement {
    actions = [
      "dynamodb:PutItem",
      "dynamodb:BatchWriteItem",
      "dynamodb:GetItem",
      "dynamodb:UpdateItem",
    ]
    resources = [aws_dynamodb_table.report_data.arn]
  }
}

resource "aws_iam_role_policy" "lambda_write" {
  name   = "${local.name_prefix}-compliance-scanner-write"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda_write.json
}

# =============================================================================
# Lambda Function
# =============================================================================

# Build a clean zip excluding __pycache__/.pyc files. The archive_file data
# source's excludes don't reliably match nested __pycache__ directories,
# which causes stale .pyc files to mask real source changes. Using
# null_resource + zip eliminates the manual "rm -rf __pycache__" deploy step.
resource "null_resource" "lambda_zip" {
  triggers = {
    source_hash = local.lambda_source_hash
  }

  provisioner "local-exec" {
    command = "cp '${path.module}/../shared/compliance_schema.py' '${path.module}/lambda/compliance_schema.py' && cp '${path.module}/../shared/compliance_contract.py' '${path.module}/lambda/compliance_contract.py' && cp '${path.module}/../shared/delta.py' '${path.module}/lambda/delta.py' && cp '${path.module}/../shared/iac_registry.py' '${path.module}/lambda/iac_registry.py' && cd '${path.module}/lambda' && rm -f ../lambda.zip && zip -qr ../lambda.zip . -x '*__pycache__*' '*.pyc' '.pytest_cache/*'"
  }
}

resource "aws_lambda_function" "compliance_scanner" {
  function_name = "${local.name_prefix}-compliance-scanner"
  role          = aws_iam_role.lambda.arn
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
      PROJECT                    = var.project
      ENVIRONMENT                = var.environment
      REPORTS_BUCKET             = aws_s3_bucket.reports.id
      SCAN_METRIC_NAMESPACE      = local.scan_metric_namespace
      TF_STATE_BUCKET            = var.tf_state_bucket
      TF_STATE_KEY               = var.tf_state_key
      REQUIRED_TAGS              = var.required_tags == null ? "" : join(",", var.required_tags)
      EXCLUSION_ARNS             = join(",", var.exclusion_arns)
      EXCLUSION_PATTERNS         = join(",", var.exclusion_patterns)
      SES_SENDER_EMAIL           = var.ses_sender_email
      SES_RECIPIENT_EMAILS       = join(",", var.ses_recipient_emails)
      HUNTERKILLER_FUNCTION_NAME = var.hunterkiller_function_name
      OVERRIDES_TABLE            = var.overrides_table_name
      PROTECTED_RESOURCES_SSM    = var.protected_resources_ssm_name
      CLI_PREFIX                 = var.cli_prefix
      REPOSITORY_URL             = var.repository_url
      PURPOSE_HINTS              = jsonencode(var.purpose_hints)
      SCANNER_SOURCE_URL         = var.scanner_source_url
      STANDARDS_URL              = var.standards_url
      REPORT_TABLE               = aws_dynamodb_table.report_data.name
      IAC_REGISTRY_PREFIX        = var.iac_registry_prefix != "" ? var.iac_registry_prefix : "/${var.project}/${var.environment}/iac-registry"
    }
  }

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-compliance-scanner"
  })
}

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${aws_lambda_function.compliance_scanner.function_name}"
  retention_in_days = 30

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-compliance-scanner-logs"
  })
}

# =============================================================================
# EventBridge - Nightly Schedule
# =============================================================================

resource "aws_cloudwatch_event_rule" "nightly_scan" {
  name                = "${local.name_prefix}-nightly-compliance-scan"
  description         = "Nightly compliance scan of entire AWS account"
  schedule_expression = var.schedule_expression

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-nightly-compliance-scan"
  })
}

resource "aws_cloudwatch_event_target" "lambda" {
  rule      = aws_cloudwatch_event_rule.nightly_scan.name
  target_id = "compliance-scanner"
  arn       = aws_lambda_function.compliance_scanner.arn
}

resource "aws_lambda_permission" "eventbridge" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.compliance_scanner.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.nightly_scan.arn
}

# =============================================================================
# Self-monitoring — alarms on this module's own functions (optional)
# =============================================================================
# Created only when alarm_sns_topic_arn is set.

resource "aws_cloudwatch_metric_alarm" "scanner_errors" {
  count = var.alarm_sns_topic_arn != "" ? 1 : 0

  alarm_name          = "${aws_lambda_function.compliance_scanner.function_name}-errors"
  alarm_description   = "The compliance scanner function ended with an error or timed out."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.compliance_scanner.function_name
  }

  alarm_actions = [var.alarm_sns_topic_arn]
  ok_actions    = [var.alarm_sns_topic_arn]
  tags          = var.tags
}

# The scanner catches its own errors so that it can still write a report, so
# the Lambda error count alone does not show a failed scan. The function
# writes ScanSucceeded / ScanFailed (lambda/index.py, _emit_scan_metric).
resource "aws_cloudwatch_metric_alarm" "scan_failed" {
  count = var.alarm_sns_topic_arn != "" ? 1 : 0

  alarm_name          = "${aws_lambda_function.compliance_scanner.function_name}-scan-failed"
  alarm_description   = "A compliance scan stopped part-way or could not store its report."
  namespace           = local.scan_metric_namespace
  metric_name         = "ScanFailed"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.compliance_scanner.function_name
  }

  alarm_actions = [var.alarm_sns_topic_arn]
  ok_actions    = [var.alarm_sns_topic_arn]
  tags          = var.tags
}

# No successful scan in scan_stale_alarm_hours: every hourly period in the
# window has no success. A period with no data counts as no success, so a
# scanner that is never invoked raises the alarm too.
resource "aws_cloudwatch_metric_alarm" "scan_stale" {
  count = var.alarm_sns_topic_arn != "" ? 1 : 0

  alarm_name          = "${aws_lambda_function.compliance_scanner.function_name}-no-successful-scan"
  alarm_description   = "No successful compliance scan in the last ${var.scan_stale_alarm_hours} hours. Until a scan succeeds, an account with no findings and a scanner that has stopped look the same."
  namespace           = local.scan_metric_namespace
  metric_name         = "ScanSucceeded"
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = var.scan_stale_alarm_hours
  datapoints_to_alarm = var.scan_stale_alarm_hours
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"

  dimensions = {
    FunctionName = aws_lambda_function.compliance_scanner.function_name
  }

  alarm_actions = [var.alarm_sns_topic_arn]
  ok_actions    = [var.alarm_sns_topic_arn]
  tags          = var.tags
}
