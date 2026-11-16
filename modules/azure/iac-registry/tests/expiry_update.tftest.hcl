# Two applies in one file: the second changes expires_at and the entry follows.
mock_provider "azurerm" {}

variables {
  storage_account_name = "examplestate"
  resource_group_name  = "example-state-rg"
  repo_slug            = "example-repo"
  repo_url             = "https://git.example/example-org/example-repo"
  state_key            = "example-repo/terraform.tfstate"
  environment          = "dev"
  tags = {
    Project     = "example"
    Environment = "dev"
  }
}

override_data {
  target = data.azurerm_storage_account.state
  values = {
    id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/example-state-rg/providers/Microsoft.Storage/storageAccounts/examplestate"
    tags = {
      role = "iac-registry"
    }
  }
}

run "expiry_set" {
  command = apply

  variables {
    lifecycle_value = "temporary"
    expires_at      = "2030-12-01T00:00:00Z"
  }
}

run "changing_expires_at_updates_the_entry" {
  command = apply

  variables {
    lifecycle_value = "temporary"
    expires_at      = "2031-06-01T00:00:00Z"
  }

  assert {
    condition     = jsondecode(azurerm_storage_blob.entry.source_content).expires_at == "2031-06-01T00:00:00Z"
    error_message = "An extended expires_at reaches the entry on the next apply."
  }
}
