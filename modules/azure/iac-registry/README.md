# iac-registry (Azure)

Writes one registry entry per repository: the blob
`iac-registry/<repo_slug>.json` in the container that holds the repository's
Terraform state. A scanner finds state storage accounts by tag, lists the
blobs under `iac-registry/` and reads each state named there.

```hcl
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
```

## The storage account carries `role=iac-registry`

A scanner finds entries with one Resource Graph query for storage accounts
tagged `role=iac-registry`. The module reads the storage account and stops the
plan when the tag is absent, so an entry is never written where no scanner
looks. Set the tag where the storage account is created:

```hcl
resource "azurerm_storage_account" "state" {
  # ...
  tags = merge(module.compliance.default_tags, { role = "iac-registry" })
}
```

The tag name is matched without regard to case, as Azure does; the value is
matched exactly.

## The entry

```json
{
  "repo_url": "https://git.example/example-org/example-repo",
  "iac_type": "terraform",
  "state_location": {
    "backend": "azurerm",
    "storage_account": "examplestate",
    "container": "tfstate",
    "key": "example-repo/terraform.tfstate"
  },
  "environment": "dev",
  "tags": { "Project": "example", "Environment": "dev" },
  "version": "1.0"
}
```

The entry has no timestamp: an apply that changes no input changes no blob,
and a changed input replaces the blob. A blob carries no Azure tags, so the
`tags` input is recorded inside the entry, where a scanner can match resources
to the repository that declares them.

`iac_type` is one of the contract's IaC tools (`iac_type.generated.tf`,
generated from `contract/contract.yaml`).

## Access

The module reads the storage account through Azure Resource Manager and writes
the blob through the storage data plane. With `storage_use_azuread = true` in
the provider the identity needs the Storage Blob Data Contributor role on the
container; without it the provider uses the storage account's shared key.

## Tests

```sh
terraform init -backend=false && terraform test
```

Ten runs against `mock_provider "azurerm"`, no credentials. The module has not
been applied to a live storage account.
<!-- BEGINNING OF PRE-COMMIT-TERRAFORM DOCS HOOK -->
## Requirements

| Name | Version |
|------|---------|
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.7.0 |
| <a name="requirement_azurerm"></a> [azurerm](#requirement\_azurerm) | >= 5.0 |

## Providers

| Name | Version |
|------|---------|
| <a name="provider_azurerm"></a> [azurerm](#provider\_azurerm) | 5.14.0 |

## Modules

No modules.

## Resources

| Name | Type |
|------|------|
| [azurerm_storage_blob.entry](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/storage_blob) | resource |
| [azurerm_storage_account.state](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/data-sources/storage_account) | data source |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|:--------:|
| <a name="input_contact"></a> [contact](#input\_contact) | Optional: where to reach the owner, as a team address or a channel, never a person's own address. Used for notices about resources of this repository that carry no contact of their own | `string` | `null` | no |
| <a name="input_container_name"></a> [container\_name](#input\_container\_name) | Blob container that holds the state; the registry entry is written to the same container | `string` | `"tfstate"` | no |
| <a name="input_environment"></a> [environment](#input\_environment) | Environment name (dev, staging, production) | `string` | n/a | yes |
| <a name="input_expires_at"></a> [expires\_at](#input\_expires\_at) | When what this repository deploys expires, in the form of the contract's expiry on this platform. Written to the entry when set; required when lifecycle\_value is temporary or experiment (rules temporary-requires-expires-at, experiment-requires-expires-at) | `string` | `null` | no |
| <a name="input_iac_type"></a> [iac\_type](#input\_iac\_type) | IaC tool that owns the state | `string` | `"terraform"` | no |
| <a name="input_lifecycle_value"></a> [lifecycle\_value](#input\_lifecycle\_value) | The lifecycle of what this repository deploys, one of the contract's lifecycle values. Written to the entry as `lifecycle`; the expiry teardown decides from it and from expires\_at | `string` | `"persistent"` | no |
| <a name="input_owner"></a> [owner](#input\_owner) | Optional: the team or role accountable for what this repository deploys. A team or role name, never a person | `string` | `null` | no |
| <a name="input_repo_slug"></a> [repo\_slug](#input\_repo\_slug) | Short name for this repository; the entry is the blob iac-registry/<repo\_slug>.json. Lowercase letters, digits, hyphens | `string` | n/a | yes |
| <a name="input_repo_url"></a> [repo\_url](#input\_repo\_url) | Git repository URL (https:// or git@) | `string` | n/a | yes |
| <a name="input_resource_group_name"></a> [resource\_group\_name](#input\_resource\_group\_name) | Resource group of the state storage account | `string` | n/a | yes |
| <a name="input_state_key"></a> [state\_key](#input\_state\_key) | Blob name of the state inside the container (the `key` of the azurerm backend) | `string` | n/a | yes |
| <a name="input_storage_account_name"></a> [storage\_account\_name](#input\_storage\_account\_name) | Storage account that holds this repository's Terraform state. It must carry the tag role=iac-registry | `string` | n/a | yes |
| <a name="input_tags"></a> [tags](#input\_tags) | Tags this repository applies to its resources: pass the default\_tags output of compliance-tags. Recorded in the entry; a blob carries no Azure tags | `map(string)` | `{}` | no |

## Outputs

| Name | Description |
|------|-------------|
| <a name="output_blob_name"></a> [blob\_name](#output\_blob\_name) | Name of the registry entry blob inside the container |
| <a name="output_blob_url"></a> [blob\_url](#output\_blob\_url) | URL of the registry entry blob |
| <a name="output_entry"></a> [entry](#output\_entry) | The registry entry as written |
<!-- END OF PRE-COMMIT-TERRAFORM DOCS HOOK -->
