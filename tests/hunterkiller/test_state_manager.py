"""Tests for hunterkiller state_manager.py — DynamoDB state CRUD.

Uses moto for DynamoDB mocking.
"""

import boto3
import pytest
from moto import mock_aws
from state_manager import StateManager


@pytest.fixture
def ddb_table():
    """Create a mocked DynamoDB table for hunterkiller state."""
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="eu-central-1")
        table = ddb.create_table(
            TableName="test-hk-state",
            KeySchema=[{"AttributeName": "resource_arn", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "resource_arn", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        table.wait_until_exists()
        yield table


@pytest.fixture
def state_mgr(ddb_table):
    """Create a StateManager pointing to the mocked table."""
    mgr = StateManager("test-hk-state", ttl_days=30)
    # Override the lazy table property to use our moto table
    mgr._table = ddb_table
    return mgr


@mock_aws
class TestStateManager:
    def test_write_and_get_warning(self, state_mgr):
        arn = "arn:aws:ec2:eu-central-1:123456789012:instance/i-test"
        state_mgr.write_warning(
            resource_arn=arn,
            batch_id="20260220T030000",
            scan_date="2026-02-20",
            contact_email="user@example.com",
            contact_source="tag:Contact",
            resource_type="ec2:instance",
            resource_name="test-instance",
            region="eu-central-1",
            estimated_monthly_cost=42.50,
            issues=["Unmanaged resource"],
            grace_period_hours=24,
            tags={"Project": "test"},
        )

        record = state_mgr.get_record(arn)
        assert record is not None
        assert record["status"] == "warned"
        assert record["batch_id"] == "20260220T030000"
        assert record["contact_email"] == "user@example.com"
        assert record["approval_status"] == "pending"
        assert record["resource_name"] == "test-instance"
        assert "grace_deadline" in record

    def test_get_nonexistent_returns_none(self, state_mgr):
        assert state_mgr.get_record("arn:aws:nonexistent") is None

    def test_get_warned_records_by_batch(self, state_mgr):
        batch_id = "20260220T040000"
        for i in range(3):
            state_mgr.write_warning(
                resource_arn=f"arn:aws:ec2:eu-central-1:123456789012:instance/i-batch{i}",
                batch_id=batch_id,
                scan_date="2026-02-20",
                contact_email="admin@example.com",
                contact_source="admin_fallback",
                resource_type="ec2:instance",
                resource_name=f"batch-{i}",
                region="eu-central-1",
                estimated_monthly_cost=10,
                issues=["test"],
                grace_period_hours=24,
            )
        # Also write one with a different batch
        state_mgr.write_warning(
            resource_arn="arn:aws:ec2:eu-central-1:123456789012:instance/i-other",
            batch_id="OTHER_BATCH",
            scan_date="2026-02-20",
            contact_email="admin@example.com",
            contact_source="admin_fallback",
            resource_type="ec2:instance",
            resource_name="other",
            region="eu-central-1",
            estimated_monthly_cost=10,
            issues=["test"],
            grace_period_hours=24,
        )

        records = state_mgr.get_warned_records(batch_id)
        assert len(records) == 3
        arns = {r["resource_arn"] for r in records}
        assert "arn:aws:ec2:eu-central-1:123456789012:instance/i-other" not in arns

    def test_update_status(self, state_mgr):
        arn = "arn:aws:ec2:eu-central-1:123456789012:instance/i-update"
        state_mgr.write_warning(
            resource_arn=arn,
            batch_id="B1",
            scan_date="2026-02-20",
            contact_email="a@example.net",
            contact_source="tag:Contact",
            resource_type="ec2:instance",
            resource_name="x",
            region="eu-central-1",
            estimated_monthly_cost=0,
            issues=[],
            grace_period_hours=24,
        )
        state_mgr.update_status(arn, "aborted")
        record = state_mgr.get_record(arn)
        assert record["status"] == "aborted"
        assert "updated_at" in record

    def test_update_status_with_result(self, state_mgr):
        arn = "arn:aws:ec2:eu-central-1:123456789012:instance/i-result"
        state_mgr.write_warning(
            resource_arn=arn,
            batch_id="B1",
            scan_date="2026-02-20",
            contact_email="a@example.net",
            contact_source="admin_fallback",
            resource_type="ec2:instance",
            resource_name="x",
            region="eu-central-1",
            estimated_monthly_cost=0,
            issues=[],
            grace_period_hours=24,
        )
        state_mgr.update_status(arn, "torn_down", teardown_result="Terminated i-result")
        record = state_mgr.get_record(arn)
        assert record["status"] == "torn_down"
        assert record["teardown_result"] == "Terminated i-result"

    def test_update_approval_status(self, state_mgr):
        arn = "arn:aws:ec2:eu-central-1:123456789012:instance/i-approve"
        state_mgr.write_warning(
            resource_arn=arn,
            batch_id="B1",
            scan_date="2026-02-20",
            contact_email="a@example.net",
            contact_source="admin_fallback",
            resource_type="ec2:instance",
            resource_name="x",
            region="eu-central-1",
            estimated_monthly_cost=0,
            issues=[],
            grace_period_hours=24,
        )
        state_mgr.update_approval_status(arn, "approved", approved_by="admin@example.com")
        record = state_mgr.get_record(arn)
        assert record["approval_status"] == "approved"
        assert record["approved_by"] == "admin@example.com"
        assert record["approved_at"] != ""


@mock_aws
class TestWarnedRecordsOfEveryBatch:
    def test_all_batches_reads_every_warned_record_and_only_those(self, state_mgr, ddb_table):
        ddb_table.put_item(Item={"resource_arn": "arn:a", "batch_id": "b1", "status": "warned"})
        ddb_table.put_item(Item={"resource_arn": "arn:b", "batch_id": "b2", "status": "warned"})
        ddb_table.put_item(Item={"resource_arn": "arn:c", "batch_id": "b2", "status": "aborted"})
        assert sorted(r["resource_arn"] for r in state_mgr.get_warned_records("", all_batches=True)) == [
            "arn:a",
            "arn:b",
        ]
        assert [r["resource_arn"] for r in state_mgr.get_warned_records("b1")] == ["arn:a"]
        assert state_mgr.get_warned_records("") == []
