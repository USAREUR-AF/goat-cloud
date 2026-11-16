# Compliance Scanner Module Variables

variable "project" {
  description = "Project name"
  type        = string
}

variable "environment" {
  description = "Environment name"
  type        = string
}

variable "tf_state_bucket" {
  description = "S3 bucket that holds the Terraform state of the root that deploys the scanner (for drift detection)"
  type        = string
}

variable "tf_state_key" {
  description = "S3 key of that state file"
  type        = string
}

variable "required_tags" {
  description = "List of tag keys that all resources should have. Null uses the required tags of the contract."
  type        = list(string)
  default     = null
}

variable "exclusion_arns" {
  description = "Specific ARNs to exclude from drift detection (bootstrap resources, TF state bucket, etc.)"
  type        = list(string)
  default     = []
}

variable "exclusion_patterns" {
  description = "Regex patterns for ARNs to exclude from drift detection"
  type        = list(string)
  default     = []
}

variable "schedule_expression" {
  description = "EventBridge schedule expression for the scan"
  type        = string
  default     = "cron(0 3 ? * * *)"
}

variable "report_retention_days" {
  description = "Days to retain compliance reports in S3"
  type        = number
  default     = 90
}

variable "ses_sender_email" {
  description = "Verified SES sender address for HTML compliance emails (e.g. compliance@example.com). Leave empty to skip SES."
  type        = string
  default     = ""
}

variable "ses_recipient_emails" {
  description = "Email addresses to receive the HTML compliance report via SES"
  type        = list(string)
  default     = []
}

variable "hunterkiller_function_name" {
  description = "Name of the hunterkiller Lambda function. When set, the scanner invokes it in warn mode after each scan."
  type        = string
  default     = ""
}

variable "overrides_table_name" {
  description = "DynamoDB table name for compliance dashboard overrides (false positives / exemptions). When set, the scanner excludes overridden resources from findings."
  type        = string
  default     = ""
}

variable "overrides_table_arn" {
  description = "DynamoDB table ARN for compliance dashboard overrides. Required for IAM permissions when overrides_table_name is set."
  type        = string
  default     = ""
}

variable "cli_prefix" {
  description = "CLI prefix for teardown commands in reports. Set it when a credential helper must precede `aws`."
  type        = string
  default     = "aws"
}

variable "repository_url" {
  description = "URL of the repository that deploys this module. Used for report links and to recognize that repository's own entry in the IaC registry."
  type        = string
}

variable "scanner_source_url" {
  description = "URL of the scanner's source, linked from the footer of each report. Empty omits the link."
  type        = string
  default     = ""
}

variable "standards_url" {
  description = "URL of the document that states the deployment's compliance standards, linked from the footer of each report. Empty omits the link."
  type        = string
  default     = ""
}

variable "purpose_hints" {
  description = "Map of resource-name fragment to purpose text, used to describe resources that carry no descriptive tag (e.g. { wiki = \"Team wiki\" })."
  type        = map(string)
  default     = {}
}

variable "protected_resources_ssm_name" {
  description = "SSM Parameter Store name containing protected resources JSON. Lambda reads at runtime."
  type        = string
  default     = ""
}

variable "iac_registry_prefix" {
  description = "SSM parameter path prefix for IaC registry entries. Defaults to /{project}/{environment}/iac-registry."
  type        = string
  default     = ""
}

variable "iac_registry_state_buckets" {
  description = "S3 bucket names that hold cross-repo TF state for IaC registry entries. The scanner Lambda gets s3:GetObject on these so it can read other repos' state for drift detection."
  type        = list(string)
  default     = []
}

variable "tags" {
  description = "Additional tags"
  type        = map(string)
  default     = {}
}

variable "manage_reports_bucket_policy" {
  description = "Attach a bucket policy to the reports bucket that refuses requests not made over TLS. Set to false when the bucket's policy is managed elsewhere (a bucket has one policy), and include the same statement there."
  type        = bool
  default     = true
}


variable "alarm_sns_topic_arn" {
  description = "SNS topic that receives this module's own failure alarms (function error, failed scan, no successful scan). Empty (the default) creates no alarm: a function that stops or fails is then not reported by this module. The topic's policy must allow cloudwatch.amazonaws.com to publish."
  type        = string
  default     = ""
}

variable "scan_stale_alarm_hours" {
  description = "Hours without a successful scan after which the no-successful-scan alarm fires. The default allows a daily scan to run two hours late."
  type        = number
  default     = 26

  validation {
    condition     = var.scan_stale_alarm_hours >= 1 && var.scan_stale_alarm_hours <= 168 && floor(var.scan_stale_alarm_hours) == var.scan_stale_alarm_hours
    error_message = "scan_stale_alarm_hours must be a whole number of hours from 1 to 168."
  }
}
