# What a repository deploys: one resource group that carries the contract, and
# the resources inside it.
#
# ../bootstrap creates the group and grants the CI identity Contributor on it
# alone; this root adopts the group on its first apply and owns its tags and
# contents from then on. Destroying this root deletes the group. Deploying
# again after that needs ../bootstrap applied again, by an Owner.

import {
  to = azurerm_resource_group.app
  id = "/subscriptions/${var.subscription_id}/resourceGroups/${var.resource_group_name}"
}

resource "azurerm_resource_group" "app" {
  name     = var.resource_group_name
  location = var.location
  tags     = local.group_tags

  lifecycle {
    precondition {
      condition     = !local.needs_expiry || var.expires_at != null
      error_message = "A temporary or experiment resource group must set expires_at."
    }
  }
}

# No tags argument: the owner, purpose and lifetime of a resource are those of
# its resource group.
resource "azurerm_virtual_network" "app" {
  name                = "${var.project}-${var.environment}-vnet"
  resource_group_name = azurerm_resource_group.app.name
  location            = azurerm_resource_group.app.location
  address_space       = ["10.0.0.0/24"]
}
