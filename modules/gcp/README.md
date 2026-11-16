# Google Cloud modules

**Status: new.** AWS support is demonstrated and stable; these Google Cloud modules are new and may change between releases.

Two Terraform modules that put a repository's Google Cloud resources under the
tag contract and register where its state lives.

| Module | What it does | Status |
|---|---|---|
| [`compliance-labels`](compliance-labels/) | Renders the contract's facts as labels for `provider "google" { default_labels }` | Built; 22 plan tests |
| [`iac-registry`](iac-registry/) | Writes the repository's registry entry as a JSON object in its state bucket | Built; 9 plan tests against a mocked provider |

Neither module has been applied to a real project in this form.

## How the contract maps to Google Cloud

The contract states facts. On AWS a fact is a tag; on Google Cloud it is a label.

| Fact | Module input | Default label key | AWS tag | Required by default |
|---|---|---|---|---|
| project | `project` | `project` | `Project` | yes |
| environment | `environment` | `environment` | `Environment` | yes |
| managed by | `managed_by` | `managed-by` | `ManagedBy` | yes |
| lifecycle | `lifecycle_value` | `lifecycle` | `Lifecycle` | yes |
| expiry | `expires_at` | `expires-at` | `ExpiresAt` | when lifecycle is `temporary` or `experiment` |
| owner | `owner` | `owner` | optional | no |
| protected | `protected` | `protected` | optional | no |
| schedule | `schedule` | `schedule` | optional | no |
| contact | `contact` of `iac-registry` | not a label | optional | carried by the registry entry |
| cost center | `cost_center` | `cost-center` | optional | no |
| repository | none | not a label | `Repository` | carried by the registry entry |

### Label rules the modules apply

Google Cloud accepts a label only when it meets these rules
([Requirements for labels](https://docs.cloud.google.com/resource-manager/docs/labels-overview)):

| Rule | Google Cloud | What the module does |
|---|---|---|
| Labels per resource | at most 64 | emits at most 8 |
| Key | 1 to 63 characters; lowercase letters, digits, `_`, `-`; starts with a lowercase letter | `key_names` refuses any other key |
| Value | 0 to 63 characters; lowercase letters, digits, `_`, `-` | lowercases, replaces every other character with `_`, cuts to 63 |

Google Cloud also allows international characters in keys and values. The
module does not: it renders ASCII only.

A fact with no value emits no label. A required fact with no value stops the
plan.

### Expiry encoding

An ISO 8601 timestamp contains `:` and uppercase letters, so it cannot be a
label value. `expires_at` takes UTC input, `YYYY-MM-DDThh:mm:ssZ` or
`YYYY-MM-DD`, and renders it as `yyyymmddthhmmz`.

| Input | Label value |
|---|---|
| `2026-12-01T00:00:00Z` | `20261201t0000z` |
| `2027-01-31T23:59:58Z` | `20270131t2359z` |
| `2026-12-01` | `20261201t0000z` |

Seconds are dropped. A timestamp with an offset other than `Z` is refused.

**Decoding rule, for a scanner.** A value is a valid expiry when it matches

```text
^([0-9]{4})([0-9]{2})([0-9]{2})t([0-9]{2})([0-9]{2})z$
```

and the five groups are year, month, day, hour and minute of a real UTC
instant: `20261201t0000z` is `2026-12-01T00:00:00Z`. A value under the expiry
key that does not match, or names an impossible date, is an invalid expiry and
is reported as such; it is never treated as no expiry.
`adapters/gcp/inventory.py` implements the rule as `decode_expires_at`.

### Other keys and other required facts

A deployment that already labels its resources under other keys passes
`key_names`, a map of fact name to label key merged over the defaults, and
`required_facts`, the list of facts that must have a value:

```hcl
key_names = {
  project     = "app_name"
  environment = "env"
  cost_center = "cost_center"
}
required_facts = ["project", "environment", "managed_by", "owner", "cost_center"]
```

Fact names are `project`, `environment`, `managed_by`, `lifecycle`,
`expires_at`, `owner`, `cost_center`, `protected`, `schedule`. A scanner grading that
deployment needs the same two values; the module's `contract` output carries
them.

### Blocking checks

Each of these stops `terraform plan`:

| Condition | Where it fails |
|---|---|
| `lifecycle_value` `temporary` or `experiment` without `expires_at` | `output.default_labels` |
| `managed_by` or `lifecycle_value` outside the allowed values | `output.default_labels` |
| a required fact with no value | `output.default_labels` |
| two facts rendered under one key | `output.default_labels` |
| `expires_at` not UTC, or not a real date | `var.expires_at` |
| a label key that breaks the key rule, or an unknown fact name | `var.key_names`, `var.required_facts` |

## Registry entry

`iac-registry` writes the object `iac-registry/<repo_slug>.json` into the
repository's own state bucket:

```json
{
  "repo_url": "https://git.example/example-org/example-service",
  "iac_type": "terraform",
  "project": "example-app",
  "state_location": {
    "backend": "gcs",
    "bucket": "example-project-example-service-tfstate",
    "prefix": "example-service"
  },
  "environment": "dev",
  "version": "1.0"
}
```

No field changes between applies, so the object is rewritten only when an
input changes.

**The state bucket must carry the label `role=iac-registry`.** That label is
how a scanner finds the buckets that hold entries, through one Cloud Asset
Inventory search (`labels.role:iac-registry` over
`storage.googleapis.com/Bucket`). The module reads the bucket and stops the
plan when the label is missing. Set the label where the bucket is created:

```hcl
resource "google_storage_bucket" "state" {
  name   = "example-project-example-service-tfstate"
  labels = { role = "iac-registry" }
  # location, versioning, uniform_bucket_level_access ...
}
```

`project` in the entry is the same value passed to `compliance-labels`. The
repository URL is not a label on Google Cloud, so a scanner ties a resource to
its repository by matching the resource's `project` label to an entry.

| AWS module | Google Cloud module |
|---|---|
| SSM parameter `/<project>/<environment>/iac-registry/<repo_slug>` | object `iac-registry/<repo_slug>.json` in the state bucket |
| `state_location = { type, bucket, key }` | `state_bucket` + `state_prefix`, written as `state_location = { backend = "gcs", bucket, prefix }` |
| `project`, `environment` in the parameter path | `project`, `environment` in the payload |
| `last_deploy`, `deployer` | not written |
| outputs `ssm_parameter_name`, `ssm_parameter_arn` | outputs `registry_object`, `registry_object_name`, `state_object`, `entry` |

The module needs no API beyond Cloud Storage, which the `gcs` backend already
uses, and no permission beyond writing objects to and reading the metadata of
the state bucket.

## A consumer root

Replace the module source with the repository and release tag you consume.

```hcl
terraform {
  required_version = ">= 1.7.0"

  backend "gcs" {
    bucket = "example-project-example-service-tfstate"
    prefix = "example-service"
  }
}

module "compliance_labels" {
  source = "git::https://github.com/USAREUR-AF/goat-cloud.git//modules/gcp/compliance-labels?ref=v0.1.0"

  project     = "example-app"
  environment = "dev"
  owner       = "platform-team"
}

provider "google" {
  project        = "example-project"
  region         = "us-central1"
  default_labels = module.compliance_labels.default_labels
}

module "iac_registry" {
  source = "git::https://github.com/USAREUR-AF/goat-cloud.git//modules/gcp/iac-registry?ref=v0.1.0"

  repo_slug    = "example-service"
  repo_url     = "https://git.example/example-org/example-service"
  project      = "example-app"
  environment  = "dev"
  state_bucket = "example-project-example-service-tfstate"
  state_prefix = "example-service"
}
```

`compliance-labels` declares no provider and no resource, so the provider
block can read its output directly. The `gcs` backend locks the state itself
and needs no lock table.

`default_labels` reaches every resource type that supports labels through the
provider. Resource types without label support carry none; the inventory
adapter reports them with an empty label set.

## Tests

From either module directory, with Terraform 1.7 or newer:

```sh
terraform init -backend=false
terraform test
```

No credentials are used and no API is called.

## Not built

- A scanner that reads the registry entries and the state they point to. The
  inventory adapter in `adapters/gcp/` lists resources and their labels only.
- Publication of `contract/dist/compliance-contract.gcp.json` to a location on
  Google Cloud; the file is generated and nothing uploads it.
