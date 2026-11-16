# goat-cloud on Kubernetes

**Status: Designed, not built.** No code for this target exists yet. This page records the design so that the work can be built in small steps and reviewed against it.

## In short

On Kubernetes the **namespace** is the unit that has an owner, a purpose and a lifetime. A cluster is one API server, and every change goes through it. Namespaces are flat, and deleting one deletes everything inside it. Every object carries labels and annotations. So goat-cloud writes the contract on the Namespace object:
- as labels, for the facts that selectors and policy act on;
- as annotations, for the facts a label value cannot hold (a repository URL, a timestamp, a contact address).

Every object in the namespace takes the contract from its namespace.

Prevention uses the admission policy built into the API server, so the base design installs no policy engine. goat-cloud adds four things Kubernetes does not have:
- ownership, read from what Helm, Argo CD, Flux and Terraform declare;
- the same warn, grace, approve and remove sequence as on the clouds;
- teardown dispatched to the owning repository;
- the join between a cluster and the cloud account that hosts it.

Every Kubernetes fact below carries a reference `[Kn]` to the sources at the end. They were read when Kubernetes 1.37 was current and 1.35 to 1.37 were supported [K10].

---

## 1. The unit

| Level | What it is | Role in goat-cloud |
|---|---|---|
| Cluster | One API server and its nodes | The report's scope; the boundary for policy and the scanner's credentials |
| Namespace | A named group of objects | **The unit.** It carries the contract; it is the level at which Kubernetes isolates, limits and deletes as a whole [K6][K7][K19] |
| Namespaced object | Deployment, Service, PersistentVolumeClaim, ConfigMap | Inherits the contract from its namespace |
| Cluster-scoped object | Node, PersistentVolume, Namespace, ClusterRole, CustomResourceDefinition | In no namespace, so it inherits nothing [K6]. A team that installs one writes the contract on it directly |

Facts about the unit that shape the design:
- **Namespaces are flat.** The Hierarchical Namespace Controller, which added parent and child namespaces, is archived [K41]. The Kubernetes multi-tenancy guide describes one namespace per workload as common practice, "even if multiple workloads are operated by the same tenant" [K19].
- **Deleting a namespace "deletes everything under the namespace".** The deletion is asynchronous [K7], and a deleted namespace cannot be restored [K16].

One namespace per stack is the rule. A stack is what one repository deploys for one environment.

## 2. How the contract renders

### 2.1 Labels and annotations

| Property | Labels | Annotations |
|---|---|---|
| Purpose | Select and group objects | Attach information; "not used to identify and select objects" [K2] |
| Key | Optional DNS-subdomain prefix, `/`, and a name of at most 63 characters `[A-Za-z0-9._-]` [K1] | Same rule [K2] |
| Value | At most 63 characters `[A-Za-z0-9._-]`, alphanumeric first and last, may be empty [K1] | Any string; all annotations on one object together at most 256 KiB [K2] |
| Usable in selectors | Yes [K1] | No |
| Usable in admission policy | Yes | Yes |

Three rules follow:
- A URL, a timestamp with colons and an e-mail address cannot be label values.
- Automation must prefix its keys [K1].
- The prefixes `kubernetes.io/` and `k8s.io/` are reserved [K1].

Kubernetes' six recommended `app.kubernetes.io/*` labels describe an application. None of them names a team, a repository or a lifetime [K3].

### 2.2 The contract on a namespace

`<prefix>` is one DNS-subdomain prefix that the project controls. It is set once in the contract's Kubernetes rendering (`key_prefix`).

| Fact | Carrier | Key | Example |
|---|---|---|---|
| project | label | `<prefix>/project` | `checkout` |
| environment | label | `<prefix>/environment` | `dev` |
| managed_by | label | `<prefix>/managed-by` | `helm` |
| lifecycle | label | `<prefix>/lifecycle` | `temporary` |
| owner | label | `<prefix>/owner` | `payments-team` |
| cost_center | label | `<prefix>/cost-center` | `cc-1420` |
| protected | label | `<prefix>/protected` | `"true"` |
| repository | annotation | `<prefix>/repository` | `https://git.example/payments/checkout` |
| expires_at | annotation | `<prefix>/expires-at` | `2026-12-01T00:00:00Z` |
| contact | annotation | `<prefix>/contact` | `payments@example.org` |

### 2.3 What the contract and report need for this target

Contract 1.0 already has what the rendering needs: a carrier per fact (`label`, `annotation`), `key_prefix`, a per-rendering `unit` and `required` list, and `helm` in `managed_by`. Report schema 2.0 already knows the `kubernetes` platform and has `container`, `inherited_tags`, `cost_basis` and `currency`.

The rest are additions that a later minor version allows:

| Where | Addition | Reason |
|---|---|---|
| `contract.yaml` | A `kubernetes` rendering with `unit: namespace` | — |
| `contract.yaml` | An encoding `k8s-label-value`: at most 63 characters of `[A-Za-z0-9._-]`, alphanumeric first and last | The `gcp-label` rule can produce a value ending in `_` or `-`, which Kubernetes refuses [K1] |
| `contract.yaml` | `argocd`, `flux`, `kustomize`, `crossplane`, `cluster-api` in `managed_by.allowed` | The tools that declare objects on a cluster |
| Report schema | Scope type `cluster` | Today's scope types are account, subscription and project |
| Report schema | Optional `annotations` and `identifiers` on records | A label and an annotation may share a key; the object UID needs a place |
| Report schema | Optional `hosted_on: {platform, scope, resource_id}` on scopes | Joins a cluster to its cloud account, so its cost is not counted twice |

### 2.4 Report fields

| Field | Kubernetes value |
|---|---|
| platform | `kubernetes` |
| scope | Cluster identifier: the UID of the `kube-system` namespace [K44] |
| location | Region of the hosting cloud, or the site of a self-hosted cluster; may be null |
| container | The namespace |
| resource_id | `<scope>/<group>/<version>/<kind>/<namespace>/<name>` |
| resource_type | `<group>/<version>/<Kind>`, for example `apps/v1/Deployment` |
| name | `metadata.name` |
| tags | The object's labels |
| inherited_tags | The namespace's labels |
| state | Kind-specific: namespace phase, replica readiness |
| created_at | `metadata.creationTimestamp` |
| estimated_monthly_cost | Section 5, on namespace records, with its `cost_basis` |

## 3. Inventory

The scanner lists namespaces, then the top-level objects in each. A top-level object has no owner reference: a Deployment, not its ReplicaSet or its Pods. The scanner walks owner references to attach dependents [K4].

- **Identity.** A ServiceAccount bound to a ClusterRole with `get` and `list` on every resource except Secrets. On a cluster someone else runs, a Role with the same verbs in the team's own namespaces [K13].
- **No Secrets.** RBAC cannot grant listing Secrets without granting their contents [K13], so the scanner reads neither. Helm ownership is read from the marks Helm writes on each object (section 4), not from Helm's release Secrets.
- **Where it runs.** As a CronJob in the cluster, or from CI where the API server is reachable. It writes the report to a configured store and nothing to the cluster.
- **Completeness.** A scan that cannot list every namespace writes no report and triggers no step.
- **Several clusters.** Clusters are found from the cloud reports, where managed clusters appear as resources, or from a list. Cluster API and the ClusterProfile API are inventory formats the scanner can read [K42][K43].

## 4. Ownership

Kubernetes records which object created which, and which client wrote which field [K4][K18]. Neither says which team or repository is responsible. The deployment tools mark what they manage, each differently:

| Tool | Mark on the object | Declared state kept where |
|---|---|---|
| Helm | Label `app.kubernetes.io/managed-by: Helm`, annotations `meta.helm.sh/release-name` and `meta.helm.sh/release-namespace`; Helm refuses to adopt an object without all three [K27] | Secrets of type `helm.sh/release.v1` in the release's namespace [K28][K29] |
| Argo CD | Annotation `argocd.argoproj.io/tracking-id`, value `<application>:<group>/<kind>:<namespace>/<name>` [K30] | `Application` objects |
| Flux | Listed in the `status.inventory` of its `Kustomization` [K31] | `Kustomization` and source objects |
| Terraform `kubernetes` provider | None visible; `kubernetes_manifest` uses server-side apply with a configurable field manager [K32][K33] | Terraform state, outside the cluster, read through the repository's registry entry |

The scanner grades each namespace against the contract, then checks each top-level object against these marks:
- **Unclaimed:** an object that no declaration claims. It has no Helm marks, no Argo CD tracking annotation naming an Application that exists, no Flux inventory entry and no address in a registered Terraform state.
- **Missing:** a Flux inventory entry or a Terraform state address with no object behind it.

A namespace is created by the repository that owns it, with its own URL in the `repository` annotation. Kubernetes gives the creator no rights in the new namespace, so a bootstrap step adds them. Where Kyverno is installed, its generate rule does this; otherwise a small job runs every few minutes and creates three things in each namespace that has a contract and no binding:
- a role binding for the repository's identity;
- a ResourceQuota;
- a LimitRange.

That job may write only those three kinds [K11][K12][K13].

CI signs in without a stored key. The API server can trust the CI system's token issuer and map the repository claim to the username [K15]. Admission then checks that the `repository` annotation matches the caller. Where a managed service does not expose that setting, the cloud's own identity mapping or a short-lived service account token is used [K14].

## 5. Cost

A cluster has no bill of its own. Cost per namespace is an estimate, replaced by better data where the cluster has it.

| Tier | Source | When | `cost_basis` |
|---|---|---|---|
| 0 | Pod CPU and memory requests and volume sizes per namespace, times a price sheet with OpenCost's fields (`CPU`, `RAM`, `GPU`, `storage` per hour) [K37] | Always | `allocated` |
| 1 | The hosting cloud's allocation by namespace [K50][K51] | Managed clusters where the feature is on | `billed` |
| 2 | OpenCost `/allocation?aggregate=namespace` [K35] | Clusters that already run OpenCost, which needs Prometheus [K36] | `allocated` |

- Every pod states its CPU and memory requests. A ResourceQuota on CPU and memory makes the API server refuse a pod without them [K11], and a LimitRange supplies defaults [K12].
- A namespace with no requests is "cost unknown", not zero.
- Tier 0 prices requests, not usage. Shared cost (control plane, idle nodes, system namespaces) is shown as its own line per cluster and is not spread across owners.
- The budget is the namespace's ResourceQuota, which, unlike a cloud budget, refuses the excess [K11]. goat-cloud alerts the namespace's contact when use passes a threshold of the quota.
- Dollar amounts for a managed cluster come from the cloud report. The Kubernetes report's amounts are shares of that cost, marked `allocated`, and the dashboard never adds them to billed amounts.

**Cloud resources the cluster creates.** Load balancers and disks created by controllers do not carry the namespace's labels:

| Managed service | What cluster tags reach | What they do not reach |
|---|---|---|
| EKS | Clusters, node groups and Fargate profiles; node instances get `aws:eks:cluster-name` [K45] | Load balancers and volumes, unless the controller is told: `elbv2.k8s.aws/cluster` and the `alb.ingress.kubernetes.io/tags` annotation [K46]; `ebs.csi.aws.com/cluster` and extra tags per StorageClass with the claim's namespace and name [K47] |
| AKS | The cluster's related resources and its node resource group [K48] | Public addresses and disks created by workloads, unless set by annotation or the StorageClass `tags` parameter [K48] |
| GKE | Node instances and their attached disks, hourly [K49] | "Workloads, forwarding rules, IP addresses, and non-attached Persistent Disks" [K49] |

So the platform owner configures each StorageClass and the load balancer controller to tag what they create with the cluster and the namespace. Teams request storage and load balancers only through those classes. In the cloud report, a resource carrying one of these cluster markers is attributed to the cluster, not reported as unclaimed. If the marked cluster no longer exists, it is reported as left behind by a deleted cluster.

## 6. Expiry

Kubernetes has no general expiry [K17]. A temporary namespace states its end in `<prefix>/expires-at`, and its owner extends it by changing the annotation. The contract rule `temporary-requires-expires-at` applies.

At expiry the scanner dispatches the `teardown` workflow of the repository named in the namespace's `repository` annotation, passing the cluster identifier and the namespace. The workflow removes the declaration first, then deletes the namespace:
- with Helm, `helm uninstall`;
- with Argo CD or Flux, the Application or Kustomization, or the commit that declares it.

Removing the declaration first matters because Argo CD and Flux re-create objects that are deleted while still declared. Flux removes what a Kustomization applied when the Kustomization itself is deleted [K31]. A namespace that reappears after removal is reported, naming the declaration that restored it.

If the workflow is missing or fails, the finding stays open and the enforcement sequence (section 7) continues. Nothing is deleted on the repository's behalf before approval.

Tools that already delete on a timer are not used for this:
- Kyverno's TTL label and `DeletingPolicy` act on schedule, with no warning, grace or approval, and `DeletingPolicy` documents no dry-run mode [K21][K22].
- kube-janitor's latest release is from July 2023 [K40].

A team may still use them inside its own namespace for short-lived objects such as preview deployments.

## 7. Enforcement

### 7.1 Prevention at admission

One ValidatingAdmissionPolicy and binding on `namespaces` is generated from `contract.yaml`, so its allowed values and patterns are the same lists the clouds use [K8]. It checks that:
- each required label is present;
- `lifecycle` and `managed-by` are in the allowed lists;
- `repository` matches the contract's pattern;
- a `temporary` namespace has a readable `expires-at`.

A second policy refuses workloads in a namespace without a contract and in `default`. It reads `namespaceObject` [K8].

The bindings start in `Warn` and `Audit`: the person creating a non-compliant namespace sees a warning, and the API audit log records it [K8]. The `failurePolicy` is `Ignore` during this stage. The platform owner switches to `Deny`, and `failurePolicy` to `Fail`, once the report shows no violations.

ValidatingAdmissionPolicy is stable since Kubernetes 1.30, which is the floor for the base design [K8]. Where Kyverno or Gatekeeper is already installed, the same rules ship in that engine's format; Gatekeeper can also generate the native policy objects [K25]. On a cluster where a team holds only namespaces, it cannot install cluster policy, and the scanner reports instead.

### 7.2 Found, warned, stopped, approved, removed

Admission acts on requests only, so what already exists is found by the scan. Removal is a namespace deletion. A reversible stop comes before it, and the API server refuses the deletion unless an approval is recorded on the namespace.

| Step | What happens | Reversible | Identity |
|---|---|---|---|
| Find | The scanner lists namespaces and top-level objects and grades each namespace | — | scanner, read-only |
| Warn | A notice goes to the contact from the contract or the registry entry. The notice time is kept in the report store, so nothing is written to the cluster | — | none in the cluster |
| Grace | The same number of days as on the clouds | — | — |
| Stop | A ResourceQuota `pods: "0"` is created in the namespace and its pods are deleted. Controllers cannot start new pods; every object, volume and Secret remains | Yes: delete the quota | remover |
| Approve | A person other than the one who ran the scan sets `<prefix>/removal-approved` on the namespace to the namespace's UID | Yes: remove the annotation | approver |
| Remove | The owner's teardown is dispatched (section 6). If it does not finish within the grace period, the remover deletes the namespace | No | owning repository, then remover |

Guards:
- Every action defaults to dry run and prints what it would do.
- The remover's RBAC grants deletion of namespaces and nothing else destructive. RBAC cannot restrict deletion by label [K13], so a ValidatingAdmissionPolicy on DELETE of `namespaces` limits it, using `oldObject` and `request.userInfo`. It refuses the remover's request unless three things hold:
  - the approval annotation equals the namespace's UID;
  - the namespace is not labeled `<prefix>/protected`;
  - its lifecycle is not `persistent`.

  The API server enforces this guard, not goat-cloud's code [K8].
- The platform owner labels `kube-system`, the platform's own namespaces and any other it names `<prefix>/protected: "true"`. Admission refuses their deletion.
- There is no restore after deletion [K16]. The Stop step is the recoverable period: it ends compute cost and leaves everything in place for the whole grace period. Before removal, the remover writes every object in the namespace except Secrets to the report store as manifests.
- The dry run lists each PersistentVolume with reclaim policy `Delete`, and its size, because it is destroyed with its claim [K5]. The approval is given with that list in view.
- The remover never removes a finalizer [K16]. A namespace that stays `Terminating` is reported with the objects that hold it.

### 7.3 Access each party needs

| Party | Permission | Scope |
|---|---|---|
| Repository's CI identity | `create` on `namespaces`; full control inside its own namespace through the bootstrap role binding | Cluster for the first, namespace for the second |
| Scanner | `get`, `list` on all resources except Secrets | Cluster, or the team's namespaces |
| Remover | `delete` on `namespaces`; `create`, `update` on `resourcequotas`; `delete` on `pods` | Cluster, limited by the admission guard |
| Approver | `patch` on `namespaces` | Cluster |

## 8. Build standard

What the platform owner sets up once per cluster:

| # | Set up once | Mechanism |
|---|---|---|
| P1 | The cluster carries the contract: tags or labels on the cluster and node pools in the hosting cloud, or a record in the bare-metal inventory | Cloud tags [K45][K48][K49] |
| P2 | Namespaces without the contract are warned about, later refused | ValidatingAdmissionPolicy [K8] |
| P3 | A new namespace starts with a quota, limits and a role binding for its repository | Bootstrap (section 4) |
| P4 | CI signs in with no stored key | AuthenticationConfiguration [K15], or per-run service account tokens [K14] |
| P5 | A read-only scanner identity, without Secrets | RBAC [K13] |
| P6 | StorageClasses and the load balancer controller tag what they create with the cluster and namespace | Driver and controller settings [K46][K47][K48] |
| P7 | Namespaces that must never be removed are labeled protected, and their deletion is refused | ValidatingAdmissionPolicy on DELETE [K8] |

The rules a repository follows:

| # | Rule | Checked by |
|---|---|---|
| 1 | One namespace per stack, carrying the contract of section 2.2 | Admission; the scanner |
| 2 | The namespace is created by the repository it names | Admission, comparing the annotation with the caller |
| 3 | Nothing is deployed to `default` or to a namespace without a contract | Admission; the scanner |
| 4 | Every top-level object is declared by Helm, Argo CD, Flux or Terraform; `kubectl apply` by hand is `managed-by: manual`, allowed only where the contract says so | The scanner (unclaimed findings) |
| 5 | Every pod states its CPU and memory requests | Admission, through the quota |
| 6 | A temporary namespace states when it expires | Admission; lifecycle findings |
| 7 | The repository has a `teardown` workflow that removes its declaration, not only its objects | The scanner checks the workflow exists |
| 8 | Storage and load balancers are requested through the platform owner's classes | The scanner lists unbound volumes and other classes |
| 9 | Cluster-scoped objects a team installs carry the contract themselves | The scanner |

## 9. Compared with installing Kyverno and OpenCost

A cluster with Kyverno and OpenCost has admission rules, background reports, scheduled deletion and cost by namespace. goat-cloud adds:

1. One contract and one report shared with the cloud accounts. A namespace and a cloud project are judged by the same rules and shown on one dashboard.
2. Ownership from declared state: the responsible repository for each object, and a list of objects nothing declares.
3. A removal sequence with a warning, a grace period, a reversible stop and a recorded approval.
4. Teardown by the owning repository instead of by a cluster controller.
5. The link between a cluster and the cloud resources it created, including those left after the cluster is deleted.
6. No always-running component and no Prometheus.

It does not add a policy language, image verification, or a cost model more accurate than OpenCost's. Where Kyverno and OpenCost are installed, goat-cloud uses them rather than duplicating them.

## 10. What it costs to run

Nothing runs between scans:
- The admission policies execute inside the API server.
- The scanner is one CronJob run a day.
- The bootstrap runs only where Kyverno is absent, as a short job every few minutes.
- The report is a file in object storage or a git repository.

## 11. Build order

| Step | Content | Done when |
|---|---|---|
| K1 | Contract `kubernetes` rendering, `k8s-label-value`, `managed_by` values | `contract-gen --check` passes; a generated namespace manifest passes `kubectl apply --dry-run=server` |
| K2 | Report schema additions of section 2.3 | The three cloud adapters' fixtures still validate; a Kubernetes document validates |
| K3 | Admission policy generator, `Warn` and `Audit` binding | On a local cluster, a namespace without the contract is created with a warning |
| K4 | Inventory adapter, no Secret access | A scan of a local cluster emits a valid document; the ClusterRole has no rule for `secrets` |
| K5 | Ownership grading per namespace | Complete, incomplete and absent contracts land in the right category |
| K6 | Helm, Argo CD and Flux readers | A Helm release is claimed; a `kubectl create` deployment is unclaimed |
| K7 | Terraform state reader for `kubernetes_*` resources | A state fixture claims two objects; a third is unclaimed |
| K8 | Lifecycle findings and notices | An expired namespace is listed; dry run prints the notice |
| K9 | Deletion guard and protected label | Deletion is refused without approval and when protected, accepted with the UID |
| K10 | Stop action | No pod runs and the Deployment remains; deleting the quota brings pods back |
| K11 | Remove action with manifest archive | Dry run lists objects and volumes; a real run archives and deletes |
| K12 | Teardown dispatch and reference workflows | A dispatched run uninstalls the release and deletes the namespace |
| K13 | Namespace bootstrap | A namespace with a contract gets its binding, quota and limits within one interval |
| K14 | Tier-0 cost and quota alert | Fixture pods give the expected amount; 85 percent of quota raises the alert |
| K15 | `hosted_on` and cluster markers in each cloud adapter | A tagged disk with no matching cluster is reported as left behind |
| K16 | CI sign-in through AuthenticationConfiguration | A token for repository A cannot create a namespace claiming repository B |
| K17 | Tier-1 and tier-2 cost readers | Recorded responses map to namespace records with the right `cost_basis` |
| K18 | Kyverno and Gatekeeper forms of the rules | The Kyverno CLI passes and fails the same fixtures as K3 |

Every step is validated on a kind or k3d cluster on a laptop, or with recorded fixtures. Only K15 and K17 also need one managed cluster.

Kubernetes comes after the three clouds, because most clusters run in one of them and the cluster-to-account join needs the cloud adapters. Bare metal comes after Kubernetes ([bare-metal.md](bare-metal.md)).

## 12. Open questions

1. **The key prefix.** Which DNS name the contract's keys use. It must be a domain the project controls.
2. **Sharing a namespace.** Whether several repositories may share one namespace. The design supports only one owner, with the others reported as unclaimed.
3. **Where the scanner runs by default.** The design assumes in the cluster.
4. **Bootstrap.** Whether a small job with write access to role bindings, quotas and limit ranges is acceptable, or Kyverno is required for self-service.
5. **Approval.** Which role approves removal, and whether one approver is enough.
6. **Older clusters.** Whether clusters below 1.30 are supported through Kyverno or Gatekeeper only.
7. **Price sheet.** Who sets tier-0 prices for a cluster with no cloud bill, and in which currency.

## 13. Not yet confirmed

These statements are used above. They are to be confirmed while building:
- A ResourceQuota of `pods: "0"` leaves running pods in place until they are deleted. This is derived from "changes to quota will not affect already created resources" [K11]; step K10 confirms it.
- Admission policy is evaluated only on requests, never against objects that already exist.
- Node objects expose the hosting cloud's instance identifier in `spec.providerID`.
- An Argo CD Application and a Flux source name the repository URL in their spec, under the field names assumed here.
- Which managed services let a customer set AuthenticationConfiguration.

## Sources

- [K1] Labels and selectors: https://kubernetes.io/docs/concepts/overview/working-with-objects/labels/
- [K2] Annotations: https://kubernetes.io/docs/concepts/overview/working-with-objects/annotations/
- [K3] Recommended labels: https://kubernetes.io/docs/concepts/overview/working-with-objects/common-labels/
- [K4] Owners and dependents: https://kubernetes.io/docs/concepts/overview/working-with-objects/owners-dependents/
- [K5] Garbage collection: https://kubernetes.io/docs/concepts/architecture/garbage-collection/
- [K6] Namespaces: https://kubernetes.io/docs/concepts/overview/working-with-objects/namespaces/
- [K7] Share a cluster with namespaces: https://kubernetes.io/docs/tasks/administer-cluster/namespaces/
- [K8] Validating admission policy: https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/
- [K10] Releases: https://kubernetes.io/releases/
- [K11] Resource quotas: https://kubernetes.io/docs/concepts/policy/resource-quotas/
- [K12] Limit ranges: https://kubernetes.io/docs/concepts/policy/limit-range/
- [K13] RBAC authorization: https://kubernetes.io/docs/reference/access-authn-authz/rbac/
- [K14] Service accounts: https://kubernetes.io/docs/concepts/security/service-accounts/
- [K15] Authenticating: https://kubernetes.io/docs/reference/access-authn-authz/authentication/
- [K16] Finalizers: https://kubernetes.io/docs/concepts/overview/working-with-objects/finalizers/
- [K17] Automatic cleanup for finished Jobs: https://kubernetes.io/docs/concepts/workloads/controllers/ttlafterfinished/
- [K18] Server-side apply: https://kubernetes.io/docs/reference/using-api/server-side-apply/
- [K19] Multi-tenancy: https://kubernetes.io/docs/concepts/security/multi-tenancy/
- [K21] Kyverno cleanup policy and TTL label: https://release-1-18-0.kyverno.io/docs/policy-types/cleanup-policy/
- [K22] Kyverno DeletingPolicy: https://release-1-18-0.kyverno.io/docs/policy-types/deleting-policy/
- [K25] Gatekeeper and ValidatingAdmissionPolicy: https://open-policy-agent.github.io/gatekeeper/website/docs/validating-admission-policy/
- [K27] Helm ownership check (source): https://raw.githubusercontent.com/helm/helm/main/pkg/action/validate.go
- [K28] Helm Secret storage driver (source): https://raw.githubusercontent.com/helm/helm/main/pkg/storage/driver/secrets.go
- [K29] Helm storage backends: https://helm.sh/docs/topics/advanced/
- [K30] Argo CD resource tracking: https://argo-cd.readthedocs.io/en/stable/user-guide/resource_tracking/
- [K31] Flux Kustomization: https://fluxcd.io/flux/components/kustomize/kustomizations/
- [K32] Terraform `kubernetes_manifest`: https://raw.githubusercontent.com/hashicorp/terraform-provider-kubernetes/main/docs/resources/manifest.md
- [K33] Terraform `kubernetes` provider: https://raw.githubusercontent.com/hashicorp/terraform-provider-kubernetes/main/docs/index.md
- [K35] OpenCost API: https://opencost.io/docs/integrations/api
- [K36] OpenCost installation: https://opencost.io/docs/installation/install
- [K37] OpenCost on-premises pricing: https://opencost.io/docs/configuration/on-prem
- [K40] kube-janitor: https://codeberg.org/hjacobs/kube-janitor
- [K41] Hierarchical Namespace Controller (archived): https://github.com/kubernetes-retired/hierarchical-namespaces
- [K42] Cluster API: https://cluster-api.sigs.k8s.io/
- [K43] ClusterProfile API: https://multicluster.sigs.k8s.io/concepts/cluster-profile-api/
- [K44] KEP-2149 ClusterId: https://github.com/kubernetes/enhancements/tree/master/keps/sig-multicluster/2149-clusterid
- [K45] EKS tags: https://docs.aws.amazon.com/eks/latest/userguide/eks-using-tags.html
- [K46] AWS Load Balancer Controller annotations: https://kubernetes-sigs.github.io/aws-load-balancer-controller/latest/guide/ingress/annotations/
- [K47] EBS CSI driver tagging: https://github.com/kubernetes-sigs/aws-ebs-csi-driver/blob/master/docs/tagging.md
- [K48] AKS tags: https://learn.microsoft.com/en-us/azure/aks/use-tags
- [K49] GKE cluster labels: https://docs.cloud.google.com/kubernetes-engine/docs/how-to/creating-managing-labels
- [K50] GKE cost allocation: https://docs.cloud.google.com/kubernetes-engine/docs/how-to/cost-allocations
- [K51] Split cost allocation data: https://docs.aws.amazon.com/cur/latest/userguide/split-cost-allocation-data.html
