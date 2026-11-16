# Report schema 2.0

Schema 2.0 is the one shape for inventory records and findings on every
platform. `adapters/schema.py` defines it and validates records and documents
(`validate_record`, `validate_document`).

The Azure and Google Cloud adapters return schema 2.0 documents. The AWS
scanner (`modules/aws/compliance-scanner`) emits schema 1.0 until it moves to
2.0; [docs/reference/report-schema.md](../docs/reference/report-schema.md) describes 1.0.

## Identity

A record is identified by `(platform, scope, resource_id)`.
`record_key(record)` returns it; stores and deltas key on it.

## Inventory document

| Field | Content |
|---|---|
| `schema_version` | `"2.0"` |
| `platform` | `aws`, `azure`, `gcp`, `kubernetes`, `baremetal` or `cloudflare` |
| `generated_at` | UTC timestamp |
| `scopes` | one entry per account, subscription or project: `scope`, `scope_type`, `name`, `state`, `tags` |
| `resources` | the records, each with the document's `platform` |

`scope_type` is `account`, `subscription` or `project`.

Optional:

| Field | Content |
|---|---|
| `currency` | the currency of every cost in the document; `USD` when absent |
| `unclassified_types` | resource type to count: types the adapter has no class for |
| `incomplete` | what the inventory is known to lack: `{scope, reason}`; `scope` is null when the whole inventory is affected |

## Record

Required on every record:

| Field | Content |
|---|---|
| `resource_id` | the platform's own identifier |
| `platform` | as on the document |
| `scope` | the AWS account id, Azure subscription id or Google Cloud project id |
| `location` | region or zone, or null |
| `resource_type` | the platform's type identifier |
| `name` | never null; the last segment of `resource_id` when the platform gives no name |
| `tags` | tags or labels as found |
| `state` | or null |
| `created_at` | or null |
| `estimated_monthly_cost` | a number in the document's currency, or null when no cost source is configured |

Optional, present on platforms that have the concept:

| Field | Content |
|---|---|
| `inherited_tags` | tags of the enclosing container |
| `kind` | the platform's sub-type, or null |
| `container` | the grouping inside the scope (Azure: the resource group), or null |
| `provisioning_state` | or null |
| `grade` | how the adapter says the record is graded (Google Cloud: `labels+ownership`, `ownership`, `none`) |
| `cost_basis` | what `estimated_monthly_cost` is: `billed`, `allocated`, `amortized`, `list-price` or `estimated` |

## Compatibility

Within major version 2 a later minor version adds optional fields and new
values of `platform`, `scope_type` and `cost_basis`. A reader accepts them: a
field or value it does not know is a note, never a problem, so a platform added
later does not break an older reader. `validate_record` and `validate_document`
return the notes in the list passed as `notes`.

## Finding categories

| 2.0 category | Meaning | 1.0 name |
|---|---|---|
| `contract` | a fact is missing, or its value is outside the contract | `tag_compliance` |
| `unclaimed` | no registered state claims the resource | `drift_detection` |
| `missing` | a registered state claims a resource the platform does not have | none |
| `lifecycle` | expired, expiring, or lacking a required expiry | `lifecycle_compliance` |
| `schedule` | runs without a schedule | `schedule_compliance` |

`adapters/schema.py` holds the names as `CATEGORY_CONTRACT`,
`CATEGORY_UNCLAIMED`, `CATEGORY_MISSING`, `CATEGORY_LIFECYCLE` and
`CATEGORY_SCHEDULE`, all of them as `FINDING_CATEGORIES`, and the mapping from
the 1.0 names as `CATEGORIES_FROM_1_0`.

## Field names that differ from 1.0

| 1.0 | 2.0 |
|---|---|
| `arn` | `resource_id` |
| `region` | `location` |
| account implied by the report | `scope` on every record |
| category names above | category names above |

`goatcloud` also reads an inventory written with the names used before 2.0
was frozen (`provider`, `sub_account_id`, `sub_accounts`, `resource_group`)
and writes only the names above.

[`focus-mapping.md`](focus-mapping.md) relates the record fields to FOCUS
columns.
