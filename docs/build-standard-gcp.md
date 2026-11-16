# Build standard for Google Cloud

This standard says how a repository builds infrastructure on Google Cloud so that every resource has an owner, a purpose and a lifetime, and so that a scanner can confirm it without asking anyone. It has two parts: what a platform owner sets up once, and ten rules each repository follows. A reference root that follows every rule and costs cents is in [`reference/gcp/`](../reference/gcp/) and is described at the end of this page.

**Status: new.** AWS support is demonstrated and stable; Google Cloud support is new and may change between releases. The rules are written. The reference root is built and was applied and destroyed in one existing project in project mode. A statement marked *not yet confirmed by a run* has not been exercised; every organization-mode form is in that state.

## Terms

| Term | Meaning |
|---|---|
| Project | The Google Cloud project. It is the unit that has an owner and a lifetime |
| Contract | The facts every project carries: workload, owner, environment, lifecycle, managed-by, and an expiry when the lifecycle is temporary. Their names and allowed values come from one contract file |
| Stack | The resources of one Terraform root. One state, one registry entry |
| Root | A directory on which `terraform apply` is run |
| Platform owner | Whoever holds Owner on the project, or administers the folder or organization above it |
| Organization mode | The platform owner can set policy on a folder or organization |
| Project mode | The platform owner holds one project and nothing above it. Every rule has a project-mode form |

## What the platform owner sets up once

A repository needs five things to exist before its first plan. In organization mode they are set on a folder and inherited by every project in it; in project mode they are set on the project.

| # | Set up once | Organization mode | Project mode | Native mechanism |
|---|---|---|---|---|
| P1 | The project carries the contract | A folder where developers may create projects; a policy that refuses a project created without the contract, in dry run first. *Not yet confirmed by a run.* | The project's owner sets the labels on the existing project: `gcloud projects update <project> --update-labels=workload=...,owner=...,environment=...,lifecycle=...,managed-by=terraform` | project labels [[45]](https://docs.cloud.google.com/resource-manager/docs/creating-managing-labels); custom constraint on projects, Preview [[10]](https://docs.cloud.google.com/organization-policy/reference/custom-constraint-supported-services); dry run [[13]](https://docs.cloud.google.com/organization-policy/test-policies) |
| P2 | Tags for the three facts that policy acts on (`lifecycle`, `environment`, `protected`) | Tag keys and values under the organization, bound to folders and projects; Tag User granted to developers on the values. *Not yet confirmed by a run.* | Tag keys and values under the project, created with the permissions of Owner. Binding them to the project: *not yet confirmed by a run*. Without tags the labels of P1 are the only carrier | resource-manager tags, inherited by every resource below [[2]](https://docs.cloud.google.com/resource-manager/docs/tags/tags-overview) [[4]](https://docs.cloud.google.com/resource-manager/docs/tags/tags-creating-and-managing) |
| P3 | A sign-in path for CI with no stored key | One workload identity pool and GitHub provider in a shared project. *Not yet confirmed by a run.* | The pool and provider in the project itself | workload identity federation [[38]](https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-deployment-pipelines) |
| P4 | A read-only identity for the scanner | A service account with `roles/cloudasset.viewer` on the folder or organization. *Not yet confirmed by a run.* | The same role on the project | Cloud Asset Inventory [[21]](https://docs.cloud.google.com/asset-inventory/docs/reference/rest/v1/TopLevel/searchAllResources) |
| P5 | A budget with alerts | One budget per project, created from the billing account, with rules on actual and forecasted spend. *Not yet confirmed by a run.* | A project-level budget created by the project's owner in the console. *Not yet confirmed by a run.* | Cloud Billing budgets [[7]](https://docs.cloud.google.com/billing/docs/how-to/budgets) |

The platform owner also makes each repository's deploy grants (rule 6): project roles for the services the stack uses, management of buckets by a name prefix, and object access to the stack's state bucket, all to the repository's two workflow files `deploy.yml` and `teardown.yml` run from `main`, and to nothing else. In organization mode two more controls exist that have no project-mode form: a policy that refuses non-compliant resources at creation, and a deny policy that keeps automated removal away from anything tagged `protected`. *Not yet confirmed by a run.*

## The rules a repository follows

Each rule states what to do, why, how it is checked, and which Google Cloud mechanism enforces or reports it. "Plan" means the check stops `terraform plan`. "Scan" means the nightly scan reports it.

**Rule 1. State lives in Cloud Storage buckets in the same project: one bucket for the owner's roots, one for the stack, one prefix per root.**

- *Rule.* Each root uses `backend "gcs"` with `bucket` and `prefix`. A repository has two state buckets:

  | Bucket | Holds | Who can read or write it |
  |---|---|---|
  | owner's, `<slug>-tfstate-<suffix>` | the state of the roots the project's owner applies (`bootstrap`, `platform`) | the project's owners only; no CI identity holds a role on it |
  | stack's, `<slug>-stack-tfstate-<suffix>` | the stack's state and the registry entry (rule 5) | the project's owners; the deploy workflows through object access only (`roles/storage.objectUser` and `roles/storage.legacyBucketReader` on that bucket); the scanner, read only |

  Both buckets have object versioning on, uniform bucket-level access on, public access prevention enforced, and the labels of rule 4. The stack's bucket also carries `role=iac-registry`. The deploy workflows cannot change either bucket's IAM policy, versioning or public access prevention, and cannot read or write the owner's state.
- *Why.* The state is the proof of which resources the repository owns. It also holds secrets, so it stays in the project whose owners may see them. The owner's roots hold the sign-in path and the deploy grants; keeping their state where CI has no access means a CI run cannot read or rewrite it.
- *Location.* The bucket's location is an input with no default. A project under a resource-location policy refuses a bucket anywhere else, with `violates constraint 'constraints/gcp.resourceLocations'` [[12]](https://docs.cloud.google.com/organization-policy/scope-policies).
- *Locking.* The `gcs` backend locks the state for every operation that could write it; no lock table or second service is needed [[39]](https://developer.hashicorp.com/terraform/language/backend/gcs) [[47]](https://developer.hashicorp.com/terraform/language/state/locking).
- *Protection.* Versioning lets a damaged or deleted state be recovered [[39]](https://developer.hashicorp.com/terraform/language/backend/gcs). A bucket that holds state is never created with `force_destroy = true` outside a test.
- *First creation.* The root that creates the buckets starts with local state and moves it into the owner's bucket in the same session with `terraform init -migrate-state`. It is the only root allowed to hold local state, and only until that command has run.
- *Checked by.* Plan: the registry module refuses a bucket without the `role` label. Scan: a bucket with `role=iac-registry` that lacks versioning, uniform access or public access prevention is a finding.
- *Native mechanism.* Cloud Storage bucket settings; Cloud Asset Inventory reports the bucket and its labels [[21]](https://docs.cloud.google.com/asset-inventory/docs/reference/rest/v1/TopLevel/searchAllResources).

**Rule 2. The project carries ownership and lifetime. A repository deploys into a project that already carries the contract, or creates the project with it.**

- *Rule.* Before its first plan the repository has a project whose labels state `workload`, `owner`, `environment`, `lifecycle` and `managed-by`, and `expires-at` when `lifecycle=temporary`. A root reads the project and stops when a required label is missing.
- *Why.* Every resource belongs to exactly one project [[1]](https://docs.cloud.google.com/resource-manager/docs/cloud-platform-resource-hierarchy), cost is reported per project with no setup [[6]](https://docs.cloud.google.com/billing/docs/how-to/reports), and a project can be removed and restored as one unit [[30]](https://docs.cloud.google.com/resource-manager/docs/delete-restore-projects). Labels on single resources are not inherited and many resource types cannot hold one [[2]](https://docs.cloud.google.com/resource-manager/docs/tags/tags-overview).
- *Checked by.* Plan: a precondition on `data.google_project`. Scan: a project missing a required fact is a finding.
- *Native mechanism.* Project labels [[45]](https://docs.cloud.google.com/resource-manager/docs/creating-managing-labels). Organization mode adds refusal at project creation [[10]](https://docs.cloud.google.com/organization-policy/reference/custom-constraint-supported-services).
- *Test setting.* A root may offer `contract_check = "report"` for a project whose labels must not be changed: the plan continues and lists the missing labels. The default stops the plan.
- *Needs above project level.* Creating projects in a folder. *Project-mode form:* use an existing project; its owner sets the labels once (P1).

**Rule 3. Three facts are also tags, so that policy can act on them.**

- *Rule.* Where tag keys exist, the project is bound to a value of `lifecycle` and `environment`, and anything that must never be removed by automation is bound to `protected=true`. The tag value equals the label value.
- *Why.* Only tags are inherited by every resource in the project and only tags can be used in IAM and organization-policy conditions [[2]](https://docs.cloud.google.com/resource-manager/docs/tags/tags-overview) [[14]](https://docs.cloud.google.com/iam/docs/tags-access-control). Labels are the record a person reads; tags are the handle policy holds.
- *Checked by.* Scan: a tag and a label that state different values for the same fact are a finding.
- *Native mechanism.* Tag bindings and inheritance [[2]](https://docs.cloud.google.com/resource-manager/docs/tags/tags-overview).
- *Needs above project level.* Tag keys under the organization. *Project-mode form:* tag keys under the project [[2]](https://docs.cloud.google.com/resource-manager/docs/tags/tags-overview) (binding and inheritance: *not yet confirmed by a run*), or no tags; rule 2's labels then carry these facts alone and tag-conditioned policy is not available.

**Rule 4. Resources are labeled by the provider, with the same keys as the project.**

- *Rule.* The `google` provider block sets `default_labels` to `workload`, `owner`, `environment`, `lifecycle`, `managed-by`, and `expires-at` for a temporary stack. No resource sets these keys by hand. The provider adds one label of its own, `goog-terraform-provisioned=true`.
- *Why.* Resource labels split cost between stacks that share a project, in billing reports and in the billing export [[6]](https://docs.cloud.google.com/billing/docs/how-to/reports) [[26]](https://docs.cloud.google.com/billing/docs/how-to/export-data-bigquery-tables/standard-usage). They are a convenience: ownership is decided by rule 2 and rule 5, never by a resource label.
- *Checked by.* Scan: informational finding for a labelable resource without the labels.
- *Native mechanism.* Labels in Cloud Billing; in organization mode a custom constraint can require a label on types that expose `resource.labels`, such as Compute Engine instances [[11]](https://docs.cloud.google.com/compute/docs/access/custom-constraints).

| Fact | Project label | Tag (rule 3) | Resource label (rule 4) | Inherited by resources |
|---|---|---|---|---|
| owner | `owner` | no | `owner` | no |
| workload | `workload` | no | `workload` | no |
| environment | `environment` | `environment` | `environment` | tag only |
| lifecycle | `lifecycle` | `lifecycle` | `lifecycle` | tag only |
| protected | `protected` | `protected` | no | tag only |
| managed by | `managed-by` | no | `managed-by` | no |
| expiry | `expires-at` | no | `expires-at` | no |
| repository | optional `repo`, informational | no | optional `repo` | registry entry (rule 5) |
| contact, purpose | no | no | no | registry entry (rule 5) |

The keys are the Google Cloud rendering of the contract file, `contract/contract.yaml`: its fact `project` (which workload a resource belongs to) is the label `workload`, because on Google Cloud the word project means the container. `workload`, `owner`, `environment`, `lifecycle` and `managed-by` are required. The project is tied to its repository by the registry entry of rule 5, which lists the project ids the repository deploys into and its URL; a `repo` label is optional and no rule reads it.

Label values are lowercase letters, digits, `_` and `-`, at most 63 characters [[3]](https://docs.cloud.google.com/resource-manager/docs/labels-overview). An expiry is written `yyyymmddthhmmz` in UTC, for example `20261201t0000z`. A deployment may use other key names through one mapping file; the facts do not change.

**Rule 5. The repository registers itself by writing one object beside its state.**

- *Rule.* The stack writes `iac-registry/<slug>.json` into the stack's state bucket. The scanner's identity holds `roles/storage.objectViewer` on that bucket, granted once by the project's owner with the platform setup (P4), so the deploy identity needs no IAM permission on the bucket.

```json
{
  "version": "1.1",
  "repo_url": "https://github.com/example-org/example-service",
  "repo_id": "123456789",
  "iac_type": "terraform",
  "state_location": { "backend": "gcs", "bucket": "example-service-stack-tfstate-1a2b", "prefix": "stack" },
  "scopes": ["example-project"],
  "owner": "payments-team",
  "contact": "payments-team@example.com",
  "purpose": "Order events topic and archive bucket for the payments service",
  "environment": "dev",
  "lifecycle": "temporary",
  "expires_at": "2026-12-01T00:00:00Z",
  "teardown_workflow": "teardown.yml"
}
```

- *Why.* The entry tells the scanner where the state is, so a resource that no registered state claims can be reported. It also holds what a label cannot: a URL, an address, a sentence.
- *Self-service.* Nothing outside the repository changes when it registers. The scanner finds the bucket through its `role=iac-registry` label.
- *Checked by.* Scan: a project with resources and no registry entry is a finding; an entry whose state cannot be read is a finding.
- *Native mechanism.* Cloud Storage objects and bucket-level IAM; discovery through Cloud Asset Inventory search on the label [[21]](https://docs.cloud.google.com/asset-inventory/docs/reference/rest/v1/TopLevel/searchAllResources).

**Rule 6. CI signs in with workload identity federation. No service account key exists.**

- *Rule.* The workflow requests an OIDC token (`permissions: id-token: write`) and exchanges it through the provider of P3. The provider maps `attribute.workflow = assertion.repository_id + '@' + assertion.workflow_ref`; `workflow_ref` is the workflow file and the ref it ran from, `<owner>/<repo>/.github/workflows/<file>@<ref>` [[48]](https://docs.github.com/en/actions/reference/security/oidc). The deploy grants go to two principals, one per workflow file, `principalSet://iam.googleapis.com/projects/<number>/locations/global/workloadIdentityPools/<pool>/attribute.workflow/<id>@<owner>/<repo>/.github/workflows/deploy.yml@refs/heads/main` and the same for `teardown.yml`. The provider's attribute condition admits only the adopter's GitHub organization and repository by numeric id, and only those two `workflow_ref` values, so any other token is refused at sign-in: a run on another ref, a pull request (`refs/pull/<n>/merge`), and any other workflow file on `main`, a `pull_request_target` workflow included. A pull request is checked with no cloud identity: format, validate and the mocked tests. A sign-in from a workflow, and another workflow or ref being refused: *not yet confirmed by a run*
- *Deploy grants.* Project roles only from an allowed list of the roles the stack needs (`roles/pubsub.editor` in the reference root; a role not on the list is refused at plan, and a stack that uses another service adds its role to the list in a reviewed change); a custom role holding `storage.buckets.create`; a custom role to read, change, empty and delete buckets, bound with the condition `resource.name.startsWith("projects/_/buckets/<prefix>")`; and object access to the stack's state bucket (rule 1). No grant includes an IAM permission, `roles/storage.admin` or a Service Usage role.
- *Bucket prefix.* The condition limits the deploy workflows to buckets in this project whose names start with the prefix: a project-level binding reaches only the project's own buckets, and the condition narrows it to the prefix. The prefix is `<slug>_`. The slug holds no underscore, so no other repository's slug, and no state bucket (`<slug>-...`), can produce a name that starts with it; two stacks in one project do not overlap. A prefix that does not start with `<slug>_` is refused at plan.
- *Why.* A key in a secret store can be copied and does not expire. A federated token lasts minutes and is issued only to a workflow of the named repository. Numeric ids cannot be taken over by re-creating a deleted repository name [[38]](https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-deployment-pipelines). Binding the workflow file and the ref keeps code on an unreviewed branch, its edited workflows, and every other workflow of the repository away from the deploy grants and from the state; `main` takes changes by reviewed pull request only.
- *Checked by.* Scan: any user-managed service account key in the project is a finding (asset type `iam.googleapis.com/ServiceAccountKey`).
- *Native mechanism.* Workload identity federation [[38]](https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-deployment-pipelines). Organization mode can add the managed constraint that disables service account key creation.
- *Needs above project level.* Nothing. A shared pool is a convenience of organization mode.

**Rule 7. A root enables the APIs it uses and never disables one.**

- *Rule.* Every API a root calls is a `google_project_service` resource with `disable_on_destroy = false`, in that root or, when the deploy identity holds no Service Usage role, in the platform root. The reference root does the second: `platform` enables the stack's API, `pubsub.googleapis.com`.
- *Why.* An API that is not enabled fails at apply with an error that names the API and not the cause. Disabling an API on destroy breaks every other stack in the project that uses it.
- *Checked by.* Plan and apply. The scan lists enabled APIs as records and never grades them.
- *Native mechanism.* Service Usage.

| Needed by | APIs |
|---|---|
| Terraform itself, enabled by the project's owner before the first plan | `serviceusage.googleapis.com`, `cloudresourcemanager.googleapis.com` |
| Rule 6 | `iam.googleapis.com`, `iamcredentials.googleapis.com`, `sts.googleapis.com` |
| Rules 1 and 5 | `storage.googleapis.com` |
| The scanner, in the project it calls from | `cloudasset.googleapis.com` |
| The stack, enabled by the platform root | each service it creates resources in |

**Rule 8. A temporary stack states its expiry in three places, from one variable.**

- *Rule.* A root with `lifecycle = "temporary"` takes `expires_at` (UTC, RFC 3339) and writes it to the registry entry, to the resource labels through rule 4, and, when the stack is the whole project, to the project label `expires-at`. A plan with `lifecycle = "temporary"` and no `expires_at` stops.
- *Why.* The registry entry is what the teardown reads; the label is what a person sees on the resource and what the billing export carries.
- *Checked by.* Plan: precondition. Scan: an expiry in the past is a finding.
- *Native mechanism.* None in general: Google Cloud has no expiry for resources. A Compute Engine VM may also be given `termination_time` with action `DELETE` [[32]](https://docs.cloud.google.com/compute/docs/instances/limit-vm-runtime).

**Rule 9. Every root that can expire has a teardown workflow, and teardown is `terraform destroy` by the repository itself.**

- *Rule.* The repository has `.github/workflows/teardown.yml`, started by `workflow_dispatch` from `main`, that signs in by rule 6 and runs `terraform destroy` on the stack root; no input chooses another directory. The job runs in the GitHub environment `teardown`, which allows the branch `main` only, with administrator bypass off, and requires a reviewer other than the person who started the run. The registry entry names the file. *Not yet confirmed by a run.*
- *Why.* The tool that created the resources removes them, in the right order, with the repository's own permissions. Nothing outside the repository needs permission to delete.
- *Checked by.* Scan: a temporary entry without `teardown_workflow` is a finding. After an expiry, the next scan confirms the stack is gone.
- *Native mechanism.* None; the dispatch is made through the GitHub API.

**Rule 10. Anything that must survive is protected in the project, not by convention.**

- *Rule.* A project that must not be removed carries a lien. A resource that must not be removed carries `protected=true` as a tag where tags exist, and its service's own deletion protection where one exists.
- *Why.* A lien blocks project deletion for every identity [[31]](https://docs.cloud.google.com/resource-manager/docs/project-liens). A tag is visible to the deny policy that fences automation [[15]](https://docs.cloud.google.com/iam/docs/deny-overview).
- *Checked by.* Scan: `lifecycle=persistent` and `environment=production` without a lien is a finding.
- *Native mechanism.* Liens [[31]](https://docs.cloud.google.com/resource-manager/docs/project-liens); tag-conditioned deny policy [[15]](https://docs.cloud.google.com/iam/docs/deny-overview). *Not yet confirmed by a run.*
- *Needs above project level.* Nothing for the lien. The deny policy is organization mode.

**What needs more than project-level permission**

| Control | Level needed | Project-mode form |
|---|---|---|
| Refuse a project created without the contract (P1) | folder or organization; Preview | owner sets labels; rule 2's plan check and the scan |
| Tag keys shared across projects (P2, rule 3) | organization | tag keys under the project, or labels only |
| A location outside the organization's resource-location policy (rule 1) | organization | none; choose an allowed location |
| Refuse a resource created without labels (rule 4) | organization, for the constraint definition | provider `default_labels` and the scan |
| Budget created by Terraform (P5) | billing account | project-level budget in the console |
| Scanner reads many projects (P4) | folder or organization | one grant per project |
| Shared sign-in pool (P3) | a shared project | pool in the project |
| Deny policy fencing automated deletes (rule 10) | organization, folder, or project with the deny-policy permission | lien and service deletion protection |

## Reference root

The reference root is three small Terraform roots and two workflows in [`reference/gcp/`](../reference/gcp/) that together follow every rule in one existing project. It creates no VM, database or load balancer: one empty Pub/Sub topic, two buckets holding a few kilobytes, one service account, one workload identity pool and IAM bindings. Expected cost is under 0.05 USD for a day.

**Status:** Built. Applied and destroyed in one existing project with the permissions of the Owner role, provider `hashicorp/google` 8.11.0, Terraform 1.11. That run used an earlier form of the deploy grants and the workflows. The table "What a run confirms" lists what the run covers and which statements of the current form it does not.

### Before the first command

| Need | Value |
|---|---|
| Caller | the permissions of Owner on one existing project |
| Tools | Terraform 1.7 or newer, `gcloud` signed in; Terraform reads the sign-in from `GOOGLE_OAUTH_ACCESS_TOKEN=$(gcloud auth print-access-token)` or from application default credentials |
| APIs already on | `serviceusage.googleapis.com`, `cloudresourcemanager.googleapis.com` |
| Project labels (P1), once | `gcloud projects update <project> --update-labels=workload=<name>,owner=<team>,environment=dev,lifecycle=persistent,managed-by=terraform` |
| A location the project may use | `location` has no default; see rule 1 |

### Files

```text
reference/gcp/
  README.md                      order of commands; what each plan shows
  .gitignore                     .terraform/, *.tfstate*, *.tfvars (not *.tfvars.example)
  bootstrap/                     rule 1: the owner's and the stack's state buckets
    versions.tf                  terraform >= 1.7; google >= 6.0; random >= 3.6
    backend.tf.example           copied to backend.tf after the first apply
    variables.tf                 project_id, location, slug, workload, owner, environment, force_destroy, soft_delete_retention_seconds
    main.tf                      random_id.suffix; google_storage_bucket.state["state"] and ["stack_state"]
    outputs.tf                   state_bucket, stack_state_bucket
    tests/buckets.tftest.hcl     one test on mocked providers
    terraform.tfvars.example
  platform/                      P2, P3, P4, rules 6 and 7: what the project's owner sets up once
    versions.tf                  backend "gcs" { prefix = "platform" }
    variables.tf                 project_id, slug, stack_state_bucket, apis, github_org_id, github_repo_id, github_repository, github_workflows, github_deploy_ref, deploy_roles, stack_bucket_prefix, scanner_account_id, tag_mode, bind_project_tags, project_lifecycle, project_environment
    main.tf                      APIs; pool; provider; deploy grants; scanner account and grants
    tags.tf                      tag keys, values and project bindings, when tag_mode = "project-keys"
    outputs.tf                   workload_identity_provider, deploy_principals, scanner_email, tag_keys, stack_bucket_prefix
    tests/identity.tftest.hcl    six tests on mocked providers
    terraform.tfvars.example
  stack/                         rules 2, 4, 5, 7, 8: what a repository does
    versions.tf                  backend "gcs" { prefix = "stack" }
    providers.tf                 provider "google" { default_labels = local.labels }
    variables.tf                 project_id, state_bucket, location, slug, repo_url, repo_id, workload, owner, contact, purpose, environment, lifecycle_value, expires_at, teardown_workflow, contract_check
    contract.tf                  data.google_project.this; labels; check "project_contract"
    main.tf                      example topic; example bucket
    registry.tf                  registry object with four preconditions
    outputs.tf                   registry_object, labels, topic, bucket, project_contract_missing
    tests/contract.tftest.hcl    nine plan-only tests on mocked providers
    terraform.tfvars.example
  cases/                         seven free resources with a known scan result each
    main.tf                      six Pub/Sub topics, one service account
  truth.yaml                     the result a scan must report for each case
  .github/workflows/
    deploy.yml                   rule 6: checks with no cloud identity on pull request; plan and apply on main
    teardown.yml                 rule 9: workflow_dispatch from main, environment teardown, terraform destroy of stack
```

The label and registry logic is inline in `stack/contract.tf` and `stack/registry.tf`. The modules `modules/gcp/compliance-labels` and `modules/gcp/iac-registry` render resource labels and a version 1.0 entry; they do not yet render the `workload` key, the project-level check or the version 1.1 entry fields, so the reference root does not call them.

### Resources and what `terraform plan` shows

| Root | Resources | Plan shows |
|---|---|---|
| `bootstrap` | `random_id.suffix`; `google_storage_bucket.state["state"]`, the owner's bucket `<slug>-tfstate-<suffix>`, and `["stack_state"]`, the stack's bucket `<slug>-stack-tfstate-<suffix>`: each with versioning on, uniform bucket-level access, public access prevention `enforced`, labels `owner`, `workload`, `environment`, `lifecycle=persistent`, `managed-by=terraform`; the stack's bucket also `role=iac-registry` | `3 to add` |
| `platform` | `google_project_service.required` for `iam`, `iamcredentials`, `sts`, `storage`, `cloudasset`, `pubsub` (6, each `disable_on_destroy = false`); `random_id.pool`; `google_iam_workload_identity_pool.github` (id `<slug>-gh-<suffix>`); `google_iam_workload_identity_pool_provider.github` (issuer `https://token.actions.githubusercontent.com`; mapping of `google.subject`, `attribute.repository_id`, `attribute.repository_owner_id`, `attribute.workflow`; condition on the organization id, the repository id and the two `workflow_ref` values); `google_project_iam_member.deploy` for `roles/pubsub.editor`; `google_project_iam_custom_role.stack_bucket_create` and `.stack_buckets` with their bindings, the second with the name-prefix condition; `google_storage_bucket_iam_member.deploy_state_objects` (`roles/storage.objectUser`) and `.deploy_state_bucket_read` (`roles/storage.legacyBucketReader`) on the stack's state bucket; each deploy binding once per workflow principal of rule 6 (5 bindings × 2 workflows); `google_service_account.scanner`; `google_project_iam_member.scanner_asset_viewer` (`roles/cloudasset.viewer`); `google_storage_bucket_iam_member.scanner_state_read` (`roles/storage.objectViewer` on the stack's state bucket) | `24 to add` |
| `platform`, `tag_mode = "project-keys"` | adds 3 `google_tags_tag_key` (`lifecycle`, `environment`, `protected`, parent `projects/<project>`), 8 `google_tags_tag_value`, and 2 `google_tags_tag_binding` of the project unless `bind_project_tags = false` | 13 more, or 11 without the bindings |
| `stack` | `random_id.bucket`; `google_pubsub_topic.example`; `google_storage_bucket.example` (name `<slug>_example-<suffix>`, inside the prefix the deploy identity may manage; uniform access, public access prevention, `force_destroy = true`, soft-delete retention 0, because it is temporary); `google_storage_bucket_object.registry` (`iac-registry/<slug>.json`) | `4 to add`; output `labels` holds the six keys of rule 4 |

An API that is already on still counts as a resource to add; applying it changes nothing in the project.

### Checks the roots contain

| Check | Where | Fails when |
|---|---|---|
| Project carries the contract (rule 2) | `check "project_contract"` in `stack/contract.tf` and a `precondition` on `google_storage_bucket_object.registry` | any of `workload`, `owner`, `environment`, `lifecycle`, `managed-by` is absent from the project's labels |
| Temporary needs an expiry (rule 8) | `validation` on `var.expires_at`; `precondition` on the registry object | `lifecycle_value = "temporary"` and `expires_at` is null, not UTC, or not a real date |
| State bucket is a registry bucket (rule 1) | `precondition` on the registry object | the bucket lacks `role=iac-registry` |
| Label values are legal (rule 4) | `locals` normalize; `precondition` | a value is empty after normalization |
| Repository URL carries no credential (rule 5) | `validation` on `var.repo_url` | the URL is not `https://host/path` or `git@host:path`, or holds a user name, password or token |
| Bucket prefix belongs to this repository (rule 6) | two `precondition`s on the bucket binding in `platform` | `stack_bucket_prefix` does not start with `<slug>_`, or matches the stack's state bucket |
| Deploy roles are on the allowed list (rule 6) | `validation` on `var.deploy_roles` in `platform` | a role is not on the list of roles the stack needs |
| Only the deploy workflows sign in (rule 6) | `attribute_condition` of the provider in `platform` | applied by Google Cloud at sign-in, not at plan: a token whose `workflow_ref` is not `deploy.yml` or `teardown.yml` on `github_deploy_ref` is refused |

`contract_check = "report"` is a test-only setting for a project whose labels must not be changed. The plan continues, the missing labels appear as a check warning and in the output `project_contract_missing`. The default is `enforce`.

### Order of commands

| Step | Command, in the named directory | Result |
|---|---|---|
| 1 | `bootstrap`: `terraform init`; `terraform apply -var force_destroy=true -var soft_delete_retention_seconds=0` | 3 added; outputs `state_bucket` (the owner's) and `stack_state_bucket` |
| 2 | `bootstrap`: `cp backend.tf.example backend.tf`; `terraform init -migrate-state -backend-config=bucket=<state_bucket>`; delete the empty local `terraform.tfstate` and its backup | object `bootstrap/default.tfstate` in the bucket |
| 3 | `platform`: `terraform init -backend-config=bucket=<state_bucket>`; `terraform apply -var stack_state_bucket=<stack_state_bucket>` | 24 added |
| 4 | `stack`: `terraform init -backend-config=bucket=<stack_state_bucket>`; `terraform plan` in a project without the labels of P1 | plan stops: `Rule 2: the project lacks the contract labels: ...` |
| 5 | `stack`: `terraform apply -var lifecycle_value=temporary -var expires_at=<tomorrow, UTC>` | 4 added |
| 6 | `gcloud storage cat gs://<stack_state_bucket>/iac-registry/<slug>.json` | the JSON of rule 5 |
| 7 | `gcloud pubsub topics describe <slug>-example` | the six labels of rule 4 and `goog-terraform-provisioned` |
| 8 | `gcloud asset search-all-resources --scope=projects/<project> --query="labels.role:iac-registry"` | the stack's state bucket |
| 9 | Optional: `platform` with `-var tag_mode=project-keys` | 13 more added |
| 10 | `stack`: `terraform destroy`. `platform`: `terraform destroy`. `bootstrap`: delete `backend.tf`, `terraform init -migrate-state`, `terraform destroy` | the project holds no resource of the three roots; enabled APIs stay on |
| 11 | `gcloud projects update <project> --remove-labels=workload,owner,environment,lifecycle,managed-by`, when the project had no contract before | project labels as before |

`force_destroy = true` and a soft-delete retention of 0 on the state buckets are test settings: a versioned bucket cannot otherwise be destroyed in step 10, and a deleted bucket is otherwise kept for the soft-delete period. A real state bucket leaves both at their defaults.

After step 10 Google Cloud keeps one thing by design: the deleted workload identity pool, in state `DELETED`, for 30 days ([source](https://docs.cloud.google.com/iam/docs/manage-workload-identity-pools-providers)).

### What a run confirms

| Rule | Shown by | Confirmed by a run |
|---|---|---|
| 1 | `bootstrap`; the owner's bucket holding `bootstrap` and `platform` state, the stack's bucket holding `stack` state and the registry entry; migration into and out of the owner's bucket | one bucket holding all three roots under three prefixes, and the migration: yes. The two-bucket form, and a CI run refused on the owner's bucket: not yet confirmed by a run |
| 1, location | a bucket in a location the project's resource-location policy does not allow is refused with `violates constraint 'constraints/gcp.resourceLocations'` | yes |
| 2 | the plan stops on a project without the contract labels; `contract_check = "report"` lists the five missing labels | the refusal and report mode: yes. A plan that passes in `enforce` mode on a labeled project: not yet confirmed by a run |
| P2, 3 | 3 tag keys and 8 values created under the project and removed | keys and values: yes. Binding the project and inheritance by its resources: not yet confirmed by a run |
| P3, 6 | pool, provider, deploy grants to the `principalSet` of each deploy workflow on the main branch: `roles/pubsub.editor`, the two custom bucket roles, object access to the stack's state bucket | pool and provider, creation and removal: yes. The `attribute.workflow` mapping and the condition on repository and `workflow_ref`, another workflow on `main` being refused, the custom roles and the `<slug>_` name-prefix condition, the state-bucket grants, a stack plan and apply with these grants only, a sign-in from a workflow, a run on another ref being refused, and enabling `sts.googleapis.com`: not yet confirmed by a run |
| P4 | scanner account, `roles/cloudasset.viewer` on the project, `roles/storage.objectViewer` on the stack's state bucket | grants made by the stack root: yes. The same grant made by `platform`, and a scan run as that account: not yet confirmed by a run |
| P5 | project-level budget in the console | not yet confirmed by a run |
| 4 | topic and bucket carry `workload`, `owner`, `environment`, `lifecycle`, `managed-by`, `expires-at` from `default_labels` | yes |
| 5 | registry object with the version 1.1 fields | yes |
| 7 | APIs declared with `disable_on_destroy = false`; the enabled-API set is the same before and after destroy | yes, with `pubsub.googleapis.com` declared in `stack`. Declared in `platform` instead: not yet confirmed by a run |
| 8 | `expires-at = yyyymmddthhmmz` on resources, RFC 3339 in the registry entry; an inventory scan decodes the label to the same instant | yes |
| 9 | `teardown.yml`: stack root only, environment `teardown` | not yet confirmed by a run; the destroy it performs was run from a laptop with an earlier form of the grants |
| 10 | lien | not yet confirmed by a run |
| Contract, per resource | `cases/` applied; a per-resource scan compared with `truth.yaml` by `python -m goatcloud verify`: 5 expected findings reported, 3 resources correctly without a finding, 0 false positives, 0 false negatives. The inventory listed the new topics within about a minute of creation, not at once | yes |
| After destroy | project IAM policy, project labels, enabled APIs, service accounts and buckets equal to their state before the first step | yes |
