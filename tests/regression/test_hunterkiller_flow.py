"""Regression: Hunterkiller warn → state → protect/abort lifecycle."""

import os
from datetime import UTC, datetime, timedelta

import pytest
from factories import make_report
from moto import mock_aws


@pytest.fixture()
def hk_index():
    """Load hunterkiller index.py via importlib (avoids scanner index.py collision)."""
    import importlib.util

    hk_lambda = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "modules",
        "aws",
        "hunterkiller",
        "lambda",
        "index.py",
    )
    spec = importlib.util.spec_from_file_location("hk_index", os.path.normpath(hk_lambda))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@mock_aws
class TestHunterkillerLifecycle:
    """Full hunterkiller lifecycle: warn → state → protect/abort."""

    def test_warn_creates_state_record(self, moto_env):
        """write_warning creates a record with expected fields."""
        from state_manager import StateManager

        mgr = StateManager("test-hk-state", ttl_days=30)
        mgr._table = moto_env["hk_table"]

        arn = "arn:aws:ec2:eu-central-1:123456789012:instance/i-lifecycle"
        mgr.write_warning(
            resource_arn=arn,
            batch_id="BATCH-1",
            scan_date="2026-02-20",
            contact_email="user@example.com",
            contact_source="tag:Contact",
            resource_type="ec2:instance",
            resource_name="lifecycle-test",
            region="eu-central-1",
            estimated_monthly_cost=42.50,
            issues=["Unmanaged resource"],
            grace_period_hours=24,
        )

        record = mgr.get_record(arn)
        assert record is not None
        assert record["status"] == "warned"
        assert record["approval_status"] == "pending"

        # Grace deadline is ~24h from now
        deadline = datetime.fromisoformat(record["grace_deadline"])
        expected = datetime.now(UTC) + timedelta(hours=24)
        assert abs((deadline - expected).total_seconds()) < 120

    def test_batch_retrieval(self, moto_env):
        """get_warned_records returns only records from the specified batch."""
        from state_manager import StateManager

        mgr = StateManager("test-hk-state", ttl_days=30)
        mgr._table = moto_env["hk_table"]

        batch_id = "BATCH-FILTER"
        for i in range(3):
            mgr.write_warning(
                resource_arn=f"arn:aws:ec2:eu-central-1:123456789012:instance/i-batch{i}",
                batch_id=batch_id,
                scan_date="2026-02-20",
                contact_email="a@example.net",
                contact_source="admin_fallback",
                resource_type="ec2:instance",
                resource_name=f"batch-{i}",
                region="eu-central-1",
                estimated_monthly_cost=10,
                issues=["test"],
                grace_period_hours=24,
            )

        # Write one with different batch
        mgr.write_warning(
            resource_arn="arn:aws:ec2:eu-central-1:123456789012:instance/i-other",
            batch_id="OTHER",
            scan_date="2026-02-20",
            contact_email="a@example.net",
            contact_source="admin_fallback",
            resource_type="ec2:instance",
            resource_name="other",
            region="eu-central-1",
            estimated_monthly_cost=10,
            issues=["test"],
            grace_period_hours=24,
        )

        records = mgr.get_warned_records(batch_id)
        assert len(records) == 3

    def test_abort_flow(self, moto_env):
        """Abort changes status to 'aborted'."""
        from state_manager import StateManager

        mgr = StateManager("test-hk-state", ttl_days=30)
        mgr._table = moto_env["hk_table"]

        arn = "arn:aws:ec2:eu-central-1:123456789012:instance/i-abort"
        mgr.write_warning(
            resource_arn=arn,
            batch_id="B1",
            scan_date="2026-02-20",
            contact_email="a@example.net",
            contact_source="admin_fallback",
            resource_type="ec2:instance",
            resource_name="abort-me",
            region="eu-central-1",
            estimated_monthly_cost=0,
            issues=[],
            grace_period_hours=24,
        )

        mgr.update_status(arn, "aborted")
        record = mgr.get_record(arn)
        assert record["status"] == "aborted"

    def test_approval_flow(self, moto_env):
        """Approval sets approval fields correctly."""
        from state_manager import StateManager

        mgr = StateManager("test-hk-state", ttl_days=30)
        mgr._table = moto_env["hk_table"]

        arn = "arn:aws:ec2:eu-central-1:123456789012:instance/i-approve"
        mgr.write_warning(
            resource_arn=arn,
            batch_id="B1",
            scan_date="2026-02-20",
            contact_email="a@example.net",
            contact_source="admin_fallback",
            resource_type="ec2:instance",
            resource_name="approve-me",
            region="eu-central-1",
            estimated_monthly_cost=0,
            issues=[],
            grace_period_hours=24,
        )

        mgr.update_approval_status(arn, "approved", approved_by="admin@example.com")
        record = mgr.get_record(arn)
        assert record["approval_status"] == "approved"
        assert record["approved_by"] == "admin@example.com"
        assert record["approved_at"] != ""

    def test_protection_filters(self, hk_index):
        """_is_protected correctly identifies protected resources."""
        # config/account_id are for SSM ARN matching; pass empty for basic tests
        config = {"resources": []}
        account_id = "123456789012"

        # Protected by type
        assert hk_index._is_protected({"resource_type": "route53:hostedzone", "tags": {}}, config, account_id) is True

        # Protected by environment tag
        assert (
            hk_index._is_protected(
                {"resource_type": "ec2:instance", "tags": {"Environment": "prod"}}, config, account_id
            )
            is True
        )

        # Protected by explicit tag
        assert (
            hk_index._is_protected({"resource_type": "ec2:instance", "tags": {"Protected": "true"}}, config, account_id)
            is True
        )

        # Not protected
        assert (
            hk_index._is_protected(
                {"resource_type": "ec2:instance", "tags": {"Environment": "dev"}}, config, account_id
            )
            is False
        )

    def test_severity_filtering(self, hk_index):
        """_extract_actionable_findings filters by severity correctly."""
        report = make_report(num_drift=2, num_tag=1, num_schedule=1, num_lifecycle=1)
        # drift=HIGH(2), tag=HIGH(1), schedule=MEDIUM(1), lifecycle=LOW(1)

        high_only = hk_index._extract_actionable_findings(report, "HIGH")
        assert all(f["severity"] == "HIGH" for f in high_only)

        all_findings = hk_index._extract_actionable_findings(report, "ALL")
        assert len(all_findings) >= len(high_only)
