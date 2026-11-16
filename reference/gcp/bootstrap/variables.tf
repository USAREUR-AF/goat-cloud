variable "project_id" {
  description = "The existing project the state bucket is created in"
  type        = string
}

variable "location" {
  description = "Bucket location, a region or a multi-region. No default: an organization policy on resource locations may allow only some"
  type        = string
}

variable "slug" {
  description = "Short name of the repository; the buckets are <slug>-tfstate-<suffix> and <slug>-stack-tfstate-<suffix>"
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,30}[a-z0-9]$", var.slug))
    error_message = "slug must be 3 to 32 lowercase letters, digits and hyphens, starting with a letter."
  }
}

variable "owner" {
  description = "Fact `owner`: the owning team, as a label value"
  type        = string
}

variable "workload" {
  description = "Fact `project` of the contract, rendered as the label `workload`: the workload the bucket belongs to, as a label value"
  type        = string
}

variable "environment" {
  description = "Fact `environment`, as a label value"
  type        = string
  default     = "dev"
}

variable "force_destroy" {
  description = "Test only. Lets terraform destroy remove the state buckets while they still hold object versions. A real state bucket leaves this false"
  type        = bool
  default     = false
}

variable "soft_delete_retention_seconds" {
  description = "Test only. Soft-delete retention of the state buckets in seconds; null keeps the Cloud Storage default. 0 lets a test leave nothing behind"
  type        = number
  default     = null
}
