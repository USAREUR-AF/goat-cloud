# No credentials and no Azure calls: the azurerm provider is mocked.
mock_provider "azurerm" {}

variables {
  subscription_id = "00000000-0000-0000-0000-000000000000"
  location        = "westeurope"
  repo_slug       = "example-repo-with-a-long-name"
  repository_url  = "https://git.example/example-org/example-repo"
  github_org      = "example-org"
  github_repo     = "example-repo"

  stack_resource_groups = ["example-dev-reference"]
}

run "state_account" {
  command = plan

  assert {
    condition     = can(regex("^[a-z0-9]{3,24}$", azurerm_storage_account.state.name))
    error_message = "A storage account name is 3 to 24 lowercase letters and digits."
  }

  assert {
    condition     = azurerm_storage_account.state.shared_access_key_enabled == false && azurerm_storage_account.state.allow_nested_items_to_be_public == false
    error_message = "State is reached with Microsoft Entra ID only and never anonymously."
  }

  assert {
    condition     = azurerm_storage_account.state.blob_properties[0].versioning_enabled
    error_message = "Blob versioning protects earlier versions of the state."
  }

  assert {
    condition     = azurerm_storage_account.state.tags["role"] == "iac-registry"
    error_message = "The scan finds the account by the tag role=iac-registry."
  }

  assert {
    condition     = azurerm_storage_container.tfstate.name == "tfstate"
    error_message = "The state container is named tfstate."
  }
}

run "ci_identity" {
  command = plan

  assert {
    condition     = azurerm_federated_identity_credential.github["deploy"].subject == "repo:example-org/example-repo:environment:deploy"
    error_message = "The deploy credential trusts the deploy environment of the repository."
  }

  assert {
    condition     = azurerm_federated_identity_credential.github["teardown"].subject == "repo:example-org/example-repo:environment:teardown"
    error_message = "The teardown credential trusts the teardown environment of the repository."
  }

  assert {
    condition     = length(azurerm_role_assignment.operator_state) == 0 && length(azurerm_management_lock.bootstrap) == 1
    error_message = "No operator assignment by default; the lock is on by default."
  }
}

run "unlocked_for_destroy" {
  command = plan

  variables {
    lock_enabled = false
  }

  assert {
    condition     = length(azurerm_management_lock.bootstrap) == 0
    error_message = "lock_enabled = false removes the lock."
  }
}

run "ci_reaches_the_stack_groups_only" {
  command = plan

  assert {
    condition     = length(azurerm_role_assignment.ci_subscription_contributor) == 0
    error_message = "By default the CI identity holds no role on the subscription."
  }

  assert {
    condition     = keys(azurerm_role_assignment.ci_stack_contributor) == ["example-dev-reference"] && alltrue([for a in azurerm_role_assignment.ci_stack_contributor : a.role_definition_name == "Contributor"])
    error_message = "Contributor is assigned once per stack resource group, and nowhere else."
  }

  assert {
    condition     = azurerm_resource_group.stack["example-dev-reference"].name == "example-dev-reference" && azurerm_resource_group.stack["example-dev-reference"].location == "westeurope"
    error_message = "The stack's group is created by this root, in its location."
  }

  assert {
    condition     = azurerm_role_assignment.ci_state.role_definition_name == "Storage Blob Data Contributor" && azurerm_role_assignment.ci_state_account_read.role_definition_name == "Reader"
    error_message = "On the state account the CI identity holds data access to the tfstate container and Reader, nothing that writes."
  }
}

run "subscription_contributor_is_opt_in" {
  command = plan

  variables {
    ci_subscription_contributor = true
  }

  assert {
    condition     = azurerm_role_assignment.ci_subscription_contributor[0].scope == "/subscriptions/00000000-0000-0000-0000-000000000000" && azurerm_role_assignment.ci_subscription_contributor[0].role_definition_name == "Contributor"
    error_message = "ci_subscription_contributor = true grants Contributor on the subscription."
  }
}

run "no_stack_group_is_refused" {
  command = plan

  variables {
    stack_resource_groups = []
  }

  expect_failures = [var.stack_resource_groups]
}

run "repository_url_with_a_token_is_refused" {
  command = plan

  variables {
    repository_url = "https://user:token@git.example/example-org/example-repo"
  }

  expect_failures = [var.repository_url]
}
