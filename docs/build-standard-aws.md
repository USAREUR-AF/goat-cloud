# Build standard: AWS

A repository that follows these twelve rules deploys to AWS without asking anyone, and everything it deploys has an owner, a purpose and a lifetime that the nightly scan can confirm. Each rule states what to do, why, how it is checked, and the mechanism that carries it.

On AWS the unit the contract attaches to is the resource: every resource carries the tags.

A complete root that follows the standard is in [quickstart-aws.md](quickstart-aws.md#2-write-the-root).

## Summary

| # | Rule | Checked by |
|---|---|---|
| 1 | One state per repository and environment, in S3, versioned and locked | registry entry; scan |
| 2 | Contract tags come from the tags module through the provider's default tags | CI lint; scan |
| 3 | Resources the provider's default tags do not reach are tagged explicitly | scan |
| 4 | The repository registers itself in the root that deploys it | scan |
| 5 | Every resource declares a lifecycle; `temporary` and `experiment` carry an expiry | Terraform check; scan |
| 6 | All resources of one stack share one expiry | expiry teardown reconciler |
| 7 | CI deploys with a short-lived role through OIDC; no stored access keys | role trust policy |
| 8 | The tag lint runs in CI before the deployment job | CI |
| 9 | The repository has a teardown workflow on its default branch | dispatch at expiry |
| 10 | Owner is a team or a role; contact is a team address or a channel | review |
| 11 | What must never be deleted is on the protected list | enforcement |
| 12 | A resource that must stay hand-made is tagged `manual` and recorded as an override | scan; dashboard |

## 1. One state per repository and environment, in S3, versioned and locked

**Rule.** Each repository keeps its state in an S3 bucket under its own key, `<repo>/<environment>/terraform.tfstate`, with bucket versioning on and state locking on.

**Why.** The scanner decides ownership by reading state. One key per repository makes the claim unambiguous, versioning recovers a damaged state, and a lock stops two applies from writing at once.

**How checked.** The registry entry names the bucket and key; a scan that cannot load it logs the failure, and the repository's resources then appear as unclaimed.

**Mechanism.**

```hcl
terraform {
  backend "s3" {
    bucket       = "acme-tf-state"
    key          = "example-repo/dev/terraform.tfstate"
    region       = "us-east-1"
    use_lockfile = true
  }
}
```

`use_lockfile` needs Terraform 1.10 or newer; earlier versions lock with a DynamoDB table named in `dynamodb_table`. Pulumi repositories use an S3 backend; CloudFormation-based tools keep state in the stack itself.

## 2. Contract tags come from the tags module through the provider's default tags

**Rule.** The root calls `modules/aws/compliance-tags` once and passes its `default_tags` output to `provider "aws" { default_tags }`. No resource sets a contract tag by hand.

**Why.** One place sets the tags, so no resource is missed and no two resources disagree.

**How checked.** `scripts/compliance-lint --iac-type terraform` fails when the provider has no `default_tags` or a required key is absent. The scan reports any deployed resource with a missing or unaccepted tag as a `tag_compliance` finding.

**Mechanism.**

```hcl
module "compliance" {
  source = "git::https://github.com/USAREUR-AF/goat-cloud.git//modules/aws/compliance-tags?ref=v0.1.0"

  project         = "acme"
  environment     = "dev"
  repository_url  = "https://github.com/acme/example-repo"
  lifecycle_value = "persistent"
}

provider "aws" {
  region = "us-east-1"
  default_tags {
    tags = module.compliance.default_tags
  }
}
```

Every additional provider block, including aliased providers for other regions, carries the same `default_tags`.

## 3. Resources the provider's default tags do not reach are tagged explicitly

**Rule.** Where AWS creates a resource on your behalf, pass the tags through the parent: `volume_tags` or a launch template's `tag_specifications` for EBS volumes and network interfaces, `propagate_tags` on ECS services, `tag` blocks with `propagate_at_launch` on Auto Scaling groups.

**Why.** Default tags apply to resources Terraform creates. A volume that EC2 creates for an instance, or a task that ECS starts, is created by AWS and gets only the tags its parent tells AWS to copy.

**How checked.** A child the scanner can attach to its parent (an instance's root volume, a load balancer's address) is graded through the parent and is claimed when the parent is. Any other untagged child is a `tag_compliance` finding.

**Mechanism.** `volume_tags = module.compliance.default_tags` on `aws_instance`; `propagate_tags = "SERVICE"` on `aws_ecs_service`.

## 4. The repository registers itself in the root that deploys it

**Rule.** The same root that deploys the resources calls `modules/aws/iac-registry` with the repository's URL, its tool and its state location.

**Why.** Registration that travels with the deployment cannot be forgotten and is removed with the stack.

**How checked.** The repository appears in the report's `iac_registry.entries` and on the dashboard's repositories view, and its resources have no `drift_detection` finding.

**Mechanism.** The SSM parameter `/<project>/<environment>/iac-registry/<repo-slug>`. A repository that does not use Terraform writes it with `aws ssm put-parameter` after each deployment. See [guides/onboard-a-repository.md](guides/onboard-a-repository.md#2-register-where-the-state-lives).

## 5. Every resource declares a lifecycle; `temporary` and `experiment` carry an expiry

**Rule.** `lifecycle_value` is `persistent`, `temporary`, `experiment` or `deprecated`. With `temporary` or `experiment`, `expires_at` is a UTC timestamp.

**Why.** A resource that says when it ends can be removed at that time without a discussion. One that does not say is how an environment fills with things nobody dares to delete.

**How checked.** The tags module reports a failed check on plan when `lifecycle_value = "temporary"` and `expires_at` is unset. The scan reports an expired resource (HIGH), a temporary resource with no expiry after 30 days (MEDIUM), and a costly resource with no lifecycle after 60 days (LOW).

**Mechanism.** Tags `Lifecycle` and `ExpiresAt`, the latter as ISO 8601, for example `2030-06-30T18:00:00Z`.

## 6. All resources of one stack share one expiry

**Rule.** A stack has one `expires_at`, set once on the tags module.

**Why.** Expiry teardown removes a stack as a whole, through the repository's own workflow. It needs one answer to "when".

**How checked.** The reconciler records a stack whose resources differ by more than five minutes as `mixed_lifecycle` and does not schedule it.

**Mechanism.** `modules/aws/stack-reaper`; see [guides/expiry-teardown.md](guides/expiry-teardown.md).

## 7. CI deploys with a short-lived role through OIDC; no stored access keys

**Rule.** The deployment workflow assumes an IAM role through GitHub's OIDC provider. The role's trust policy names the organization, the repository and the branch. No AWS access key is stored as a secret.

**Why.** A stored key is a credential that outlives the job and can be copied. A role assumed per run cannot.

**How checked.** The trust policy's condition on the token's `sub` claim; the absence of access-key secrets in the repository.

**Mechanism.**

```yaml
permissions:
  id-token: write
  contents: read
steps:
  - uses: aws-actions/configure-aws-credentials@v4
    with:
      role-to-assume: ${{ secrets.AWS_OIDC_ROLE }}
      aws-region: us-east-1
```

The trust policy limits `token.actions.githubusercontent.com:sub` to `repo:<org>/<repo>:ref:refs/heads/main`.

## 8. The tag lint runs in CI before the deployment job

**Rule.** A job runs `scripts/compliance-lint` on every change, and the deployment job depends on it.

**Why.** A missing tag is cheapest to fix before the resource exists.

**How checked.** The deployment job's `needs:` names the lint job.

**Mechanism.** [guides/onboard-a-repository.md](guides/onboard-a-repository.md#3-check-tags-in-ci).

## 9. The repository has a teardown workflow on its default branch

**Rule.** `.github/workflows/teardown.yml` exists on `main`, triggers on `workflow_dispatch`, runs only when the input `confirm` equals `destroy`, and runs the repository's own destroy command.

**Why.** The tool that created a stack is the one that can remove all of it correctly. goat-cloud holds no permission to delete a stack's resources; it asks the repository to.

**How checked.** `gh workflow run teardown.yml -f confirm=test` completes without destroying anything. At expiry, the dispatch result is recorded per stack.

**Mechanism.** [guides/expiry-teardown.md](guides/expiry-teardown.md#2-add-the-teardown-workflow-to-the-repository).

## 10. Owner is a team or a role; contact is a team address or a channel

**Rule.** When `Owner` and `Contact` are set, `Owner` names a team or a role and `Contact` is a team mailbox or a channel. Neither names a person.

**Why.** People change teams and leave; a resource outlives them. Tags are also visible to everyone who can list resources.

**How checked.** Review of the root. The enforcement notifier sends warnings to `Contact`, so a wrong value shows up as a warning nobody receives.

**Mechanism.** Additional keys merged into the default tags: `tags = merge(module.compliance.default_tags, { Owner = "payments-team", Contact = "payments-oncall@acme.example" })`.

## 11. What must never be deleted is on the protected list

**Rule.** State buckets, lock tables, audit logs and systems of record are entries of the protected-resources parameter, or carry `Protected=true`.

**Why.** Enforcement deletes single resources that have serious findings. Protection is a separate statement that holds even when a resource is approved by mistake.

**How checked.** The dashboard's protected view lists each protected resource; the executor marks a protected resource `protected` and skips it; for EC2, RDS, ECS, Lambda, ElastiCache and load balancers its role also allows deletes only on resources not tagged `Protected`.

**Mechanism.** [guides/arm-enforcement.md](guides/arm-enforcement.md#protect-what-must-never-be-deleted).

## 12. A resource that must stay hand-made is tagged `manual` and recorded as an override

**Rule.** A resource created outside any repository, which has to stay that way, carries all contract tags with `ManagedBy=manual`, and an administrator records an override for it.

**Why.** Hand-made resources are what the scan exists to find. An exception is acceptable when it is deliberate, visible and attributed.

**How checked.** The resource passes the tag check; its unclaimed finding is suppressed by the override, and the override is listed with who made it.

**Mechanism.** The dashboard's overrides view.
