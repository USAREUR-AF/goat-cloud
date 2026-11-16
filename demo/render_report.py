#!/usr/bin/env python3
"""Render the HTML report for the synthetic estate.

Uses the scanner's own reporter (the file it attaches to the nightly email)
on ./data/compliance-report.json and writes ./data/compliance-report.html.
Needs boto3 importable; no AWS call is made.

    python3 render_report.py
"""

import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("DEMO_DATA_DIR", os.path.join(HERE, "data"))

d = HERE
while not os.path.isdir(os.path.join(d, "modules", "aws", "compliance-scanner", "lambda")):
    if d == os.path.dirname(d):
        sys.exit("modules/aws/compliance-scanner/lambda not found above " + HERE)
    d = os.path.dirname(d)
LAMBDA = os.path.join(d, "modules", "aws", "compliance-scanner", "lambda")
SHARED = os.path.join(d, "modules", "aws", "shared")

os.environ.update(
    CLI_PREFIX="aws",
    REPOSITORY_URL="https://git.acme.example/acme/platform-infra",
    AWS_DEFAULT_REGION="us-east-1",
    DASHBOARD_URL="https://compliance.dev.acme.example",
)
for name in os.listdir(SHARED):
    if name.endswith(".py"):
        shutil.copy2(os.path.join(SHARED, name), os.path.join(LAMBDA, name))
sys.path.insert(0, LAMBDA)

from delta import compute_delta  # noqa: E402
from reporters import html_email  # noqa: E402

with open(os.path.join(DATA, "compliance-report.json")) as fh:
    report = json.load(fh)
with open(os.path.join(DATA, "previous-report.json")) as fh:
    previous = json.load(fh)

date = report["generated_at"][:10]
html = html_email._build_full_html_report(
    report, "acme-dev-compliance-reports", f"reports/{date}", delta=compute_delta(report, previous)
)
with open(os.path.join(DATA, "compliance-report.html"), "w") as fh:
    fh.write(html)
print(f"wrote {os.path.join(DATA, 'compliance-report.html')} ({len(html)} bytes)")
