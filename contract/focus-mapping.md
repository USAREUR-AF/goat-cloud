# Report fields and FOCUS columns

[FOCUS](https://focus.finops.org/) is the FinOps Foundation's specification
for cost and usage data. The inventory records of report schema 2.0
(`adapters/schema.py`) use the same identities as a FOCUS Cost and Usage
dataset, so a record joins to a billing row without a lookup table.

The column names below are those of FOCUS 1.4, with the section of the
specification that defines each.

| Report field | FOCUS column | Column ID | Section | Correspondence |
|---|---|---|---|---|
| `resource_id` | Resource ID | `ResourceId` | 3.1.51 | The identifier the cloud assigns: the ARN on AWS, the resource ID on Azure, the full resource name on Google Cloud. The Azure adapter lowercases it; compare without regard to case |
| `name` | Resource Name | `ResourceName` | 3.1.52 | The display name; the last segment of `resource_id` when the cloud gives none |
| `resource_type` | Resource Type | `ResourceType` | 3.1.53 | The report holds the cloud's own type identifier (`ec2:instance`, `microsoft.compute/virtualmachines`, `compute.googleapis.com/Instance`). FOCUS leaves the value to the data generator, and billing exports often use a display name, so this field is matched through a per-cloud table, not by equality |
| `location` | Region ID | `RegionId` | 3.1.49 | Equal for regional resources. The report also holds a zone (`us-central1-a`) or `global` where the cloud reports one; FOCUS puts a zone in Availability Zone (3.1.6) |
| `scope` | Sub Account ID | `SubAccountId` | 3.1.62 | The AWS account id, the Azure subscription id, the Google Cloud project id |
| `scopes[].name` | Sub Account Name | `SubAccountName` | 3.1.63 | The display name of the account, subscription or project |
| `tags` | Tags | `Tags` | 3.1.65 | Tags or labels as found on the resource, as a flat object of strings |
| `platform` | Service Provider Name | `ServiceProviderName` | 3.1.54 | The report holds a short code (`aws`, `azure`, `gcp`, ...); FOCUS holds the provider's name |

## Differences to account for

- **Tags.** The FOCUS Tags column may include tags a resource inherits from
  its sub account or another container, as each data generator defines. The
  report keeps the two apart: `tags` holds what is on the resource and
  `inherited_tags` (Azure) holds the tags of its resource group. Merge
  `inherited_tags` under `tags` before comparing with a FOCUS row. FOCUS also
  requires provider-defined tag keys to carry a provider prefix; the report
  holds keys as the cloud returns them.
- **Sub account.** A resource outside any project on Google Cloud (a folder,
  an organization policy) has the organization as its `scope`; a
  billing row for it has no Sub Account ID.
- **Cost.** `estimated_monthly_cost` is in the document's `currency` (`USD`
  when absent), or `null` when no cost source is configured. `cost_basis`
  says what it is (`billed`, `allocated`, `amortized`, `list-price`,
  `estimated`). It corresponds to no FOCUS column; billed amounts come from the
  FOCUS dataset itself.
- **Fields with no FOCUS column:** `state`, `created_at`, `kind`,
  `container`, `provisioning_state`.
