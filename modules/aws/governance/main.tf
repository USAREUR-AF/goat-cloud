# Governance Module
#
# Implements cost management, lifecycle enforcement, and alerting

locals {
  name_prefix = "${var.project}-${var.environment}"

  lambda_source_hash = base64sha256(join("", [
    for f in sort(fileset("${path.module}/lambda", "**"))
    : filesha256("${path.module}/lambda/${f}")
    if length(regexall("__pycache__|\\.pyc$|\\.pytest_cache", f)) == 0
  ]))
}

# =============================================================================
# SNS Topic for Alerts
# =============================================================================

resource "aws_sns_topic" "governance_alerts" {
  name = "${local.name_prefix}-governance-alerts"

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-governance-alerts"
  })
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.governance_alerts.arn
  protocol  = "email"
  endpoint  = var.owner_email
}

# =============================================================================
# AWS Budgets for Cost Alerts
# =============================================================================

resource "aws_budgets_budget" "monthly" {
  name         = "${local.name_prefix}-monthly-budget"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_limit)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "TagKeyValue"
    values = ["Project$${var.project}"]
  }

  dynamic "notification" {
    for_each = var.cost_alert_thresholds
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "FORECASTED"
      subscriber_email_addresses = [var.owner_email]
    }
  }

  dynamic "notification" {
    for_each = var.cost_alert_thresholds
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = [var.owner_email]
    }
  }
}

# =============================================================================
# Lambda for Lifecycle Management
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

resource "aws_iam_role" "lifecycle_lambda" {
  name               = "${local.name_prefix}-lifecycle-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-lifecycle-lambda"
  })
}

data "aws_iam_policy_document" "lifecycle_lambda" {
  # CloudWatch Logs
  statement {
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents"
    ]
    resources = ["arn:aws:logs:*:*:*"]
  }

  # Resource tagging and discovery
  statement {
    actions = [
      "tag:GetResources",
      "tag:GetTagKeys",
      "tag:GetTagValues"
    ]
    resources = ["*"]
  }

  # Cost Explorer
  statement {
    actions = [
      "ce:GetCostAndUsage",
      "ce:GetCostForecast"
    ]
    resources = ["*"]
  }

  # SNS for alerts
  statement {
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.governance_alerts.arn]
  }
}

resource "aws_iam_role_policy" "lifecycle_lambda" {
  name   = "${local.name_prefix}-lifecycle-lambda-policy"
  role   = aws_iam_role.lifecycle_lambda.id
  policy = data.aws_iam_policy_document.lifecycle_lambda.json
}

resource "aws_lambda_function" "lifecycle_manager" {
  function_name = "${local.name_prefix}-lifecycle-manager"
  role          = aws_iam_role.lifecycle_lambda.arn
  handler       = "index.handler"
  runtime       = "python3.12"
  timeout       = 300
  memory_size   = 256

  depends_on       = [null_resource.lambda_zip]
  filename         = "${path.module}/lambda.zip"
  source_code_hash = local.lambda_source_hash

  tracing_config {
    mode = "PassThrough"
  }

  environment {
    variables = {
      PROJECT          = var.project
      ENVIRONMENT      = var.environment
      SNS_TOPIC_ARN    = aws_sns_topic.governance_alerts.arn
      OWNER_EMAIL      = var.owner_email
      WARNING_DAYS     = tostring(var.lifecycle_warning_days)
      TERMINATION_DAYS = tostring(var.lifecycle_termination_days)
      MONTHLY_BUDGET   = tostring(var.monthly_budget_limit)
    }
  }

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-lifecycle-manager"
  })
}

# Build a clean zip excluding __pycache__/.pyc files.
resource "null_resource" "lambda_zip" {
  triggers = {
    source_hash = local.lambda_source_hash
  }

  provisioner "local-exec" {
    command = "cd '${path.module}/lambda' && rm -f ../lambda.zip && zip -qr ../lambda.zip . -x '*__pycache__*' '*.pyc' '.pytest_cache/*'"
  }
}

resource "aws_cloudwatch_log_group" "lifecycle_lambda" {
  name              = "/aws/lambda/${aws_lambda_function.lifecycle_manager.function_name}"
  retention_in_days = 30

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-lifecycle-lambda-logs"
  })
}

# =============================================================================
# EventBridge for Weekly Scheduling
# =============================================================================

resource "aws_cloudwatch_event_rule" "weekly_governance" {
  name                = "${local.name_prefix}-weekly-governance"
  description         = "Weekly governance check and cost report"
  schedule_expression = "cron(0 9 ? * MON *)" # Every Monday at 9 AM UTC

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-weekly-governance"
  })
}

resource "aws_cloudwatch_event_target" "lifecycle_lambda" {
  rule      = aws_cloudwatch_event_rule.weekly_governance.name
  target_id = "lifecycle-manager"
  arn       = aws_lambda_function.lifecycle_manager.arn
}

resource "aws_lambda_permission" "eventbridge" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.lifecycle_manager.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.weekly_governance.arn
}

# =============================================================================
# Self-monitoring — alarms on this module's own functions (optional)
# =============================================================================
# Created only when alarm_sns_topic_arn is set.

resource "aws_cloudwatch_metric_alarm" "lifecycle_manager_errors" {
  count = var.alarm_sns_topic_arn != "" ? 1 : 0

  alarm_name          = "${aws_lambda_function.lifecycle_manager.function_name}-errors"
  alarm_description   = "The governance report function ended with an error or timed out."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.lifecycle_manager.function_name
  }

  alarm_actions = [var.alarm_sns_topic_arn]
  ok_actions    = [var.alarm_sns_topic_arn]
  tags          = var.tags
}
