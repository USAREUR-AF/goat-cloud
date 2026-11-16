# Compliance dashboard Lambda

Server-rendered web dashboard over the scanner's reports: findings, Terraform
state, cost, architecture views and the enforcement controls. Standard library
and `boto3` only.

## Request path

```text
Browser -> sign-in in front (OIDC proxy or ALB authenticate-oidc) -> index.py
                                                                       |
                                                              path-based routing
                                                      GET views   POST actions (admin)
                                                     (views*.py)    (actions.py)
                                                              \        /
                                                        data*.py (S3, DynamoDB)
```

`index.py` accepts ALB and Function URL events, reads the signed-in user's
email (`auth.py`), and dispatches. GET handlers load data and render; POST
handlers require an admin email, write, and redirect.

## Files

| File | Purpose |
|---|---|
| `index.py` | Handler and router |
| `auth.py` | User identity from the upstream sign-in; admin and viewer checks |
| `actions.py` | Writes: overrides, abort, extend, trigger |
| `data_core.py` | Clients, required inputs, cache |
| `data_*.py`, `data.py` | Reports, Terraform state, cost, security posture; `data.py` re-exports them |
| `views_base.py` | Layout, navigation, CSS, shared helpers |
| `views_*.py`, `views.py` | One module per page; `views.py` re-exports the common ones |
| `arch_shared.py` | Module display metadata and service-to-service edges |
| `arch_custom.py`, `arch_repo.py`, `arch_service.py`, `arch_drawio.py` | Architecture renderers |
| `site_config.py` | Loads the deployment's own content (below) |

`compliance_schema.py` and `delta.py` are copied in from `modules/aws/shared/`
when the package is built.

## Inputs

Required environment variables, set by the Terraform module: `PROJECT`,
`REGION`, `REPOSITORY_URL`, `DOMAIN_NAME`. The code stops at import with the
variable's name when one is missing.

## Deployment-specific content

The code names no deployment's services. A deployment that wants its services
named on the architecture, security and overview pages passes a directory as
the module input `site_config_dir`. Two files are read from it:

| File | Content |
|---|---|
| `site.json` | Display names and descriptions per module, service-to-service edges, identity and public-by-design modules, how sign-in is classified on the security page, an internet edge node, the product name. Keys and defaults are in `site_config.py`. |
| `overview.html` | Body of the overview page, replacing the built-in one |

## Data

1. The scanner writes a report per run to DynamoDB and S3; `data_reports.py`
   reads the latest.
2. `data_terraform.py` reads the Terraform state from S3, builds the module
   tree and joins it with findings.
3. Enforcement state and overrides live in DynamoDB.
4. With `LOCAL_DATA_DIR` set to a directory holding `compliance-report.json`
   (for example the synthetic data `demo/generate.py` writes), no AWS call is
   made.

## Build

The module's `null_resource.lambda_zip` copies the shared files and the site
directory into this directory and zips it, excluding bytecode. The zip is not
committed.
