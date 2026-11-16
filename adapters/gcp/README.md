# Google Cloud inventory adapter

`inventory.py` lists every resource in a Google Cloud organization, folder or
project with its labels, through one read-only API: Cloud Asset Inventory
[`searchAllResources`](https://docs.cloud.google.com/asset-inventory/docs/reference/rest/v1/TopLevel/searchAllResources).
It writes nothing to the cloud.

| Piece | Status |
|---|---|
| Paging, record conversion, project id resolution, `expires-at` decoding, grades, command line | Built; 119 unit tests on hand-written fixtures |
| A run against a real project | Done over REST through the `fetch_page` hook, with page sizes 500 and 50; the `google-cloud-asset` transport has not made a live call |
| Registry, state, cost and action adapters | Not built |

## Use

```sh
pip install google-cloud-asset pyyaml
python -m adapters.gcp.inventory --scope projects/example-project --out inventory.json
```

| Option | Meaning |
|---|---|
| `--scope` | `organizations/<number>`, `folders/<number>`, `projects/<id>` or `projects/<number>` |
| `--out` | file to write; `-` or omitted writes to standard output |
| `--asset-type` | limit to one asset type, for example `storage.googleapis.com/Bucket`; repeatable |
| `--query` | a `searchAllResources` query, for example `labels.role:iac-registry` |
| `--page-size` | 1 to 500 (the API's maximum, and the default) |
| `--max-pages` | stop the search after this many pages and report it in `incomplete` (default 10000) |

From Python:

```python
from adapters.gcp import inventory

document = inventory.discover("folders/222222222222")
records = document["resources"]
```

Without the client library, or with a token from another source, pass the REST
transport, which uses the standard library only:

```python
fetch = inventory.rest_page_fetcher(access_token, quota_project="example-project")
document = inventory.discover("projects/example-project", fetch)
```

The REST transport sends the token in a header urllib never copies onto a
redirected request, and by default refuses every redirect. The default
transport uses Application Default Credentials. The module is
self-contained: the standard library, PyYAML to read `resource_types.yaml`,
and `google-cloud-asset`, which is imported only when a real call is made.

## What the caller needs

| Need | Value |
|---|---|
| API enabled | Cloud Asset API, `cloudasset.googleapis.com`, in the project the call is made from (the credential's quota project), not in every scanned project |
| Permission on the scope | `cloudasset.assets.searchAllResources` |
| Permission on the calling project | `serviceusage.services.use` |
| Predefined roles that carry them | `roles/cloudasset.viewer` on the scope; `roles/serviceusage.serviceUsageConsumer` on the calling project |

## Record

One record per resource, in report schema 2.0. `adapters/schema.py` defines the record and document and validates them.

| Field | Source in `ResourceSearchResult` | Notes |
|---|---|---|
| `resource_id` | `name` | the full resource name, `//compute.googleapis.com/projects/example-project/zones/us-central1-a/instances/web-1` |
| `platform` | constant | `"gcp"` |
| `scope` | `name`, `project` | the project id; see below |
| `location` | `location` | `global`, a multi-region (`eu`), a region or a zone; `null` when the resource reports none |
| `resource_type` | `assetType` | `compute.googleapis.com/Instance` |
| `name` | `displayName` | the last segment of `resource_id` when the resource has no display name |
| `tags` | `labels` | the labels exactly as found; `{}` when the resource has none |
| `state` | `state` | the resource's own vocabulary (`RUNNING`, `ACTIVE`, ...); `null` when absent |
| `created_at` | `createTime` | RFC 3339, to the second; `null` when absent |
| `estimated_monthly_cost` | none | always `null` (unknown): this adapter reads no cost source |
| `grade` | `assetType` | `labels+ownership`, `ownership` or `none`; see Grades |

`discover()` and the command line return the document

```json
{
  "schema_version": "2.0",
  "platform": "gcp",
  "generated_at": "2026-01-15T08:30:00+00:00",
  "scopes": [{"scope": "example-project", "scope_type": "project", "name": "Example", "state": "ACTIVE", "tags": {}}],
  "unclassified_types": {},
  "resources": []
}
```

`scopes` has one entry per project the resources belong to. Its `name`,
`state` and `tags` come from the project's own asset and are `null`, `null`
and `{}` when the scan did not return that asset.

The document keys other than `unclassified_types`, and the record fields from
`resource_id` to `estimated_monthly_cost`, have the same names and meanings as
in `adapters/azure/inventory.py`. `grade` and `unclassified_types` exist in
this adapter only. The Azure records also carry `kind`,
`container`, `inherited_tags` and `provisioning_state`, which have no
Google Cloud counterpart in this adapter: labels are not inherited from a
project or folder.

### `scope`: project id, not project number

The API's `project` field is always `projects/<number>`. The record's `scope`
is the project id, established without any further API call:

1. **From the resource name.** Most names carry the id:
   `//compute.googleapis.com/projects/example-project/...`. A project id
   starts with a letter and a project number is all digits, so a
   `projects/<segment>` that is not all digits is the id.
2. **From another result in the same scan.** Some names carry the number
   (`//secretmanager.googleapis.com/projects/123456789012/secrets/...`) and
   some carry no project (`//storage.googleapis.com/<bucket>`). Any result that
   shows both the id in its name and the number in `project` gives the pair,
   and so does a `cloudresourcemanager.googleapis.com/Project` result through
   `additionalAttributes.projectId`. All pages are read before records are
   built, so the pair can come from any page.
3. **From the scope.** With `--scope projects/<id>` every result is in that
   project.

4. **From one more search, under a filter only.** With `--asset-type` or
   `--query`, the results can show a project's number and nothing that gives
   its id (a scan of buckets alone, for example). The adapter then searches
   the same scope once more for `cloudresourcemanager.googleapis.com/Project`
   and takes the pairs from those results. This costs one extra request per
   500 projects in the scope, and is made only when a record would otherwise
   carry the number. The project results are used for the map and are not
   added to the records.

About half of the names in a typical project carry the number or no project
(enabled APIs, buckets, log buckets and sinks, API keys, App Engine versions),
so step 2 does most of the work. The project's own
`cloudresourcemanager.googleapis.com/Project` asset names the project by its
id and is returned by a scan of the project, its folder or its organization.

When none of these yields the id, `scope` is the project number as a string.
A resource that belongs to no project (a folder, an organization) has its
`organizations/<number>`; when the result names no organization, `scope` is
`""`.

### `expires-at`

`tags` keeps the expiry label as found. `decode_expires_at("20261201t0000z")`
returns the UTC datetime `2026-12-01T00:00:00Z`, and raises `ValueError` for
any value that is not `yyyymmddthhmmz` or that names an impossible date. The
rule is stated in [`modules/gcp/README.md`](../../modules/gcp/README.md).

## Grades

The search returns every supported asset. Many are not things a team deploys,
and many deployed types cannot carry labels. `resource_types.yaml` gives each
asset type a class, and each record gets the grade of its type's class:

| Class | Meaning | `grade` |
|---|---|---|
| `resource` | deployed, and the type has a labels field | `labels+ownership` |
| `resource-unlabelable` | deployed, but the type has no labels field (firewall rules, networks, service accounts) | `ownership` |
| `record` | not a deployed resource: an enabled API, a backup, a key, an image digest, a version, per-project settings | `none` |

A rule that checks labels reads only records graded `labels+ownership`; a rule
that checks whether a repository owns the resource reads those and `ownership`.
Records graded `none` are listed and never graded.

A type that is not in the file is graded as `resource`, and the document counts
it under `unclassified_types` (asset type to number of records), so a gap in
the file shows in every scan that meets it.

The file lists 93 types. Labelability comes from each type's schema in
Google's API discovery documents; the entries classified without that check
carry an `unverified` comment in the file. The file has not been compared
with a live scan.

## What the API does not return

- **Asset types outside the search API.** Only the types Cloud Asset Inventory
  supports are returned, and some supported types are excluded from search:
  the [asset type list](https://docs.cloud.google.com/asset-inventory/docs/asset-types)
  marks them "Not available in the analysis and search APIs", for example
  `cloudbuild.googleapis.com/Build`, `k8s.io/Secret`,
  `cloudresourcemanager.googleapis.com/TagBinding` and the `iap.googleapis.com`
  types. A resource of such a type is absent from the inventory.
- **Labels on types that have none.** `labels` is present only when the
  resource type has a labels field. A resource of a type without one comes
  back with `tags: {}`; the API does not say which case it is, which is what
  `grade` records.
- **Location, state, creation time and display name** for types whose own API
  has no such field. The record holds `null`, or the name fallback above.
- **Objects inside buckets.** The only Cloud Storage asset type is the bucket.
- **Tags.** Resource Manager tags (`tags`, `effectiveTags`) are a separate
  mechanism from labels. The adapter reads labels only.
- **A live view.** Cloud Asset Inventory is eventually consistent
  ([overview](https://docs.cloud.google.com/asset-inventory/docs/asset-inventory-overview)):
  a resource created, changed or deleted shortly before the call can be
  missing or stale.

`searchAllResources` is a synchronous call that counts against the Cloud Asset
API quota of the calling project; a large organization takes one call per 500
resources.

## Incomplete results

The document's `incomplete` list states what the inventory is known to lack,
as `{scope, reason}` entries; `scope` is `null` when the whole inventory is
affected. It is empty when nothing is known to lack.

| Reported | When |
|---|---|
| a project number | a resource shows the number and nothing in the results gives the project id |
| `null` | the search was refused with HTTP 403 (the caller cannot search the scope) or 429 (throttled). The results read before the refusal are in the document |

Any other error raises and no document is returned. An organization or folder
in which the caller may search only some projects returns only those, with no
signal from the API.
