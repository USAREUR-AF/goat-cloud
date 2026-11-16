# compliance-labels

Renders the tag contract's facts as Google Cloud labels for
`provider "google" { default_labels }`. The mapping, the label rules, the
expiry encoding and its decoding rule are in [../README.md](../README.md).

The module declares no provider and creates no resource.

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
| <a name="input_cost_center"></a> [cost\_center](#input\_cost\_center) | Optional fact `cost_center`: who pays | `string` | `null` | no |
| <a name="input_environment"></a> [environment](#input\_environment) | Fact `environment` (for example dev, staging, production) | `string` | n/a | yes |
| <a name="input_expires_at"></a> [expires\_at](#input\_expires\_at) | Fact `expires_at`: when the resources may be removed, in UTC, as `YYYY-MM-DDThh:mm:ssZ` or `YYYY-MM-DD` (midnight UTC). Rendered as `yyyymmddthhmmz`, for example `20261201t0000z` | `string` | `null` | no |
| <a name="input_key_names"></a> [key\_names](#input\_key\_names) | Label key to render a fact under, as a map of fact name to label key, merged over the defaults (workload, environment, managed-by, lifecycle, expires-at, owner, cost-center, protected, schedule). Fact names: project, environment, managed\_by, lifecycle, expires\_at, owner, cost\_center, protected, schedule | `map(string)` | `{}` | no |
| <a name="input_lifecycle_value"></a> [lifecycle\_value](#input\_lifecycle\_value) | Fact `lifecycle`: persistent, temporary, experiment or deprecated. `temporary` and `experiment` require expires\_at | `string` | `"persistent"` | no |
| <a name="input_managed_by"></a> [managed\_by](#input\_managed\_by) | Fact `managed_by`: the tool that creates and removes these resources | `string` | `"terraform"` | no |
| <a name="input_owner"></a> [owner](#input\_owner) | Fact `owner`: the owning team or role, never a person. Required by the contract on Google Cloud | `string` | `null` | no |
| <a name="input_project"></a> [project](#input\_project) | Fact `project`: the name of the workload or system these resources belong to. A registry entry is matched to resources on this value | `string` | n/a | yes |
| <a name="input_protected"></a> [protected](#input\_protected) | Optional fact `protected`: true marks resources that enforcement never removes | `bool` | `false` | no |
| <a name="input_required_facts"></a> [required\_facts](#input\_required\_facts) | Facts that must have a non-empty value. Fact names as in key\_names. Null uses the required facts of the contract | `list(string)` | `null` | no |
| <a name="input_schedule"></a> [schedule](#input\_schedule) | Optional fact `schedule`: the name of the schedule on which the resources are stopped and started | `string` | `null` | no |

## Outputs

| Name | Description |
|------|-------------|
| <a name="output_contract"></a> [contract](#output\_contract) | The contract as this module applies it: version, key names, required facts, allowed values and the expiry encoding |
| <a name="output_default_labels"></a> [default\_labels](#output\_default\_labels) | The labels for provider.default\_labels: one per fact that has a value, under its key from key\_names |
| <a name="output_key_names"></a> [key\_names](#output\_key\_names) | The effective map of fact name to label key |
| <a name="output_required_labels"></a> [required\_labels](#output\_required\_labels) | The label keys of the required facts, as rendered by key\_names |
<!-- END OF PRE-COMMIT-TERRAFORM DOCS HOOK -->
