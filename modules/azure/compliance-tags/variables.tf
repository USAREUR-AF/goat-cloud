variable "project" {
  description = "Project tag value"
  type        = string

  validation {
    condition     = length(var.project) >= 1 && length(var.project) <= 256
    error_message = "project must be 1 to 256 characters (Azure tag value limit)."
  }
}

variable "environment" {
  description = "Environment tag value (dev, staging, production)"
  type        = string

  validation {
    condition     = length(var.environment) >= 1 && length(var.environment) <= 256
    error_message = "environment must be 1 to 256 characters (Azure tag value limit)."
  }
}

variable "lifecycle_value" {
  description = "Lifecycle tag value (persistent, temporary, experiment, deprecated)"
  type        = string
  default     = "persistent"
}

variable "repository_url" {
  description = "Git repository URL, as https://host/path or git@host:path"
  type        = string

  validation {
    condition     = can(regex("^(https://[^/@:]+(:[0-9]+)?/[^@]+|git@[^/:@]+:[^@]+)$", var.repository_url)) && length(var.repository_url) <= 256
    error_message = "repository_url must be https://host/path or git@host:path, with no user name, password or token in it, and at most 256 characters (Azure tag value limit)."
  }
}

variable "managed_by" {
  description = "ManagedBy tag value"
  type        = string
  default     = "terraform"
}

variable "expires_at" {
  description = "Expiry as a UTC timestamp ending in Z (2030-01-31T00:00:00Z). Required when lifecycle_value is temporary or experiment"
  type        = string
  default     = null
}

variable "extra_tags" {
  description = "Optional tags added to the required ones (Owner, Contact, CostCenter, Protected, Schedule). Names are compared without regard to case, as Azure does"
  type        = map(string)
  default     = {}

  validation {
    condition     = alltrue([for k in keys(var.extra_tags) : length(k) >= 1 && length(k) <= 512 && !can(regex("[<>%&\\\\?/]", k))])
    error_message = "Azure tag names are 1 to 512 characters and cannot contain < > % & \\ ? /."
  }

  validation {
    condition     = alltrue([for v in values(var.extra_tags) : length(v) <= 256])
    error_message = "Azure tag values are at most 256 characters."
  }

  validation {
    condition     = length(distinct([for k in keys(var.extra_tags) : lower(k)])) == length(var.extra_tags)
    error_message = "Azure tag names are case-insensitive: extra_tags holds two names that differ only by case."
  }
}
