variable "subscription_id" {
  description = "Test subscription the case resource groups are created in"
  type        = string
}

variable "location" {
  description = "Region of the case resource groups"
  type        = string
}

variable "prefix" {
  description = "Name prefix of every case resource group; truth.yaml matches on the part after it"
  type        = string
  default     = "ref"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,7}$", var.prefix))
    error_message = "prefix must be 2 to 8 lowercase letters, digits and hyphens, starting with a letter."
  }
}

variable "repository_url" {
  description = "Repository tag of every case resource group"
  type        = string
  default     = "https://git.example/example-org/example-repo"
}

variable "budget_start_month" {
  description = "Month the case 7 budget starts, as YYYY-MM: the current month"
  type        = string

  validation {
    condition     = can(regex("^[0-9]{4}-(0[1-9]|1[0-2])$", var.budget_start_month))
    error_message = "budget_start_month must have the form YYYY-MM."
  }
}
