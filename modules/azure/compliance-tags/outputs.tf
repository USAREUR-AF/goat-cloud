output "required_tags" {
  description = "Required tag names from the contract"
  value       = local.required_tags
}

output "default_tags" {
  description = "Tag map for the tags argument of every resource group and resource"
  value       = local.default_tags

  precondition {
    condition     = !contains(local.expiry_lifecycles, var.lifecycle_value) || var.expires_at != null
    error_message = "Resources with lifecycle ${join(" or ", local.expiry_lifecycles)} must set expires_at."
  }

  precondition {
    condition     = var.expires_at == null || can(regex(local.contract.patterns.expires_at, var.expires_at))
    error_message = "expires_at must be a UTC timestamp ending in Z, for example 2030-01-31T00:00:00Z."
  }

  precondition {
    condition     = contains(local.contract.allowed.managed_by, lower(var.managed_by))
    error_message = "managed_by must be one of: ${join(", ", local.contract.allowed.managed_by)}."
  }

  precondition {
    condition     = contains(local.contract.allowed.lifecycle, var.lifecycle_value)
    error_message = "lifecycle_value must be one of: ${join(", ", local.contract.allowed.lifecycle)}."
  }

  precondition {
    condition     = length(local.colliding_extra_names) == 0
    error_message = "extra_tags repeats a contract tag name (Azure tag names are case-insensitive): ${join(", ", local.colliding_extra_names)}."
  }

  precondition {
    condition     = length(local.default_tags) <= local.contract.limits.max_tags_per_resource
    error_message = "Azure allows at most 50 tags on a resource, resource group or subscription."
  }
}

output "contract" {
  description = "The tag contract this module renders, with the Azure tag limits it enforces"
  value       = local.contract
}
