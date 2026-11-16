# Hunterkiller Module Variables

variable "project" {
  description = "Project name"
  type        = string
}

variable "environment" {
  description = "Environment name"
  type        = string
}

variable "executor_armed" {
  description = "When true, creates the executor Lambda with destructive IAM permissions. Default false — no destructive infrastructure exists."
  type        = bool
  default     = false
}

variable "dry_run" {
  description = "When true, the hunterkiller logs teardown actions but does not execute them. Safety net."
  type        = bool
  default     = true
}

variable "unrecoverable_teardown" {
  description = "Remove data-holding resources with no recovery point. Default false: an RDS instance is deleted with a final snapshot and its automated backups retained, an EBS volume (and the volumes an instance termination deletes) is snapshotted first, a single-node Redis or Valkey cache cluster takes a final snapshot, a secret gets a 30-day recovery window, a KMS key a 30-day pending window, and a bucket or image repository that still holds data is refused. true selects the plain delete for all of these and grants the role s3:DeleteObject."
  type        = bool
  default     = false
}

variable "grace_period_hours" {
  description = "Hours between the warning and the enforcement action"
  type        = number
  default     = 24
}

variable "compliance_reports_bucket" {
  description = "S3 bucket where the compliance scanner stores JSON reports"
  type        = string
}

variable "compliance_scanner_function_name" {
  description = "Name of the compliance scanner Lambda function (for invoke permissions)"
  type        = string
}

variable "ses_sender_email" {
  description = "Verified SES sender address for warning and teardown notification emails"
  type        = string
  default     = ""
}

variable "ses_admin_emails" {
  description = "Admin email addresses: the fallback contacts when a resource names no owner"
  type        = list(string)
  default     = []
}

variable "protected_environments" {
  description = "Environment tag values that are exempt from teardown. Null uses the contract's protected environment names (prod, production)"
  type        = list(string)
  default     = null
}

variable "protected_tag_key" {
  description = "Tag key that marks a resource as exempt from teardown. Null uses the contract's key for the protected fact (Protected)"
  type        = string
  default     = null
}

variable "contact_tag_keys" {
  description = "Tag keys to check for direct contact email (checked in order, first non-empty wins). The default reads the contract's Contact tag only; add keys such as OwnerEmail to read others."
  type        = list(string)
  default     = ["Contact"]
}

variable "dashboard_url" {
  description = "URL of the compliance dashboard, linked from warning emails so an owner can object before enforcement"
  type        = string
  default     = ""
}

variable "state_ttl_days" {
  description = "Days to retain DynamoDB notification records before TTL cleanup"
  type        = number
  default     = 30
}

variable "protected_resource_types" {
  description = "Resource types that are always protected from teardown. Require manual action to delete."
  type        = list(string)
  default     = ["route53:hostedzone", "payments:payment-instrument", "s3:bucket"]
}

variable "protected_resources_ssm_name" {
  description = "SSM Parameter Store name containing protected resources JSON. Executor reads at runtime."
  type        = string
  default     = ""
}

variable "auto_approve_expired" {
  description = "Approve teardown automatically for resources whose ExpiresAt is in the past. For non-production environments only."
  type        = bool
  default     = false
}

variable "bedrock_summary_model_id" {
  description = "Bedrock model or inference-profile id for an LLM-written summary at the top of enforcement emails. Empty (the default) turns the summary off and grants no Bedrock permission."
  type        = string
  default     = ""
}

variable "bedrock_summary_region" {
  description = "Region of the Bedrock runtime endpoint to call. Empty uses the function's own region."
  type        = string
  default     = ""
}

variable "tags" {
  description = "Additional tags"
  type        = map(string)
  default     = {}
}

variable "contact_allowed_email_domains" {
  description = "Mail domains a contact address taken from a resource tag (or an IAM user's Email tag) may be in. A warning for a resource whose contact is outside these domains goes to the admins instead. Empty accepts any domain: anyone who can tag a resource then chooses where its warning email is sent."
  type        = list(string)
  default     = []
}


variable "alarm_sns_topic_arn" {
  description = "SNS topic that receives this module's own failure alarms. Empty (the default) creates no alarm: a function that stops or fails is then not reported by this module. The topic's policy must allow cloudwatch.amazonaws.com to publish."
  type        = string
  default     = ""
}

variable "destroy_now_report_max_age_hours" {
  description = "\"Destroy now\" acts only on a resource with a HIGH finding in the newest compliance report generated no more than this many hours ago."
  type        = number
  default     = 36

  validation {
    condition     = var.destroy_now_report_max_age_hours > 0 && var.destroy_now_report_max_age_hours <= 168
    error_message = "destroy_now_report_max_age_hours must be between 1 and 168."
  }
}
