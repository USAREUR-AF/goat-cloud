# No credentials and no Azure calls: the azurerm provider is mocked.
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

run "entry_blob" {
  command = plan

  assert {
    condition     = azurerm_storage_blob.entry.name == "iac-registry/example-repo.json"
    error_message = "The entry must be the blob iac-registry/<repo_slug>.json."
  }

  assert {
    condition     = azurerm_storage_blob.entry.storage_container_id == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/example-state-rg/providers/Microsoft.Storage/storageAccounts/examplestate/blobServices/default/containers/tfstate"
    error_message = "The entry must be written to the state container, tfstate by default."
  }

  assert {
    condition     = azurerm_storage_blob.entry.type == "Block" && azurerm_storage_blob.entry.content_type == "application/json"
    error_message = "The entry must be a JSON block blob."
  }
}

run "payload" {
  command = plan

  assert {
    condition = jsondecode(azurerm_storage_blob.entry.source_content) == {
      repo_url = "https://git.example/example-org/example-repo"
      iac_type = "terraform"
      state_location = {
        backend         = "azurerm"
        storage_account = "examplestate"
        container       = "tfstate"
        key             = "example-repo/terraform.tfstate"
      }
      environment = "dev"
      tags = {
        Project     = "example"
        Environment = "dev"
      }
      version   = "1.0"
      lifecycle = "persistent"
    }
    error_message = "The payload must hold exactly the registry entry fields, with no timestamp."
  }

  assert {
    condition     = output.entry.state_location.backend == "azurerm"
    error_message = "The entry output must mirror the payload."
  }
}

run "custom_container" {
  command = plan

  variables {
    container_name = "state"
  }

  assert {
    condition     = endswith(azurerm_storage_blob.entry.storage_container_id, "/blobServices/default/containers/state") && jsondecode(azurerm_storage_blob.entry.source_content).state_location.container == "state"
    error_message = "container_name must set both where the entry is written and what it records."
  }
}

run "role_tag_name_any_case" {
  command = plan

  override_data {
    target = data.azurerm_storage_account.state
    values = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/example-state-rg/providers/Microsoft.Storage/storageAccounts/examplestate"
      tags = {
        Role = "iac-registry"
      }
    }
  }

  assert {
    condition     = azurerm_storage_blob.entry.name == "iac-registry/example-repo.json"
    error_message = "A tag named Role is the same Azure tag as role."
  }
}

run "account_without_role_tag_fails" {
  command = plan

  override_data {
    target = data.azurerm_storage_account.state
    values = {
      id   = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/example-state-rg/providers/Microsoft.Storage/storageAccounts/examplestate"
      tags = {}
    }
  }

  expect_failures = [data.azurerm_storage_account.state]
}

run "account_with_other_role_fails" {
  command = plan

  override_data {
    target = data.azurerm_storage_account.state
    values = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/example-state-rg/providers/Microsoft.Storage/storageAccounts/examplestate"
      tags = {
        role = "logs"
      }
    }
  }

  expect_failures = [data.azurerm_storage_account.state]
}

run "bad_slug_fails" {
  command = plan

  variables {
    repo_slug = "Example_Repo"
  }

  expect_failures = [var.repo_slug]
}

run "bad_repo_url_fails" {
  command = plan

  variables {
    repo_url = "example-repo"
  }

  expect_failures = [var.repo_url]
}

run "unknown_iac_type_fails" {
  command = plan

  variables {
    iac_type = "shell-script"
  }

  expect_failures = [var.iac_type]
}

run "state_key_inside_registry_prefix_fails" {
  command = plan

  variables {
    state_key = "iac-registry/example-repo.json"
  }

  expect_failures = [var.state_key]
}

run "owner_and_contact_are_absent_by_default" {
  command = plan

  assert {
    condition     = !contains(keys(jsondecode(azurerm_storage_blob.entry.source_content)), "owner") && !contains(keys(jsondecode(azurerm_storage_blob.entry.source_content)), "contact")
    error_message = "An entry without owner and contact inputs has neither field."
  }
}

run "owner_and_contact_are_carried_when_set" {
  command = plan

  variables {
    owner   = "platform-team"
    contact = "platform-team@example.invalid"
  }

  assert {
    condition     = jsondecode(azurerm_storage_blob.entry.source_content).owner == "platform-team"
    error_message = "owner is written to the entry."
  }

  assert {
    condition     = jsondecode(azurerm_storage_blob.entry.source_content).contact == "platform-team@example.invalid"
    error_message = "contact is written to the entry."
  }
}

run "lifecycle_and_expiry_are_carried_when_set" {
  command = plan

  variables {
    lifecycle_value = "experiment"
    expires_at      = "2030-12-01T00:00:00Z"
  }

  assert {
    condition     = jsondecode(azurerm_storage_blob.entry.source_content).lifecycle == "experiment"
    error_message = "lifecycle is written to the entry."
  }

  assert {
    condition     = jsondecode(azurerm_storage_blob.entry.source_content).expires_at == "2030-12-01T00:00:00Z"
    error_message = "expires_at is written to the entry."
  }
}

run "iac_type_compares_without_regard_to_case" {
  command = plan

  variables {
    iac_type = "Bicep"
  }

  assert {
    condition     = jsondecode(azurerm_storage_blob.entry.source_content).iac_type == "bicep"
    error_message = "iac_type is accepted in any case and written in lowercase."
  }
}

run "experiment_without_expiry_is_refused" {
  command = plan

  variables {
    lifecycle_value = "experiment"
  }

  expect_failures = [azurerm_storage_blob.entry]
}

run "expiry_in_another_form_is_refused" {
  command = plan

  variables {
    lifecycle_value = "temporary"
    expires_at      = "20301201t0000z"
  }

  expect_failures = [azurerm_storage_blob.entry]
}

run "persistent_by_default_with_no_expiry" {
  command = plan

  assert {
    condition     = jsondecode(azurerm_storage_blob.entry.source_content).lifecycle == "persistent"
    error_message = "lifecycle defaults to persistent and is always written."
  }

  assert {
    condition     = !contains(keys(jsondecode(azurerm_storage_blob.entry.source_content)), "expires_at")
    error_message = "A persistent entry without expires_at has no expires_at field."
  }
}

run "temporary_without_expiry_fails_the_plan" {
  command = plan

  variables {
    lifecycle_value = "temporary"
  }

  expect_failures = [azurerm_storage_blob.entry]
}

run "credential_in_repo_url_is_refused" {
  command = plan

  variables {
    repo_url = "https://user:token@git.example/example-org/example-repo"
  }

  expect_failures = [var.repo_url]
}

run "user_in_repo_url_is_refused" {
  command = plan

  variables {
    repo_url = "ssh://git@git.example/example-org/example-repo.git"
  }

  expect_failures = [var.repo_url]
}

run "git_ssh_form_is_accepted" {
  command = plan

  variables {
    repo_url = "git@git.example:example-org/example-repo.git"
  }
}
