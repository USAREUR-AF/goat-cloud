# Repository bootstrap, once per repository, by a subscription Owner:
# the state storage account and the CI identity in the bootstrap group, and the
# stack's resource groups. Nothing else goes in the bootstrap group.
#
# The CI identity holds Contributor on the stack's groups only, data access to
# the tfstate container and Reader on the state account. It has no write on the
# bootstrap group, so a CI run
# cannot add a federated credential to its own identity, change the state
# account's settings or list its keys.

locals {
  subscription_scope = "/subscriptions/${var.subscription_id}"

  # Storage account names are global: lowercase letters and digits, 3 to 24.
  state_account_name = "${substr(replace(var.repo_slug, "-", ""), 0, 16)}${substr(sha1("${var.subscription_id}/${var.repo_slug}"), 0, 8)}"

  group_tags = {
    Repository  = var.repository_url
    Project     = var.repo_slug
    Environment = var.environment
    Lifecycle   = "persistent"
    ManagedBy   = "terraform"
  }
}

resource "azurerm_resource_group" "bootstrap" {
  name     = "${var.repo_slug}-bootstrap"
  location = var.location
  tags     = local.group_tags
}

resource "azurerm_storage_account" "state" {
  name                     = local.state_account_name
  resource_group_name      = azurerm_resource_group.bootstrap.name
  location                 = azurerm_resource_group.bootstrap.location
  account_tier             = "Standard"
  account_replication_type = "LRS"

  shared_access_key_enabled       = false
  allow_nested_items_to_be_public = false
  min_tls_version                 = "TLS1_2"

  blob_properties {
    versioning_enabled = true

    delete_retention_policy {
      days = 7
    }

    container_delete_retention_policy {
      days = 7
    }
  }

  # The scan finds state storage accounts by this tag.
  tags = { role = "iac-registry" }
}

resource "azurerm_storage_container" "tfstate" {
  name               = "tfstate"
  storage_account_id = azurerm_storage_account.state.id
}

resource "azurerm_user_assigned_identity" "ci" {
  name                = "${var.repo_slug}-ci"
  resource_group_name = azurerm_resource_group.bootstrap.name
  location            = azurerm_resource_group.bootstrap.location
}

resource "azurerm_federated_identity_credential" "github" {
  for_each = var.github_environments

  name                      = "github-${each.key}"
  user_assigned_identity_id = azurerm_user_assigned_identity.ci.id
  issuer                    = "https://token.actions.githubusercontent.com"
  subject                   = "repo:${var.github_org}/${var.github_repo}:environment:${each.key}"
  audience                  = ["api://AzureADTokenExchange"]
}

# The stack's resource groups. The stack root adopts each one with an import
# block and owns its tags from then on; this root sets the contract tags it
# knows at creation and leaves later tag changes to the stack.
resource "azurerm_resource_group" "stack" {
  for_each = var.stack_resource_groups

  name     = each.key
  location = var.location
  tags     = local.group_tags

  lifecycle {
    ignore_changes = [tags]
  }
}

resource "azurerm_role_assignment" "ci_stack_contributor" {
  for_each = azurerm_resource_group.stack

  scope                = each.value.id
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.ci.principal_id
  principal_type       = "ServicePrincipal"
}

# Opt-in, off by default: Contributor on the whole subscription, so the stack
# can create resource groups of its own. It also reaches the bootstrap group:
# the CI identity can then add federated credentials to itself and change the
# state account's settings. Read rule 9 of the build standard before turning
# it on.
resource "azurerm_role_assignment" "ci_subscription_contributor" {
  count = var.ci_subscription_contributor ? 1 : 0

  scope                = local.subscription_scope
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.ci.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "ci_state" {
  scope                = azurerm_storage_container.tfstate.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.ci.principal_id
  principal_type       = "ServicePrincipal"
}

# Read the state account's properties and tags: the stack checks the tag
# role=iac-registry, and the provider looks the account up before it writes a
# blob. Reader holds no action, so it cannot list the account's keys.
resource "azurerm_role_assignment" "ci_state_account_read" {
  scope                = azurerm_storage_account.state.id
  role_definition_name = "Reader"
  principal_id         = azurerm_user_assigned_identity.ci.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "operator_state" {
  count = var.operator_object_id == "" ? 0 : 1

  scope                = azurerm_storage_container.tfstate.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = var.operator_object_id
}

resource "azurerm_management_lock" "bootstrap" {
  count = var.lock_enabled ? 1 : 0

  name       = "state-and-identity"
  scope      = azurerm_resource_group.bootstrap.id
  lock_level = "CanNotDelete"
  notes      = "Terraform state and the CI identity of ${var.repo_slug}."

  depends_on = [
    azurerm_storage_container.tfstate,
    azurerm_federated_identity_credential.github,
    azurerm_role_assignment.ci_state,
    azurerm_role_assignment.ci_state_account_read,
    azurerm_role_assignment.operator_state,
  ]
}
