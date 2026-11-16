# Resource contract

`contract.yaml` is the single definition of what every resource states about
itself, on every platform: which facts exist, which are required, which values
are accepted, what carries each fact and under which key. Every other copy in
this repository is generated from it.

The contract is at version **1.0**. [Compatibility](#compatibility) and
[Versioning](#versioning) state what a later version may change.

## Facts

A required fact is resolvable for every resource through its unit: it is
present on the unit the contract attaches to on that platform (see
[Renderings](#renderings)), and the resources inside the unit inherit it.

| Fact | Required | Accepted values |
|---|---|---|
| `project` | yes | any |
| `environment` | yes | any; a deployment may list the accepted names |
| `managed_by` | yes | `terraform`, `opentofu`, `pulumi`, `serverless`, `cdk`, `cloudformation`, `sam`, `copilot`, `bicep`, `arm`, `infrastructure-manager`, `ansible`, `helm`, `manual` |
| `lifecycle` | yes | `persistent`, `temporary`, `experiment`, `deprecated` |
| `expires_at` | when `lifecycle` is `temporary` or `experiment` | a UTC timestamp in the form of the platform's encoding |
| `repository` | yes, except where a rendering sets its own required set | `https://host/path` or `git@host:path`, with no user name, password or token |
| `owner` | no, except on Google Cloud | a team or role name |
| `contact` | no | a team address or a channel |
| `cost_center` | no | any |
| `protected` | no | `true` |
| `schedule` | no | any |

- **`managed_by`** has one list for every platform, and its values compare
  without regard to case: `Terraform` and `terraform` are the same tool. Every
  Python reader (the AWS scanner, `scripts/compliance-lint`, `goatcloud`)
  compares it so, and the Terraform modules lowercase it before they check it.
  `manual` is an accepted value and is not an infrastructure-as-code tool, so
  a registry entry accepts every value except `manual`.
- **`owner` and `contact`** hold no personal information. `owner` is a team or
  role name, never a person's name. `contact` is a team address or a channel,
  never a person's own address.
- **`protected`** marks a resource that enforcement never removes.
- **`environment`** carries `protected_values` (`prod`, `production`): the
  environments whose resources enforcement never removes.

### Rules

| Rule | Meaning |
|---|---|
| `temporary-requires-expires-at` | a resource with `lifecycle` `temporary` states `expires_at` |
| `experiment-requires-expires-at` | a resource with `lifecycle` `experiment` states `expires_at` |

### Expiry

`expires_at` matches the pattern of the encoding its platform renders it with.

| Encoding | Pattern | Example |
|---|---|---|
| `iso8601` (AWS, Azure) | `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?Z$` | `2026-12-01T00:00:00Z` |
| `compact-utc` (Google Cloud) | `^\d{8}t\d{4}z$` | `20261201t0000z` |

A value outside the pattern is reported as a finding and is never ignored. The
Terraform modules refuse it. A scanner still reads a date alone, or a
timestamp without a zone, as UTC, so a resource with such a value expires.

### Repository

The value matches `^(https://[^/@:]+(:[0-9]+)?/[^@]+|git@[^/:@]+:[^@]+)$`: a
URL that carries a user name, a password or a token is refused, because the
tag and the registry entry are readable by every reader of the scope. The
registry modules refuse such a `repo_url` at plan.

The canonical form of a repository value is `https://<host>/<path>`: https
scheme, lowercase host, no user name, no trailing `.git`, no trailing slash.
`normalize_repository()` in the generated Python returns it, and
`same_repository()` compares two values after normalizing both. Use them
wherever a tag value is compared with a registry entry:
`git@host:org/repo.git` and `https://host/org/repo` name the same repository.

## Renderings

A rendering says, for one platform, what the contract attaches to and where
each fact is written.

| Entry | Meaning |
|---|---|
| `unit` | what the contract attaches to: `resource` (AWS), `resource-group` (Azure), `project` (Google Cloud). The required facts are present on the unit; resources inside it inherit them |
| `carrier` | what holds a fact, by default for every fact of the rendering. A fact may set its own |
| `key_prefix` | optional text put before every key; none by default |
| `required` | optional: the required facts on this platform, in place of the `required` marks of the facts |
| `facts` | each fact as `{key, encoding}`, with an optional `carrier` |

The known carriers are `tag`, `label`, `annotation`, `resource-manager-tag`,
`custom-field`, `registry-entry` and `inventory-record`. The list is open: a
carrier this version does not know is a note, never an error. A fact without a
key on the resource is written `{key: null, carrier: registry-entry}`: the
registry entry of its repository carries it.

| Fact | AWS tag | Azure tag | Google Cloud |
|---|---|---|---|
| `project` | `Project` | `Project` | label `workload` |
| `environment` | `Environment` | `Environment` | label `environment` |
| `managed_by` | `ManagedBy` | `ManagedBy` | label `managed-by` |
| `lifecycle` | `Lifecycle` | `Lifecycle` | label `lifecycle` |
| `expires_at` | `ExpiresAt` | `ExpiresAt` | label `expires-at` |
| `repository` | `Repository` | `Repository` | registry entry |
| `owner` | `Owner` | `Owner` | label `owner` |
| `contact` | `Contact` | `Contact` | registry entry |
| `cost_center` | `CostCenter` | `CostCenter` | label `cost-center` |
| `protected` | `Protected` | `Protected` | label `protected` |
| `schedule` | `Schedule` | `Schedule` | label `schedule` |

On Google Cloud a label named `project` reads as the Google Cloud project
itself, so the fact `project` is written under the label key `workload`. The
Google Cloud rendering sets its own required set: `project`, `environment`,
`managed_by`, `lifecycle` and `owner`. The repository is not a label there;
the registry entry carries it.

Google Cloud label keys and values allow only lowercase letters, digits, `_`
and `-`, up to 63 characters (encoding `gcp-label`). A URL, an address or an
ISO 8601 timestamp cannot be a label value, so the expiry has a compact form
there and the repository and the contact live in the registry entry.

## Registry entry

Each repository writes one registry entry (the `iac-registry` module of its
platform). Beside the repository URL, the tool and the state location, an entry
has four optional fields:

| Field | Meaning |
|---|---|
| `owner` | the team or role accountable for what the repository deploys |
| `contact` | where to reach it: a team address or a channel |
| `lifecycle` | the lifecycle of what the repository deploys, one of the `lifecycle` values (module variable `lifecycle_value`) |
| `expires_at` | when it expires, in the platform's expiry form; required when `lifecycle` requires an expiry |

`lifecycle` defaults to `persistent` and is always written; `expires_at` is
written when set. The expiry teardown decides from these two fields, and a
changed input (an extended expiry) rewrites the entry. The modules refuse a `lifecycle` value
outside the contract, an `expires_at` outside the platform's pattern, and a
`lifecycle` that requires an expiry without one (rules
`temporary-requires-expires-at`, `experiment-requires-expires-at`), and a
`repo_url` with user info. The AWS scanner reads the
four fields (`modules/aws/shared/iac_registry.py`, copied into the scanner and teardown packages);
an absent, empty or non-text field reads as none.

The contact for a notice about a resource is resolved in this order: the
contact tag on the resource, then the `contact` of the registry entry of the
resource's repository, then the administrators.

## Published contract

`scripts/contract-gen` writes one JSON file per platform into
`contract/dist/`. These fields are normative and have the same meaning on
every platform:

| Field | Content |
|---|---|
| `version` | the contract's `schema_version` |
| `platform` | the platform the file is for |
| `unit` | what the contract attaches to on the platform |
| `key_prefix` | the text before every key; empty when none |
| `required_facts` | the facts every resource states, resolvable through its unit |
| `keys` | fact to key, for every fact written on the resource |
| `carriers` | fact to what holds it |
| `allowed` | fact to its only accepted values |
| `patterns` | fact to the regular expression its value matches |
| `rules` | the rules, each with `id`, `when` (`fact`, `equals`) and `require` |
| `protected_values` | fact to the values enforcement never acts on (`environment`) |

`allowed`, `rules` and `protected_values` are equal in all three files.
`unit`, `keys`, `carriers`, `patterns.expires_at` and, where a rendering sets
its own, `required_facts` differ by platform.

### Deprecated fields (AWS file only)

`compliance-contract.aws.json` also carries the field names it had before
version 1.0. They are deprecated; read the normative fields instead. The Azure
and Google Cloud files do not carry them.

| Deprecated field | Read instead |
|---|---|
| `required_tags` | `required_facts` through `keys` |
| `valid_managed_by` | `allowed.managed_by` |
| `valid_lifecycle` | `allowed.lifecycle` |
| `tag_validation` | `allowed` and `patterns` through `keys` |

## Compatibility

- An allowed-value list grows in a minor version. A reader written for 1.0
  meets values it does not list.
- An unknown value is a finding in the scanner and in the lint, never a crash.
- An unknown carrier, and an entry of the contract or of the published file a
  reader does not know, is a note and is ignored, never an error.
- Readers accept the earlier names of renamed entries: `kind` for `carrier`,
  `cloud` for `platform` in `targets.yaml`, `--cloud` for `--platform`.
  Nothing in this repository writes the earlier names.

## Generated files

`scripts/contract-gen` writes the files listed in `targets.yaml` from
`contract.yaml`. Do not edit them.

| File | Read by |
|---|---|
| `modules/aws/compliance-tags/contract.generated.tf` | the AWS `compliance-tags` module, as its built-in contract |
| `modules/aws/hunterkiller/contract.generated.tf` | the enforcement module, for the protected tag key and the protected environments |
| `modules/azure/compliance-tags/contract.generated.tf` | the Azure `compliance-tags` module |
| `modules/gcp/compliance-labels/contract.generated.tf` | the Google Cloud `compliance-labels` module |
| `modules/{aws,azure,gcp}/iac-registry/iac_type.generated.tf` | each `iac-registry` module: the `iac_type` variable and its accepted values, and the lifecycle values and expiry form an entry accepts |
| `modules/aws/shared/compliance_contract.py` | `compliance_schema.py`, and through it the scanner, dashboard and enforcement Lambdas |
| `contract/dist/compliance-contract.{aws,azure,gcp}.json` | whatever publishes the contract on that platform; the AWS environment root reads the AWS file |
| `scripts/compliance-lint`, between the `GENERATED CONTRACT` markers | the lint, as its fallback when no published contract is reachable |

The generated Python module holds the constants and six functions:
`normalize_repository`, `same_repository`, `parse_expires_at`,
`expires_at_is_valid`, `is_allowed` and `expiry_required`.

To generate the contract for another module, add an entry to `targets.yaml`
with the file path, the kind of file and the platform:

```yaml
  - path: modules/azure/another-module/contract.generated.tf
    kind: hcl-locals
    platform: azure
```

An `hcl-iac-type-variable` entry also takes an optional `description` and
`default` for the variable.

Each Lambda build copies `compliance_contract.py` into its archive beside
`compliance_schema.py`.

## Changing the contract

1. Edit `contract.yaml`.
2. Run `scripts/contract-gen`.
3. Commit `contract.yaml` and the generated files together.

`scripts/contract-gen --check` exits 1 and prints a diff when a generated file
differs from what `contract.yaml` produces. It runs in pre-commit and in the
`Contract Check` workflow on every pull request.

The generator needs Python 3 and PyYAML.

## Deployment mapping

A deployment that already uses other key names, holds a fact somewhere else,
requires a different set of facts or has its own environment names keeps a
mapping file. A mapping does these things and nothing else:

- it renames the key of a fact on a platform, or sets a `key_prefix`;
- it changes what holds a fact on a platform (`carrier`), with the `encoding`
  when the fact gains a key;
- it replaces the required set, for every platform or for one platform; a
  top-level set replaces every platform's set, including one the contract
  states per platform;
- it sets the accepted environment names (`environment.allowed`) and the
  protected ones (`environment.protected_values`).

Facts, the accepted values of every other fact, patterns, encodings and rules
always come from `contract.yaml`. A mapping that names an unknown fact or
platform, reuses a key, or breaks a platform's label limits is refused.

```yaml
contract_schema_version: "1.0"   # must have the major version of contract.yaml

required:                        # optional: the required facts on every platform
  - project
  - environment
  - managed_by
  - lifecycle
  - repository
  - owner

environment:                     # optional
  allowed: [dev, staging, live]
  protected_values: [live]

renderings:
  aws:
    facts:
      cost_center: {key: BillingCode}
  gcp:
    required:                    # optional: the required facts on this platform only
      - project
      - environment
      - managed_by
      - owner
      - cost_center
    facts:
      project: {key: app}
      environment: {key: stage, carrier: resource-manager-tag}
      cost_center: {key: billing_code}
      contact: {key: team-channel, carrier: label, encoding: gcp-label}
```

`mapping.example.yaml` holds this example. Apply a mapping when producing the
contract a deployment publishes:

```sh
scripts/contract-gen --mapping path/to/mapping.yaml --out-dir build/contract
```

This writes `compliance-contract.<platform>.json` for each platform into the
output directory (`--platform aws` limits it to one; `--cloud` is the
deprecated earlier name of the flag). The generated files inside this
repository never carry a mapping; they always hold the contract as written.

A deployment's own mapping file belongs in that deployment's repository.

## Versioning

`schema_version` is `major.minor`. A reader of the published contract accepts
any minor version of the major version it was written for and ignores fields
it does not know.

| Change | Version |
|---|---|
| A new optional fact | minor |
| A new accepted value of a fact | minor |
| A new optional field of the published contract or of the registry entry | minor |
| A new platform rendering, a new encoding, a new carrier | minor |
| A new entry a deployment mapping may set | minor |
| Removing or renaming a fact | major |
| Making a fact required, or adding a rule | major |
| Removing an accepted value | major |
| Tightening a pattern, or changing the canonical repository form | major |
| Changing the key, carrier, encoding or unit of a fact on a platform | major |
| Removing or renaming a normative field of the published contract | major |
| Removing the deprecated fields of the AWS file | major |

## Related

- [`focus-mapping.md`](focus-mapping.md): how the report fields correspond to
  FOCUS columns.
- [`report-schema-2.0.md`](report-schema-2.0.md): the field and category names
  of report schema 2.0.
