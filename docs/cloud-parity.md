# Capability parity across clouds

AWS, Azure and Google Cloud get the same capabilities from goat-cloud, each through the mechanism that cloud offers. The contract, the finding categories, the severities and the report shape are the same everywhere; what differs is the unit the contract attaches to and how each capability is delivered.

The tables on this page are generated from [`parity.yaml`](../parity.yaml) by `scripts/parity-gen`. `scripts/parity-gen --check` runs in CI and fails when a table differs from the data.

## Status

Built means the code is complete and usable today. Written means the document or code exists and is not yet in use end to end. Designed means the approach is decided and no code exists.

<!-- BEGIN GENERATED: summary -->
| Capability | AWS | Azure | Google Cloud |
|---|---|---|---|
| Unit the contract attaches to | Built | Written | Written |
| Declare ownership | Built | Written | Written |
| Register a repository and its state | Built | Written | Written |
| List every resource | Built | Built | Built |
| Decide which types are graded | Built | Built | Built |
| Grade against the contract and report | Built | Built | Built |
| Ownership from registered state | Built | Designed | Designed |
| Cost | Built | Designed | Designed |
| Tear down an expired unit | Built | Designed | Designed |
| Delete one resource, with approval | Built | Designed | Designed |
| Refuse at creation | Designed | Designed | Designed |
<!-- END GENERATED: summary -->

## Mechanism per cloud

<!-- BEGIN GENERATED: mechanisms -->
| Capability | AWS | Azure | Google Cloud |
|---|---|---|---|
| Unit the contract attaches to | The resource. Every resource carries the contract's tags. | The resource group. The group carries the tags and is removed as a whole. | The project. The project carries the labels and is removed as a whole. |
| Declare ownership | Tags from the provider's `default_tags`, produced by `modules/aws/compliance-tags`. | Tags on the resource group and on each resource, produced by `modules/azure/compliance-tags`. | Labels from the provider's `default_labels`, produced by `modules/gcp/compliance-labels`. |
| Register a repository and its state | SSM parameter `/<project>/<environment>/iac-registry/<slug>`, written by `modules/aws/iac-registry`. | Blob `iac-registry/<slug>.json` beside the state, in a storage account tagged `role=iac-registry`. | Object `iac-registry/<slug>.json` beside the state, in a bucket labelled `role=iac-registry`. |
| List every resource | Resource Groups Tagging API, plus per-service listing for untagged resources. | Azure Resource Graph, over subscriptions or a management group. Checked read-only against a live subscription. | Cloud Asset Inventory `searchAllResources`, over an organization, folder or project. Checked read-only against a live project. |
| Decide which types are graded | Exclusion rules for resources the cloud creates by itself, and a list of types that cannot carry tags. | Every listed resource is graded; a resource is satisfied by its resource group's tags. | A class per type (`resource`, `resource-unlabelable`, `record`); only resources are graded. |
| Grade against the contract and report | Nightly scanner Lambda. Report format 1.0 as JSON, Markdown and HTML. | `python -m goatcloud scan --platform azure`. Report format 2.0 as JSON and HTML. | `python -m goatcloud scan --platform gcp`. Report format 2.0 as JSON and HTML. |
| Ownership from registered state | Terraform state and Pulumi checkpoints in S3; CloudFormation stacks. | Registry blobs and state in Azure Storage. | Registry objects and state in Cloud Storage. |
| Cost | Cost Explorer for billed spend, plus price tables for a monthly estimate per resource. | Cost Management query API. | Cloud Billing export in BigQuery. |
| Tear down an expired unit | Dispatch the owning repository's `teardown.yml` at the stack's expiry. | The same dispatch; the workflow removes the resource group. | The same dispatch; the workflow removes the project. |
| Delete one resource, with approval | An executor role whose deletes are allowed only on resources not tagged `Protected` and not in a protected environment (all types but Route 53), and denied on reserved and protected IAM principals. | An executor role limited to named scopes and a short list of delete operations. | An executor role limited to named projects and a short list of delete permissions. |
| Refuse at creation | An AWS Organizations tag policy rendered from the contract. | An Azure Policy initiative rendered from the contract. | Organization policy custom constraints rendered from the contract. |
<!-- END GENERATED: mechanisms -->

## Where a cloud's mechanism is weaker

Each row names a place where one cloud cannot do what another does, and the step that makes up for it.

<!-- BEGIN GENERATED: compensating -->
| Capability | Cloud | Where the mechanism is weaker, and what to do |
|---|---|---|
| Declare ownership | Azure | The provider has no default tags and a resource does not inherit its group's tags. Assign the built-in policy "Inherit a tag from the resource group" once per tag name. |
| Declare ownership | Google Cloud | A label value cannot hold a URL or an ISO 8601 timestamp. The repository URL is carried by the registry entry, and the expiry is written as `yyyymmddthhmmz`. |
| Decide which types are graded | Google Cloud | A large share of inventory records are not deployed resources. A type with no class is graded as a resource and listed as unclassified; add it to `adapters/gcp/resource_types.yaml`. |
| Grade against the contract and report | Azure | Run it from a scheduled CI job with workload identity federation; a scheduled runtime in the cloud is designed. |
| Grade against the contract and report | Google Cloud | Run it from a scheduled CI job with workload identity federation; a scheduled runtime in the cloud is designed. |
| Cost | Google Cloud | The export exists only when the adopter has enabled it; without it, cost is reported as unknown. |
| Delete one resource, with approval | Azure | A role assignment cannot be conditioned on tags for most resource types. Limit the role by scope and by operation list. |
| Delete one resource, with approval | Google Cloud | IAM cannot be conditioned on labels for most resource types. Limit the role by scope and by permission list. |
| Refuse at creation | Google Cloud | Custom constraints cover only the services that support them; the scan reports what they do not cover. |
<!-- END GENERATED: compensating -->

## What is the same on every cloud

| | |
|---|---|
| Facts | project, environment, managing tool, lifecycle, repository, expiry; optional owner, contact, cost center |
| Accepted values | one list of tools and one list of lifecycles for every cloud |
| Finding severities | HIGH, MEDIUM, LOW, and INFO for notices |
| Report format | 2.0: `resource_id`, `provider`, `scope`, `location`, `resource_type`, `tags` |
| Availability | a finding category a cloud does not support is reported as not available for that cloud, with the reason; it is never left out without a statement |

A deployment that already uses other key names maps the contract's facts to them and is graded on its own keys. See [reference/tag-contract.md](reference/tag-contract.md#deployment-mapping).

## Per cloud

| Cloud | Build standard | Guide |
|---|---|---|
| AWS | [build-standard-aws.md](build-standard-aws.md) | [quickstart-aws.md](quickstart-aws.md) |
| Azure | [build-standard-azure.md](build-standard-azure.md) | [guides/add-a-cloud.md](guides/add-a-cloud.md#scan-a-subscription-or-a-project) |
| Google Cloud | [build-standard-gcp.md](build-standard-gcp.md) | [guides/add-a-cloud.md](guides/add-a-cloud.md#scan-a-subscription-or-a-project) |
