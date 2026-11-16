output "default_labels" {
  description = "The labels for provider.default_labels: one per fact that has a value, under its key from key_names"
  value       = local.default_labels

  precondition {
    condition     = length(local.missing_required_facts) == 0
    error_message = "A required fact has no value: ${join(", ", local.missing_required_facts)}."
  }

  precondition {
    condition     = length(distinct(values(local.key_names))) == length(local.key_names)
    error_message = "key_names renders two facts under the same label key."
  }

  precondition {
    condition     = contains(local.valid_managed_by, lookup(local.facts, "managed_by", ""))
    error_message = "managed_by must be one of: ${join(", ", local.valid_managed_by)}."
  }

  precondition {
    condition     = contains(local.valid_lifecycle, lookup(local.facts, "lifecycle", ""))
    error_message = "lifecycle_value must be one of: ${join(", ", local.valid_lifecycle)}."
  }

  precondition {
    condition     = !contains(local.expiry_lifecycles, lookup(local.facts, "lifecycle", "")) || local.expires_at_label != null
    error_message = "lifecycle_value ${join(" or ", local.expiry_lifecycles)} requires expires_at."
  }

  precondition {
    condition     = local.expires_at_label == null || can(regex(local.generated_contract.patterns.expires_at, local.expires_at_label))
    error_message = "The rendered expires-at label does not match the contract's pattern."
  }
}

output "required_labels" {
  description = "The label keys of the required facts, as rendered by key_names"
  value       = [for fact in local.required_facts : local.key_names[fact]]
}

output "key_names" {
  description = "The effective map of fact name to label key"
  value       = local.key_names
}

output "contract" {
  description = "The contract as this module applies it: version, key names, required facts, allowed values and the expiry encoding"
  value = {
    version           = local.contract_version
    key_names         = local.key_names
    required_facts    = local.required_facts
    valid_managed_by  = local.valid_managed_by
    valid_lifecycle   = local.valid_lifecycle
    expires_at_format = "yyyymmddthhmmz"
    patterns          = local.generated_contract.patterns
    carriers          = local.generated_contract.carriers
    unit              = local.generated_contract.unit
    rules             = local.generated_contract.rules
  }
}
