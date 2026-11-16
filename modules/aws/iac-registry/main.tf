resource "aws_ssm_parameter" "registration" {
  name      = "/${var.project}/${var.environment}/iac-registry/${var.repo_slug}"
  type      = "String"
  overwrite = true

  # owner, contact and expires_at are part of the entry only when set; lifecycle
  # always is.
  value = jsonencode(merge(
    {
      repo_url       = var.repo_url
      iac_type       = lower(var.iac_type)
      state_location = var.state_location
      deployer       = var.deployer
      environment    = var.environment
      version        = "1.0"
    },
    var.owner != null ? { owner = var.owner } : {},
    var.contact != null ? { contact = var.contact } : {},
    { lifecycle = var.lifecycle_value },
    var.expires_at != null ? { expires_at = var.expires_at } : {},
  ))

  tags = var.tags

  # The entry holds no timestamp, so an apply that changes no input changes
  # nothing, and a changed input (an extended expires_at) reaches the entry.
  lifecycle {
    precondition {
      condition     = contains(local.registry_lifecycle_values, var.lifecycle_value)
      error_message = "lifecycle_value must be one of: ${join(", ", local.registry_lifecycle_values)}."
    }

    precondition {
      condition     = var.expires_at == null || can(regex(local.registry_expires_at_pattern, coalesce(var.expires_at, "-")))
      error_message = "expires_at must match ${local.registry_expires_at_pattern}."
    }

    precondition {
      condition     = !contains(local.registry_expiry_lifecycles, var.lifecycle_value) || var.expires_at != null
      error_message = "lifecycle_value ${var.lifecycle_value} requires expires_at (rule ${var.lifecycle_value}-requires-expires-at)."
    }
  }
}
