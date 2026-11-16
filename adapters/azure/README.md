# Azure inventory adapter

`inventory.py` lists every resource in a set of subscriptions, or under a
management group, through Azure Resource Graph, and returns report schema 2.0
records. It is read-only. It makes two kinds of call: the Resource Graph query,
`POST /providers/Microsoft.ResourceGraph/resources`, and one Resource Manager
list of resource groups per subscription,
`GET /subscriptions/<id>/resourcegroups`, for their creation time. The Reader
role on the subscription covers both.
`adapters/schema.py` defines the record and document and validates them.

It is a self-contained module. Registry, state, cost and action adapters for
Azure are not built.

## Run

```sh
pip install azure-identity azure-mgmt-resourcegraph
python -m adapters.azure.inventory --subscription 00000000-0000-0000-0000-000000000000 --out inventory.json
python -m adapters.azure.inventory --management-group example-mg --out inventory.json
```

`--subscription` and `--management-group` repeat. Sign-in is the
`DefaultAzureCredential` chain (environment, workload identity, managed
identity, Azure CLI). `--az-cli` signs in as the Azure CLI account that owns
the first `--subscription`, for a workstation where the CLI holds more than one
account. The identity needs read access to the resources; Reader on the
subscription or management group is enough.

From Python:

```python
from adapters.azure import inventory

document = inventory.discover(inventory.sdk_page_fn(), subscriptions=["00000000-0000-0000-0000-000000000000"])
```

## What it reads

| Table | Rows | Used for |
|---|---|---|
| `resources` | every resource | the records |
| `resourcecontainers`, type `microsoft.resources/subscriptions/resourcegroups` | resource groups | a record per group, and `inherited_tags` |
| `resourcecontainers`, type `microsoft.resources/subscriptions` | subscriptions | the `scopes` list |

Each query asks for 1000 rows, the Resource Graph maximum, and follows
`$skipToken` until the response carries none. Every query projects `id` and
orders by it, because Resource Graph returns no `$skipToken` for a query whose
output columns are all dynamic or that uses `limit`, `take` or `sample`. A
response that reports fewer rows than `totalRecords` and no `$skipToken` is
reported in the document's `incomplete` list.

## Incomplete results

The document's `incomplete` list states what the inventory is known to lack,
as `{scope, reason}` entries; `scope` is `null` when the whole inventory is
affected. It is empty when nothing is known to lack.

| Reported | When |
|---|---|
| a subscription id | it was asked for and Resource Graph returned no subscription row for it: no read access, or it does not exist |
| `null` | a query returned fewer rows than `totalRecords` with no `$skipToken`, set `resultTruncated`, or returned a `$skipToken` with an empty page. The rows read so far are in the document |
| `null` | a query reached the page cap (`--max-pages`, `max_pages`; default 10000 pages) with pages left |
| a subscription id | its Resource Manager resource group list reached the page cap |

An HTTP error from Resource Graph raises and no document is returned.

The Resource Manager token is sent only to `https://management.azure.com/`,
in a header urllib never copies onto a redirected request, and the default
transport refuses every redirect.
`query_rows` called without an `incomplete` list raises `InventoryError`
instead of reporting.

## Record

```json
{
  "resource_id": "/subscriptions/00000000-0000-0000-0000-000000000000/resourcegroups/example-app-rg/providers/microsoft.compute/virtualmachines/example-vm",
  "platform": "azure",
  "scope": "00000000-0000-0000-0000-000000000000",
  "location": "westeurope",
  "resource_type": "microsoft.compute/virtualmachines",
  "name": "Example-VM",
  "kind": null,
  "container": "example-app-rg",
  "tags": { "project": "example", "Owner": "platform-team" },
  "inherited_tags": { "Project": "example", "Environment": "dev" },
  "state": "deallocated",
  "provisioning_state": "Succeeded",
  "created_at": "2030-01-02T03:04:06.0000000Z",
  "estimated_monthly_cost": null
}
```

| Field | Source | Notes |
|---|---|---|
| `resource_id` | `id` | Lowercased. Azure resource ids are case-insensitive and Resource Graph returns them in mixed case |
| `scope` | `subscriptionId` | |
| `location` | `location` | `global` for resources with no region |
| `resource_type` | `type` | Lowercased |
| `name` | `name` | As found |
| `container` | `resourceGroup` | Lowercased, as the `resources` table returns it |
| `tags` | `tags` | As found, with the names in the case they were written. Empty when the resource has none. Azure tag names are case-insensitive, so a consumer compares them without regard to case; `tag_coverage` does |
| `inherited_tags` | the resource group's `tags` | Not merged into `tags`. Azure does not copy them to the resource; a grader decides whether they count |
| `state` | see below | `null` for most types |
| `provisioning_state` | `properties.provisioningState` | The result of the last deployment operation (`Succeeded`, `Failed`), not a run state |
| `created_at` | `properties.timeCreated`, else `properties.creationTime`; for a resource group, `createdTime` from Resource Manager | `null` for the types that expose neither |
| `estimated_monthly_cost` | none | `null`: no cost source is configured |

### State

Resource Graph has no common run-state field. The adapter returns one for the
two types where the `resources` table carries it:

| Type | Source | Example values |
|---|---|---|
| `microsoft.compute/virtualmachines` | `properties.extended.instanceView.powerState.code`, prefix `PowerState/` removed, lowercased | `running`, `deallocated`, `stopped` |
| `microsoft.compute/disks` | `properties.diskState`, lowercased | `attached`, `unattached` |

The virtual machine power state is not part of the machine's own properties in
Azure Resource Manager; Resource Graph adds it under `properties.extended`.
For every other type `state` is `null`.

### Creation time

Azure has no creation time common to all resource types in the `resources`
table. Compute resources (virtual machines, disks, snapshots) expose
`properties.timeCreated`, and storage accounts expose `properties.creationTime`.
Types that expose neither return `null`.

Resource Graph holds no creation time for a resource group. The adapter reads
it from Resource Manager: `GET /subscriptions/<id>/resourcegroups` with
`$expand=createdTime` (API version 2021-04-01), following `nextLink`, once per
subscription the scan returned. Each group in the response carries
`createdTime`. Microsoft's reference for this operation does not list
`$expand`; it documents `createdTime` under `$expand` for the resources list
of the same API version. A group whose row carries no `createdTime` gets
`null`, and so does every group of a subscription where Resource Manager
refuses the list with HTTP 403. `discover()` makes the call only when given
`arm_get`; the command line always passes it.

## Tests

`tests/azure_adapter/` runs against hand-written responses in the REST shape
and needs no Azure package:

```sh
pytest tests/azure_adapter
```
