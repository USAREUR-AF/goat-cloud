# The armed executor's role: which deletes carry tag conditions and which IAM
# principals it can never remove. A plan only: the provider is configured with
# placeholder credentials and every account lookup is overridden, so no
# credential is used and no API is called.

provider "aws" {
  region                      = "us-east-1"
  access_key                  = "mock_access_key"
  secret_key                  = "mock_secret_key"
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true
}

override_data {
  target = data.aws_caller_identity.current
  values = {
    account_id = "123456789012"
  }
}

override_data {
  target = data.aws_region.current
  values = {
    name = "us-east-1"
  }
}

variables {
  project                          = "example"
  environment                      = "dev"
  compliance_reports_bucket        = "example-dev-compliance-reports"
  compliance_scanner_function_name = "example-dev-compliance-scanner"
  executor_armed                   = true
}

run "tag_conditioned_deletes" {
  command = plan

  # Every allow statement that deletes from one of these services names both the
  # protected tag and the environment tag in its conditions.
  assert {
    condition = alltrue([
      for s in data.aws_iam_policy_document.executor_permissions[0].statement :
      length(setintersection(toset(coalesce(s.actions, [])), toset([
        "ssm:DeleteParameter", "logs:DeleteLogGroup", "s3:DeleteBucket", "ecr:DeleteRepository",
        "events:DeleteRule", "secretsmanager:DeleteSecret", "kms:ScheduleKeyDeletion",
        ]))) == 0 || coalesce(s.effect, "Allow") != "Allow" || (
        contains([for c in s.condition : c.variable], "aws:ResourceTag/Protected") &&
        contains([for c in s.condition : c.variable], "aws:ResourceTag/Environment")
      )
    ])
    error_message = "A delete on SSM, logs, S3, ECR, EventBridge, Secrets Manager or KMS is allowed without both tag conditions."
  }

  assert {
    condition = alltrue([
      for s in data.aws_iam_policy_document.executor_permissions[0].statement :
      alltrue([for c in s.condition : c.variable != "aws:ResourceTag/Environment" || toset(c.values) == toset(["prod", "production"])])
    ])
    error_message = "The environment conditions use the contract's protected environments."
  }
}

run "reserved_iam_is_denied" {
  command = plan

  assert {
    condition = alltrue([
      for s in [for s in data.aws_iam_policy_document.executor_permissions[0].statement : s if s.sid == "DenyReservedIam"] :
      s.effect == "Deny" &&
      contains(s.resources, "arn:aws:iam::123456789012:role/aws-service-role/*") &&
      contains(s.resources, "arn:aws:iam::123456789012:role/aws-reserved/*") &&
      contains(s.resources, "arn:aws:iam::123456789012:role/admin/*") &&
      contains(s.resources, "arn:aws:iam::123456789012:user/admin/*") &&
      contains(s.resources, "arn:aws:iam::123456789012:policy/admin/*") &&
      contains(s.actions, "iam:DeleteRole") && contains(s.actions, "iam:DeleteUser") && contains(s.actions, "iam:DeletePolicy")
    ]) && length([for s in data.aws_iam_policy_document.executor_permissions[0].statement : s if s.sid == "DenyReservedIam"]) == 1
    error_message = "Service-linked roles, Identity Center roles and the admin path are not all denied."
  }

  assert {
    condition = length([
      for s in data.aws_iam_policy_document.executor_permissions[0].statement : s
      if s.effect == "Deny" && contains(coalesce(s.actions, []), "iam:DeleteRole") &&
      contains([for c in s.condition : c.variable], "aws:ResourceTag/Protected")
      ]) == 1 && length([
      for s in data.aws_iam_policy_document.executor_permissions[0].statement : s
      if s.effect == "Deny" && contains(coalesce(s.actions, []), "iam:DeleteRole") &&
      contains([for c in s.condition : c.variable], "aws:ResourceTag/Environment")
    ]) == 1
    error_message = "A role tagged Protected or in a protected environment is not denied."
  }
}

run "extra_protected_path" {
  command = plan

  variables {
    protected_iam_paths = ["/admin/", "/break-glass/"]
  }

  assert {
    condition = length([
      for s in [for s in data.aws_iam_policy_document.executor_permissions[0].statement : s if s.sid == "DenyReservedIam"] : s
      if contains(s.resources, "arn:aws:iam::123456789012:role/break-glass/*")
    ]) == 1
    error_message = "An added protected path is not denied."
  }
}

run "path_without_slashes_is_refused" {
  command = plan

  variables {
    protected_iam_paths = ["admin"]
  }

  expect_failures = [var.protected_iam_paths]
}
