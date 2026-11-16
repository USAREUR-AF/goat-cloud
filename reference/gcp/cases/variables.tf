variable "project_id" {
  description = "The existing project the case resources are created in"
  type        = string
}

variable "prefix" {
  description = "Name prefix of every case resource; truth.yaml matches on the part after it"
  type        = string
  default     = "ref"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,7}$", var.prefix))
    error_message = "prefix must be 2 to 8 lowercase letters, digits and hyphens, starting with a letter."
  }
}
