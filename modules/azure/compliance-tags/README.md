# compliance-tags (Azure)

**Status: new.** AWS support is demonstrated and stable; Azure and Google Cloud support is new and may change between releases.

Renders the tag contract as a map for Azure resources and resource groups. The
module declares no provider and creates no resource.

```hcl
module "compliance" {
  source = "git::https://github.com/USAREUR-AF/goat-cloud.git//modules/azure/compliance-tags?ref=v0.1.0"

  project        = "example"
  environment    = "dev"
  repository_url = "https://git.example/example-org/example-repo"
}

resource "azurerm_resource_group" "app" {
  name     = "example-app-rg"
  location = "westeurope"
  tags     = module.compliance.default_tags
}
```

The `azurerm` provider has no provider-level default tags, so `default_tags`
is passed to the `tags` argument of every resource group and resource. Azure
resources do not inherit tags from their resource group. To cover resources
that are not tagged directly, assign the built-in Azure Policy definition
"Inherit a tag from the resource group" once per tag name; its `modify` effect
copies the tag when a resource is created or updated, and a remediation task
applies it to existing resources.

## What it emits

`Project`, `Environment`, `ManagedBy`, `Lifecycle`, `Repository`, plus
`ExpiresAt` when `expires_at` is set, plus `extra_tags`. The names are the same
as in the AWS module.

## What it refuses

Each of these stops the plan:

| Input | Rule |
|---|---|
| `lifecycle_value` `temporary` or `experiment` without `expires_at` | Contract rule |
| `managed_by` not in the contract's list (`contract.generated.tf`, generated from `contract/contract.yaml`) | Contract |
| `lifecycle_value` not in `persistent`, `temporary`, `experiment`, `deprecated` | Contract |
| `repository_url` not starting with `https://` or `git@` | Contract |
| `expires_at` that is not a UTC timestamp ending in `Z` (`2030-01-31T00:00:00Z`); a date alone is refused | Contract |
| A tag value longer than 256 characters | Azure limit |
| A tag name longer than 512 characters, or containing `<` `>` `%` `&` `\` `?` `/` | Azure limit |
| Two names in `extra_tags` that differ only by case, or a name in `extra_tags` that repeats a contract name in another case | Azure tag names are case-insensitive |
| More than 50 tags in total | Azure limit |

Storage accounts allow tag names of at most 128 characters; the contract names
are far shorter, and a longer name in `extra_tags` fails at apply on a storage
account.

## Tests

```sh
terraform init -backend=false && terraform test
```

Eighteen runs, no provider, no credentials.
<!-- BEGINNING OF PRE-COMMIT-TERRAFORM DOCS HOOK -->
## Requirements

| Name | Version |
|------|---------|
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.7.0 |

## Providers

No providers.

## Modules

No modules.

## Resources

No resources.

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|:--------:|
| <a name="input_environment"></a> [environment](#input\_environment) | Environment tag value (dev, staging, production) | `string` | n/a | yes |
| <a name="input_expires_at"></a> [expires\_at](#input\_expires\_at) | Expiry as a UTC timestamp ending in Z (2030-01-31T00:00:00Z). Required when lifecycle\_value is temporary or experiment | `string` | `null` | no |
| <a name="input_extra_tags"></a> [extra\_tags](#input\_extra\_tags) | Optional tags added to the required ones (Owner, Contact, CostCenter, Protected, Schedule). Names are compared without regard to case, as Azure does | `map(string)` | `{}` | no |
| <a name="input_lifecycle_value"></a> [lifecycle\_value](#input\_lifecycle\_value) | Lifecycle tag value (persistent, temporary, experiment, deprecated) | `string` | `"persistent"` | no |
| <a name="input_managed_by"></a> [managed\_by](#input\_managed\_by) | ManagedBy tag value | `string` | `"terraform"` | no |
| <a name="input_project"></a> [project](#input\_project) | Project tag value | `string` | n/a | yes |
| <a name="input_repository_url"></a> [repository\_url](#input\_repository\_url) | Git repository URL, as https://host/path or git@host:path | `string` | n/a | yes |

## Outputs

| Name | Description |
|------|-------------|
| <a name="output_contract"></a> [contract](#output\_contract) | The tag contract this module renders, with the Azure tag limits it enforces |
| <a name="output_default_tags"></a> [default\_tags](#output\_default\_tags) | Tag map for the tags argument of every resource group and resource |
| <a name="output_required_tags"></a> [required\_tags](#output\_required\_tags) | Required tag names from the contract |
<!-- END OF PRE-COMMIT-TERRAFORM DOCS HOOK -->
