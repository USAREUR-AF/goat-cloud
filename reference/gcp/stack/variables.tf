variable "project_id" {
  description = "The project the stack deploys into; it must already carry the contract (rule 2)"
  type        = string
}

variable "state_bucket" {
  description = "The stack's state bucket, the output stack_state_bucket of the bootstrap root. It holds this root's state and the registry entry, and no other root's state"
  type        = string
}

variable "location" {
  description = "Location of the example bucket. No default: an organization policy on resource locations may allow only some"
  type        = string
}

variable "slug" {
  description = "Short name of the repository; the registry entry is iac-registry/<slug>.json"
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,30}[a-z0-9]$", var.slug))
    error_message = "slug must be 3 to 32 lowercase letters, digits and hyphens, starting with a letter."
  }
}

variable "repo_url" {
  description = "URL of the repository, as https://host/path or git@host:path. It is written to the registry entry, which the scanner reads, so it never carries a user name or token"
  type        = string

  validation {
    condition     = can(regex("^(https://[^/@:]+(:[0-9]+)?/[^@]+|git@[^/:@]+:[^@]+)$", var.repo_url))
    error_message = "repo_url must be https://host/path or git@host:path, with no user name, password or token in it."
  }
}

variable "repo_id" {
  description = "Numeric id of the repository at its host"
  type        = string
}

variable "workload" {
  description = "Fact `project` of the contract, rendered as the label `workload`: the workload these resources belong to"
  type        = string
}

variable "owner" {
  description = "Fact `owner`: the owning team"
  type        = string
}

variable "contact" {
  description = "Where to reach the owner: an address or a channel. Written to the registry entry only"
  type        = string
}

variable "purpose" {
  description = "One sentence saying what the stack is for. Written to the registry entry only"
  type        = string
}

variable "environment" {
  description = "Fact `environment`"
  type        = string
  default     = "dev"
}

variable "lifecycle_value" {
  description = "Fact `lifecycle`: persistent, temporary, experiment or deprecated. `temporary` requires expires_at (rule 8)"
  type        = string
  default     = "persistent"

  validation {
    condition     = contains(["persistent", "temporary", "experiment", "deprecated"], var.lifecycle_value)
    error_message = "lifecycle_value must be persistent, temporary, experiment or deprecated."
  }
}

variable "expires_at" {
  description = "When the stack may be removed, in UTC, as YYYY-MM-DDThh:mm:ssZ. Rendered in labels as yyyymmddthhmmz"
  type        = string
  default     = null

  validation {
    condition     = var.expires_at == null || can(regex("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$", var.expires_at))
    error_message = "expires_at must be UTC in the form YYYY-MM-DDThh:mm:ssZ."
  }

  validation {
    condition     = var.expires_at == null || can(formatdate("YYYY", var.expires_at))
    error_message = "expires_at is not a real date and time."
  }
}

variable "teardown_workflow" {
  description = "File name of the workflow that destroys this stack (rule 9)"
  type        = string
  default     = "teardown.yml"
}

variable "contract_check" {
  description = "`enforce` stops the plan when the project lacks a contract label (rule 2). `report` is for tests in a project whose labels must not be changed: the plan continues and the missing labels are shown as a check warning and in the output project_contract_missing"
  type        = string
  default     = "enforce"

  validation {
    condition     = contains(["enforce", "report"], var.contract_check)
    error_message = "contract_check must be enforce or report."
  }
}
