"""Governance function: it reports expired resources and removes nothing."""

import importlib.util
import os
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "modules", "aws", "governance", "lambda", "index.py")
)
_MODULE_DIR = os.path.dirname(os.path.dirname(_PATH))


@pytest.fixture()
def governance(monkeypatch):
    monkeypatch.setenv("SNS_TOPIC_ARN", "arn:aws:sns:eu-central-1:123456789012:alerts")
    monkeypatch.setenv("ENABLE_AUTO_TERMINATION", "true")  # a leftover setting must have no effect
    spec = importlib.util.spec_from_file_location("governance_index", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_expired_resource_is_reported_and_no_other_service_is_called(governance):
    arn = "arn:aws:rds:eu-central-1:123456789012:db:old-database"
    page = {
        "ResourceTagMappingList": [
            {
                "ResourceARN": arn,
                "Tags": [
                    {"Key": "Lifecycle", "Value": "temporary"},
                    {"Key": "ExpiresAt", "Value": "2020-01-01T00:00:00Z"},
                ],
            }
        ]
    }
    clients = {}

    def client(name, **kwargs):
        clients.setdefault(name, MagicMock(name=name))
        if name == "resourcegroupstaggingapi":
            clients[name].get_paginator.return_value.paginate.return_value = [page]
        return clients[name]

    with patch.object(governance.boto3, "client", side_effect=client):
        results = governance.check_resource_lifecycle()

    assert [r["arn"] for r in results["expired"]] == [arn]
    assert "terminated" not in results
    assert set(clients) == {"resourcegroupstaggingapi", "sns"}
    assert arn in clients["sns"].publish.call_args.kwargs["Message"]


def test_function_source_has_no_delete_call(governance):
    assert not hasattr(governance, "terminate_resource")
    source = Path(_PATH).read_text()
    assert not re.search(r"\.(terminate_|delete_|release_|update_service)", source)


def test_role_has_no_delete_permission_and_true_fails_the_plan():
    main_tf = Path(_MODULE_DIR, "main.tf").read_text()
    assert not re.search(r'"[a-z0-9]+:(Delete|Terminate|Release|Update)[A-Za-z]*"', main_tf)
    assert "ENABLE_AUTO_TERMINATION" not in main_tf
    variables_tf = Path(_MODULE_DIR, "variables.tf").read_text()
    block = variables_tf.split('variable "enable_auto_termination"', 1)[1].split("\nvariable ", 1)[0]
    assert "validation" in block and "var.enable_auto_termination == false" in block
