# Remove a temporary stack at its expiry

A temporary stack states when it expires, and at that time goat-cloud asks the stack's own repository to destroy it. The tooling holds no permission to delete the stack's resources: it dispatches the repository's `teardown.yml` workflow, and the workflow runs the same tool that created the stack.

The module is `modules/aws/stack-reaper`. It has two Lambda functions. The **reconciler** runs every six hours, reads each registry entry and the `ExpiresAt` tags of that repository's resources, and keeps one schedule per temporary stack. The **dispatcher** fires at the scheduled time, checks the tags again, and dispatches the workflow. The dispatcher exists only while `armed = true`.

## 1. Mark the stack temporary

In the repository, give every resource the same expiry:

```hcl
module "compliance" {
  source = "git::https://github.com/USAREUR-AF/goat-cloud.git//modules/aws/compliance-tags?ref=v0.1.0"

  project         = "acme"
  environment     = "dev"
  repository_url  = "https://github.com/acme/load-test-lab"
  lifecycle_value = "temporary"
  expires_at      = "2030-06-30T18:00:00Z"
}
```

The repository must also be registered ([onboard-a-repository.md](onboard-a-repository.md)); the reconciler finds stacks through the registry and finds their resources by the `Repository` tag.

The registry entry decides whether a stack is torn down. A stack is scheduled only when its entry has `lifecycle` set to `temporary` or `experiment` and an `expires_at`; the `iac-registry` module writes both from the same inputs. Resource tags can delay or block a teardown, never start one, so a single resource tagged with an expiry cannot remove a stack whose entry says it is persistent.

All resources of the stack must agree on `ExpiresAt` to within five minutes. A stack whose resources carry different expiry times is recorded as `mixed_lifecycle` and is not scheduled.

## 2. Add the teardown workflow to the repository

`.github/workflows/teardown.yml` on the repository's `main` branch:

```yaml
name: Teardown
on:
  workflow_dispatch:
    inputs:
      confirm:
        description: Type "destroy" to confirm
        required: true
      triggered_by:
        description: Who or what started this run
        required: false
        default: manual

permissions:
  id-token: write
  contents: read

jobs:
  teardown:
    if: github.event.inputs.confirm == 'destroy'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_OIDC_ROLE }}
          aws-region: us-east-1
      - uses: hashicorp/setup-terraform@v3
      - run: terraform init
      - run: terraform destroy -auto-approve
```

| Tool | Destroy step |
|---|---|
| Terraform | `terraform init && terraform destroy -auto-approve` |
| CloudFormation, SAM | `aws cloudformation delete-stack --stack-name <name>`, then `aws cloudformation wait stack-delete-complete --stack-name <name>` |
| Serverless Framework | `npx serverless remove --stage <stage>` |
| CDK | `npx cdk destroy --all --force` |
| Pulumi | `pulumi destroy --yes --stack <stack>` |

A repository that wrote its registry entry with the CLI deletes it as the last step: `aws ssm delete-parameter --name /acme/dev/iac-registry/<slug>`. With the Terraform registry module, `terraform destroy` removes the entry.

The `confirm` guard means a run with any other value does nothing, which gives a safe test: `gh workflow run teardown.yml -f confirm=test`.

The workflow assumes the repository's deploy role through GitHub's OIDC provider, the same role its deploy workflow uses; `AWS_OIDC_ROLE` is a repository secret holding that role's ARN. The role's trust policy is described in [build-standard-aws.md, item 7](../build-standard-aws.md#7-ci-deploys-with-a-short-lived-role-through-oidc-no-stored-access-keys).

## 3. Give the dispatcher a way to start workflows

Dispatching a workflow needs a GitHub App installed on the organization that holds the repositories.

1. Create a GitHub App in the organization settings with repository permissions **Actions: Read and write** and **Contents: Read-only**, no webhook, installable only on this account.
2. Note the App ID and generate a private key.
3. Install the App on the organization, for all repositories or for the ones with temporary stacks.
4. Store both values in one Secrets Manager secret as JSON with the keys `app_id` and `private_key`:

```hcl
resource "aws_secretsmanager_secret" "teardown_github_app" {
  name = "acme-dev-stack-teardown-github-app"
}
```

```sh
aws secretsmanager put-secret-value \
  --secret-id acme-dev-stack-teardown-github-app \
  --secret-string "$(jq -n --arg app_id '<APP_ID>' --arg pk "$(cat private-key.pem)" \
    '{app_id: $app_id, private_key: $pk}')"
```

The secret's value is set with the CLI so that the private key is never in Terraform state. Delete the downloaded `.pem` file afterwards.

## 4. Deploy the module, unarmed

```hcl
module "expiry_teardown" {
  source = "git::https://github.com/USAREUR-AF/goat-cloud.git//modules/aws/stack-reaper?ref=v0.1.0"

  project     = "acme"
  environment = "dev"

  iac_registry_prefix   = "/acme/dev/iac-registry"
  repository_url        = "https://github.com/acme/platform"
  github_app_secret_arn = aws_secretsmanager_secret.teardown_github_app.arn

  ses_sender_email = "cloud-reports@acme.example"
  ses_admin_emails = ["cloud-admins@acme.example"]

  # defaults, shown for clarity
  armed   = false
  dry_run = true
}
```

With `armed = false` only the reconciler exists. Run it once and read what it found:

```sh
aws lambda invoke --function-name acme-dev-stack-reaper-reconciler \
  --payload '{}' --cli-binary-format raw-in-base64-out /dev/stdout
aws dynamodb scan --table-name acme-dev-stack-reaper-state
```

Each registered repository has one row with a `status`:

| Status | Meaning |
|---|---|
| `persistent` | no resource has `ExpiresAt`; never scheduled |
| `scheduled` | the stack expires at `expires_at`; the one-time schedule is created once the dispatcher is armed |
| `mixed_lifecycle` | resources disagree on `ExpiresAt`; not scheduled |
| `no_resources` | no resource carries this repository's `Repository` tag |

## 5. Arm with dry run, then arm

```hcl
  armed   = true
  dry_run = true
```

The dispatcher now exists. At a stack's expiry it re-reads the live tags and, in dry run, records `dry_run` and mails the administrators what it would have dispatched. When that matches your expectation for a few stacks, set `dry_run = false`.

From then on, at a stack's expiry time:

1. The schedule invokes the dispatcher with the stack's slug.
2. The dispatcher reads the current `ExpiresAt` from the stack's resources. If the expiry was moved later, it records `precheck_skipped` and stops; the next reconciler run reschedules. If the tags cannot be read, it also stops.
3. It dispatches `teardown.yml` on `main` with `confirm=destroy` and `triggered_by=stack-reaper`, records `dispatched` with the HTTP status, and mails the administrators.
4. The repository's workflow destroys the stack and removes the registry entry.

A dispatch that fails is recorded as `dispatch_failed` and retried on the next reconciler run. The repository's Actions tab holds the log of every teardown.

To run the dispatcher for one stack without waiting for its schedule (it honors `dry_run` and re-reads the tags first):

```sh
aws lambda invoke --function-name acme-dev-stack-reaper \
  --payload '{"stack_id":"load-test-lab","scheduled_expires_at":"2030-06-30T18:00:00Z"}' \
  --cli-binary-format raw-in-base64-out /dev/stdout
```

## Keep a stack longer

Change `expires_at` in the repository and apply. The reconciler moves the schedule on its next run, and if the old schedule fires first, the dispatcher's re-read of the tags sees the new expiry and does nothing.

## Inputs used in this guide

| Input | Default | Meaning |
|---|---|---|
| `armed` | `false` | create the dispatcher Lambda |
| `dry_run` | `true` | record and mail, do not dispatch |
| `reconciler_schedule` | `rate(6 hours)` | how often schedules are reconciled |
| `iac_registry_prefix` | required | where registry entries are read from |
| `github_app_secret_arn` | `""` | secret holding `app_id` and `private_key` |
| `github_org` | `""` | dispatch to this organization in place of the one in each entry's `repo_url` |

All inputs: [reference/module-inputs.md](../reference/module-inputs.md).
