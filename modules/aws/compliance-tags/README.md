# compliance-tags

<!-- BEGINNING OF PRE-COMMIT-TERRAFORM DOCS HOOK -->
## Requirements

No requirements.

## Providers

| Name | Version |
|------|---------|
| <a name="provider_aws"></a> [aws](#provider\_aws) | n/a |

## Modules

No modules.

## Resources

| Name | Type |
|------|------|
| [aws_ssm_parameter.contract](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/ssm_parameter) | data source |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|:--------:|
| <a name="input_environment"></a> [environment](#input\_environment) | Environment name (dev, staging, production) | `string` | n/a | yes |
| <a name="input_expires_at"></a> [expires\_at](#input\_expires\_at) | Expiry as a UTC timestamp ending in Z (2030-01-31T00:00:00Z). Required when lifecycle\_value is temporary or experiment | `string` | `null` | no |
| <a name="input_lifecycle_value"></a> [lifecycle\_value](#input\_lifecycle\_value) | Lifecycle tag value (persistent, temporary, experiment, deprecated) | `string` | `"persistent"` | no |
| <a name="input_managed_by"></a> [managed\_by](#input\_managed\_by) | ManagedBy tag value | `string` | `"terraform"` | no |
| <a name="input_project"></a> [project](#input\_project) | Project name (must match the compliance contract project) | `string` | n/a | yes |
| <a name="input_repository_url"></a> [repository\_url](#input\_repository\_url) | Git repository URL (https:// or git@) | `string` | n/a | yes |
| <a name="input_use_ssm_contract"></a> [use\_ssm\_contract](#input\_use\_ssm\_contract) | Whether to load the compliance contract from SSM. Set to false if the contract SSM parameter hasn't been deployed yet. | `bool` | `true` | no |

## Outputs

| Name | Description |
|------|-------------|
| <a name="output_contract"></a> [contract](#output\_contract) | Full parsed compliance contract |
| <a name="output_default_tags"></a> [default\_tags](#output\_default\_tags) | Complete set of required tags ready for provider.default\_tags |
| <a name="output_required_tags"></a> [required\_tags](#output\_required\_tags) | List of required tag keys from the compliance contract |
<!-- END OF PRE-COMMIT-TERRAFORM DOCS HOOK -->
