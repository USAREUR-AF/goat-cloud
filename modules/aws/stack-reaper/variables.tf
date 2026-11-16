# Stack Reaper Module Variables

variable "project" {
  description = "Project name"
  type        = string
}

variable "environment" {
  description = "Environment name"
  type        = string
}

variable "armed" {
  description = "When true, creates the reaper Lambda with teardown dispatch permissions. Default false — only the reconciler exists."
  type        = bool
  default     = false
}

variable "dry_run" {
  description = "When true, the reaper logs teardown actions but does not execute them. Safety net."
  type        = bool
  default     = true
}

variable "reconciler_schedule" {
  description = "EventBridge schedule expression for the reconciler (e.g. rate(6 hours))"
  type        = string
  default     = "rate(6 hours)"
}

variable "iac_registry_prefix" {
  description = "SSM Parameter Store prefix for IaC registry entries"
  type        = string
}

variable "github_app_secret_arn" {
  description = "Secrets Manager ARN for GitHub App credentials (app_id + private_key). Required for workflow_dispatch teardowns."
  type        = string
  default     = ""
}

variable "repository_url" {
  description = "URL of the repository that deploys this module. Its own IaC registry entry is never scheduled for teardown."
  type        = string
}

variable "github_org" {
  description = "GitHub org to dispatch teardown workflows against. Overrides the org parsed from IaC registry repo_url."
  type        = string
  default     = ""
}

variable "ses_sender_email" {
  description = "Verified SES sender address for teardown notification emails"
  type        = string
  default     = ""
}

variable "ses_admin_emails" {
  description = "Admin email addresses for teardown notifications"
  type        = list(string)
  default     = []
}

variable "state_ttl_days" {
  description = "Days to retain DynamoDB state records before TTL cleanup"
  type        = number
  default     = 90
}

variable "tags" {
  description = "Additional tags"
  type        = map(string)
  default     = {}
}


variable "alarm_sns_topic_arn" {
  description = "SNS topic that receives this module's own failure alarms. Empty (the default) creates no alarm: a function that stops or fails is then not reported by this module. The topic's policy must allow cloudwatch.amazonaws.com to publish."
  type        = string
  default     = ""
}
