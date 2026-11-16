# Platform setup, once per subscription, by a subscription Owner:
# the tag rules for resource groups, the backstop budget and the read-only
# scanner identity.

locals {
  subscription_scope = "/subscriptions/${var.subscription_id}"

  # Built-in policy definition "Require a tag on resource groups".
  require_tag_on_resource_groups = "/providers/Microsoft.Authorization/policyDefinitions/96670d01-0a4d-4649-9c89-2d3abc0a5025"

  group_tags = {
    Repository  = var.repository_url
    Project     = var.name_prefix
    Environment = "platform"
    Lifecycle   = "persistent"
    ManagedBy   = "terraform"
  }
}

# A subscription uses a resource type only after its resource provider is
# registered. Repositories need managed identities and storage accounts.
resource "azurerm_resource_provider_registration" "standard" {
  for_each = var.resource_providers_to_register

  name = each.key
}

resource "azurerm_resource_group" "platform" {
  name     = "${var.name_prefix}-platform"
  location = var.location
  tags     = local.group_tags
}

resource "azurerm_subscription_policy_assignment" "require_tag" {
  for_each = var.required_group_tags

  name                 = "${var.name_prefix}-rg-tag-${lower(each.key)}"
  display_name         = "Resource groups carry the tag ${each.key}"
  subscription_id      = local.subscription_scope
  policy_definition_id = local.require_tag_on_resource_groups
  enforce              = var.enforce

  parameters = jsonencode({
    tagName = { value = each.key }
  })
}

resource "azurerm_consumption_budget_subscription" "backstop" {
  count = var.budget_enabled ? 1 : 0

  name            = "${var.name_prefix}-subscription"
  subscription_id = local.subscription_scope
  amount          = var.budget_amount
  time_grain      = "Monthly"

  time_period {
    start_date = "${var.budget_start_month}-01T00:00:00Z"
  }

  notification {
    threshold      = 80
    threshold_type = "Actual"
    operator       = "GreaterThanOrEqualTo"
    contact_emails = var.budget_contact_emails
    contact_roles  = var.budget_contact_roles
  }

  notification {
    threshold      = 100
    threshold_type = "Forecasted"
    operator       = "GreaterThanOrEqualTo"
    contact_emails = var.budget_contact_emails
    contact_roles  = var.budget_contact_roles
  }

  lifecycle {
    ignore_changes = [time_period]
  }
}

resource "azurerm_user_assigned_identity" "scanner" {
  name                = "${var.name_prefix}-scanner"
  resource_group_name = azurerm_resource_group.platform.name
  location            = azurerm_resource_group.platform.location

  depends_on = [azurerm_resource_provider_registration.standard]
}

resource "azurerm_role_assignment" "scanner_reader" {
  scope                = local.subscription_scope
  role_definition_name = "Reader"
  principal_id         = azurerm_user_assigned_identity.scanner.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "scanner_cost" {
  scope                = local.subscription_scope
  role_definition_name = "Cost Management Reader"
  principal_id         = azurerm_user_assigned_identity.scanner.principal_id
  principal_type       = "ServicePrincipal"
}
