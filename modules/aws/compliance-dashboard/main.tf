# =============================================================================
# Compliance Dashboard — Lambda web service behind an ALB or a Function URL
# =============================================================================
#
# Interactive dashboard for compliance management:
#   - View non-compliant resources with context
#   - Mark false positives with notes
#   - Abort pending 24-hour destroys (escape hatch)
#   - Extend grace periods
#   - Trigger early destruction
#
# Sign-in happens in front of the Lambda: the ALB's authenticate-oidc action,
# a sign-in proxy, or an IAM-authorized Function URL.

locals {
  name_prefix = "${var.project}-${var.environment}"
  table_name  = "${local.name_prefix}-compliance-overrides"

  shared_schema_path   = "${path.module}/../shared/compliance_schema.py"
  shared_contract_path = "${path.module}/../shared/compliance_contract.py"
  shared_delta_path    = "${path.module}/../shared/delta.py"
  lambda_source_hash = base64sha256(join("", concat(
    [
      for f in sort(fileset("${path.module}/lambda", "**"))
      : filesha256("${path.module}/lambda/${f}")
      if length(regexall("__pycache__|\\.pyc$|\\.pytest_cache|^site/", f)) == 0
    ],
    [filesha256(local.shared_schema_path)],
    [filesha256(local.shared_contract_path)],
    [filesha256(local.shared_delta_path)],
    # The deployment's own content, copied into the package as site/ at build time
    var.site_config_dir != "" ? [
      for f in sort(fileset(var.site_config_dir, "**")) : filesha256("${var.site_config_dir}/${f}")
    ] : [],
  )))

  # ARN of the load balancer that owns the listener: the only signer the
  # Lambda accepts for an authenticate-oidc identity token. Set only when this
  # module creates the authenticate-oidc rule; the token must also name that
  # rule's client id and issuer.
  alb_oidc_rule = var.enable_alb && var.create_public_listener_rule && var.alb_oidc_config != null
  alb_arn       = local.alb_oidc_rule && var.alb_listener_arn != null ? replace(replace(var.alb_listener_arn, ":listener/", ":loadbalancer/"), "/\\/[^\\/]+$/", "") : ""
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# =============================================================================
# DynamoDB — Override / False Positive Tracking
# =============================================================================

resource "aws_dynamodb_table" "overrides" {
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
# Lambda Function
# =============================================================================

resource "null_resource" "lambda_zip" {
  triggers = {
    source_hash = local.lambda_source_hash
  }

  provisioner "local-exec" {
    command = "cp '${path.module}/../shared/compliance_schema.py' '${path.module}/lambda/compliance_schema.py' && cp '${path.module}/../shared/compliance_contract.py' '${path.module}/lambda/compliance_contract.py' && cp '${path.module}/../shared/delta.py' '${path.module}/lambda/delta.py' && rm -rf '${path.module}/lambda/site' && ${var.site_config_dir != "" ? "cp -R '${var.site_config_dir}' '${path.module}/lambda/site'" : "true"} && cd '${path.module}/lambda' && rm -f ../lambda.zip && zip -qr ../lambda.zip . -x '*__pycache__*' '*.pyc' '.pytest_cache/*'"
  }
}

resource "aws_lambda_function" "dashboard" {
  function_name = "${local.name_prefix}-compliance-dashboard"
  handler       = "index.handler"
  runtime       = "python3.12"
  role          = aws_iam_role.lambda.arn
  timeout       = 30
  memory_size   = 256
  publish       = true

  depends_on       = [null_resource.lambda_zip]
  filename         = "${path.module}/lambda.zip"
  source_code_hash = local.lambda_source_hash

  environment {
    variables = {
      PROJECT                   = var.project
      ENVIRONMENT               = var.environment
      REPORTS_BUCKET            = var.compliance_reports_bucket
      HK_STATE_TABLE            = var.hunterkiller_state_table_name
      OVERRIDES_TABLE           = aws_dynamodb_table.overrides.name
      SCANNER_FUNCTION_NAME     = var.scanner_function_name
      HK_FUNCTION_NAME          = var.hunterkiller_function_name
      HK_EXECUTOR_FUNCTION_NAME = var.hunterkiller_executor_function_name
      HK_EXECUTOR_ARMED         = tostring(var.hunterkiller_executor_armed)
      ADMIN_EMAILS              = join(",", var.admin_emails)
      ENABLE_VIEWER_GATE        = tostring(var.enable_viewer_gate)
      VIEWER_EMAILS             = join(",", var.viewer_emails)
      INVITE_HMAC_SECRET_ARN    = var.enable_viewer_gate ? aws_secretsmanager_secret.invite_hmac[0].arn : ""
      SES_SENDER_EMAIL          = var.ses_sender_email
      DASHBOARD_URL             = var.dashboard_url
      TF_STATE_BUCKET           = var.tf_state_bucket
      TF_STATE_KEY              = var.tf_state_key
      IMAGE_VERSIONS_SSM        = var.image_versions_ssm_name
      CLI_PREFIX                = var.cli_prefix
      REPOSITORY_URL            = var.repository_url
      PROTECTED_RESOURCES_SSM   = var.protected_resources_ssm_name
      DOMAIN_NAME               = var.domain_name
      MODULE_FILE_MAP           = jsonencode(var.module_file_map)
      REGION                    = var.region
      ACCOUNT_ID                = data.aws_caller_identity.current.account_id
      REPORT_TABLE              = var.compliance_report_table_name
      REQUIRED_TAGS             = var.required_tags == null ? "" : join(",", var.required_tags)

      # Which fronts may assert a user identity (lambda/auth.py). Permission
      # to invoke the Function URL is not one of them: only the principals
      # named in function_url_identity_proxy_principal_arns. Shared secrets
      # are passed as SHA-256 digests, never as values.
      ALB_ARN                      = local.alb_arn
      ALB_OIDC_CLIENT_ID           = local.alb_oidc_rule ? var.alb_oidc_config.client_id : ""
      ALB_OIDC_ISSUER              = local.alb_oidc_rule ? var.alb_oidc_config.issuer : ""
      TRUSTED_PROXY_PRINCIPAL_ARNS = var.enable_function_url && var.function_url_authorization_type == "AWS_IAM" ? join(",", var.function_url_identity_proxy_principal_arns) : ""
      PROXY_AUTH_HEADER_NAME       = nonsensitive(var.enable_alb && var.oauth2_proxy_bypass_header_value != null ? var.oauth2_proxy_bypass_header_name : "")
      PROXY_AUTH_HEADER_SHA256     = nonsensitive(var.enable_alb && var.oauth2_proxy_bypass_header_value != null ? sha256(var.oauth2_proxy_bypass_header_value) : "")
      TRUSTED_ORIGINS              = join(",", var.trusted_origins)
      SCAN_STALE_HOURS             = tostring(var.scan_stale_hours)
    }
  }

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-compliance-dashboard"
  })
}

# =============================================================================
# Provisioned Concurrency (eliminates cold starts)
# =============================================================================

resource "aws_lambda_alias" "live" {
  count            = var.provisioned_concurrency > 0 ? 1 : 0
  name             = "live"
  function_name    = aws_lambda_function.dashboard.function_name
  function_version = aws_lambda_function.dashboard.version
}

resource "aws_lambda_provisioned_concurrency_config" "dashboard" {
  count                             = var.provisioned_concurrency > 0 ? 1 : 0
  function_name                     = aws_lambda_function.dashboard.function_name
  qualifier                         = aws_lambda_alias.live[0].name
  provisioned_concurrent_executions = var.provisioned_concurrency
}

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${aws_lambda_function.dashboard.function_name}"
  retention_in_days = 14
  tags              = var.tags
}

# =============================================================================
# ALB Target Group + Listener Rules (only when enable_alb)
# =============================================================================

resource "aws_lb_target_group" "dashboard" {
  count       = var.enable_alb ? 1 : 0
  name        = "${local.name_prefix}-enforcer"
  target_type = "lambda"
  tags        = var.tags

  lifecycle {
    precondition {
      condition     = var.alb_listener_arn != null && var.host_header != ""
      error_message = "enable_alb = true needs alb_listener_arn and host_header. Set enable_alb = false to serve the dashboard through the Function URL only."
    }
  }
}

resource "aws_lb_target_group_attachment" "dashboard" {
  count            = var.enable_alb ? 1 : 0
  target_group_arn = aws_lb_target_group.dashboard[0].arn
  target_id        = var.provisioned_concurrency > 0 ? aws_lambda_alias.live[0].arn : aws_lambda_function.dashboard.arn
  depends_on       = [aws_lambda_permission.alb]
}

# These three resources were single instances before enable_alb existed.
moved {
  from = aws_lb_target_group.dashboard
  to   = aws_lb_target_group.dashboard[0]
}

moved {
  from = aws_lb_target_group_attachment.dashboard
  to   = aws_lb_target_group_attachment.dashboard[0]
}

moved {
  from = aws_lambda_permission.alb
  to   = aws_lambda_permission.alb[0]
}

# With OIDC authentication (when identity stack is enabled)
resource "aws_lb_listener_rule" "dashboard" {
  count        = var.enable_alb && var.create_public_listener_rule && var.alb_oidc_config != null ? 1 : 0
  listener_arn = var.alb_listener_arn
  priority     = var.listener_rule_priority

  action {
    type  = "authenticate-oidc"
    order = 1

    authenticate_oidc {
      authorization_endpoint = var.alb_oidc_config.authorization_endpoint
      client_id              = var.alb_oidc_config.client_id
      client_secret          = var.alb_oidc_config.client_secret
      issuer                 = var.alb_oidc_config.issuer
      token_endpoint         = var.alb_oidc_config.token_endpoint
      user_info_endpoint     = var.alb_oidc_config.user_info_endpoint
      scope                  = var.alb_oidc_config.scope
      session_timeout        = var.alb_oidc_config.session_timeout

      on_unauthenticated_request = "authenticate"
    }
  }

  action {
    type             = "forward"
    order            = 2
    target_group_arn = aws_lb_target_group.dashboard[0].arn
  }

  condition {
    host_header {
      values = [var.host_header]
    }
  }

  tags = var.tags
}

# Without authentication (when identity stack is disabled)
resource "aws_lb_listener_rule" "dashboard_no_auth" {
  count        = var.enable_alb && var.create_public_listener_rule && var.alb_oidc_config == null ? 1 : 0
  listener_arn = var.alb_listener_arn
  priority     = var.listener_rule_priority

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.dashboard[0].arn
  }

  condition {
    host_header {
      values = [var.host_header]
    }
  }

  tags = var.tags

  lifecycle {
    precondition {
      condition     = var.allow_unauthenticated_public_rule
      error_message = "create_public_listener_rule with alb_oidc_config = null publishes the dashboard to every caller of the listener with no sign-in. Set alb_oidc_config, or set create_public_listener_rule = false and put an identity-aware proxy in front, or set allow_unauthenticated_public_rule = true to accept a read-only dashboard without sign-in."
    }
  }
}

# =============================================================================
# Viewer Gate — HMAC secret for invite links (conditional)
# =============================================================================

resource "random_password" "invite_hmac" {
  count   = var.enable_viewer_gate ? 1 : 0
  length  = 64
  special = false
}

resource "aws_secretsmanager_secret" "invite_hmac" {
  count       = var.enable_viewer_gate ? 1 : 0
  name        = "${local.name_prefix}-dashboard-invite-hmac"
  description = "HMAC key for compliance dashboard invite links"
  tags        = merge(var.tags, { Name = "${local.name_prefix}-dashboard-invite-hmac" })
}

resource "aws_secretsmanager_secret_version" "invite_hmac" {
  count         = var.enable_viewer_gate ? 1 : 0
  secret_id     = aws_secretsmanager_secret.invite_hmac[0].id
  secret_string = random_password.invite_hmac[0].result
}

# =============================================================================
# Sign-in proxy upstream bypass — static header added by the proxy
# =============================================================================
#
# A sign-in proxy in front of the dashboard can add a static secret header to
# every upstream call. This rule matches that exact header and forwards to the
# Lambda without running listener authentication again, so the proxy can call
# back into the hostname it serves. It only matches requests that carry the
# secret header.

resource "aws_lb_listener_rule" "oauth2_proxy_bypass" {
  count        = var.enable_alb && var.oauth2_proxy_bypass_header_value != null ? 1 : 0
  listener_arn = var.alb_listener_arn
  priority     = var.oauth2_proxy_bypass_priority

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.dashboard[0].arn
  }

  condition {
    host_header {
      values = [var.host_header]
    }
  }

  condition {
    http_header {
      http_header_name = var.oauth2_proxy_bypass_header_name
      values           = [var.oauth2_proxy_bypass_header_value]
    }
  }

  tags = var.tags
}

# =============================================================================
# Lambda Permissions
# =============================================================================

resource "aws_lambda_permission" "alb" {
  count         = var.enable_alb ? 1 : 0
  statement_id  = "AllowALBInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.dashboard.function_name
  qualifier     = var.provisioned_concurrency > 0 ? aws_lambda_alias.live[0].name : null
  principal     = "elasticloadbalancing.amazonaws.com"
  source_arn    = aws_lb_target_group.dashboard[0].arn
}

# =============================================================================
# Function URL — default off
# =============================================================================
# Targets the provisioned-concurrency alias when one exists, as the ALB target
# does.

resource "aws_lambda_function_url" "dashboard" {
  count              = var.enable_function_url ? 1 : 0
  function_name      = aws_lambda_function.dashboard.function_name
  qualifier          = var.provisioned_concurrency > 0 ? aws_lambda_alias.live[0].name : null
  authorization_type = var.function_url_authorization_type
}

# Grant each fronting proxy's role the right to invoke the AWS_IAM-gated
# Function URL — one permission per principal, so a single service fans in to
# multiple private networks with per-network, independently-revocable access.
# Empty list / NONE auth → no resource-based permissions (callers rely on
# their own identity policy).
resource "aws_lambda_permission" "function_url" {
  for_each = var.enable_function_url && var.function_url_authorization_type == "AWS_IAM" ? toset(var.function_url_invoke_principal_arns) : toset([])

  statement_id           = "AllowFunctionUrlInvoke-${substr(md5(each.value), 0, 8)}"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.dashboard.function_name
  qualifier              = var.provisioned_concurrency > 0 ? aws_lambda_alias.live[0].name : null
  principal              = each.value
  function_url_auth_type = "AWS_IAM"
}

# =============================================================================
# IAM Role
# =============================================================================

resource "aws_iam_role" "lambda" {
  name = "${local.name_prefix}-compliance-dashboard"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
    }]
  })

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-compliance-dashboard"
  })
}

resource "aws_iam_role_policy" "lambda_permissions" {
  name = "${local.name_prefix}-compliance-dashboard-permissions"
  role = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # CloudWatch Logs
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = ["arn:aws:logs:*:*:*"]
      },
      # Overrides DynamoDB table — full CRUD
      {
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:GetItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem",
          "dynamodb:Query",
          "dynamodb:Scan",
        ]
        Resource = [aws_dynamodb_table.overrides.arn]
      },
      # Hunterkiller state table — read + update (for abort/extend)
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:UpdateItem",
          "dynamodb:Scan",
        ]
        Resource = [var.hunterkiller_state_table_arn]
      },
      # Compliance report data table — read only (scanner writes, dashboard reads)
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:Query",
        ]
        Resource = [var.compliance_report_table_arn]
      },
      # Compliance reports in S3 — read only
      {
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:ListBucket"]
        Resource = [
          "arn:aws:s3:::${var.compliance_reports_bucket}",
          "arn:aws:s3:::${var.compliance_reports_bucket}/*",
        ]
      },
      # Terraform state in S3 — read only (for CURE page resource mapping)
      {
        Effect = "Allow"
        Action = ["s3:GetObject"]
        Resource = [
          "arn:aws:s3:::${var.tf_state_bucket}/${var.tf_state_key}",
        ]
      },
      # EC2 read-only — volume/instance lookups for CURE page context
      {
        Effect = "Allow"
        Action = [
          "ec2:DescribeVolumes",
          "ec2:DescribeInstances",
        ]
        Resource = ["*"]
      },
      # Invoke hunterkiller notifier Lambda
      {
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = compact([var.scanner_function_arn, var.hunterkiller_function_arn, var.hunterkiller_executor_function_arn, var.sprawl_test_function_arn])
      },
      # Cancel EventBridge Scheduler schedules (abort pending enforcements)
      {
        Effect = "Allow"
        Action = [
          "scheduler:GetSchedule",
          "scheduler:DeleteSchedule",
          "scheduler:ListSchedules",
        ]
        Resource = ["arn:aws:scheduler:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:schedule/default/${local.name_prefix}-hk-enforce-*"]
      },
      # ECR — read image metadata and scan findings for security dashboard
      {
        Effect = "Allow"
        Action = [
          "ecr:DescribeImages",
          "ecr:DescribeImageScanFindings",
        ]
        Resource = ["arn:aws:ecr:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:repository/*"]
      },
      # Cost Explorer — live spend + forecast for burn page
      {
        Effect   = "Allow"
        Action   = ["ce:GetCostAndUsage", "ce:GetCostForecast"]
        Resource = ["*"]
      },
      # SSM Parameter Store — read protected resources config at runtime
      {
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = ["arn:aws:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter/${var.project}/${var.environment}/*"]
      },
      # Secrets Manager — invite HMAC key for viewer gate (conditional)
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = var.enable_viewer_gate ? [aws_secretsmanager_secret.invite_hmac[0].arn] : ["arn:aws:secretsmanager:*:*:secret:none"]
      },
    ]
  })
}
