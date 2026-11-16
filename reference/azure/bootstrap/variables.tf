variable "subscription_id" {
  description = "Subscription the repository deploys into"
  type        = string
}

variable "location" {
  description = "Region for the bootstrap resource group, from the allowed list"
  type        = string
}

variable "repo_slug" {
  description = "Short name of the repository: lowercase letters, digits, hyphens"
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
    error_message = "repository_url must be https://host/path or git@host:path, at most 256 characters, with no user name, password or token in it: every Reader of the subscription can read the tag."
  }
}

variable "github_org" {
  description = "GitHub organization or user that owns the repository"
  type        = string
}

variable "github_repo" {
  description = "GitHub repository name"
  type        = string
}

variable "stack_resource_groups" {
  description = "Names of the resource groups the stack root deploys into. Each is created here, in var.location, and the CI identity gets Contributor on it"
  type        = set(string)

  validation {
    condition     = length(var.stack_resource_groups) > 0 && alltrue([for name in var.stack_resource_groups : can(regex("^[-\\w._()]{1,89}[-\\w_()]$", name))])
    error_message = "stack_resource_groups holds at least one name of 1 to 90 letters, digits, underscores, hyphens, periods and parentheses, not ending in a period."
  }
}

variable "ci_subscription_contributor" {
  description = "Opt-in. true also grants the CI identity Contributor on the whole subscription, which reaches the bootstrap group and its own identity. false keeps it to the stack's groups"
  type        = bool
  default     = false
}

variable "github_environments" {
  description = "GitHub environments whose workflow jobs may sign in as the CI identity"
  type        = set(string)
  default     = ["deploy", "teardown"]
}

variable "environment" {
  description = "Environment tag of the bootstrap resource group"
  type        = string
  default     = "shared"
}

variable "operator_object_id" {
  description = "Object id of a person who applies the stack root from a workstation; empty when only CI applies"
  type        = string
  default     = ""
}

variable "lock_enabled" {
  description = "CanNotDelete lock on the bootstrap resource group; set to false and apply before destroying this root"
  type        = bool
  default     = true
}
