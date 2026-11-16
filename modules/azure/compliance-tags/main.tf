# compliance-tags
# Renders the tag contract as a map for Azure resources and resource groups.
#
# The module declares no provider and creates no resource. The azurerm provider
# has no provider-level default tags, so a consumer passes the default_tags
# output to the `tags` argument of each resource group and resource.

locals {
  # The contract itself is generated from contract/contract.yaml
  # (contract.generated.tf); the Azure tag limits are added here.
  contract = merge(local.generated_contract, {
    limits = {
      max_tags_per_resource = 50
      max_name_length       = 512
      max_value_length      = 256
      forbidden_name_chars  = ["<", ">", "%", "&", "\\", "?", "/"]
      names_case_sensitive  = false
    }
  })

  required_tags = [for fact in local.contract.required_facts : local.contract.keys[fact] if contains(keys(local.contract.keys), fact)]

  # Lifecycle values for which a rule of the contract requires an expiry.
  expiry_lifecycles = [
    for rule in local.contract.rules : rule.when.equals
    if rule.when.fact == "lifecycle" && contains(rule.require, "expires_at")
  ]

  contract_tags = merge(
    {
      Project     = var.project
      Environment = var.environment
      ManagedBy   = var.managed_by
      Lifecycle   = var.lifecycle_value
      Repository  = var.repository_url
    },
    var.expires_at != null ? { ExpiresAt = var.expires_at } : {}
  )

  # Azure compares tag names without regard to case, so "project" in
  # extra_tags is the same tag as "Project". An extra tag may not repeat a
  # tag this module writes; the contract's optional tags (Owner, Contact,
  # CostCenter, Protected, Schedule) are passed through extra_tags.
  written_names = [for k in concat(local.required_tags, [local.contract.keys["expires_at"]]) : lower(k)]
  colliding_extra_names = [
    for k in keys(var.extra_tags) : k if contains(local.written_names, lower(k))
  ]

  default_tags = merge(var.extra_tags, local.contract_tags)
}
