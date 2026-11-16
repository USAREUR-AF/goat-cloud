variable "project" {
  description = "Project name (must match the compliance contract project)"
  type        = string
}

variable "environment" {
  description = "Environment name (dev, staging, production)"
  type        = string
}

variable "lifecycle_value" {
  description = "Lifecycle tag value (persistent, temporary, experiment, deprecated)"
  type        = string
  default     = "persistent"
}

variable "repository_url" {
  description = "Git repository URL (https:// or git@)"
  type        = string
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

variable "use_ssm_contract" {
  description = "Whether to load the compliance contract from SSM. Set to false if the contract SSM parameter hasn't been deployed yet."
  type        = bool
  default     = true
}
