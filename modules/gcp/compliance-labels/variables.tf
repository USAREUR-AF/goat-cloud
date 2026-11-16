variable "project" {
  description = "Fact `project`: the name of the workload or system these resources belong to. A registry entry is matched to resources on this value"
  type        = string
}

variable "environment" {
  description = "Fact `environment` (for example dev, staging, production)"
  type        = string
}

variable "managed_by" {
  description = "Fact `managed_by`: the tool that creates and removes these resources"
  type        = string
  default     = "terraform"
}

variable "lifecycle_value" {
  description = "Fact `lifecycle`: persistent, temporary, experiment or deprecated. `temporary` and `experiment` require expires_at"
  type        = string
  default     = "persistent"
}

variable "expires_at" {
  description = "Fact `expires_at`: when the resources may be removed, in UTC, as `YYYY-MM-DDThh:mm:ssZ` or `YYYY-MM-DD` (midnight UTC). Rendered as `yyyymmddthhmmz`, for example `20261201t0000z`"
  type        = string
  default     = null

  validation {
    condition     = var.expires_at == null || can(regex("^[0-9]{4}-[0-9]{2}-[0-9]{2}$", var.expires_at)) || can(regex("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$", var.expires_at))
    error_message = "expires_at must be UTC in the form YYYY-MM-DDThh:mm:ssZ or YYYY-MM-DD. A timestamp with a UTC offset other than Z is refused: convert it to UTC first."
  }

  validation {
    condition     = var.expires_at == null || can(formatdate("YYYY", length(var.expires_at) == 10 ? "${var.expires_at}T00:00:00Z" : var.expires_at))
    error_message = "expires_at is not a real date and time."
  }
}

variable "owner" {
  description = "Fact `owner`: the owning team or role, never a person. Required by the contract on Google Cloud"
  type        = string
  default     = null
}

variable "cost_center" {
  description = "Optional fact `cost_center`: who pays"
  type        = string
  default     = null
}

variable "protected" {
  description = "Optional fact `protected`: true marks resources that enforcement never removes"
  type        = bool
  default     = false
}

variable "schedule" {
  description = "Optional fact `schedule`: the name of the schedule on which the resources are stopped and started"
  type        = string
  default     = null
}

variable "key_names" {
  description = "Label key to render a fact under, as a map of fact name to label key, merged over the defaults (workload, environment, managed-by, lifecycle, expires-at, owner, cost-center, protected, schedule). Fact names: project, environment, managed_by, lifecycle, expires_at, owner, cost_center, protected, schedule"
  type        = map(string)
  default     = {}

  validation {
    condition     = length(setsubtract(keys(var.key_names), ["project", "environment", "managed_by", "lifecycle", "expires_at", "owner", "cost_center", "protected", "schedule"])) == 0
    error_message = "key_names may only name these facts: project, environment, managed_by, lifecycle, expires_at, owner, cost_center, protected, schedule."
  }

  validation {
    condition     = alltrue([for k in values(var.key_names) : can(regex("^[a-z][a-z0-9_-]{0,62}$", k))])
    error_message = "Each label key must start with a lowercase letter, contain only lowercase letters, digits, underscores and dashes, and be at most 63 characters."
  }
}

variable "required_facts" {
  description = "Facts that must have a non-empty value. Fact names as in key_names. Null uses the required facts of the contract"
  type        = list(string)
  default     = null

  validation {
    condition     = length(setsubtract(coalesce(var.required_facts, []), ["project", "environment", "managed_by", "lifecycle", "expires_at", "owner", "cost_center", "protected", "schedule"])) == 0
    error_message = "required_facts may only name these facts: project, environment, managed_by, lifecycle, expires_at, owner, cost_center, protected, schedule."
  }
}
