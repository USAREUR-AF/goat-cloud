"""Regression test fixtures — full moto environment with DynamoDB tables and seeded data."""

import importlib
import importlib.util
import os
import sys

import boto3
import pytest
from factories import make_report
from moto import mock_aws

_DASHBOARD_LAMBDA = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "modules", "aws", "compliance-dashboard", "lambda")
)


def _load_dashboard_handler():
    """Load the dashboard index.py explicitly via importlib (avoids scanner collision).

    Loads data sub-modules first (data_core creates boto3 clients at import time),
    then the re-export layer, then actions/auth/index.
    """
    # Load data sub-modules first (data_core creates boto3 clients inside moto)
    for mod_name in [
        "data_core",
        "data_reports",
        "data_compliance",
        "data_terraform",
        "data_security",
        "data_cost",
        "data_resources",
    ]:
        mod_path = os.path.join(_DASHBOARD_LAMBDA, f"{mod_name}.py")
        spec = importlib.util.spec_from_file_location(mod_name, mod_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)

    # Then load data.py (re-export layer)
    data_path = os.path.join(_DASHBOARD_LAMBDA, "data.py")
    spec = importlib.util.spec_from_file_location("dashboard_data", data_path)
    data_mod = importlib.util.module_from_spec(spec)
    sys.modules["data"] = data_mod
    spec.loader.exec_module(data_mod)

    # Reload actions module
    actions_path = os.path.join(_DASHBOARD_LAMBDA, "actions.py")
    spec = importlib.util.spec_from_file_location("dashboard_actions", actions_path)
    actions_mod = importlib.util.module_from_spec(spec)
    sys.modules["actions"] = actions_mod
    spec.loader.exec_module(actions_mod)

    # Reload auth module
    auth_path = os.path.join(_DASHBOARD_LAMBDA, "auth.py")
    spec = importlib.util.spec_from_file_location("dashboard_auth", auth_path)
    auth_mod = importlib.util.module_from_spec(spec)
    sys.modules["auth"] = auth_mod
    spec.loader.exec_module(auth_mod)

    # Load the handler
    index_path = os.path.join(_DASHBOARD_LAMBDA, "index.py")
    spec = importlib.util.spec_from_file_location("dashboard_index", index_path)
    index_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(index_mod)

    return index_mod.handler, auth_mod


@pytest.fixture()
def moto_env(monkeypatch):
    """Full moto AWS environment with DynamoDB tables and seeded report data."""
    with mock_aws():
        region = "eu-central-1"
        ddb = boto3.resource("dynamodb", region_name=region)

        # --- Report table (PK/SK) ---
        report_table = ddb.create_table(
            TableName="test-report-data",
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        report_table.wait_until_exists()

        # --- HK state table ---
        hk_table = ddb.create_table(
            TableName="test-hk-state",
            KeySchema=[{"AttributeName": "resource_arn", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "resource_arn", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        hk_table.wait_until_exists()

        # --- Overrides table ---
        overrides_table = ddb.create_table(
            TableName="test-overrides",
            KeySchema=[{"AttributeName": "resource_arn", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "resource_arn", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        overrides_table.wait_until_exists()

        # Set env vars BEFORE loading dashboard modules
        monkeypatch.setenv("REPORT_TABLE", "test-report-data")
        monkeypatch.setenv("HK_STATE_TABLE", "test-hk-state")
        monkeypatch.setenv("OVERRIDES_TABLE", "test-overrides")
        monkeypatch.setenv("ADMIN_EMAILS", "admin@example.com")
        monkeypatch.setenv("ENABLE_VIEWER_GATE", "false")
        monkeypatch.setenv("LOCAL_DATA_DIR", "")
        monkeypatch.setenv("REPORTS_BUCKET", "")
        monkeypatch.setenv("TF_STATE_BUCKET", "")
        monkeypatch.setenv("TF_STATE_KEY", "")
        monkeypatch.setenv("PROTECTED_RESOURCES_SSM", "")
        monkeypatch.setenv("IMAGE_VERSIONS_JSON", "{}")
        monkeypatch.setenv("REQUIRED_TAGS", "")  # empty: fall back to the generated contract

        # Load the dashboard handler within moto context
        handler, auth_mod = _load_dashboard_handler()

        # Seed a report via the scanner's writer
        from reporters.dynamodb_report import write_report_to_dynamodb

        report = make_report(num_drift=3, num_tag=2, num_schedule=1, num_lifecycle=1)
        timestamp = "2026-02-20T19:41:56.123456"
        write_report_to_dynamodb("test-report-data", timestamp, report)

        yield {
            "report_table": report_table,
            "hk_table": hk_table,
            "overrides_table": overrides_table,
            "report": report,
            "report_key": "2026-02-20T19:41:56",
            "timestamp": timestamp,
            "ddb": ddb,
            "handler": handler,
            "auth_mod": auth_mod,
        }
