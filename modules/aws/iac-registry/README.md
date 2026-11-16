# iac-registry

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
| [aws_ssm_parameter.registration](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/ssm_parameter) | resource |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|:--------:|
| <a name="input_contact"></a> [contact](#input\_contact) | Optional: where to reach the owner, as a team address or a channel, never a person's own address. Used for notices about resources of this repository that carry no contact of their own | `string` | `null` | no |
| <a name="input_deployer"></a> [deployer](#input\_deployer) | Who deploys this repo (e.g. github-actions, manual) | `string` | `"github-actions"` | no |
| <a name="input_environment"></a> [environment](#input\_environment) | Environment name | `string` | n/a | yes |
| <a name="input_expires_at"></a> [expires\_at](#input\_expires\_at) | When what this repository deploys expires, in the form of the contract's expiry on this platform. Written to the entry when set; required when lifecycle\_value is temporary or experiment (rules temporary-requires-expires-at, experiment-requires-expires-at) | `string` | `null` | no |
| <a name="input_iac_type"></a> [iac\_type](#input\_iac\_type) | IaC tool type | `string` | n/a | yes |
| <a name="input_lifecycle_value"></a> [lifecycle\_value](#input\_lifecycle\_value) | The lifecycle of what this repository deploys, one of the contract's lifecycle values. Written to the entry as `lifecycle`; the expiry teardown decides from it and from expires\_at | `string` | `"persistent"` | no |
| <a name="input_owner"></a> [owner](#input\_owner) | Optional: the team or role accountable for what this repository deploys. A team or role name, never a person | `string` | `null` | no |
| <a name="input_project"></a> [project](#input\_project) | Project name | `string` | n/a | yes |
| <a name="input_repo_slug"></a> [repo\_slug](#input\_repo\_slug) | Short name of the repository; the last segment of the SSM parameter path (e.g. 'example-repo') | `string` | n/a | yes |
| <a name="input_repo_url"></a> [repo\_url](#input\_repo\_url) | Git repository URL, as https://host/path or git@host:path | `string` | n/a | yes |
| <a name="input_state_location"></a> [state\_location](#input\_state\_location) | Where the repository's IaC state lives; the shape depends on iac\_type | `any` | n/a | yes |
| <a name="input_tags"></a> [tags](#input\_tags) | Tags to apply to the SSM parameter | `map(string)` | `{}` | no |

## Outputs

| Name | Description |
|------|-------------|
| <a name="output_ssm_parameter_arn"></a> [ssm\_parameter\_arn](#output\_ssm\_parameter\_arn) | The SSM parameter ARN for this registry entry |
| <a name="output_ssm_parameter_name"></a> [ssm\_parameter\_name](#output\_ssm\_parameter\_name) | The SSM parameter name for this registry entry |
<!-- END OF PRE-COMMIT-TERRAFORM DOCS HOOK -->
