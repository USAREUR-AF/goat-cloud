# Azure reference root

**Status: new.** AWS support is demonstrated and stable; this Azure reference root is new, has been applied and destroyed once in a live subscription, and may change between releases.

A minimal Terraform example that follows every rule of the
[build standard for Azure](../../docs/build-standard-azure.md). It
creates 27 resources in three roots, no virtual machine, and costs under one
cent a month while it exists.

| Root | Who applies it | When | Needs | Resources |
|---|---|---|---|---|
| [`platform/`](platform/) | Platform owner | Once per subscription | Owner | 12 |
| [`bootstrap/`](bootstrap/) | Repository team, by hand | Once per repository | Owner | 12 |
| [`stack/`](stack/) | CI, or a person with the operator role assignment | Every deployment | Contributor on the stack's resource group, data access to the state container, Reader on the state account | 4, one of them the group adopted from `bootstrap/` |

`.github/workflows/` holds the two workflows a repository copies to its own
root: `deploy.yml` and `teardown.yml`. [`cases/`](cases/) and `truth.yaml` are
the verification cases for a scan. [`github/`](github/) is an optional root
that sets up the two GitHub environments the workflows use (step B1).

All three roots pin `hashicorp/azurerm` to 5.14.0 and need Terraform 1.7 or
newer. Each reads its inputs from a `terraform.tfvars` that is not committed;
copy `terraform.tfvars.example` and fill it in.

## What each root creates

### `platform/`

| Resource | Count | Purpose |
|---|---|---|
| `azurerm_resource_provider_registration` | 2 | `Microsoft.ManagedIdentity` and `Microsoft.Storage`; leave out any that is already registered |
| `azurerm_resource_group` | 1 | `<name_prefix>-platform`, with the contract tags |
| `azurerm_subscription_policy_assignment` | 5 | The built-in "Require a tag on resource groups", once per required tag, report-only (`enforce = false`) |
| `azurerm_consumption_budget_subscription` | 1 | Backstop budget: alerts at 80 % actual and 100 % forecast |
| `azurerm_user_assigned_identity` | 1 | The read-only scanner identity |
| `azurerm_role_assignment` | 2 | Reader and Cost Management Reader on the subscription, to the scanner |

`terraform plan` shows `Plan: 12 to add, 0 to change, 0 to destroy`.

### `bootstrap/`

| Resource | Count | Purpose |
|---|---|---|
| `azurerm_resource_group` | 1 | `<repo_slug>-bootstrap`, `Lifecycle=persistent` |
| `azurerm_resource_group` | 1 per name in `stack_resource_groups` | The stack's group, created here with the five contract tags; `stack/` adopts it and owns its tags from then on |
| `azurerm_storage_account` | 1 | State: shared key off, no anonymous access, TLS 1.2, versioning, blob and container soft delete, tag `role=iac-registry` |
| `azurerm_storage_container` | 1 | `tfstate` |
| `azurerm_user_assigned_identity` | 1 | `<repo_slug>-ci` |
| `azurerm_federated_identity_credential` | 2 | Subjects `repo:<org>/<repo>:environment:deploy` and `:teardown` |
| `azurerm_role_assignment` | 4 | CI: Contributor on each stack group, Storage Blob Data Contributor on the container, and Reader on the state account. Operator: Storage Blob Data Contributor on the container (absent when `operator_object_id` is empty) |
| `azurerm_role_assignment` | 0 | CI: Contributor on the subscription, only with the opt-in `ci_subscription_contributor = true` (rule 9 of the build standard says what it allows) |
| `azurerm_management_lock` | 1 | `CanNotDelete` on the group (absent when `lock_enabled` is false) |

`terraform plan` shows `Plan: 12 to add, 0 to change, 0 to destroy` with one
stack group; 10 with no operator and no lock.

The CI identity has no role on the bootstrap group. A CI run therefore cannot
add a federated credential to its own identity, change the state account's
settings (shared-key access, network rules) or list its keys; on the state
account it reads the properties and reaches the blobs of `tfstate`, and
nothing else.

This root keeps local state. To move it into the state account afterwards, use
a container of its own that the CI identity has no role on, never `tfstate`:
the CI identity can write every blob in `tfstate`.

### `stack/`

| Resource | Count | Purpose |
|---|---|---|
| `azurerm_resource_group` | 1 | `resource_group_name`, created by `bootstrap/` and adopted here by an `import` block; six tags: `Repository`, `Project`, `Environment`, `Lifecycle=temporary`, `ManagedBy`, `ExpiresAt` |
| `azurerm_virtual_network` | 1 | An empty network with no `tags` argument: a resource needs none |
| `azurerm_consumption_budget_resource_group` | 1 | The group's budget |
| `azurerm_storage_blob` | 1 | The registry entry `iac-registry/<repo_slug>.json` in the `tfstate` container |

The first `terraform plan` shows `Plan: 1 to import, 3 to add, 1 to change, 0
to destroy`: the group is imported and its tags change from the ones
`bootstrap/` set to the stack's six. The virtual network has no tags. A second
plan straight after apply shows `No changes`.

`terraform destroy` deletes the group with its contents. To deploy again after
that, an Owner applies `bootstrap/` again, which creates the group and its role
assignment anew.

The tag map is written in `stack/contract.tf` so that the example stands alone;
`modules/azure/compliance-tags` replaces that file once it renders group-level
tags.

## Tests without an Azure account

```sh
cd bootstrap && terraform init -backend=false && terraform test
cd stack     && terraform init -backend=false && terraform test
cd github    && terraform init -backend=false && terraform test
```

The suites mock the providers.

## Run sheet

Stage A needs a workstation signed in to Azure as an Owner of a test
subscription. Stage B also needs a GitHub repository.

### Stage A: workstation

| Step | Action | Passes when |
|---|---|---|
| A1 | `platform/`: `terraform init`, `plan`, `apply` | The plan adds 12; five policy assignments exist with enforcement off |
| A2 | `bootstrap/`: `terraform init`, `plan`, `apply`, with `operator_object_id` set to your own object id and `stack_resource_groups` holding the stack's group name | The plan adds 12; a container listing signed with the storage account key is refused; `az role assignment list --assignee <ci principal id> --all` lists Contributor on the stack's group, Storage Blob Data Contributor on the container and Reader on the state account, and nothing at the subscription or on the bootstrap group |
| A3 | `stack/`: `terraform init -backend-config="resource_group_name=<state_resource_group>" -backend-config="storage_account_name=<state_storage_account>"`, then `plan`, `apply`, `plan`, with `resource_group_name` set to that group | The first plan imports 1, adds 3 and changes 1; the container holds `stack.tfstate` and `iac-registry/<repo_slug>.json`; the second plan shows no changes |
| A4 | Read back | Resource Graph returns six tags on the stack's group; a budget exists at the group's scope; after `az policy state trigger-scan` the three groups are compliant on all five assignments |
| A5 | Negative check: create a resource group with no tags, trigger a scan | The group is created (report-only) and is non-compliant on all five assignments; delete it afterwards |
| A6 | Destroy in reverse order: `stack/`, which deletes the stack's group; then `bootstrap/` after `terraform apply -var lock_enabled=false`; then `platform/` | Each destroy ends with nothing left; no resource group, policy assignment, role assignment, budget, lock or managed identity of the run remains |

If `terraform init` in A3 is refused on the state container straight after A2,
the role assignment has not taken effect yet; wait and run it again.

A subscription created in the last 48 hours cannot hold a budget; set
`budget_enabled = false` in `platform/` and `stack/` until it can.

A deleted storage account stays listed as recoverable for 14 days. It holds no
data that is billed.

### Stage B: CI

The CI identity trusts any job that names the environment `deploy` or
`teardown` of the repository: Microsoft Entra ID matches the subject
`repo:<org>/<repo>:environment:<name>` and nothing else. Which branches may
run such a job is set in GitHub, on the environment. B1 therefore sets each
environment to the branch `main` only, and gives `teardown` required
reviewers, before the first run; B4 checks it.

| Step | Action | Passes when |
|---|---|---|
| B1 | In a repository you control: copy `stack/` and `.github/workflows/` to its root. Create the environments `deploy` and `teardown`, each with **Deployment branches and tags** set to **Selected branches and tags** with the single rule `main`, and administrator bypass turned off; give `teardown` **Required reviewers** with **Prevent self-review** on. The optional [`github/`](github/) root makes exactly these settings. Set the repository variables `AZURE_CLIENT_ID` and `AZURE_TENANT_ID` (outputs of `bootstrap/`), `AZURE_SUBSCRIPTION_ID`, `STATE_RESOURCE_GROUP`, `STATE_STORAGE_ACCOUNT`; supply the remaining stack inputs, `TF_VAR_resource_group_name` among them, as `TF_VAR_` repository variables | The repository holds no Azure secret. For each environment, `gh api repos/<org>/<repo>/environments/<name> --jq .deployment_branch_policy` returns `{"custom_branch_policies":true,"protected_branches":false}` and `gh api repos/<org>/<repo>/environments/<name>/deployment-branch-policies --jq '[.branch_policies[].name]'` returns `["main"]`; for `teardown`, `--jq '.protection_rules[].type'` on the environment includes `required_reviewers` |
| B2 | Run `deploy.yml` from `main` | The job signs in through workload identity federation and applies the stack |
| B3 | Run `teardown.yml` from `main` with the stack's resource group id, and approve it as a reviewer other than the person who started it | The stack's group is gone; the bootstrap group is untouched |
| B4 | Push a branch other than `main` with a copy of `deploy.yml` that triggers on that branch and has no `if:` on its job, and let it run | GitHub refuses the job before it starts, because the branch is not allowed to use the environment `deploy`; no token is issued and nothing in Azure changes |

`bootstrap/` must have been applied with `github_org` and `github_repo` set to
that repository: the CI identity trusts its `deploy` and `teardown`
environments and nothing else.

### Optional: `github/`

Manages the two environments of B1 with the `integrations/github` provider: a
branch rule `main` on each, no administrator bypass, and required reviewers
with self-review prevented on `teardown`. It needs a GitHub token with
administration rights on the repository in `GITHUB_TOKEN`, used by this root
only; the other roots and the workflows need no GitHub token. It keeps local
state.

```sh
cd github
cp terraform.tfvars.example terraform.tfvars   # github_org, github_repo, reviewers
terraform init && terraform apply              # 4 to add
```

## Verification cases

`cases/` creates nine empty resource groups, each a known answer for a scan,
and `truth.yaml` states the result a scan must report for each. On Azure the
resource group is the unit a scan grades, and an empty resource group is free.

| Case | Resource group tags | Expected |
|---|---|---|
| 1 | The five required tags, `Lifecycle=persistent` | compliant |
| 2 | No `Environment` | non-compliant, `missing-required` |
| 3 | `Lifecycle=temporary`, `ExpiresAt` in the past | non-compliant, `expired` |
| 4 | `Lifecycle=temporary`, `ExpiresAt=next-week` | non-compliant, `expiry-unreadable` |
| 5 | `ManagedBy=chef` | non-compliant, `invalid-value` |
| 6 | Conforming, plus `Protected=true` | compliant, `protected` |
| 7 | Conforming; the group holds a budget, a type with no tags property | compliant |
| 8 | `Lifecycle=temporary`, no `ExpiresAt` | non-compliant, `temporary-requires-expires-at` |
| 9 | `Lifecycle=experiment`, no `ExpiresAt` | non-compliant, `experiment-requires-expires-at` |

```sh
cd cases
terraform init && terraform apply
# wait a few minutes for Resource Graph
python -m goatcloud scan --platform azure --subscription <id> --lag-minutes 0 --out report.json
python -m goatcloud verify --truth ../truth.yaml --report report.json
terraform destroy
```

The root keeps local state: it is a test fixture that exists for minutes.

Applied once in one test subscription and destroyed in the same run, with the
contract as it was before 1.0: `verify` reported TP 6, TN 3, FP 0, FN 2, both
false negatives on case 9, which that contract did not cover. Contract 1.0 adds
the `experiment-requires-expires-at` rule that case 9 expects; the cases have
not been applied since, and `tests/scan_report/test_reference_truth.py` checks
offline that the contract covers every case. In case 7 the budget does not appear in the
inventory, because the Resource Graph `resources` table does not hold budgets;
the group is graded on its own tags.

## What has been run

Stage A has been run in one test subscription: A2 to A6 passed as written
above. A1 passed, but not yet in a single apply since the root began registering
the `Microsoft.ManagedIdentity` and `Microsoft.Storage` providers itself; in a
subscription where they are not registered, a first apply can stop at the
managed identity, and a second apply completes. Stage B has not been run.

That run used an earlier form of `bootstrap/` and `stack/`, with a wider role
for the CI identity and a group the stack created itself. The current form (the
stack's group created by `bootstrap/` with Contributor on it alone, the group
adopted by an `import` block, the teardown limited to `stack.tfstate`, the
`github/` root) has not been run.
