# Demo

This directory runs the dashboard on your machine against an invented estate. It needs Python 3.11 or newer and the `boto3` package, which the dashboard code imports. It uses no cloud account and no credentials, and makes no AWS call.

```sh
pip install boto3
python3 demo/serve.py        # http://localhost:8888
```

The estate is a company called `acme` with one AWS account, `123456789012`, in `us-east-1`: seven registered repositories that use Terraform, the Serverless Framework, Pulumi and CDK, about 150 resources, a temporary stack that is past its expiry and one that expires in five days, and a handful of resources made by hand. The scan has 27 findings across the four categories, and two notices of an expiry within 14 days.

| File | What it does |
|---|---|
| `generate.py` | Writes `data/`: a scan report, the report of the day before, a Terraform state, and enforcement state records. The same `--as-of` date gives the same output |
| `serve.py` | Serves the dashboard Lambda's code on localhost, reading `data/` in place of the report bucket and the enforcement table. Creates `data/` on first run. Read-only |
| `render_report.py` | Renders `data/compliance-report.html` with the scanner's own HTML reporter. Needs `boto3` importable; makes no AWS call |
| `capture.mjs` | Takes the screenshots in `docs/img/screens/` with Playwright |

Expiry dates in the estate are relative to the day `generate.py` runs, so "expired 3 days ago" stays true. Delete `data/` to regenerate it.

## What differs from a deployed dashboard

| View | In the demo |
|---|---|
| Cost forecast | shows N/A; it comes from a live Cost Explorer call |
| Job status for enforcement and expiry teardown | shows "Never"; it comes from SSM parameters the jobs write |
| Approve, abort, extend, destroy | disabled; the demo refuses POST requests |
| Architecture and security views | drawn from the platform root's module metadata, which the synthetic state does not carry |

## View your own report

Put a `compliance-report.json` from your report bucket and the `terraform.tfstate` of your platform root in a directory, and point the runner at it:

```sh
DEMO_DATA_DIR=/path/to/dir PROJECT=acme ENVIRONMENT=dev python3 demo/serve.py
```

## Regenerate the screenshots

```sh
python3 demo/generate.py && python3 demo/render_report.py
python3 demo/serve.py &
npm install playwright
node demo/capture.mjs
```
