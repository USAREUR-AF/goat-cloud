# The registry entry: one JSON blob beside the state. It holds no timestamp, so
# an apply that changes no input changes no blob.

data "azurerm_storage_account" "state" {
  name                = var.state_storage_account
  resource_group_name = var.state_resource_group

  lifecycle {
    postcondition {
      condition     = contains([for k, v in self.tags : "${lower(k)}=${v}"], "role=iac-registry")
      error_message = "The state storage account must carry the tag role=iac-registry, or no scan finds this entry."
    }
  }
}

locals {
  registry_entry = merge({
    version  = "1.0"
    repo_url = var.repository_url
    iac_type = "terraform"
    state_location = {
      backend         = "azurerm"
      storage_account = var.state_storage_account
      container       = "tfstate"
      key             = "stack.tfstate"
    }
    scopes = [var.subscription_id]
    teardown = {
      workflow = var.teardown_workflow
      ref      = var.teardown_ref
    }
    environment = var.environment
    # The expiry teardown decides from these two fields.
    lifecycle = var.lifecycle_value
  }, var.expires_at != null ? { expires_at = var.expires_at } : {})
}

resource "azurerm_storage_blob" "registry" {
  name                 = "iac-registry/${var.repo_slug}.json"
  storage_container_id = "${data.azurerm_storage_account.state.id}/blobServices/default/containers/tfstate"
  type                 = "Block"
  content_type         = "application/json"
  source_content       = jsonencode(local.registry_entry)
}
