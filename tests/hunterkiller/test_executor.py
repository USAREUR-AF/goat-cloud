"""Tests for hunterkiller executor.py — teardown execution logic."""

import pytest
from executor import _describe_action, _extract_id, execute_teardowns


class TestExecuteTeardowns:
    def test_dry_run_all_succeed(self):
        records = [
            {
                "resource_arn": "arn:aws:ec2:eu-central-1:123456789012:instance/i-abc123",
                "resource_type": "ec2:instance",
                "region": "eu-central-1",
                "resource_name": "test-instance",
            },
            {
                "resource_arn": "arn:aws:ec2:eu-central-1:123456789012:volume/vol-def456",
                "resource_type": "ec2:volume",
                "region": "eu-central-1",
                "resource_name": "test-volume",
            },
        ]
        results = execute_teardowns(records, dry_run=True)
        assert len(results) == 2
        for r in results:
            assert r["success"] is True
            assert r["dry_run"] is True
            assert "DRY RUN" in r["message"]

    def test_dry_run_preserves_metadata(self):
        records = [
            {
                "resource_arn": "arn:aws:ec2:eu-central-1:123456789012:instance/i-abc",
                "resource_type": "ec2:instance",
                "region": "eu-central-1",
                "resource_name": "my-instance",
            },
        ]
        results = execute_teardowns(records, dry_run=True)
        r = results[0]
        assert r["resource_arn"] == records[0]["resource_arn"]
        assert r["resource_type"] == "ec2:instance"
        assert r["resource_name"] == "my-instance"
        assert r["region"] == "eu-central-1"

    def test_empty_records(self):
        results = execute_teardowns([], dry_run=True)
        assert results == []


class TestDescribeAction:
    @pytest.mark.parametrize(
        "rtype,keyword",
        [
            ("ec2:instance", "terminate"),
            ("ec2:volume", "delete"),
            ("ec2:security-group", "delete"),
            ("ec2:eip", "release"),
            ("rds:instance", "delete"),
            ("ecs:service", "delete"),
            ("lambda:function", "delete"),
            ("s3:bucket", "delete"),
            ("elasticache:cluster", "delete"),
            ("elbv2:application", "delete"),
        ],
    )
    def test_known_types(self, rtype, keyword):
        action = _describe_action(rtype, f"arn:aws:test:eu-central-1:123:type/{rtype}", "eu-central-1")
        assert keyword in action.lower()

    def test_unknown_type(self):
        action = _describe_action("unknown:thing", "arn:aws:unknown:eu-central-1:123:thing/x", "eu-central-1")
        assert "skip" in action.lower()

    def test_elb_arn_pattern(self):
        arn = "arn:aws:elasticloadbalancing:eu-central-1:123:loadbalancer/app/my-alb/abc123"
        action = _describe_action("custom:type", arn, "eu-central-1")
        assert "load balancer" in action.lower()


class TestExtractId:
    def test_slash_separator(self):
        assert _extract_id("arn:aws:ec2:eu-central-1:123456789012:instance/i-abc123") == "i-abc123"

    def test_colon_separator(self):
        assert _extract_id("arn:aws:rds:eu-central-1:123456789012:db:mydb") == "mydb"

    def test_nested_slashes(self):
        assert _extract_id("arn:aws:ecs:eu-central-1:123:cluster/my-cluster/service-name") == "service-name"

    def test_empty_arn(self):
        assert _extract_id("") == ""

    def test_none_safe(self):
        assert _extract_id(None) == ""
