# Google Cloud reference root

**Status: new.** AWS support is demonstrated and stable; this Google Cloud reference root is new, has been applied and destroyed in one existing project, and may change between releases.

Three Terraform roots and two workflows that follow every rule of the
[build standard for Google Cloud](../../docs/build-standard-gcp.md)
in one existing project. No VM, database or load balancer: one empty Pub/Sub
topic, two small buckets, one service account, one workload identity pool and
IAM bindings. Under 0.05 USD for a day.

| Root | Who applies it | Rules | Plan shows |
|---|---|---|---|
| `bootstrap/` | the project's owner, once | 1 | 3 to add |
| `platform/` | the project's owner, once | P2, P3, P4, 5, 6, 7 | 24 to add (37 with `tag_mode = "project-keys"`) |
| `stack/` | the repository, on every change | 2, 4, 5, 8 | 4 to add |

`.github/workflows/deploy.yml` and `teardown.yml` (rules 6 and 9) take effect
when this directory is the root of a GitHub repository whose numeric ids were
given to `platform/`.

## Two state buckets

| Bucket | Output of `bootstrap/` | Holds | CI access |
|---|---|---|---|
| `<slug>-tfstate-<suffix>` | `state_bucket` | `bootstrap/` and `platform/` state (and `cases/`) | none; the project's owners only |
| `<slug>-stack-tfstate-<suffix>` | `stack_state_bucket` | `stack/` state and `iac-registry/<slug>.json`; label `role=iac-registry` | object access, by the two deploy workflows |

The owner's roots create the sign-in path and the deploy grants, so their
state stays where no CI run can read or rewrite it.

## What CI may do

`platform/` admits two workflow files of the repository, `deploy.yml` and
`teardown.yml` (`github_workflows`), run from `refs/heads/main`
(`github_deploy_ref`), and nothing else. The provider's condition checks the
token's `workflow_ref`, `<owner>/<repo>/.github/workflows/<file>@<ref>`, so a
pull request, another branch, and any other workflow file on `main`, a
`pull_request_target` workflow included, is refused at sign-in. Each grant
below goes to the two workflow principals. On a pull request `deploy.yml`
runs format, validate and the mocked tests, with no cloud identity.

`github_repository` is `<owner>/<repo>` exactly as GitHub writes it in
`workflow_ref`, the same case; a renamed repository signs in again only after
`platform/` is applied with the new name.

| Grant | Scope | Allows |
|---|---|---|
| `roles/pubsub.editor` | project | the example topic; reading the project (`resourcemanager.projects.get`) |
| custom role `<slug>_<suffix>_bucket_create` | project | `storage.buckets.create` only |
| custom role `<slug>_<suffix>_stack_buckets` | project, condition: bucket name starts with `stack_bucket_prefix`, default `<slug>_` | read, change, empty and delete this project's buckets whose names start with the prefix; no IAM permission |
| `roles/storage.objectUser` | the stack's state bucket | read and write the stack's state and the registry entry, take the state lock |
| `roles/storage.legacyBucketReader` | the stack's state bucket | read the bucket's labels (rule 1 check) |

`deploy_roles` takes only roles from an allowed list of the roles the stack
needs, `roles/pubsub.editor` here; any other is refused at plan. A stack that
uses another service adds its role to that list, in `platform/variables.tf`,
by reviewed change.

The bucket prefix is `<slug>_`. The slug holds no underscore, so no other
repository's buckets and no state bucket can start with it, and two stacks in
one project do not overlap. A prefix that does not start with `<slug>_` is
refused at plan.

No grant can change IAM, a state bucket's settings or the enabled APIs.
`pubsub.googleapis.com` is enabled by `platform/`. The scanner's read grant on
the stack's state bucket is made by `platform/` too.

Before the first run of a workflow, in the repository's settings:

- `main` takes changes by reviewed pull request only (branch protection or a
  ruleset): whoever can push to `main` deploys.
- The environment `teardown` exists with **Deployment branches and tags** set
  to **Selected branches and tags**, rule `main`, administrator bypass off,
  and **Required reviewers** with **Prevent self-review** on. Check:
  `gh api repos/<org>/<repo>/environments/teardown/deployment-branch-policies --jq '[.branch_policies[].name]'`
  returns `["main"]`.
- Repository variables `WORKLOAD_IDENTITY_PROVIDER`, `PROJECT_ID`,
  `STATE_BUCKET` (the stack's state bucket, `stack_state_bucket`), and the
  remaining stack inputs as `TF_VAR_` variables.

## Before the first command

- The permissions of Owner on one existing project.
- Terraform 1.7 or newer and a `gcloud` sign-in:
  `export GOOGLE_OAUTH_ACCESS_TOKEN=$(gcloud auth print-access-token)`.
- `serviceusage.googleapis.com` and `cloudresourcemanager.googleapis.com` on.
- The project carries the contract labels:
  `gcloud projects update <project> --update-labels=workload=<name>,owner=<team>,environment=dev,lifecycle=persistent,managed-by=terraform`.
- In each root, copy `terraform.tfvars.example` to `terraform.tfvars` and set
  your values. `location` has no default: use one your project's
  resource-location policy allows.

## Order of commands

| Step | Directory | Command | Result |
|---|---|---|---|
| 1 | `bootstrap` | `terraform init` then `terraform apply` | 3 added; outputs `state_bucket` and `stack_state_bucket` |
| 2 | `bootstrap` | `cp backend.tf.example backend.tf`; `terraform init -migrate-state -backend-config=bucket=<state_bucket>`; delete the empty local `terraform.tfstate` and its backup | `bootstrap/default.tfstate` in the bucket |
| 3 | `platform` | `terraform init -backend-config=bucket=<state_bucket>`; `terraform apply -var stack_state_bucket=<stack_state_bucket>` | 24 added |
| 4 | `stack` | `terraform init -backend-config=bucket=<stack_state_bucket>`; `terraform apply` | 4 added |
| 5 | any | `gcloud storage cat gs://<stack_state_bucket>/iac-registry/<slug>.json` | the registry entry |
| 6 | any | `gcloud pubsub topics describe <slug>-example` | the contract labels |

A plan in `stack` against a project without the contract labels stops with
`Rule 2: the project lacks the contract labels: ...`.

## Removing it

For a test, apply `bootstrap` with `-var force_destroy=true -var
soft_delete_retention_seconds=0`; a versioned state bucket cannot otherwise be
destroyed.

1. `stack`: `terraform destroy`
2. `platform`: `terraform destroy`
3. `bootstrap`: delete `backend.tf`; `terraform init -migrate-state`;
   `terraform destroy`

Enabled APIs stay on. The deleted workload identity pool stays in state
`DELETED` for 30 days; its id carries a random suffix so a later apply does not
collide with it.

## Test-only settings

| Variable | Root | Use |
|---|---|---|
| `force_destroy`, `soft_delete_retention_seconds` | `bootstrap` | let a test remove the state buckets and leave nothing |
| `contract_check = "report"` | `stack` | in a project whose labels must not be changed, continue the plan and list the missing labels in the output `project_contract_missing` |
| `bind_project_tags = false` | `platform` | with `tag_mode = "project-keys"`, create tag keys and values without binding the project |

## Tests

```sh
cd stack     && terraform init -backend=false && terraform test
cd platform  && terraform init -backend=false && terraform test
cd bootstrap && terraform init -backend=false && terraform test
```

Nine tests in `stack`, six in `platform` and one in `bootstrap`, on mocked
providers; no credentials and no API call.

## What has been run

The three roots were applied and destroyed once in one project with an
earlier form of the state buckets, the deploy grants and the workflows. The
current two-bucket layout, grants and workflows have not been run.

## Verification cases

`cases/` creates seven free resources, each a known answer for a scan: six
empty Pub/Sub topics (conforming, one missing label, expired, unreadable
expiry, a `managed-by` value outside the contract, protected) and one service
account, a type that cannot carry labels. `truth.yaml` states the result a
scan must report for each. The label keys are the contract's Google Cloud
defaults, so no mapping file is needed; `contract/mapping.example.yaml` shows
how an estate with other key names maps them. Applied once with a per-resource
scan: `verify` reported TP 5, TN 3, FP 0, FN 0.

```sh
cd cases
terraform init -backend-config=bucket=<state_bucket>
terraform apply            # 7 to add
python -m goatcloud scan --grade resources ...
python -m goatcloud verify --truth ../truth.yaml --report report.json
terraform destroy
```
