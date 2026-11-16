# No credentials and no Azure calls: the azurerm provider is mocked.
mock_provider "azurerm" {}

variables {
  subscription_id       = "00000000-0000-0000-0000-000000000000"
  location              = "westeurope"
  repo_slug             = "example-repo"
  resource_group_name   = "example-dev-reference"
  repository_url        = "https://git.example/example-org/example-repo"
  project               = "example"
  environment           = "dev"
  expires_at            = "2030-01-31T00:00:00Z"
  state_resource_group  = "example-repo-bootstrap"
  state_storage_account = "examplerepo00000000"
  budget_start_month    = "2030-01"
  budget_enabled        = false
}

override_data {
  target = data.azurerm_storage_account.state
  values = {
    id   = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/example-repo-bootstrap/providers/Microsoft.Storage/storageAccounts/examplerepo00000000"
    tags = { role = "iac-registry" }
  }
}

# The group is imported from ../bootstrap; a mocked provider imports nothing,
# so the imported group is stated here.
override_resource {
  target = azurerm_resource_group.app
  values = {
    id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/example-dev-reference"
  }
}

run "group_carries_the_contract" {
  command = plan

  assert {
    condition     = length(azurerm_resource_group.app.tags) == 6
    error_message = "A temporary resource group carries exactly six tags."
  }

  assert {
    condition     = azurerm_resource_group.app.tags["Lifecycle"] == "temporary" && azurerm_resource_group.app.tags["ExpiresAt"] == "2030-01-31T00:00:00Z"
    error_message = "Lifecycle and ExpiresAt must be on the resource group."
  }

  assert {
    condition     = azurerm_virtual_network.app.tags == null
    error_message = "A resource needs no tags."
  }
}

run "registry_entry_beside_the_state" {
  command = plan

  assert {
    condition     = azurerm_storage_blob.registry.name == "iac-registry/example-repo.json"
    error_message = "The entry is the blob iac-registry/<repo_slug>.json."
  }

  assert {
    condition     = endswith(azurerm_storage_blob.registry.storage_container_id, "/containers/tfstate")
    error_message = "The entry is written to the tfstate container."
  }

  assert {
    condition     = jsondecode(azurerm_storage_blob.registry.source_content).teardown.workflow == "teardown.yml"
    error_message = "The entry names the teardown workflow."
  }

  assert {
    condition     = jsondecode(azurerm_storage_blob.registry.source_content).lifecycle == var.lifecycle_value && jsondecode(azurerm_storage_blob.registry.source_content).expires_at == "2030-01-31T00:00:00Z"
    error_message = "The entry carries lifecycle and expires_at, which the expiry teardown decides from."
  }
}

run "temporary_without_expiry_is_refused" {
  command = plan

  variables {
    expires_at = null
  }

  expect_failures = [azurerm_resource_group.app]
}

run "date_only_expiry_is_refused" {
  command = plan

  variables {
    expires_at = "2030-01-31"
  }

  expect_failures = [var.expires_at]
}

run "persistent_needs_no_expiry" {
  command = plan

  variables {
    lifecycle_value = "persistent"
    expires_at      = null
  }

  assert {
    condition     = length(azurerm_resource_group.app.tags) == 5
    error_message = "A persistent resource group carries five tags."
  }
}

run "group_is_adopted_from_bootstrap" {
  command = plan

  assert {
    condition     = azurerm_resource_group.app.name == "example-dev-reference"
    error_message = "The stack deploys into the group ../bootstrap created."
  }
}

run "repository_url_with_a_token_is_refused" {
  command = plan

  variables {
    repository_url = "https://user:token@git.example/example-org/example-repo"
  }

  expect_failures = [var.repository_url]
}
