variable "project" {
  description = "Project name"
  type        = string
}

variable "environment" {
  description = "Environment name"
  type        = string
}

variable "repo_slug" {
  description = "Short name of the repository; the last segment of the SSM parameter path (e.g. 'example-repo')"
  type        = string
}

variable "repo_url" {
  description = "Git repository URL, as https://host/path or git@host:path"
  type        = string

  validation {
    condition     = can(regex("^(https://[^/@:]+(:[0-9]+)?/[^@]+|git@[^/:@]+:[^@]+)$", var.repo_url))
    error_message = "repo_url must be https://host/path or git@host:path, with no user name, password or token in it: the registry entry is read by every scanner."
  }
}

# variable "iac_type" is generated from the contract: iac_type.generated.tf

variable "state_location" {
  description = "Where the repository's IaC state lives; the shape depends on iac_type"
  type        = any
}

variable "deployer" {
  description = "Who deploys this repo (e.g. github-actions, manual)"
  type        = string
  default     = "github-actions"
}

variable "tags" {
  description = "Tags to apply to the SSM parameter"
  type        = map(string)
  default     = {}
}

variable "owner" {
  description = "Optional: the team or role accountable for what this repository deploys. A team or role name, never a person"
  type        = string
  default     = null
}

variable "contact" {
  description = "Optional: where to reach the owner, as a team address or a channel, never a person's own address. Used for notices about resources of this repository that carry no contact of their own"
  type        = string
  default     = null
}

variable "lifecycle_value" {
  description = "The lifecycle of what this repository deploys, one of the contract's lifecycle values. Written to the entry as `lifecycle`; the expiry teardown decides from it and from expires_at"
  type        = string
  default     = "persistent"
  nullable    = false
}

variable "expires_at" {
  description = "When what this repository deploys expires, in the form of the contract's expiry on this platform. Written to the entry when set; required when lifecycle_value is temporary or experiment (rules temporary-requires-expires-at, experiment-requires-expires-at)"
  type        = string
  default     = null
}
