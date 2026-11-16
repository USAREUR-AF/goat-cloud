# goatcloud: scan, report, verify

One command lists a cloud scope, grades it against the resource contract and
writes a report. It is read-only: it lists resources and changes nothing.

Built for Azure and Google Cloud. The package needs the standard library and
PyYAML; a cloud SDK is imported only by that cloud's inventory adapter, when a
scan runs.

## Run

```sh
# Azure: a subscription (repeatable) or a management group
python -m goatcloud scan --platform azure --subscription 00000000-0000-0000-0000-000000000000 \
  --out report.json --html report.html

# Google Cloud: an organization, a folder or a project
python -m goatcloud scan --platform gcp --scope projects/example-project \
  --out report.json --html report.html

# Grade a saved inventory, with no cloud access
python -m goatcloud scan --platform gcp --scope projects/example-project --inventory-out inventory.json --out report.json
python -m goatcloud grade --inventory inventory.json --mapping mapping.yaml --out report.json --html report.html

# Compare a report with what a test ground was built to show
python -m goatcloud verify --truth truth.yaml --report report.json
```

| Option | Meaning |
|---|---|
| `--out` | JSON report, schema 2.0. `-` writes to standard output |
| `--html` | The same report as one HTML file with no script and no external asset |
| `--grade units` | Default. Grades the unit that carries the contract |
| `--grade resources` | Grades every resource instead |
| `--mapping` | A deployment mapping: the key names and the required set of one deployment. Format: `contract/mapping.example.yaml` |
| `--contract` | Another contract file. Default: `contract/contract.yaml` |
| `--lag-minutes` | A unit created within this many minutes that misses a required fact is `pending`. Default 15 |
| `--report-inherited` | With `--grade resources`: adds a LOW finding for a required fact found only on the unit |
| `--inventory-out` | `scan` only: also writes the inventory |
| `--expect-scope` | `grade` only: a subscription or project the inventory must hold |

Sign-in:

| Cloud | How |
|---|---|
| Azure | The `DefaultAzureCredential` chain. `--az-cli` uses the Azure CLI account that owns the first `--subscription`. Needs `azure-identity` and `azure-mgmt-resourcegraph`, and Reader on the scope. Calls Resource Graph, and Resource Manager once per subscription for resource-group creation times |
| Google Cloud | Application Default Credentials through `google-cloud-asset`. When `GOOGLE_OAUTH_ACCESS_TOKEN` holds an access token, the scan calls the REST endpoint with it and needs no client library; `--quota-project` names the project billed for the calls. Needs `cloudasset.assets.searchAllResources` on the scope |

## The unit

The contract is carried by a unit, and the unit is what is graded.

| | Azure | Google Cloud |
|---|---|---|
| Unit | resource group | project |
| Unit id in the report | the resource group's resource id, lowercased | the project id |
| Read from | the resource group's tags | the project's labels |
| Key names | compared without regard to case | compared exactly |
| Expiry form | ISO 8601 | `yyyymmddthhmmz` |
| `repository` | graded | not a label; not graded, and listed as such |

Resources are listed under their unit with how many carry tags or labels of
their own. That is information: a resource without its own tags is not a
finding.

## Results

Each unit gets one result.

| Result | Meaning |
|---|---|
| `compliant` | evaluated; no HIGH or MEDIUM finding |
| `non-compliant` | evaluated; at least one HIGH or MEDIUM finding |
| `not-evaluated` | could not be evaluated; the reason is stated |
| `pending` | a required fact is missing and the unit's creation time is known and within the lag |

The compliance percentage is `compliant / (compliant + non-compliant)`. Units
that are `not-evaluated` or `pending` are in neither number and are counted
beside it. An unknown creation time never makes a unit `pending`. On Azure the
creation time of a resource group comes from Resource Manager; on Google
Cloud the project's comes from Cloud Asset Inventory.

## Rules

| Rule id | Severity | Category | Fires when |
|---|---|---|---|
| `missing-required` | HIGH when every required fact is missing, or `project` or `managed_by` is; else MEDIUM | `contract` | a required fact is absent or empty |
| `invalid-value` | MEDIUM | `contract` | `managed_by`, `lifecycle` or `repository` has a value the contract does not accept |
| `expired` | HIGH | `lifecycle` | the expiry is in the past |
| `expiry-unreadable` | MEDIUM | `lifecycle` | the expiry is not a timestamp in the cloud's form |
| `temporary-requires-expires-at` | MEDIUM | `lifecycle` | `lifecycle` is `temporary` and there is no expiry |
| `expiring-soon` | INFO | `lifecycle` | the expiry is within 14 days |
| `protected` | INFO | `lifecycle` | the unit carries `Protected=true` (Azure) or `protected=true` (Google Cloud) |
| `inherited-only` | LOW | `contract` | `--grade resources --report-inherited`: a required fact is on the unit and not on the resource |

Severities and thresholds follow the AWS scanner. Categories `unclaimed`, `missing`
and `schedule` are listed in every report as `not_available`: they are not built.

Every finding carries `evidence`, one entry per fact it rests on:

```json
{"fact": "managed_by", "key": "ManagedBy", "value": "chef", "read_from": "unit"}
```

`value` is `null` and `read_from` is `absent` for a fact that was not found.
`read_from` is otherwise `unit`, `resource` or `registry`.

## What is never graded

| Case | Result |
|---|---|
| A type that cannot carry tags or labels | `--grade resources`: `not-evaluated`. Under a unit: counted as "cannot carry", never as lacking tags |
| A type nothing classifies | `not-evaluated: unclassified type`, listed with its count |
| A unit in a scope the scan is incomplete for | `not-evaluated: incomplete scan` |

Which types can carry:

| Cloud | Source |
|---|---|
| Azure | `data/azure_tag_support.yaml`, generated by `data/build_azure_tag_support.py` from Microsoft's "Tag support for Azure resources" article: 842 types that can, 1776 that cannot. A type in neither list is unclassified. One hand-classified type is marked `unverified` in the file |
| Google Cloud | the `grade` field the adapter writes on each record from `adapters/gcp/resource_types.yaml`, and the document's `unclassified_types` |

## Incomplete scans

A report states at its top what the scan is known to lack, gives no compliance
figure for the affected subscription or project, and gives no overall figure.

| Detected | From |
|---|---|
| A subscription or project that was asked for and not returned | `--subscription`, `--scope projects/<id>`, `--expect-scope` |
| No subscription or project returned at all | the inventory |
| A Google Cloud project number that was not resolved to a project id | the inventory |
| Resources of a subscription or project the scan did not list | the inventory |
| A resource group that resources point at and the inventory does not hold | the inventory |
| A partial result the adapter reports | an `incomplete` list in the inventory document: `[{scope, reason}]` |

Both adapters write the `incomplete` list. Azure: a subscription asked for and
not returned, and a Resource Graph result cut short. Google Cloud: a search
refused with HTTP 403 or 429 (the results read so far stand), and a project
number that was not resolved. Any other adapter error stops the command with a
non-zero exit and no report. A Google Cloud organization or folder in which the
caller may search only some projects returns only those, with no signal.

## Truth file

`verify` compares a report with a truth file: for each unit a test ground was
built with, the result the scan must give and the exact set of rule ids it must
report.

```yaml
schema_version: "1.0"
platform: azure          # azure or gcp; must match the report (`provider:` is read too)
grade: units             # optional: units (default) or resources; must match the report
units:
  - id: "*/resourcegroups/acme-ref-conforming"
    result: compliant    # compliant | non-compliant | not-evaluated | pending
    rules: []            # every rule id expected on the unit, any severity
  - id: "*/resourcegroups/acme-ref-expired"
    result: non-compliant
    rules: [expired]
```

`id` is a shell-style pattern (`*`, `?`, `[...]`) matched without regard to
case against the unit id; it must match exactly one unit. With
`grade: resources` the unit id is the resource id.

`verify` prints one line per mismatch and the counts, and exits 1 when FP or
FN is not zero. "Positive" means the scan reports a problem.

| Count | Counted when |
|---|---|
| TP | an expected rule id is reported on the unit |
| TN | a unit expected `compliant`, `pending` or `not-evaluated` is reported so, with exactly the expected rule ids |
| FP | a rule id is reported that the truth does not list; or the unit is reported `non-compliant` and expected otherwise |
| FN | an expected rule id is not reported; or the result is wrong in any other way; or the pattern matches no unit, or more than one |

Scan a few minutes after the test ground is created. A cloud inventory lists
a new resource with a delay; a unit it has not listed yet is absent from the
report and counts as FN. The `pending` result does not cover it: `pending`
needs a unit that is listed.

Units in the report that no pattern covers are counted in the output and do
not fail the check. `--strict` fails when one of them is `non-compliant`.

## Report

| Key | Content |
|---|---|
| `schema_version`, `platform`, `generated_at`, `graded`, `scopes` | what was scanned, when, and whether units or resources were graded |
| `incomplete` | what the scan lacks: `[{scope, reason}]` |
| `contract` | key of each fact, its carrier (`carrier`: tag or label), required facts, the unit, whether a mapping was applied |
| `summary` | units by result; compliance percentage; the same per subscription or project; findings by severity and category; resources by type with whether the type can carry; coverage per fact |
| `categories` | `contract`, `unclaimed`, `missing`, `lifecycle`, `schedule`, each `available` or `not_available` with the reason |
| `not_evaluated` | units by reason; types that cannot carry; unclassified types; resources that belong to no unit; facts not attached on this platform |
| `units` | `id`, `kind`, `name`, `result`, `reason`, `rules`, `facts`, and the summary of its resources |
| `findings` | `category`, `severity`, `rule`, `unit_id`, `resource_id`, `resource_type`, `location`, `issue`, `recommendation`, `evidence` |
| `resources` | the inventory records |

## Layout

| File | Does |
|---|---|
| `contract.py` | loads the contract and a mapping; resolves one platform |
| `profiles.py` | per-platform grading data; whether a type can carry tags or labels |
| `names.py` | schema 2.0 field and category names, from `adapters/schema.py` where it defines them |
| `grade.py` | inventory in; units, findings and incompleteness out |
| `report.py` | report document and HTML |
| `verify.py` | truth file check |
| `__main__.py` | command line |
| `data/` | Azure tag-support data and the script that generates it |

`contract.py` repeats the loading and mapping rules of `scripts/contract-gen`,
and `grade.py` repeats the rules of the AWS scanner's `tag_compliance.py` and
`lifecycle_compliance.py` against facts. Both are to be consolidated when the
package layout lands.

## Tests

```sh
pytest tests/scan_report
```

Synthetic inventories only, written by `tests/scan_report/build_fixtures.py`.
Each inventory is a truth set with its truth file beside it: one conforming
unit, and units that each break one rule.
