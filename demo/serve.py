#!/usr/bin/env python3
"""Run the dashboard on this machine against the synthetic estate.

No cloud account and no credentials are used. The dashboard code is the same
code the Lambda runs; this script feeds it files from ./data in place of the
report bucket and the enforcement table.

    python3 serve.py              # http://localhost:8888
    PORT=9000 python3 serve.py

./data is created by generate.py on first run. Delete it to rebuild.

To view a report of your own, put compliance-report.json and terraform.tfstate
in a directory and point DEMO_DATA_DIR at it. previous-report.json and
enforcement-state.json are optional. PROJECT, ENVIRONMENT, ACCOUNT_ID, REGION
and REPOSITORY_URL may be set in the environment.
"""

import http.server
import json
import os
import shutil
import subprocess
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("DEMO_DATA_DIR", os.path.join(HERE, "data"))
PORT = int(os.environ.get("PORT", "8888"))
VIEWER = "admin@acme.example"


def find_root():
    d = HERE
    while d != os.path.dirname(d):
        if os.path.isdir(os.path.join(d, "modules", "aws", "compliance-dashboard", "lambda")):
            return d
        d = os.path.dirname(d)
    sys.exit("modules/aws/compliance-dashboard/lambda not found above " + HERE)


ROOT = find_root()
LAMBDA = os.path.join(ROOT, "modules", "aws", "compliance-dashboard", "lambda")
SHARED = os.path.join(ROOT, "modules", "aws", "shared")

if not os.path.isfile(os.path.join(DATA, "compliance-report.json")):
    subprocess.run([sys.executable, os.path.join(HERE, "generate.py"), "--out", DATA], check=True)

os.environ["LOCAL_DATA_DIR"] = DATA
for key, value in {
    "PROJECT": "acme",
    "ENVIRONMENT": "dev",
    "ACCOUNT_ID": "123456789012",
    "REGION": "us-east-1",
    "DOMAIN_NAME": "acme.example",
    "REPOSITORY_URL": "https://git.acme.example/acme/platform-infra",
    "CLI_PREFIX": "aws",
    "ADMIN_EMAILS": VIEWER,
    "DASHBOARD_URL": f"http://localhost:{PORT}",
    "HK_STATE_TABLE": "demo-enforcement-state",
    "HK_EXECUTOR_ARMED": "false",
}.items():
    os.environ.setdefault(key, value)

# The Lambda build copies the shared modules beside the handler; do the same here.
for name in os.listdir(SHARED):
    if name.endswith(".py"):
        shutil.copy2(os.path.join(SHARED, name), os.path.join(LAMBDA, name))

sys.path.insert(0, LAMBDA)


class _Table:
    """Stands in for the enforcement state table: reads enforcement-state.json."""

    def _items(self):
        path = os.path.join(DATA, "enforcement-state.json")
        if not os.path.isfile(path):
            return []
        with open(path) as fh:
            return json.load(fh)

    def scan(self, **_):
        return {"Items": self._items()}

    def get_item(self, Key):
        for item in self._items():
            if item["resource_arn"] == Key.get("resource_arn"):
                return {"Item": item}
        return {}


class _Dynamo:
    def Table(self, _name):
        return _Table()


try:
    import boto3  # noqa: F401  (the dashboard modules import it; no AWS call is made here)
except ImportError:
    sys.exit("The dashboard code imports boto3. Install it with: pip install boto3")

import data_core  # noqa: E402

data_core.dynamodb = _Dynamo()

import index  # noqa: E402


def _previous(_current=None):
    path = os.path.join(DATA, "previous-report.json")
    if not os.path.isfile(path):
        return None, None
    with open(path) as fh:
        return "previous", json.load(fh)


# In local mode the dashboard knows one report. Give the changes and cost views the earlier one.
index.get_24h_previous_report = _previous
index.get_month_start_report = _previous


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self.send_response(405)
        self.end_headers()
        self.wfile.write(b"The demo is read-only.")

    def _handle(self, method):
        parsed = urllib.parse.urlparse(self.path)
        qs = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query, keep_blank_values=True).items()}
        event = {
            "requestContext": {"elb": {}},
            "httpMethod": method,
            "path": parsed.path,
            "queryStringParameters": qs,
            "headers": {"cf-access-authenticated-user-email": VIEWER},
            "body": "",
            "isBase64Encoded": False,
        }
        try:
            response = index.handler(event, None)
        except Exception as exc:  # show the failure in the page; the demo has no log sink
            import traceback

            traceback.print_exc()
            self.send_response(500)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(f"500: {exc}".encode())
            return
        self.send_response(response.get("statusCode", 200))
        for k, v in response.get("headers", {}).items():
            self.send_header(k, v)
        self.end_headers()
        body = response.get("body", "")
        if response.get("isBase64Encoded"):
            import base64

            body = base64.b64decode(body)
        self.wfile.write(body.encode("utf-8") if isinstance(body, str) else body)

    def log_message(self, fmt, *args):
        sys.stderr.write(f"  {args[0]}\n")


if __name__ == "__main__":
    print(f"Demo dashboard: http://localhost:{PORT}  (data: {DATA})")
    http.server.HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
