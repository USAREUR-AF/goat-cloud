# Report schema

Every scan writes one JSON document that holds every resource found, every finding, and cost per service and region. This page describes that document. The machine-readable definition is `modules/aws/shared/compliance_schema.py`, which the scanner, the dashboard and enforcement all package; its `validate_report(data)` returns a list of problems, empty when the document is valid, and the scanner runs it before it stores a report.

The document described here is schema version `1.0`, which the AWS scanner writes. Schema `2.0`, described at the end, renames the identifying fields so that one document can describe a resource on any cloud; the Azure and Google Cloud inventory adapters already write `2.0` records.

## Where reports are stored

| Object | Key in the report bucket |
|---|---|
| JSON report | `reports/<YYYY-MM-DD>/compliance-report.json` |
| Markdown report | `reports/<YYYY-MM-DD>/compliance-report.md` |
| HTML report | `reports/<YYYY-MM-DD>/compliance-report.html` |
| First report of each month, kept as the month's baseline | `baselines/<YYYY-MM>.json` |

The bucket is `<project>-<environment>-compliance-reports`. Objects expire after `report_retention_days` (default 90).

## Top level

| Key | Type | Description |
|---|---|---|
| `report_version` | string | `"1.0"` |
| `generated_at` | string | ISO 8601 time the scan finished |
| `scan_duration_seconds` | number | wall-clock duration |
| `account_id` | string | AWS account id |
| `project`, `environment` | string | the scanner's own `project` and `environment` |
| `regions_scanned` | list of string | regions the scan covered |
| `regions_with_resources` | list of string | regions in which a resource was found |
| `errors` | list of string | non-fatal errors; each check runs independently, so one failure does not stop the others |
| `summary` | object | counts; see below |
| `discovered_resources` | list of object | every resource; see below |
| `tag_compliance`, `drift_detection`, `schedule_compliance`, `lifecycle_compliance` | object | `{"findings": [...]}`; `drift_detection` also has `terraform_managed` and `unmanaged` counts |
| `service_costs` | object | billed spend per service over the trailing 30 days, keyed by Cost Explorer service name |
| `region_costs` | object | billed spend per region over the same period |
| `resource_type_counts` | object | resource type to count |
| `region_type_counts` | object | region to resource type to count |
| `iac_registry` | object | `entries`: one per registered repository with `repo_slug`, `repo_url`, `iac_type`, `last_deploy`, `resource_count`; `total_external_managed` |
| `multi_state_detection` | object | `state_files` and `lock_tables`: other Terraform state files and lock tables found in the account |
| `other_iac_analysis` | list of object | per registered state: how many of its resources were found |
| `protected_resources` | object | the protected list as configured, and how many findings it annotated |
| `termination_events` | list of object | recent terminate and delete events from CloudTrail |

### `summary`

```json
{
  "total_resources_discovered": 152,
  "total_findings": 29,
  "findings_by_severity": {"HIGH": 16, "MEDIUM": 9, "LOW": 2, "INFO": 2},
  "findings_by_category": {"tag_compliance": 10, "drift_detection": 8, "schedule_compliance": 4, "lifecycle_compliance": 7},
  "excluded_resources": 61
}
```

## Resource

Each entry of `discovered_resources`:

| Field | Type | Description |
|---|---|---|
| `arn` | string | the resource's ARN |
| `resource_type` | string | `service:kind`, for example `ec2:instance`, `rds:instance`, `s3:bucket` |
| `name` | string | the `Name` tag, or the name the service gives |
| `region` | string | AWS region, or `global` |
| `state` | string or null | the service's own state word, for example `running`, `available` |
| `tags` | object | tags as found |
| `estimated_monthly_cost` | number or null | three-state cost; see below |
| `instance_type`, `instance_class` | string | for EC2 and RDS |

### Cost values

| Value | Meaning |
|---|---|
| number above 0 | estimated monthly rate from the scanner's price model |
| `0` | free to keep (IAM roles, security groups, certificates, stopped instances) |
| `null` | no price model; the cost depends on usage (SNS, SQS, API Gateway, CloudTrail) |

A consumer must test for `null` before arithmetic: `cost is not None and cost > 0`.

| Estimated from | Types |
|---|---|
| instance type or class, on-demand rate | EC2 instances, RDS instances (doubled for multi-AZ), ECS services (Fargate vCPU and memory) |
| size | EBS volumes, S3 buckets, EFS file systems, ECR repositories, log groups |
| a fixed monthly rate | NAT gateways, load balancers, ElastiCache clusters, unattached Elastic IPs, secrets, KMS keys, DynamoDB tables, Route 53 zones |

Data transfer, request charges, log ingestion and other usage charges are not attributed to a resource. They appear in `service_costs`.

## Finding

Every finding has these fields:

| Field | Type | Description |
|---|---|---|
| `category` | string | `tag_compliance`, `drift_detection`, `schedule_compliance` or `lifecycle_compliance` |
| `severity` | string | `HIGH`, `MEDIUM` or `LOW`; lifecycle findings for an expiry within 14 days use `INFO`, which the dashboard leaves out of its finding counts |
| `resource_arn` | string | the resource |
| `resource_type`, `resource_name`, `region`, `state` | | as on the resource |
| `issue` | string | the problem, in one sentence |
| `recommendation` | string | what to do |
| `tags` | object | the resource's tags (`existing_tags` on missing-tag findings) |
| `estimated_monthly_cost` | number or null | three-state cost |
| `created_at`, `created_by`, `running_duration_days` | | age, and the creator when CloudTrail has the event |

| Category | Extra fields |
|---|---|
| `tag_compliance` | `missing_tags` |
| `drift_detection` | `confidence` (equal to `severity`) |
| any, when the resource matches the protected list | `protected: true`, `protection_category`, `protection_reason` |
| any, when a registered repository's state claims the resource | `managed_by_repo_slug` |

Severity rules are in [concepts](../concepts.md#findings).

## Changes between two reports

`modules/aws/shared/delta.py` compares two reports. `compute_delta(today, yesterday)` returns `new_findings`, `resolved_findings`, `severity_changes`, and a `summary` with counts and `burn_delta`, the change in the estimated monthly cost of resources with findings. The nightly email and the dashboard's changes view both use it.

## Enforcement state record

The enforcement notifier writes one item per warned resource to the table `<project>-<environment>-hunterkiller-state`.

| Field | Description |
|---|---|
| `resource_arn` | key |
| `batch_id` | time of the scan batch, `YYYYMMDDTHHMMSS` |
| `status` | `warned`, `enforcing`, `torn_down`, `resolved`, `protected`, `error`, `dry_run`, `aborted` |
| `warned_at`, `grace_deadline` | ISO 8601 |
| `contact_email`, `contact_source` | who was warned, and whether the address came from a tag, an IAM user or the administrator list |
| `resource_type`, `resource_name`, `region`, `estimated_monthly_cost`, `issues`, `tags`, `scan_date` | copied from the findings |
| `approval_status` | `pending`, `approved`, `expired` |
| `approved_by`, `approved_at` | the administrator's address, or `auto:expired`; ISO 8601 |
| `ttl_expires` | when the item is removed (`state_ttl_days`, default 30) |

## Schema 2.0

In `2.0` a resource is identified by the cloud's own canonical id, and the document says which cloud and scope it covers.

| 1.0 | 2.0 | Values |
|---|---|---|
| `arn`, `resource_arn` | `resource_id` | AWS ARN; Google Cloud full resource name; Azure resource id, lowercased |
| | `provider` | `aws`, `gcp`, `azure` |
| `account_id` | `scope` | account id; project id; subscription id |
| `region` | `location` | the cloud's own region or location name |
| `resource_type` | `resource_type` | `ec2:instance`; `compute.googleapis.com/Instance`; `microsoft.compute/virtualmachines` |
| `tags` | `tags` | tags or labels as found |

| Writer | Schema | Status |
|---|---|---|
| AWS scanner | 1.0 | Built |
| AWS scanner, dashboard and enforcement on 2.0 | 2.0 | Designed; AWS reports keep `arn` beside `resource_id` during the change |
| `adapters/azure/inventory.py`, `adapters/gcp/inventory.py` | 2.0 resource records | Written |
