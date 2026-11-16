variable "subscription_id" {
  description = "Subscription this setup applies to"
  type        = string
}

variable "location" {
  description = "Region for the platform resource group, from the allowed list"
  type        = string
}

variable "repository_url" {
  description = "Repository that holds this root; the Repository tag of the platform resource group"
  type        = string

  validation {
    condition     = can(regex("^(https://[^/@:]+(:[0-9]+)?/[^@]+|git@[^/:@]+:[^@]+)$", var.repository_url)) && length(var.repository_url) <= 256
    error_message = "repository_url must be https://host/path or git@host:path, at most 256 characters, with no user name, password or token in it: every Reader of the subscription can read the tag."
  }
}

variable "name_prefix" {
  description = "Prefix for the platform resource group, the scanner identity, the policy assignments and the budget"
  type        = string
  default     = "goat-cloud"
}

variable "required_group_tags" {
  description = "Tags every resource group must carry"
  type        = set(string)
  default     = ["Repository", "Project", "Environment", "Lifecycle", "ManagedBy"]
}

variable "enforce" {
  description = "false reports a resource group without the tags; true refuses its creation"
  type        = bool
  default     = false
}

variable "budget_enabled" {
  description = "Set to false on a subscription created in the last 48 hours, which cannot hold a budget yet"
  type        = bool
  default     = true
}

variable "budget_amount" {
  description = "Monthly subscription budget, in the billing currency"
  type        = number
  default     = 25
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

variable "resource_providers_to_register" {
  description = "Azure resource providers the build standard uses that are not yet registered in the subscription. Leave out any that `az provider show` reports as Registered"
  type        = set(string)
  default     = ["Microsoft.ManagedIdentity", "Microsoft.Storage"]
}
