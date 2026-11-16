# Cloudflare as a goat-cloud platform

**Status: Proposed, not built.** No code, module or contract rendering for Cloudflare exists. This page records the design so that one can be built; every Cloudflare API, field and permission name below was read in Cloudflare's public documentation on 2026-11-16, and each item that could not be confirmed there is marked **unverified**.

Cloudflare differs from AWS, Azure and Google Cloud in two ways that shape the design: most of what a team builds on it is either inside a zone (DNS records, rulesets, load balancers, certificates) or directly in the account (Workers, R2, D1, KV, Pages), and tags are held by a separate tagging service rather than on each resource.

## Summary

| Capability | Proposed mechanism | Status |
|---|---|---|
| Unit the contract attaches to | The zone for zone-level resources; the resource itself for account-level resources. The account is the scope | Designed |
| Declare ownership | Resource Tagging API tags on the zone and on each account-level resource | Designed |
| Register a repository and its state | Registry entry beside the state in an R2 bucket, as on the other platforms | Designed |
| List every resource | Per-type list endpoints of the Cloudflare API, with a read-only token | Designed |
| Grade against the contract and report | `python -m goatcloud scan --platform cloudflare`, report format 2.0 | Designed |
| Ownership from registered state | Terraform state written by the `cloudflare/cloudflare` provider | Designed |
| Cost | Billable Usage API and subscriptions, per account and per product; not per resource | Designed |
| Tear down an expired unit | Dispatch of the owning repository's `teardown.yml`, as on the other platforms | Designed |
| Delete one resource, with approval | Not proposed for the first version | Not proposed |
| Refuse at creation | Not investigated | Not proposed |

## The unit

**Proposed: the zone is the unit for everything inside a zone; a resource that lives in the account and not in a zone is its own unit. The account is the scope, as the AWS account, the Azure subscription and the Google Cloud project are.**

| Option | For | Against |
|---|---|---|
| The account | One place to declare ownership | An account holds the zones and Workers of many teams and lives for years; it cannot be torn down per stack, and one set of facts on it says nothing about any one resource |
| The zone | A zone is the unit Cloudflare creates and deletes with its DNS records, rulesets, load balancers and certificates; one declaration covers every zone-level resource, as a resource group does on Azure | Workers, R2 buckets, D1 databases, KV namespaces and Pages projects belong to the account, not to a zone [[1]](https://developers.cloudflare.com/resource-tagging/reference/resource-types/) |
| Each resource | Matches AWS; covers account-level resources | A zone of hundreds of DNS records would need tags on each |

The zone plus account-level resources combines the two that fit: zone-level resources inherit their zone's facts, the way Azure resources are satisfied by their resource group, and each account-level resource carries its own, the way an AWS resource does. A record in the report is identified by `(platform, scope, resource_id)` with `platform` `cloudflare` and `scope` the account id; a zone-level record also carries its zone as `container`.

## How the facts are carried

### Resource Tagging API

Cloudflare's Resource Tagging attaches key-value tags to resources, stored separately from the resources themselves [[2]](https://developers.cloudflare.com/resource-tagging/). It is the proposed carrier for every type it supports.

| Property | Value | Source |
|---|---|---|
| Status | Public beta; the API is described as stable, behaviour may change | [[2]](https://developers.cloudflare.com/resource-tagging/) |
| Plans | "Available on all plans" | [[2]](https://developers.cloudflare.com/resource-tagging/) |
| Account-level types | `account`, `access_application`, `access_group`, `access_service_token`, `account_ruleset`, `ai_gateway`, `alerting_policy`, `alerting_webhook`, `cloudflared_tunnel`, `d1_database`, `durable_object_namespace`, `gateway_list`, `gateway_rule`, `image`, `infrastructure_target`, `kv_namespace`, `load_balancer_monitor`, `load_balancer_pool`, `pages_project`, `queue`, `r2_bucket`, `resource_share`, `stream_live_input`, `stream_video`, `vectorize_index`, `worker`, `worker_version` | [[1]](https://developers.cloudflare.com/resource-tagging/reference/resource-types/) |
| Zone-level types | `access_application_policy`, `api_gateway_operation`, `custom_certificate`, `custom_hostname`, `dns_record`, `healthcheck`, `load_balancer`, `managed_client_certificate`, `worker_route`, `zone`, `zone_ruleset` | [[1]](https://developers.cloudflare.com/resource-tagging/reference/resource-types/) |
| Write | `PUT /accounts/{account_id}/tags` or `PUT /zones/{zone_id}/tags` with `resource_type`, `resource_id` and `tags`; a PUT replaces every tag on the resource | [[3]](https://developers.cloudflare.com/resource-tagging/how-to/manage-tags/) |
| Read | `GET /accounts/{account_id}/tags/resources`, filterable by `type`, `tag` and `id`, cursor-paginated at 100 per page | [[4]](https://developers.cloudflare.com/api/resources/resource_tagging/methods/list/), [[5]](https://developers.cloudflare.com/resource-tagging/llms-full.txt) |
| Keys | Up to 256 characters: letters, digits, `_`, `.`, `-`; case-sensitive | [[5]](https://developers.cloudflare.com/resource-tagging/llms-full.txt) |
| Values | Up to 1,024 characters; case-sensitive; may be empty | [[4]](https://developers.cloudflare.com/api/resources/resource_tagging/methods/list/), [[5]](https://developers.cloudflare.com/resource-tagging/llms-full.txt) |
| Limit | 10,000 tags per account during the beta (100,000 stated as the limit after it); more on request | [[5]](https://developers.cloudflare.com/resource-tagging/llms-full.txt) |

The contract's keys (`Project`, `Environment`, `ManagedBy`, `Lifecycle`, `Repository`, `ExpiresAt`) fit the key rules as written for AWS and Azure. The rules Cloudflare applies to characters in a value are not stated; that a repository URL and an ISO 8601 timestamp are accepted is **unverified** and is the first thing the build tests. At seven facts per unit the beta limit allows about 1,400 tagged units per account.

Two consequences shape the scan: the tagging list returns tagged resources only, so it cannot be the inventory ([Inventory](#inventory) lists every type by its own endpoint and joins the tags); and a `PUT` replaces all tags, so a module that writes the contract's tags must merge with tags it does not own.

### Tags that some resources carry themselves

| Resource | Field | Limits | Use in this design |
|---|---|---|---|
| DNS record | `comment` | One per record; 100 characters on Free, 500 on paid plans | Not used for facts; free text for people |
| DNS record | `tags`, as `name:value` | Pro plan and above; 20 per record; name 32, value 100 characters [[6]](https://developers.cloudflare.com/dns/manage-dns-records/reference/record-attributes/) | Not used: Resource Tagging covers `dns_record` on every plan |
| Worker script | `tags`, a list of strings | Returned by the script list [[7]](https://developers.cloudflare.com/api/resources/workers/subresources/scripts/methods/list/); not key-value | Not used |

### The registry entry as the fallback

A type Resource Tagging does not cover (for example a page rule, an email routing rule or a waiting room) carries no tags. Its facts come from the registry entry of the repository whose state holds it, as the repository URL does on Google Cloud: the entry already holds `repo_url`, `iac_type`, `owner`, `contact`, `lifecycle` and `expires_at`. Such a resource is graded through ownership from state; one that no state claims is reported as unclaimed.

The registry entry is the object `iac-registry/<slug>.json` beside the state, in the R2 bucket that holds it. R2 serves Terraform state through the `s3` backend [[8]](https://developers.cloudflare.com/terraform/advanced-topics/remote-backend/); whether that backend's state locking works against R2 is **unverified**. A repository may equally keep its state on another platform's storage; the entry then sits beside it there.

## Inventory

The scan lists every type it grades through that type's own list endpoint, then joins each record with its tags from `GET /accounts/{account_id}/tags/resources`. All calls are reads.

| What | Endpoint | Token permission |
|---|---|---|
| Zones | `GET /zones`, up to 50 per page [[9]](https://developers.cloudflare.com/api/resources/zones/methods/list/) | Zone Read |
| DNS records | `GET /zones/{zone_id}/dns_records` [[10]](https://developers.cloudflare.com/api/resources/dns/subresources/records/methods/list/) | DNS Read |
| Workers | `GET /accounts/{account_id}/workers/scripts` [[7]](https://developers.cloudflare.com/api/resources/workers/subresources/scripts/methods/list/) | Workers Scripts Read |
| R2 buckets | `GET /accounts/{account_id}/r2/buckets` [[11]](https://developers.cloudflare.com/api/resources/r2/subresources/buckets/methods/list/) | Workers R2 Storage Read |
| Tags | `GET /accounts/{account_id}/tags/resources` [[4]](https://developers.cloudflare.com/api/resources/resource_tagging/methods/list/) | scope `com.cloudflare.api.account.tag.list` [[12]](https://developers.cloudflare.com/resource-tagging/get-started/); the dashboard name of the read permission is **unverified** |
| Plans and prices | `GET /accounts/{account_id}/subscriptions`, `GET /zones/{zone_id}/subscriptions` [[13]](https://developers.cloudflare.com/api/resources/accounts/subresources/subscriptions/methods/get/) | Billing Read |
| Usage and cost | Billable Usage API [[14]](https://blog.cloudflare.com/billable-usage-api/) | Billing Read |

Other types are added the same way, each with its read permission from Cloudflare's permission list [[15]](https://developers.cloudflare.com/fundamentals/api/reference/permissions/): Workers KV Storage Read, D1 Read, Cloudflare Pages Read, Load Balancers Read and Load Balancing: Monitors and Pools Read, Access: Apps Read, Cloudflare Tunnel Read, SSL and Certificates Read, Zone Settings Read, Account Rulesets Read. Which read permission each remaining list endpoint accepts is checked when that type is added.

The scanner uses one API token owned by the account rather than by a person, the kind Cloudflare recommends for automation [[2]](https://developers.cloudflare.com/resource-tagging/), holding only the Read permissions above, scoped to the accounts and zones scanned. The token is held in a secret store and passed to the scan in an environment variable; it is never written to a file.

## Ownership from Terraform state

A repository that manages Cloudflare with Terraform uses the `cloudflare/cloudflare` provider (version 5.29.0 on the registry on 2026-11-16), which reads its token from `CLOUDFLARE_API_TOKEN` [[16]](https://registry.terraform.io/providers/cloudflare/cloudflare/latest/docs). Its state records each resource with its type, for example `cloudflare_zone`, `cloudflare_dns_record`, `cloudflare_workers_script` and `cloudflare_r2_bucket`, and the Cloudflare id. The scan reads every state a registry entry names, collects the ids, and matches them to the inventory:

| Inventory record | Result |
|---|---|
| In a registered state | claimed by that repository; its facts may come from the registry entry |
| In no registered state, with complete tags | graded on its tags; noted as not in any registered state |
| In no registered state, without tags | unclaimed (category `unclaimed`) |
| In a state, absent from the inventory | missing (category `missing`) |

How each resource type's id is written in provider 5.x state is **unverified** per type and is fixed with the first fixtures. The provider has no resource for Resource Tagging tags in 5.29.0 (no `tag` resource in its documentation); a repository writes them with the API from its deploy workflow until the provider covers them. A DNS record's own `comment` and `tags` are provider arguments of `cloudflare_dns_record`.

## Reported and acted on

| Finding | Reported | Acted on |
|---|---|---|
| Missing or invalid contract fact on a zone or account-level resource | Yes | No |
| Unclaimed resource, missing resource | Yes | No |
| Expired unit (`ExpiresAt` in the past) | Yes | The owning repository's `teardown.yml` is dispatched, as on the other platforms; the workflow destroys the stack with the repository's own token |
| `Protected` set, or environment protected | Yes | Never acted on |

The first version deletes nothing itself. Whether a Cloudflare API token can be conditioned on a resource's tags, so that a deleting executor is confined to resources that carry the contract, is **unverified**; it is checked before any delete is proposed. A mechanism that refuses creation of an untagged resource has not been investigated.

## Cost

| Source | Grain | Limits |
|---|---|---|
| Billable Usage API (`GET /client/v4/accounts/{account_id}/billable-usage`, Billing Read) | One row per product per charge period, mostly daily; zone where usage is tied to one [[14]](https://blog.cloudflare.com/billable-usage-api/) | Self-serve accounts only at publication (2026-08-03), with Enterprise "in the works"; per product, not per resource. The API reference also lists `GET /accounts/{account_id}/billable/usage` with FOCUS-aligned fields, marked Alpha, Restricted and deprecated [[17]](https://developers.cloudflare.com/api/resources/billing/subresources/usage/methods/get/) |
| Subscriptions (Billing Read) | Each account's and zone's plan, with `price`, `currency`, `frequency` and `rate_plan` [[13]](https://developers.cloudflare.com/api/resources/accounts/subresources/subscriptions/methods/get/) | Plan price, not usage |

So the report gives cost per account and per product, and per zone where Cloudflare attributes usage to one; a Worker, an R2 bucket or a DNS record gets no cost of its own, and `estimated_monthly_cost` is left empty (cost unknown) rather than estimated. An Enterprise account on contract pricing has no cost in the report until Cloudflare's API covers it. The usage fields follow FOCUS (`BilledCost`, `BillingCurrency`, `ChargePeriodStart`, `SubAccountId`), so they map onto the report's cost fields as the other platforms' exports do.

## Not verified

| Item | How it is settled |
|---|---|
| A Resource Tagging value accepts a URL and an ISO 8601 timestamp | Write and read one on a test account |
| Dashboard name of the permission that reads Resource Tagging tags | Create the read-only token and list its permission groups |
| Resource id form per type in provider 5.x state | First state fixtures |
| State locking of the `s3` backend against R2 | A concurrent plan against a test bucket |
| Whether an API token can be conditioned on tags | Cloudflare's token policy documentation, read in full |
| Which read permission each further list endpoint accepts | That endpoint's API reference, when the type is added |

## Sources

1. Cloudflare, Resource Tagging, resource types: https://developers.cloudflare.com/resource-tagging/reference/resource-types/
2. Cloudflare, Resource Tagging: https://developers.cloudflare.com/resource-tagging/
3. Cloudflare, Resource Tagging, manage tags: https://developers.cloudflare.com/resource-tagging/how-to/manage-tags/
4. Cloudflare API, list tagged resources: https://developers.cloudflare.com/api/resources/resource_tagging/methods/list/
5. Cloudflare, Resource Tagging, full text (limits and validation): https://developers.cloudflare.com/resource-tagging/llms-full.txt
6. Cloudflare, DNS record attributes: https://developers.cloudflare.com/dns/manage-dns-records/reference/record-attributes/
7. Cloudflare API, list Workers scripts: https://developers.cloudflare.com/api/resources/workers/subresources/scripts/methods/list/
8. Cloudflare, Terraform remote R2 backend: https://developers.cloudflare.com/terraform/advanced-topics/remote-backend/
9. Cloudflare API, list zones: https://developers.cloudflare.com/api/resources/zones/methods/list/
10. Cloudflare API, list DNS records: https://developers.cloudflare.com/api/resources/dns/subresources/records/methods/list/
11. Cloudflare API, list R2 buckets: https://developers.cloudflare.com/api/resources/r2/subresources/buckets/methods/list/
12. Cloudflare, Resource Tagging, get started: https://developers.cloudflare.com/resource-tagging/get-started/
13. Cloudflare API, account and zone subscriptions: https://developers.cloudflare.com/api/resources/accounts/subresources/subscriptions/methods/get/
14. Cloudflare blog, Billable Usage API: https://blog.cloudflare.com/billable-usage-api/
15. Cloudflare, API token permissions: https://developers.cloudflare.com/fundamentals/api/reference/permissions/
16. Terraform Registry, cloudflare/cloudflare provider: https://registry.terraform.io/providers/cloudflare/cloudflare/latest/docs
17. Cloudflare API, billable usage: https://developers.cloudflare.com/api/resources/billing/subresources/usage/methods/get/
