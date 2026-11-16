"""A registered stack whose state cannot be read: one finding, and its resources are not called click-ops."""

from unittest.mock import MagicMock, patch

import index as scanner_index
import pytest
from factories import make_resource
from utils.state_parser import StateUnreadable, load_terraform_state_arns

ACCOUNT = "123456789012"
REPO = "https://github.com/acme/payments"
ENTRY = {
    "repo_slug": "payments",
    "repo_url": REPO,
    "iac_type": "terraform",
    "state_location": {"bucket": "acme-state", "key": "payments/terraform.tfstate"},
    "_ssm_name": "/acme/prod/iac-registry/payments",
}


def _stack_resource(name, repository=REPO):
    return make_resource(
        arn=f"arn:aws:ec2:eu-central-1:{ACCOUNT}:instance/i-{name}",
        name=name,
        tags={
            "Project": "acme",
            "Environment": "prod",
            "ManagedBy": "terraform",
            "Lifecycle": "persistent",
            "Repository": repository,
        },
    )


def _scan(resources, state_loader):
    sts = MagicMock()
    sts.get_caller_identity.return_value = {"Account": ACCOUNT}
    with (
        patch.object(scanner_index.boto3, "client", return_value=sts),
        patch.object(scanner_index, "discover_active_regions", return_value=(["eu-central-1"], {})),
        patch.object(scanner_index, "get_service_costs", return_value={}),
        patch.object(scanner_index, "discover_all_resources", return_value=resources),
        patch.object(scanner_index, "load_iac_registry", return_value=[dict(ENTRY)]),
        patch.object(scanner_index, "load_terraform_state_arns", side_effect=state_loader),
        patch.object(scanner_index, "_load_protected_resources", return_value=None),
        patch.object(scanner_index, "REPORTS_BUCKET", ""),
        patch.object(scanner_index, "TF_STATE_BUCKET", ""),
        patch.object(scanner_index, "send_html_email", MagicMock()),
    ):
        scanner_index.handler({}, None)


@pytest.fixture()
def captured(monkeypatch):
    """The report the scan builds, taken at validation."""
    seen = {}
    real = scanner_index.validate_report

    def capture(report):
        seen["report"] = report
        return real(report)

    monkeypatch.setattr(scanner_index, "validate_report", capture)
    return seen


def _drift(report):
    return report["drift_detection"]["findings"]


def test_unreadable_state_gives_one_medium_finding_and_no_unmanaged_resources(captured):
    def unreadable(bucket, key, strict=False):
        raise StateUnreadable("AccessDenied")

    resources = [_stack_resource("0aaaaaaaaaaaaaaa1"), _stack_resource("0aaaaaaaaaaaaaaa2")]
    _scan(resources, unreadable)
    drift = _drift(captured["report"])
    assert [(f["severity"], f["issue"]) for f in drift] == [
        ("MEDIUM", "Registered in the IaC registry, state unreadable")
    ]
    assert drift[0]["resource_arn"].endswith(":parameter/acme/prod/iac-registry/payments")


def test_other_resources_are_still_checked(captured):
    def unreadable(bucket, key, strict=False):
        raise StateUnreadable("AccessDenied")

    stray = _stack_resource("0bbbbbbbbbbbbbbb1", repository="https://github.com/acme/other")
    _scan([_stack_resource("0aaaaaaaaaaaaaaa1"), stray], unreadable)
    arns = {f["resource_arn"] for f in _drift(captured["report"])}
    assert stray["arn"] in arns


def test_readable_state_gives_no_registry_finding(captured):
    resource = _stack_resource("0aaaaaaaaaaaaaaa1")
    _scan([resource], lambda bucket, key, strict=False: {resource["arn"]})
    assert _drift(captured["report"]) == []


class TestStrictLoader:
    def test_unreadable_raises_when_strict(self):
        s3 = MagicMock()
        s3.get_object.side_effect = RuntimeError("AccessDenied")
        with patch("utils.state_parser.boto3.client", return_value=s3):
            assert load_terraform_state_arns("b", "k") == set()
            with pytest.raises(StateUnreadable):
                load_terraform_state_arns("b", "k", strict=True)
