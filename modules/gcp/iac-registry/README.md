# iac-registry

Writes a repository's registry entry as the object
`iac-registry/<repo_slug>.json` in its Cloud Storage state bucket. The bucket
must carry the label `role=iac-registry`. The payload and the mapping to the
AWS module are in [../README.md](../README.md).

<!-- BEGINNING OF PRE-COMMIT-TERRAFORM DOCS HOOK -->
## Requirements

| Name | Version |
|------|---------|
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.7.0 |
| <a name="requirement_google"></a> [google](#requirement\_google) | >= 5.0 |

## Providers

| Name | Version |
|------|---------|
| <a name="provider_google"></a> [google](#provider\_google) | 8.2.0 |

## Modules

No modules.

## Resources

| Name | Type |
|------|------|
| [google_storage_bucket_object.registration](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/storage_bucket_object) | resource |
| [google_storage_bucket.state](https://registry.terraform.io/providers/hashicorp/google/latest/docs/data-sources/storage_bucket) | data source |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|:--------:|
| <a name="input_contact"></a> [contact](#input\_contact) | Optional: where to reach the owner, as a team address or a channel, never a person's own address. Used for notices about resources of this repository that carry no contact of their own | `string` | `null` | no |
| <a name="input_environment"></a> [environment](#input\_environment) | Environment name, as passed to compliance-labels | `string` | n/a | yes |
| <a name="input_expires_at"></a> [expires\_at](#input\_expires\_at) | When what this repository deploys expires, in the form of the contract's expiry on this platform. Written to the entry when set; required when lifecycle\_value is temporary or experiment (rules temporary-requires-expires-at, experiment-requires-expires-at) | `string` | `null` | no |
| <a name="input_iac_type"></a> [iac\_type](#input\_iac\_type) | IaC tool that owns the state | `string` | `"terraform"` | no |
| <a name="input_lifecycle_value"></a> [lifecycle\_value](#input\_lifecycle\_value) | The lifecycle of what this repository deploys, one of the contract's lifecycle values. Written to the entry as `lifecycle`; the expiry teardown decides from it and from expires\_at | `string` | `"persistent"` | no |
| <a name="input_owner"></a> [owner](#input\_owner) | Optional: the team or role accountable for what this repository deploys. A team or role name, never a person | `string` | `null` | no |
| <a name="input_project"></a> [project](#input\_project) | The contract's `project` fact, exactly as passed to compliance-labels. The repository URL is not a label on Google Cloud, so a scanner matches resources to this entry on the label that carries the fact `project` (`workload` by default) | `string` | n/a | yes |
| <a name="input_repo_slug"></a> [repo\_slug](#input\_repo\_slug) | Short name for this repository; the entry is the object iac-registry/<repo\_slug>.json | `string` | n/a | yes |
| <a name="input_repo_url"></a> [repo\_url](#input\_repo\_url) | Git repository URL (https:// or git@) | `string` | n/a | yes |
| <a name="input_require_bucket_label"></a> [require\_bucket\_label](#input\_require\_bucket\_label) | Refuse the plan when the state bucket does not carry role=iac-registry. Turn off only when the identity running Terraform cannot read the bucket's metadata | `bool` | `true` | no |
| <a name="input_state_bucket"></a> [state\_bucket](#input\_state\_bucket) | Cloud Storage bucket that holds this repository's state. The entry is written to the same bucket, and the bucket must carry the label role=iac-registry | `string` | n/a | yes |
| <a name="input_state_prefix"></a> [state\_prefix](#input\_state\_prefix) | The gcs backend's prefix; the default workspace's state object is <state\_prefix>/default.tfstate | `string` | n/a | yes |

## Outputs

| Name | Description |
|------|-------------|
| <a name="output_entry"></a> [entry](#output\_entry) | The registry entry as written |
| <a name="output_registry_object"></a> [registry\_object](#output\_registry\_object) | The registry entry, as gs://<state\_bucket>/iac-registry/<repo\_slug>.json |
| <a name="output_registry_object_name"></a> [registry\_object\_name](#output\_registry\_object\_name) | The object name of the registry entry inside the state bucket |
| <a name="output_state_object"></a> [state\_object](#output\_state\_object) | The default workspace's state object, as gs://<state\_bucket>/<state\_prefix>/default.tfstate |
<!-- END OF PRE-COMMIT-TERRAFORM DOCS HOOK -->
