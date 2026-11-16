variable "subscription_id" {
  description = "Subscription the stack deploys into"
  type        = string
}

variable "location" {
  description = "Region, from the allowed list; the location ../bootstrap created the resource group in"
  type        = string
}

variable "repo_slug" {
  description = "Short name of the repository; names the registry entry"
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{0,61}[a-z0-9]$", var.repo_slug))
    error_message = "repo_slug must match ^[a-z0-9][a-z0-9-]{0,61}[a-z0-9]$."
  }
}

variable "repository_url" {
  description = "URL of the repository; the Repository tag"
  type        = string

  validation {
    condition     = can(regex("^(https://[^/@:]+(:[0-9]+)?/[^@]+|git@[^/:@]+:[^@]+)$", var.repository_url)) && length(var.repository_url) <= 256
    error_message = "repository_url must be https://host/path or git@host:path, at most 256 characters, with no user name, password or token in it: every Reader of the subscription can read the tag, and the registry entry repeats it."
  }
}

variable "resource_group_name" {
  description = "The stack's resource group: one of the names given to ../bootstrap as stack_resource_groups. The CI identity holds Contributor on that group only"
  type        = string
}

variable "project" {
  description = "Project tag: what the stack is for"
  type        = string
}

variable "environment" {
  description = "Environment tag"
  type        = string
}

variable "lifecycle_value" {
  description = "Lifecycle tag: persistent, temporary, experiment or deprecated"
  type        = string
  default     = "temporary"

  validation {
    condition     = contains(["persistent", "temporary", "experiment", "deprecated"], var.lifecycle_value)
    error_message = "lifecycle_value must be persistent, temporary, experiment or deprecated."
  }
}

variable "expires_at" {
  description = "ExpiresAt tag: a UTC timestamp in the form 2030-01-31T00:00:00Z. Required when lifecycle_value is temporary or experiment"
  type        = string
  default     = null

  validation {
    condition     = var.expires_at == null || can(regex("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$", var.expires_at))
    error_message = "expires_at must have the form YYYY-MM-DDTHH:MM:SSZ."
  }
}

variable "state_resource_group" {
  description = "Resource group of the state storage account (output of ../bootstrap)"
  type        = string
}

variable "state_storage_account" {
  description = "State storage account (output of ../bootstrap)"
  type        = string
}

variable "teardown_workflow" {
  description = "Workflow file that destroys this stack"
  type        = string
  default     = "teardown.yml"
}

variable "teardown_ref" {
  description = "Branch the teardown workflow runs from"
  type        = string
  default     = "main"
}

variable "budget_enabled" {
  description = "Set to false on a subscription created in the last 48 hours, which cannot hold a budget yet"
  type        = bool
  default     = true
}

variable "budget_amount" {
  description = "Monthly budget of the resource group, in the billing currency"
  type        = number
  default     = 5
}

variable "budget_contact_emails" {
  description = "Addresses that receive budget alerts"
  type        = list(string)
  default     = []
}

variable "budget_contact_roles" {
  description = "Subscription roles whose holders receive budget alerts"
  type        = list(string)
  default     = ["Owner"]
}

variable "budget_start_month" {
  description = "Month the budget starts, as YYYY-MM; the current month when the budget is first created"
  type        = string

  validation {
    condition     = can(regex("^[0-9]{4}-(0[1-9]|1[0-2])$", var.budget_start_month))
    error_message = "budget_start_month must have the form YYYY-MM."
  }
}
