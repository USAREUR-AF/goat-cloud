"""Tests for scanners/schedule_compliance.py — off-hours scheduling checks."""

import pytest
from factories import make_resource
from scanners.schedule_compliance import scan_schedule_compliance


class TestScanScheduleCompliance:
    def test_non_schedulable_type_skipped(self):
        """S3 buckets and other non-schedulable types are ignored."""
        r = make_resource(resource_type="s3:bucket", arn="arn:aws:s3:::test")
        findings = scan_schedule_compliance([r])
        assert len(findings) == 0

    def test_persistent_lifecycle_opted_out(self):
        """Resources with Lifecycle=persistent are exempt from scheduling."""
        r = make_resource(
            resource_type="ec2:instance",
            tags={**make_resource()["tags"], "Lifecycle": "persistent"},
        )
        findings = scan_schedule_compliance([r])
        assert len(findings) == 0

    def test_stopped_resource_skipped(self):
        """Stopped/inactive resources don't need scheduling."""
        for state in ("stopped", "terminated", "deleted", "inactive"):
            r = make_resource(resource_type="ec2:instance", state=state, tags={})
            findings = scan_schedule_compliance([r])
            assert len(findings) == 0, f"Expected no findings for state={state}"

    def test_running_ec2_without_schedule_tag(self):
        """Running EC2 without schedule tags produces a finding."""
        tags = {"Project": "test", "Environment": "dev"}
        r = make_resource(
            resource_type="ec2:instance",
            state="running",
            tags=tags,
            estimated_monthly_cost=50.0,
            running_duration_days=30,
        )
        findings = scan_schedule_compliance([r])
        assert len(findings) == 1
        assert findings[0]["category"] == "schedule_compliance"
        assert findings[0]["severity"] == "MEDIUM"
        assert "$50.00/mo" in findings[0]["issue"]

    def test_low_cost_resource_is_low_severity(self):
        """Resources costing <= $10/mo get LOW severity."""
        r = make_resource(
            resource_type="ec2:instance",
            state="running",
            tags={},
            estimated_monthly_cost=5.0,
        )
        findings = scan_schedule_compliance([r])
        assert len(findings) == 1
        assert findings[0]["severity"] == "LOW"

    def test_no_cost_is_low_severity(self):
        """Resources with unknown cost get LOW severity."""
        r = make_resource(
            resource_type="rds:instance",
            state="available",
            tags={},
            estimated_monthly_cost=None,
        )
        findings = scan_schedule_compliance([r])
        assert len(findings) == 1
        assert findings[0]["severity"] == "LOW"
        assert "unknown" in findings[0]["issue"]

    def test_schedule_tag_present(self):
        """Resource with a 'Schedule' tag is not flagged."""
        r = make_resource(
            resource_type="ec2:instance",
            state="running",
            tags={"Schedule": "businesshours"},
        )
        findings = scan_schedule_compliance([r])
        assert len(findings) == 0

    def test_contract_schedule_tag_satisfies_the_check(self):
        """The contract's Schedule tag produces no finding."""
        r = make_resource(resource_type="ec2:instance", state="running", tags={"Schedule": "businesshours"})
        assert scan_schedule_compliance([r]) == []

    @pytest.mark.parametrize("key", ["AutoStop", "AutoShutdown", "BusinessHours", "OffHours", "schedule"])
    def test_legacy_schedule_tag_is_accepted_and_reported_low(self, key):
        """An earlier tag name satisfies the check and gets a LOW finding naming the contract key."""
        r = make_resource(resource_type="ecs:service", state="running", tags={key: "9-17"}, estimated_monthly_cost=500)
        findings = scan_schedule_compliance([r])
        assert len(findings) == 1
        finding = findings[0]
        assert finding["category"] == "schedule_compliance"
        assert finding["severity"] == "LOW"
        assert key in finding["issue"] and "contract's key is Schedule" in finding["issue"]
        assert "without schedule tags" not in finding["issue"]

    def test_contract_tag_beside_a_legacy_tag_is_clean(self):
        r = make_resource(resource_type="ec2:instance", state="running", tags={"Schedule": "x", "AutoStop": "true"})
        assert scan_schedule_compliance([r]) == []

    @pytest.mark.parametrize("lifecycle", ["temporary", "experiment"])
    def test_expiring_lifecycle_with_expiry_is_skipped(self, lifecycle):
        tags = {"Lifecycle": lifecycle, "ExpiresAt": "2099-01-01T00:00:00Z"}
        r = make_resource(resource_type="ec2:instance", state="running", tags=tags)
        assert scan_schedule_compliance([r]) == []

    def test_expiring_lifecycle_with_unreadable_expiry_is_not_skipped(self):
        tags = {"Lifecycle": "temporary", "ExpiresAt": "next week"}
        r = make_resource(resource_type="ec2:instance", state="running", tags=tags)
        assert len(scan_schedule_compliance([r])) == 1

    def test_all_schedulable_types(self):
        """All four schedulable types produce findings when untagged."""
        types = ["ec2:instance", "rds:instance", "ecs:service", "elasticache:cluster"]
        for rt in types:
            r = make_resource(resource_type=rt, state="running", tags={})
            findings = scan_schedule_compliance([r])
            assert len(findings) == 1, f"Expected finding for {rt}"

    def test_finding_structure(self):
        """Finding dict has all expected fields."""
        r = make_resource(resource_type="ec2:instance", state="running", tags={})
        findings = scan_schedule_compliance([r])
        f = findings[0]
        assert f["category"] == "schedule_compliance"
        assert "resource_arn" in f
        assert "resource_type" in f
        assert "recommendation" in f
        assert "Lifecycle=persistent" in f["recommendation"]

    def test_empty_resources(self):
        """Empty resource list produces no findings."""
        findings = scan_schedule_compliance([])
        assert findings == []

    def test_multiple_mixed_resources(self):
        """Mix of schedulable and non-schedulable, tagged and untagged."""
        resources = [
            make_resource(resource_type="s3:bucket", arn="arn:aws:s3:::skip", tags={}),
            make_resource(
                resource_type="ec2:instance",
                arn="arn:aws:ec2:eu-central-1:123456789012:instance/i-flagged",
                state="running",
                tags={},
            ),
            make_resource(
                resource_type="ec2:instance",
                arn="arn:aws:ec2:eu-central-1:123456789012:instance/i-scheduled",
                state="running",
                tags={"Schedule": "24x7"},
            ),
            make_resource(
                resource_type="rds:instance",
                arn="arn:aws:rds:eu-central-1:123456789012:db:stopped-db",
                state="stopped",
                tags={},
            ),
        ]
        findings = scan_schedule_compliance(resources)
        assert len(findings) == 1
        assert findings[0]["resource_arn"].endswith("i-flagged")
