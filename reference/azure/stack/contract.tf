# The tag contract of a resource group, written inline so that this example
# stands alone. modules/azure/compliance-tags replaces this file once it
# renders group-level tags.

locals {
  needs_expiry = contains(["temporary", "experiment"], var.lifecycle_value)

  group_tags = merge(
    {
      Repository  = var.repository_url
      Project     = var.project
      Environment = var.environment
      Lifecycle   = var.lifecycle_value
      ManagedBy   = "terraform"
    },
    var.expires_at != null ? { ExpiresAt = var.expires_at } : {}
  )
}
