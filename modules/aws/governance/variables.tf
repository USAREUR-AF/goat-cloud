# Governance Module Variables

variable "project" {
  description = "Project name"
  type        = string
}

variable "environment" {
  description = "Environment name"
  type        = string
}

variable "owner_email" {
  description = "Infrastructure owner email for alerts"
  type        = string
}

variable "monthly_budget_limit" {
  description = "Monthly budget limit in USD"
  type        = number
  default     = 200
}

variable "lifecycle_warning_days" {
  description = "Days before expiry to start sending warnings"
  type        = number
  default     = 30
}

variable "lifecycle_termination_days" {
  description = "Days after creation at which a temporary resource is reported as expired"
  type        = number
  default     = 60
}

# Read only by its own validation: kept so an existing call parses.
# tflint-ignore: terraform_unused_declarations
variable "enable_auto_termination" {
  description = "Removed. This module reports and alerts and removes nothing. Kept so an existing call still parses; true fails the plan."
  type        = bool
  default     = false

  validation {
    condition     = var.enable_auto_termination == false
    error_message = "enable_auto_termination is removed: the governance module no longer deletes resources. Remove the input or set it to false. Removal goes through the enforcement module (warning, grace period, approval) or the expiry teardown dispatch."
  }
}

variable "cost_alert_thresholds" {
  description = "Budget thresholds for cost alerts (percentages)"
  type        = list(number)
  default     = [50, 80, 100, 120]
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
