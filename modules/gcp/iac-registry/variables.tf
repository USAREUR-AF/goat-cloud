variable "repo_slug" {
  description = "Short name for this repository; the entry is the object iac-registry/<repo_slug>.json"
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", var.repo_slug))
    error_message = "repo_slug must be 1 to 63 lowercase letters, digits and hyphens, starting and ending with a letter or digit."
  }
}

variable "repo_url" {
  description = "Git repository URL (https:// or git@)"
  type        = string

  validation {
    condition     = can(regex("^(https://[^/@:]+(:[0-9]+)?/[^@]+|git@[^/:@]+:[^@]+)$", var.repo_url))
    error_message = "repo_url must be https://host/path or git@host:path, with no user name, password or token in it: the registry entry is read by every scanner."
  }
}

variable "project" {
  description = "The contract's `project` fact, exactly as passed to compliance-labels. The repository URL is not a label on Google Cloud, so a scanner matches resources to this entry on the label that carries the fact `project` (`workload` by default)"
  type        = string
}

variable "environment" {
  description = "Environment name, as passed to compliance-labels"
  type        = string
}

# variable "iac_type" is generated from the contract: iac_type.generated.tf

variable "state_bucket" {
  description = "Cloud Storage bucket that holds this repository's state. The entry is written to the same bucket, and the bucket must carry the label role=iac-registry"
  type        = string
}

variable "state_prefix" {
  description = "The gcs backend's prefix; the default workspace's state object is <state_prefix>/default.tfstate"
  type        = string

  validation {
    condition     = !startswith(var.state_prefix, "iac-registry/") && var.state_prefix != "iac-registry"
    error_message = "state_prefix must not be under iac-registry/: that prefix holds registry entries."
  }
}

variable "require_bucket_label" {
  description = "Refuse the plan when the state bucket does not carry role=iac-registry. Turn off only when the identity running Terraform cannot read the bucket's metadata"
  type        = bool
  default     = true
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
