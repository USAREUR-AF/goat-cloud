# Build standard for Azure

**Status: new.** AWS support is demonstrated and stable; this Azure standard and its reference root are new, have been applied and destroyed once in a live subscription, and may change between releases.

This standard says how a repository team builds infrastructure on Azure so that every resource has an owner, a purpose and a lifetime from the moment it is created. A team that follows the twelve rules needs no review by a central team; Azure itself refuses or reports what breaks them.

Each rule gives the rule, the reason, how it is checked, and the Azure mechanism that enforces or reports it. "Enforced" means Azure refuses the action. "Reported" means the action succeeds and appears as a finding.

## Who does what

A platform owner sets a subscription up once. Each repository then bootstraps itself once and deploys freely.

| Who | When | What |
|---|---|---|
| Platform owner | Once per subscription, or once per management group | Registers the resource providers repositories need (`Microsoft.ManagedIdentity`, `Microsoft.Storage`); assigns the tag policy rules; creates the subscription budget; creates the read-only scanner identity; publishes the allowed locations |
| Repository team | Once per repository (bootstrap) | Creates the bootstrap resource group (the state storage account, the CI identity and its federated credentials), the stack's resource groups, and the role assignments; sets the GitHub environments to the branch `main` only |
| Repository team | Every deployment | Creates resource groups with the contract tags, a budget per group, the registry entry; ships a teardown workflow |

## The rules

**Rule 1. The resource group is the unit of ownership and lifetime, and one repository owns each group.**

- Why: a resource group is the unit Azure deletes, locks, budgets and reports cost for [[20]](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/lock-resources)[[21]](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/delete-resource-group)[[30]](https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/tutorial-acm-create-budgets)[[34]](https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/group-filter). Declaring ownership once per group covers every resource in it, including types that cannot carry tags [[3]](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/tag-support).
- Rule in practice: every resource a repository deploys sits in a resource group that the same repository creates: its bootstrap creates the group, and its stack adopts it and owns its tags and contents. Two repositories never deploy into one group. Nothing is created by hand in a group a repository owns.
- Checked by: the group's `Repository` tag names one repository, and that repository's Terraform state contains the group.
- Mechanism: reported by the goat-cloud scan as an unclaimed group or an unclaimed resource.

**Rule 2. A repository creates its bootstrap resource group before anything else.**

- Why: state storage and the CI identity must exist before the first deployment, and they must outlive every stack the repository creates.
- Rule in practice: one resource group per repository, named `<repo>-bootstrap`, holds the state storage account and the CI identity. It is created from the repository's `bootstrap/` Terraform root by a person, once. Nothing else goes in it.
- Checked by: the group exists, carries `Lifecycle=persistent`, and contains only a storage account and a managed identity.
- Mechanism: reported by the scan.

**Rule 3. Terraform state lives in the repository's own storage account, in a container named `tfstate`, and is reached with Microsoft Entra ID only.**

- Why: state "is stored in plain text and might contain secrets" [[52]](https://learn.microsoft.com/en-us/azure/developer/terraform/store-state-in-azure-storage). One account per repository means no repository can read another's state. Entra sign-in means there is no storage key to leak.
- Rule in practice: the backend is `azurerm` with `use_azuread_auth = true` and, in CI, `use_oidc = true` [[35]](https://developer.hashicorp.com/terraform/language/backend/azurerm). Each Terraform root has its own key, `<root>.tfstate`. The storage account has shared-key access turned off and anonymous access turned off [[54]](https://learn.microsoft.com/en-us/azure/storage/common/secure-storage).
- Checked by: the backend block in the repository; the storage account properties.
- Mechanism: enforced by the storage account itself, which rejects any request signed with an account key once shared-key access is off [[54]](https://learn.microsoft.com/en-us/azure/storage/common/secure-storage). The built-in Policy definition "Storage accounts should prevent shared key access" (`8c6a50c6-9ffd-4ae7-986f-5fa6111f9a54`) reports accounts that allow it.

**Rule 4. State is protected against overwrite and deletion.**

- Why: losing state orphans every resource it describes.
- Rule in practice: blob versioning, blob soft delete and container soft delete are on; minimum TLS version is 1.2; the bootstrap resource group has a `CanNotDelete` lock.
- Checked by: the storage account's blob service properties; the lock on the group.
- Mechanism: versioning and soft delete keep earlier versions of the state blob and are "part of the recommended data protection configuration" [[53]](https://learn.microsoft.com/en-us/azure/storage/blobs/versioning-overview). The lock is enforced by Azure for every user and role, and is inherited by everything in the group [[20]](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/lock-resources). Before the bootstrap root can be destroyed, the lock is removed with `lock_enabled = false` and an apply.

**Rule 5. State is locked during every write, and CI runs one job per root at a time.**

- Why: two concurrent applies corrupt state.
- Rule in practice: the backend's locking is left on; `-lock=false` is never used in a workflow; each workflow sets a concurrency group per root.
- Checked by: the workflow files.
- Mechanism: enforced by Azure Storage. "Azure Storage blobs are automatically locked before any operation that writes state" [[52]](https://learn.microsoft.com/en-us/azure/developer/terraform/store-state-in-azure-storage)[[35]](https://developer.hashicorp.com/terraform/language/backend/azurerm).

**Rule 6. Every resource group carries the contract tags. Resources need none.**

| Level | Tags | How the rest is covered |
|---|---|---|
| Subscription | None required | — |
| Resource group | `Repository`, `Project`, `Environment`, `Lifecycle`, `ManagedBy`; `ExpiresAt` when `Lifecycle` is `temporary` or `experiment` | — |
| Resource | None required | A resource's owner, purpose and lifetime are those of its group. Azure does not copy tags down [[1]](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/tag-resources); a platform owner who wants them on each resource assigns "Inherit a tag from the resource group if missing" [[2]](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/tag-policies) |

- Why: one declaration per group, checked at creation, replaces a tag on every resource.
- Values: `Repository` is the repository URL. `Lifecycle` is `persistent`, `temporary`, `experiment` or `deprecated`. `ManagedBy` is one of the contract's tool names, for example `terraform`, `bicep`, `pulumi`, `arm` or `manual`, compared without regard to case. `ExpiresAt` is a UTC timestamp in the form `2030-01-31T00:00:00Z`. Tag names are written exactly as shown; Azure treats names as case-insensitive and values as case-sensitive [[1]](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/tag-resources).
- Checked by: the tags on the group.
- Mechanism: Azure Policy, with the built-in "Require a tag on resource groups", assigned once per tag [[2]](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/tag-policies). In report-only mode a group without the tags is created and shown as non-compliant, and a group with them as compliant. In enforcing mode creation is refused.
- Groups that Azure creates for itself, such as the network watcher group, carry no tags and show as non-compliant. The platform owner records a policy exemption for each [[10]](https://learn.microsoft.com/en-us/azure/governance/policy/concepts/exemption-structure).

**Rule 7. A repository registers itself by writing one file beside its state.**

- Why: the registry tells the scan which state belongs to which repository and which workflow tears a stack down. Writing it needs no permission the repository does not already hold.
- Rule in practice: the state storage account carries the tag `role=iac-registry`. Each root writes the blob `iac-registry/<slug>.json` in the `tfstate` container with the repository URL, the IaC tool, the state key and the teardown workflow name.
- Checked by: the blob exists and names a state key that exists.
- Mechanism: reported by the scan, which finds accounts by the tag through Resource Graph [[13]](https://learn.microsoft.com/en-us/azure/governance/resource-graph/concepts/query-language) and lists the blobs.

**Rule 8. CI signs in through workload identity federation. No client secret or storage key is stored anywhere.**

- Why: a stored secret can leak and must be rotated; a federated credential is a trust statement with nothing to steal.
- Rule in practice: the repository has one user-assigned managed identity. It has one federated credential per GitHub environment, with issuer `https://token.actions.githubusercontent.com` and subject `repo:<org>/<repo>:environment:<name>` [[36]](https://learn.microsoft.com/en-us/entra/workload-id/workload-identity-federation-considerations)[[38]](https://docs.github.com/en/actions/reference/security/oidc). Workflows request the token with `permissions: id-token: write` and pass the client id, tenant id and subscription id, which are identifiers and are stored as repository variables [[55]](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-azure). One identity serves one repository; an identity holds at most 20 federated credentials [[36]](https://learn.microsoft.com/en-us/entra/workload-id/workload-identity-federation-considerations).
- Checked by: the workflow files contain no secret reference for Azure; the identity has no client secret (a managed identity cannot have one).
- Each GitHub environment the identity trusts allows jobs from the branch `main` only (deployment branches and tags: selected branches, rule `main`), with administrator bypass off; `teardown` also requires a reviewer other than the person who started the run [[39]](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments). GitHub puts the environment name in the token's subject for any job that names the environment, from any branch [[38]](https://docs.github.com/en/actions/reference/security/oidc); the branch rule on the environment is what keeps other branches out.
- Mechanism: enforced by Microsoft Entra ID, which issues a token only when the workflow's subject matches a credential exactly; wildcards are not supported [[36]](https://learn.microsoft.com/en-us/entra/workload-id/workload-identity-federation-considerations). The branch rule and the reviewers are enforced by GitHub, which does not start a job that names the environment from another branch, or before a reviewer approves it.

**Rule 9. The CI identity holds Contributor on its stack's resource groups and data access to its own state container, and nothing more.**

- Why: Contributor on a group lets a repository change and delete everything in that group without asking anyone. It cannot assign roles, remove locks or change policy [[20]](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/lock-resources)[[4]](https://learn.microsoft.com/en-us/azure/governance/policy/overview). Held on the stack's groups only, it gives no write on the bootstrap group, so a CI run cannot add a federated credential to its own identity, change the state account's settings or list its keys.
- Rule in practice: the role assignments are created at bootstrap: Contributor on each resource group the stack deploys into, which bootstrap creates; Storage Blob Data Contributor on the `tfstate` container [[35]](https://developer.hashicorp.com/terraform/language/backend/azurerm); and Reader on the state storage account, to read its tag and properties, which allows no action such as listing keys. The CI identity has no other role at subscription scope or on the bootstrap group. A new resource group for a stack is added at bootstrap, by a person allowed to assign roles.
- Opt-in: Contributor at subscription scope, for a repository that must create resource groups itself. It reaches the bootstrap group too: the CI identity can then add federated credentials to its own identity, which keep working after the run that added them, and can turn shared-key access back on and list the state account's keys. A `CanNotDelete` lock does not stop either, because both are writes, not deletes. In the reference example the opt-in is `ci_subscription_contributor = true`.
- Bootstrap state is kept outside the `tfstate` container, because the CI identity can write every blob in it.
- Checked by: the identity's role assignments: none at subscription scope, and on the bootstrap group only Reader on the state account.
- Mechanism: enforced by Azure RBAC.

**Rule 10. A temporary stack declares its expiry on its resource group and ships its own teardown.**

- Why: the tool that created a stack is the right tool to remove it, and an expiry that is declared can be acted on.
- Rule in practice: the group has `Lifecycle=temporary` (or `experiment`) and `ExpiresAt`. The repository contains `.github/workflows/teardown.yml`, started by `workflow_dispatch` from `main`, which runs `terraform destroy` for the stack root with the CI identity in the environment `teardown`. The state key is fixed in the workflow to the stack's; no input selects another state. A lifetime is extended by changing `ExpiresAt` in a pull request, not by hand.
- Checked by: the tags; the workflow file; the registry entry names the workflow.
- Mechanism: a missing `ExpiresAt` is refused, and a date further ahead than the platform's maximum is refused, by custom Policy rules that compare against `utcNow()` [[7]](https://learn.microsoft.com/en-us/azure/governance/policy/concepts/definition-structure-policy-rule). When the time passes, the goat-cloud scan dispatches the workflow.

**Rule 11. Names and locations follow four fixed conventions.**

| Thing | Convention | Why it matters |
|---|---|---|
| State container | `tfstate` | The scanner's read access is limited to containers of this name |
| Registry blob | `iac-registry/<slug>.json`, slug in lowercase letters, digits and hyphens | Found by listing one prefix |
| Bootstrap group | `<repo>-bootstrap` | Recognized as the repository's persistent group |
| Location | One from the platform owner's allowed list | Resources outside it are refused |

- Storage account names are globally unique [[52]](https://learn.microsoft.com/en-us/azure/developer/terraform/store-state-in-azure-storage) and limited to lowercase letters and digits; the bootstrap root derives one from the repository slug and a short suffix.
- No other naming rule is part of this standard. Ownership comes from tags and state, never from a name.
- Mechanism: the location list is enforced by the built-in Policy definition "Allowed Locations" [[4]](https://learn.microsoft.com/en-us/azure/governance/policy/overview). The other three are reported by the scan.

**Rule 12. Every resource group a repository creates has a budget.**

- Why: the owner hears about cost from Azure before the bill arrives.
- Rule in practice: a budget on the group with a threshold on forecast cost and one on actual cost, and a contact address the team reads [[30]](https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/tutorial-acm-create-budgets).
- Checked by: a budget exists at the group's scope.
- Mechanism: Cost Management evaluates the budget every 24 hours and sends the alert; it never stops a resource [[30]](https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/tutorial-acm-create-budgets). A group without a budget is reported by the scan.

**Protection.** A resource that must never be removed by cleanup carries the tag `Protected=true`, or its group carries a `CanNotDelete` lock. Azure refuses the deletion in both cases [[6]](https://learn.microsoft.com/en-us/azure/governance/policy/concepts/effect-deny-action)[[20]](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/lock-resources).

## Permissions each rule needs

Everything a repository does after bootstrap needs only Contributor on its stack's groups. Bootstrap and the platform setup need Owner on the subscription. Nothing in this standard needs a role in the directory.

| Step | Minimum built-in role | Why Contributor is not enough |
|---|---|---|
| Create the stack's resource groups (rule 1), at bootstrap | Contributor on the subscription, held by the person who bootstraps | — |
| Tag the stack's groups and create resources, budgets and the registry blob in them; delete them (rules 1, 6, 7, 10, 12) | Contributor on those groups, plus Storage Blob Data Contributor on the state container | — |
| Create the managed identity and federated credentials (rule 8), at bootstrap | Contributor on the bootstrap group | — |
| Assign Contributor on the stack's groups, Storage Blob Data Contributor and Reader on the state account to the CI identity (rules 3, 9) | Owner, or Role Based Access Control Administrator [[48]](https://learn.microsoft.com/en-us/azure/role-based-access-control/delegate-role-assignments-overview) | Contributor cannot write role assignments |
| Set the GitHub environments to the branch `main` and require reviewers on `teardown` (rule 8) | Administration of the GitHub repository | Not an Azure permission |
| Lock the bootstrap group (rule 4) | Owner or User Access Administrator [[20]](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/lock-resources) | Contributor cannot write locks |
| Assign policy at a subscription (rules 6, 10, 11) | Owner or Resource Policy Contributor [[4]](https://learn.microsoft.com/en-us/azure/governance/policy/overview) | Contributor can read policy but not assign it |
| Scanner identity with Reader | Owner | Role assignment |
| Register a resource provider | Contributor | — |

Above subscription Owner:

| Step | Needs |
|---|---|
| Assign the policy initiative at a management group so it covers several subscriptions | A role on the management group; at the root group, a Global Administrator who has elevated access [[17]](https://learn.microsoft.com/en-us/azure/governance/management-groups/overview) |
| Turn on cost tag inheritance for a billing account or billing profile | A billing role on that account; available on Enterprise Agreement, Microsoft Customer Agreement and Microsoft Partner Agreement accounts [[29]](https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/enable-tag-inheritance) |

A single subscription needs neither.

## Reference example

`reference/azure/` is a minimal Terraform example that follows every rule: a platform root, a bootstrap root, a stack root and two workflows, and an optional root that sets up the two GitHub environments. It creates no virtual machine and costs under one cent a month while it exists. Its README gives the steps to apply, check and destroy it.

### What a run confirms

The reference example was applied, checked and destroyed in one test subscription by a subscription Owner, with provider `hashicorp/azurerm` 5.14.0 and Terraform 1.11. The run used an earlier form of the bootstrap and stack roots, with a wider role for the CI identity and a group the stack created itself. The rows below say which statements the current form has not yet had confirmed by a run.

| Rule | Shown by | Confirmed by a run |
|---|---|---|
| Platform setup | `platform`: two resource provider registrations, five tag policy assignments, subscription budget, scanner identity with Reader and Cost Management Reader | yes. Creating a managed identity before `Microsoft.ManagedIdentity` is registered is refused with `MissingSubscriptionRegistration` |
| 1 | `bootstrap` creates the stack's group; `stack` adopts it with an `import` block and holds its resources | a group created by the stack root itself, holding its resources: yes. Creation by `bootstrap` and adoption by `stack`: not yet confirmed by a run. A scan reporting an unclaimed group: not yet confirmed by a run |
| 2 | `bootstrap`: a group holding only the state storage account and the CI identity | yes |
| 3 | state of `stack` in the container `tfstate`, reached with Microsoft Entra ID; a container listing signed with the account key is refused | yes |
| 4 | versioning, blob and container soft delete, TLS 1.2 on the account; `CanNotDelete` lock on the group, removed before destroy | settings and lock: yes. Recovering an earlier state version, and the lock refusing a deletion: not yet confirmed by a run |
| 5 | backend locking left on | a concurrent second apply being refused: not yet confirmed by a run |
| 6 | six tags on the stack's group; the virtual network inside it has none; tagged groups compliant and an untagged group non-compliant on all five rules in report-only mode | yes. Enforcing mode refusing a group, and a policy exemption for a platform-created group: not yet confirmed by a run |
| 7 | registry blob `iac-registry/<slug>.json` in the state container; account tagged `role=iac-registry` | yes |
| 8 | managed identity with two federated credentials; environments `deploy` and `teardown` limited to `main`, reviewers on `teardown` | identity and credentials, creation and removal: yes. A sign-in from a workflow, the environment rules (set by hand or by the `github` root), and a job from another branch being refused: not yet confirmed by a run |
| 9 | Contributor on the stack's group, Storage Blob Data Contributor on the container and Reader on the state account, to the CI identity; nothing else at subscription scope or on the bootstrap group | the container role: yes. Contributor on the stack's group alone, the stack root applying and destroying its group with it, and the CI identity being refused a write on the bootstrap group: not yet confirmed by a run |
| 10 | `Lifecycle=temporary` and `ExpiresAt` on the stack's group; `teardown.yml` fixed to the stack's state | tags: yes. The policy rules on `ExpiresAt`, and a teardown started by workflow dispatch: not yet confirmed by a run |
| 11 | container name, registry blob name and bootstrap group name as stated | names: yes. "Allowed Locations" refusing a location: not yet confirmed by a run |
| 12 | budget at resource group scope with thresholds on actual and forecast cost | creation and removal: yes. An alert being sent: not yet confirmed by a run |
| Protection | — | `Protected=true` with `denyAction`, and a lock refusing cleanup: not yet confirmed by a run |

### What a scan of the verification cases confirms

`reference/azure/cases/` creates nine empty resource groups, one per known scan answer, and `reference/azure/truth.yaml` states the expected result of each.

| Case | Confirmed by a run |
|---|---|
| Conforming group; missing required tag; expired; unreadable expiry; `ManagedBy` outside the contract; `Protected=true`; temporary without expiry | yes: each reported with exactly the expected result and rule |
| Experiment without expiry | not yet confirmed by a run |
| A group holding a resource of a type that cannot carry tags | The group is reported compliant: yes. The classification of the resource is not exercised on Azure: the case uses a budget, and the Resource Graph `resources` table, which the scan reads, does not hold budgets, so the resource never reaches the scan. That classification is covered by unit tests |
