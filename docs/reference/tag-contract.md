# Tag contract

The contract is the set of facts a resource states about itself, the values each fact accepts, and how each fact is written on each platform. This page summarizes it for readers; [`contract/README.md`](../../contract/README.md) is the normative definition.

The contract has one source file, `contract/contract.yaml`. The Terraform defaults in each tag module, the Python constants the scanner uses, the JSON published to each cloud and the lint's built-in fallback are generated from it by `scripts/contract-gen`, and `scripts/contract-gen --check` fails in CI when a generated copy differs. The tables on this page are written from the same file.

## Facts

| Fact | Required | Accepted values |
|---|---|---|
| `project` | yes | any |
| `environment` | yes | any; `prod` and `production` are protected from enforcement |
| `managed_by` | yes | `terraform`, `opentofu`, `pulumi`, `serverless`, `cdk`, `cloudformation`, `sam`, `copilot`, `bicep`, `arm`, `infrastructure-manager`, `ansible`, `helm`, `manual`; compared without regard to case |
| `lifecycle` | yes | `persistent`, `temporary`, `experiment`, `deprecated` |
| `expires_at` | when `lifecycle` is `temporary` or `experiment` | a UTC timestamp, in the form of the platform's encoding |
| `repository` | yes, except on Google Cloud | matches `^(https://\|git@)` |
| `owner` | on Google Cloud only | a team or a role, never a person |
| `contact` | no | a team address or a channel, never a person's address |
| `cost_center` | no | any |
| `protected` | no | `true` |
| `schedule` | no | any |

`manual` is an accepted `managed_by` value and is not an infrastructure-as-code tool. A registry entry accepts every `managed_by` value except `manual`.

On Google Cloud the required facts are `project`, `environment`, `managed_by`, `lifecycle` and `owner`: a label cannot hold a URL, so `repository` is carried by the registry entry instead.

## Unit and carrier

Each platform names the unit the contract attaches to and the carrier that holds a fact on it. Resources inside the unit inherit its facts.

| Platform | Unit | Carrier |
|---|---|---|
| AWS (`aws`) | resource | tag |
| Azure (`azure`) | resource group | tag |
| Google Cloud (`gcp`) | project | label; `repository` and `contact` in the registry entry |

## Lifecycle values

| Value | Meaning | Checks that apply |
|---|---|---|
| `persistent` | Meant to exist until someone decides otherwise | exempt from the schedule check |
| `temporary` | Has an end date | must carry an expiry; reported when past it; a whole temporary stack is torn down at its expiry |
| `experiment` | Short-lived trial | must carry an expiry; schedule check applies |
| `deprecated` | Being retired | schedule check applies |

## Rule

| Rule | When | Requires |
|---|---|---|
| `temporary-requires-expires-at` | `lifecycle` is `temporary` | `expires_at` |
| `experiment-requires-expires-at` | `lifecycle` is `experiment` | `expires_at` |

## Renderings

A rendering gives the key name and the value encoding of each fact on one cloud.

| Fact | AWS tag | Azure tag | Google Cloud label |
|---|---|---|---|
| `project` | `Project` | `Project` | `workload` (`project` would read as the Google Cloud project) |
| `environment` | `Environment` | `Environment` | `environment` |
| `managed_by` | `ManagedBy` | `ManagedBy` | `managed-by` |
| `lifecycle` | `Lifecycle` | `Lifecycle` | `lifecycle` |
| `expires_at` | `ExpiresAt`, ISO 8601 | `ExpiresAt`, ISO 8601 | `expires-at`, compact UTC |
| `repository` | `Repository`, URL | `Repository`, URL | not a label; the registry entry carries it |
| `owner` | `Owner` | `Owner` | `owner` |
| `contact` | `Contact` | `Contact` | not a label; the registry entry carries it |
| `cost_center` | `CostCenter` | `CostCenter` | `cost-center` |
| `protected` | `Protected` | `Protected` | `protected` |
| `schedule` | `Schedule` | `Schedule` | `schedule` |

| Encoding | Form | Example |
|---|---|---|
| text | the value as written | `acme` |
| URL | a repository URL as written | `https://github.com/acme/example-repo` |
| ISO 8601 | a UTC timestamp | `2030-12-01T00:00:00Z` |
| compact UTC | lowercase `yyyymmddthhmmz` | `20301201t0000z` |
| label | lowercase; each character outside `a-z`, `0-9`, `_`, `-` becomes `_`; at most 63 characters | `platform_team` |

Google Cloud label keys match `^[a-z][a-z0-9_-]{0,62}$` and values match `^[a-z0-9_-]{0,63}$`.

## Other tags the tooling reads on AWS

The enforcement and the scanner on AWS also read these tags, some of them in more forms than the contract states. None is required.

| Tag | Read by | Effect |
|---|---|---|
| `Name` | scanner, dashboard | display name |
| `Service`, `Subcomponent`, `Purpose` | dashboard, reports | grouping, attributed cost, context in findings |
| `Protected` | enforcement | `true`, `yes` or `1` exempts the resource from single-resource deletion |
| `Contact` | enforcement notifier | the address warnings go to; `contact_tag_keys` sets which keys are read, in order (default `Contact`) |
| `Schedule`, `AutoStop`, `AutoShutdown`, `BusinessHours`, `OffHours` | scanner | the presence of any one satisfies the schedule check |

## Deployment mapping

A deployment that already uses other key names, holds a fact somewhere else, requires a different set of facts or has its own environment names keeps a mapping file. A mapping renames the key of a fact on a platform or sets a `key_prefix`, changes the `carrier` that holds a fact, replaces the required set (for every platform or for one), and sets the accepted and protected environment names. Facts, the accepted values of every other fact, patterns, encodings and rules always come from `contract.yaml`. [`contract/README.md`](../../contract/README.md#deployment-mapping) gives every field; `contract/mapping.example.yaml` is a complete example.

```yaml
contract_schema_version: "1.0"

required:
  - project
  - environment
  - managed_by
  - lifecycle
  - repository
  - owner

renderings:
  aws:
    facts:
      cost_center: {key: BillingCode}
  gcp:
    facts:
      project: {key: app}
      environment: {key: stage}
```

```sh
scripts/contract-gen --mapping path/to/mapping.yaml --out-dir build/contract
```

This writes `compliance-contract.<platform>.json` for each platform. A mapping that names an unknown fact or platform, reuses a key, or breaks a cloud's key limits is refused.

## Published contract

The contract a deployment publishes for its repositories and CI jobs is one JSON document. On AWS it is the SSM parameter `/<project>/<environment>/compliance-contract`.

```json
{
  "version": "1.0",
  "platform": "aws",
  "unit": "resource",
  "key_prefix": "",
  "required_facts": ["project", "environment", "managed_by", "lifecycle", "repository"],
  "keys": {"project": "Project", "environment": "Environment", "managed_by": "ManagedBy", "lifecycle": "Lifecycle", "expires_at": "ExpiresAt", "repository": "Repository", "owner": "Owner", "contact": "Contact", "cost_center": "CostCenter", "protected": "Protected", "schedule": "Schedule"},
  "carriers": {"project": "tag", "environment": "tag", "managed_by": "tag", "lifecycle": "tag", "expires_at": "tag", "repository": "tag", "owner": "tag", "contact": "tag", "cost_center": "tag", "protected": "tag", "schedule": "tag"},
  "allowed": {
    "managed_by": ["terraform", "opentofu", "pulumi", "serverless", "cdk", "cloudformation", "sam", "copilot", "bicep", "arm", "infrastructure-manager", "ansible", "helm", "manual"],
    "lifecycle": ["persistent", "temporary", "experiment", "deprecated"],
    "protected": ["true"]
  },
  "patterns": {"expires_at": "^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}(:\\d{2})?Z$", "repository": "^(https://|git@)"},
  "rules": [{"id": "temporary-requires-expires-at", "when": {"fact": "lifecycle", "equals": "temporary"}, "require": ["expires_at"]}, {"id": "experiment-requires-expires-at", "when": {"fact": "lifecycle", "equals": "experiment"}, "require": ["expires_at"]}],
  "protected_values": {
    "environment": ["prod", "production"]
  },
  "required_tags": ["Project", "Environment", "ManagedBy", "Lifecycle", "Repository"],
  "valid_managed_by": ["terraform", "opentofu", "pulumi", "serverless", "cdk", "cloudformation", "sam", "copilot", "bicep", "arm", "infrastructure-manager", "ansible", "helm", "manual"],
  "valid_lifecycle": ["persistent", "temporary", "experiment", "deprecated"],
  "tag_validation": {
    "ManagedBy": {"enum": ["terraform", "opentofu", "pulumi", "serverless", "cdk", "cloudformation", "sam", "copilot", "bicep", "arm", "infrastructure-manager", "ansible", "helm", "manual"]},
    "Lifecycle": {"enum": ["persistent", "temporary", "experiment", "deprecated"]},
    "ExpiresAt": {"pattern": "^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}(:\\d{2})?Z$"},
    "Repository": {"pattern": "^(https://|git@)"},
    "Protected": {"enum": ["true"]}
  }
}
```

`required_facts`, `allowed`, `patterns` and `rules` name facts; `keys` maps each fact to its key on that platform in that deployment, so a consumer finds a renamed key by its fact name. The AWS document also carries the fields it had before 1.0 (`required_tags`, `valid_managed_by`, `valid_lifecycle`, `tag_validation`), which use key names. [`contract/README.md`](../../contract/README.md) is the normative description of every field.

| Consumer | How it reads the contract |
|---|---|
| `modules/aws/compliance-tags` | the SSM parameter when `use_ssm_contract = true`; its generated built-in copy otherwise |
| `scripts/compliance-lint` | `--contract-ssm`, `--contract-s3`, or its generated built-in copy |
| scanner, dashboard, enforcement | the generated Python constants packaged with each Lambda; `required_tags` can be overridden per deployment |
