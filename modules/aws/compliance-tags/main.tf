# Load the compliance contract from the parameter store. When the parameter
# does not exist yet (first-time setup), set use_ssm_contract = false to use
# the contract built into this module (contract.generated.tf).

data "aws_ssm_parameter" "contract" {
  count = var.use_ssm_contract ? 1 : 0
  name  = "/${var.project}/${var.environment}/compliance-contract"
}

check "lifecycle_requires_expires_at" {
  assert {
    condition     = !contains(local.expiry_lifecycles, var.lifecycle_value) || var.expires_at != null
    error_message = "Resources with lifecycle ${join(" or ", local.expiry_lifecycles)} must set expires_at."
  }
}

locals {
  contract = var.use_ssm_contract ? jsondecode(data.aws_ssm_parameter.contract[0].value) : local.generated_contract

  # A contract published before version 1.0 carries no rules and no patterns;
  # the ones built into this module apply then.
  rules              = try(local.contract.rules, local.generated_contract.rules)
  expires_at_pattern = try(local.contract.patterns.expires_at, local.generated_contract.patterns.expires_at)

  # Lifecycle values for which a rule requires an expiry.
  expiry_lifecycles = [
    for rule in local.rules : rule.when.equals
    if rule.when.fact == "lifecycle" && contains(rule.require, "expires_at")
  ]
}
