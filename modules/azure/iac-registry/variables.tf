variable "storage_account_name" {
  description = "Storage account that holds this repository's Terraform state. It must carry the tag role=iac-registry"
  type        = string
}

variable "resource_group_name" {
  description = "Resource group of the state storage account"
  type        = string
}

variable "container_name" {
  description = "Blob container that holds the state; the registry entry is written to the same container"
  type        = string
  default     = "tfstate"
}

variable "repo_slug" {
  description = "Short name for this repository; the entry is the blob iac-registry/<repo_slug>.json. Lowercase letters, digits, hyphens"
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{0,61}[a-z0-9]$", var.repo_slug))
    error_message = "repo_slug must match ^[a-z0-9][a-z0-9-]{0,61}[a-z0-9]$."
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

# variable "iac_type" is generated from the contract: iac_type.generated.tf

variable "state_key" {
  description = "Blob name of the state inside the container (the `key` of the azurerm backend)"
  type        = string

  validation {
    condition     = length(var.state_key) > 0 && !startswith(var.state_key, "iac-registry/")
    error_message = "state_key must be set and must not be under iac-registry/, which holds registry entries."
  }
}

variable "environment" {
  description = "Environment name (dev, staging, production)"
  type        = string
}

variable "tags" {
  description = "Tags this repository applies to its resources: pass the default_tags output of compliance-tags. Recorded in the entry; a blob carries no Azure tags"
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
