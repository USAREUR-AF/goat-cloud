# compliance-labels
# Renders the contract's facts as Google Cloud labels for provider default_labels.
#
# A label value holds only lowercase letters, digits, underscores and dashes,
# up to 63 characters. Every value is lowercased, every other character is
# replaced by "_", and the result is cut to 63 characters. The expiry cannot be
# an ISO 8601 timestamp for the same reason and is rendered as yyyymmddthhmmz.
# The repository URL and the contact are not labels: the registry entry
# carries them.

locals {
  # The contract is generated from contract/contract.yaml (contract.generated.tf).
  # Nothing else in the module states a default key or an allowed value.
  contract_version  = local.generated_contract.version
  default_key_names = local.generated_contract.keys
  valid_managed_by  = local.generated_contract.allowed.managed_by
  valid_lifecycle   = local.generated_contract.allowed.lifecycle

  # The contract's required facts that are labels. A required fact carried
  # by the registry entry is checked by the iac-registry module.
  contract_required_facts = [
    for fact in local.generated_contract.required_facts : fact
    if contains(keys(local.generated_contract.keys), fact)
  ]
  required_facts = var.required_facts == null ? local.contract_required_facts : var.required_facts

  # Lifecycle values for which a rule of the contract requires an expiry.
  expiry_lifecycles = [
    for rule in local.generated_contract.rules : rule.when.equals
    if rule.when.fact == "lifecycle" && contains(rule.require, "expires_at")
  ]

  key_names = merge(local.default_key_names, var.key_names)

  expires_at_rfc3339 = var.expires_at == null ? null : (
    length(var.expires_at) == 10 ? "${var.expires_at}T00:00:00Z" : var.expires_at
  )
  expires_at_label = local.expires_at_rfc3339 == null ? null : formatdate("YYYYMMDD't'hhmm'z'", local.expires_at_rfc3339)

  normalized = {
    for fact, value in {
      project     = var.project
      environment = var.environment
      managed_by  = var.managed_by
      lifecycle   = var.lifecycle_value
      owner       = var.owner
      cost_center = var.cost_center
      protected   = var.protected ? "true" : null
      schedule    = var.schedule
    } : fact => value == null ? null : substr(replace(lower(value), "/[^a-z0-9_-]/", "_"), 0, 63)
  }

  # Fact name => label value, for every fact that has a value.
  facts = {
    for fact, value in merge(local.normalized, { expires_at = local.expires_at_label }) : fact => value
    if value != null && value != ""
  }

  # Two facts under one key are refused by a precondition of the output; the
  # grouping keeps this expression valid until that check has reported it.
  labels_by_key  = { for fact, value in local.facts : local.key_names[fact] => value... }
  default_labels = { for key, values in local.labels_by_key : key => values[0] }

  missing_required_facts = [for fact in local.required_facts : fact if !contains(keys(local.facts), fact)]
}
