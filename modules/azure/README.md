# Azure modules

**Status: new.** AWS support is demonstrated and stable; these Azure modules are new and may change between releases.

Two Terraform modules give an Azure root the same two things the AWS modules
give an AWS root: a tag map that satisfies the tag contract, and a registry
entry that tells a scanner where the root's state lives.

| Module | What it does | Provider |
|---|---|---|
| [`compliance-tags`](compliance-tags/) | Renders the tag contract as a map and refuses values Azure or the contract would reject | none |
| [`iac-registry`](iac-registry/) | Writes the registry entry as a blob beside the Terraform state | `azurerm` >= 5.0 |

Both have `terraform test` suites that run against a mocked provider, with no
Azure credentials. Neither has been applied to a live subscription.

## AWS to Azure

| Concept | AWS | Azure |
|---|---|---|
| Required tags | `Project`, `Environment`, `ManagedBy`, `Lifecycle`, `Repository`; `ExpiresAt` when temporary or experiment | The same names |
| Tag name case | Case-sensitive | Case-insensitive: `project` and `Project` are one tag. Values are case-sensitive |
| Tag limits | 50 tags; name 128, value 256 characters | 50 tags; name 512, value 256 characters; a name cannot contain `<` `>` `%` `&` `\` `?` `/`. Storage accounts: name 128 characters |
| Applying tags to everything | `provider "aws" { default_tags }` | No provider-level default. Pass `tags = module.compliance.default_tags` to each resource group and resource |
| Resources not tagged directly | Covered by `default_tags` | Resources do not inherit tags from their resource group. Assign the built-in Azure Policy definition "Inherit a tag from the resource group" (or "... if missing"), once per tag name |
| Registry entry | SSM parameter `/<project>/<env>/iac-registry/<slug>` | Blob `iac-registry/<slug>.json` in the state container |
| How a scanner finds entries | `GetParametersByPath` | Resource Graph query for storage accounts tagged `role=iac-registry`, then a blob listing under `iac-registry/` |
| State backend | `s3` bucket and key | `azurerm` storage account, container and key |
| State locking | DynamoDB table or S3 lock file | A lease on the state blob, taken by the backend; nothing to create |

## Consumer root

The state storage account exists before the root that uses it as a backend, so
it is created in a separate bootstrap root. That is where it gets its
`role=iac-registry` tag:

```hcl
resource "azurerm_storage_account" "state" {
  name                     = "examplestate"
  resource_group_name      = azurerm_resource_group.state.name
  location                 = azurerm_resource_group.state.location
  account_tier             = "Standard"
  account_replication_type = "GRS"

  tags = merge(module.compliance.default_tags, { role = "iac-registry" })
}

resource "azurerm_storage_container" "state" {
  name               = "tfstate"
  storage_account_id = azurerm_storage_account.state.id
}
```

A root that deploys an application:

```hcl
terraform {
  required_version = ">= 1.7.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = ">= 5.0"
    }
  }

  backend "azurerm" {
    resource_group_name  = "example-state-rg"
    storage_account_name = "examplestate"
    container_name       = "tfstate"
    key                  = "example-repo/terraform.tfstate"
    use_azuread_auth     = true
  }
}

provider "azurerm" {
  features {}
  subscription_id     = "00000000-0000-0000-0000-000000000000"
  storage_use_azuread = true
}

module "compliance" {
  source = "git::https://github.com/USAREUR-AF/goat-cloud.git//modules/azure/compliance-tags?ref=v0.1.0"

  project        = "example"
  environment    = "dev"
  repository_url = "https://git.example/example-org/example-repo"
}

module "iac_registry" {
  source = "git::https://github.com/USAREUR-AF/goat-cloud.git//modules/azure/iac-registry?ref=v0.1.0"

  storage_account_name = "examplestate"
  resource_group_name  = "example-state-rg"
  repo_slug            = "example-repo"
  repo_url             = "https://git.example/example-org/example-repo"
  state_key            = "example-repo/terraform.tfstate"
  environment          = "dev"
  tags                 = module.compliance.default_tags
}

resource "azurerm_resource_group" "app" {
  name     = "example-app-rg"
  location = "westeurope"
  tags     = module.compliance.default_tags
}

resource "azurerm_virtual_network" "app" {
  name                = "example-vnet"
  resource_group_name = azurerm_resource_group.app.name
  location            = azurerm_resource_group.app.location
  address_space       = ["10.0.0.0/16"]
  tags                = module.compliance.default_tags
}
```

With `storage_use_azuread = true` the identity that runs Terraform writes the
entry with its own Microsoft Entra ID token and needs the Storage Blob Data
Contributor role on the state container, which it already holds to write
state. Without it the provider uses the storage account's shared key.

## Tests

```sh
cd modules/azure/compliance-tags && terraform init -backend=false && terraform test
cd modules/azure/iac-registry    && terraform init -backend=false && terraform test
```

Terraform 1.7 or newer is needed for `mock_provider`.

## Not built

- A scanner that reads these entries (the registry and state adapters).
- An Azure Policy initiative generated from the contract.
