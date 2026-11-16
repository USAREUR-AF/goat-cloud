"""Tests for scanners/lifecycle_compliance.py — lifecycle tag validation logic."""

from datetime import UTC, datetime, timedelta

import pytest
from factories import make_resource
from scanners.lifecycle_compliance import scan_lifecycle_compliance


class TestScanLifecycleCompliance:
    def test_compliant_persistent_resource(self):
        """Resource with Lifecycle=persistent and no ExpiresAt is fine."""
        r = make_resource(tags={**make_resource()["tags"], "Lifecycle": "persistent"})
        findings = scan_lifecycle_compliance([r])
        assert len(findings) == 0

    def test_exempt_resource_type_skipped(self):
        """IAM roles and other exempt types produce no findings."""
        r = make_resource(
            resource_type="iam:role",
            arn="arn:aws:iam::123456789012:role/test-role",
            tags={},
        )
        findings = scan_lifecycle_compliance([r])
        assert len(findings) == 0

    def test_expired_resource_high_severity(self):
        """Resource past its ExpiresAt date produces a HIGH finding."""
        yesterday = (datetime.now(UTC) - timedelta(days=3)).isoformat()
        tags = {**make_resource()["tags"], "ExpiresAt": yesterday}
        r = make_resource(tags=tags)
        findings = scan_lifecycle_compliance([r])
        assert len(findings) == 1
        assert findings[0]["severity"] == "HIGH"
        assert findings[0]["category"] == "lifecycle_compliance"
        assert "expired" in findings[0]["issue"].lower()

    def test_not_yet_expired_no_finding(self):
        """Resource with future ExpiresAt produces no findings."""
        future = (datetime.now(UTC) + timedelta(days=30)).isoformat()
        tags = {**make_resource()["tags"], "ExpiresAt": future}
        r = make_resource(tags=tags)
        findings = scan_lifecycle_compliance([r])
        assert len(findings) == 0

    def test_unreadable_expires_at_counts_as_no_expiry(self):
        """A value that names no moment never reads as expired; tag_compliance reports its form."""
        tags = {**make_resource()["tags"], "Lifecycle": "temporary", "ExpiresAt": "not-a-date"}
        r = make_resource(tags=tags, resource_type="ec2:instance", running_duration_days=45)
        findings = scan_lifecycle_compliance([r])
        assert len(findings) == 1
        assert findings[0]["severity"] == "MEDIUM"
        assert "without ExpiresAt" in findings[0]["issue"]

    @pytest.mark.parametrize(
        "value",
        [
            "2020-12-01",  # a date alone
            "2020-12-01T00:00:00",  # no zone
            "2020-12-01T00:00",  # no zone, no seconds
            "20201201t0000z",  # the compact form of another cloud
            "2020-12-01T00:00:00Z",  # the contract's form
            "2020-12-01T00:00Z",  # the contract's form without seconds
            "2020-12-01T01:00:00+01:00",  # an offset
        ],
    )
    def test_past_expiry_in_any_readable_form_is_expired(self, value):
        """A date alone or a timestamp without a zone is taken as UTC and still expires."""
        tags = {**make_resource()["tags"], "Lifecycle": "temporary", "ExpiresAt": value}
        findings = scan_lifecycle_compliance([make_resource(tags=tags)])
        assert len(findings) == 1
        assert findings[0]["severity"] == "HIGH"
        assert "expired" in findings[0]["issue"].lower()
        assert value in findings[0]["issue"]

    @pytest.mark.parametrize("value", ["2099-12-01", "2099-12-01T00:00:00", "20991201t0000z", "2099-12-01T00:00:00Z"])
    def test_future_expiry_in_any_readable_form_is_not_expired(self, value):
        tags = {**make_resource()["tags"], "Lifecycle": "temporary", "ExpiresAt": value}
        assert scan_lifecycle_compliance([make_resource(tags=tags, running_duration_days=400)]) == []

    def test_experiment_without_expiry_over_30_days(self):
        """An experiment running >30 days without ExpiresAt = MEDIUM finding, as for temporary."""
        tags = {**make_resource()["tags"], "Lifecycle": "experiment"}
        findings = scan_lifecycle_compliance([make_resource(tags=tags, running_duration_days=45)])
        assert len(findings) == 1
        assert findings[0]["severity"] == "MEDIUM"
        assert findings[0]["issue"].startswith("Experiment resource running for 45 days")

    def test_experiment_with_expiry_is_clean(self):
        tags = {**make_resource()["tags"], "Lifecycle": "experiment", "ExpiresAt": "2099-01-01T00:00:00Z"}
        assert scan_lifecycle_compliance([make_resource(tags=tags, running_duration_days=45)]) == []

    def test_temporary_without_expiry_over_30_days(self):
        """Temporary resource running >30 days without ExpiresAt = MEDIUM finding."""
        tags = {**make_resource()["tags"], "Lifecycle": "temporary"}
        del tags["Repository"]  # keep it minimal
        r = make_resource(tags=tags, running_duration_days=45)
        findings = scan_lifecycle_compliance([r])
        assert len(findings) == 1
        assert findings[0]["severity"] == "MEDIUM"
        assert "temporary" in findings[0]["issue"].lower()

    def test_temporary_with_expiry_no_finding(self):
        """Temporary resource WITH ExpiresAt (in the future) is fine."""
        future = (datetime.now(UTC) + timedelta(days=30)).isoformat()
        tags = {
            **make_resource()["tags"],
            "Lifecycle": "temporary",
            "ExpiresAt": future,
        }
        r = make_resource(tags=tags, running_duration_days=45)
        findings = scan_lifecycle_compliance([r])
        assert len(findings) == 0

    def test_temporary_under_30_days_no_finding(self):
        """Temporary resource running <30 days without ExpiresAt is OK."""
        tags = {**make_resource()["tags"], "Lifecycle": "temporary"}
        r = make_resource(tags=tags, running_duration_days=15)
        findings = scan_lifecycle_compliance([r])
        assert len(findings) == 0

    def test_costly_type_no_lifecycle_over_60_days(self):
        """Costly resource with no Lifecycle tag running >60 days = LOW finding."""
        tags = {**make_resource()["tags"]}
        del tags["Lifecycle"]
        r = make_resource(
            tags=tags,
            resource_type="ec2:instance",
            running_duration_days=90,
            estimated_monthly_cost=50.0,
        )
        findings = scan_lifecycle_compliance([r])
        assert len(findings) == 1
        assert findings[0]["severity"] == "LOW"
        assert "$50.00/mo" in findings[0]["issue"]

    def test_costly_type_no_lifecycle_under_60_days(self):
        """Costly resource without Lifecycle but <60 days is OK."""
        tags = {**make_resource()["tags"]}
        del tags["Lifecycle"]
        r = make_resource(tags=tags, resource_type="rds:instance", running_duration_days=30)
        findings = scan_lifecycle_compliance([r])
        assert len(findings) == 0

    def test_non_costly_type_no_lifecycle_ignored(self):
        """Non-costly resource types without Lifecycle don't get check 3."""
        tags = {**make_resource()["tags"]}
        del tags["Lifecycle"]
        r = make_resource(
            tags=tags,
            resource_type="s3:bucket",
            arn="arn:aws:s3:::test-bucket",
            running_duration_days=120,
        )
        findings = scan_lifecycle_compliance([r])
        assert len(findings) == 0

    def test_multiple_resources_mixed(self):
        """Mix of compliant and non-compliant resources."""
        yesterday = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        resources = [
            make_resource(
                arn="arn:aws:ec2:eu-central-1:123456789012:instance/i-good",
                tags={**make_resource()["tags"], "Lifecycle": "persistent"},
            ),
            make_resource(
                arn="arn:aws:ec2:eu-central-1:123456789012:instance/i-expired",
                tags={**make_resource()["tags"], "ExpiresAt": yesterday},
            ),
            make_resource(
                resource_type="iam:role",
                arn="arn:aws:iam::123456789012:role/exempt",
                tags={},
            ),
        ]
        findings = scan_lifecycle_compliance(resources)
        assert len(findings) == 1
        assert findings[0]["resource_arn"].endswith("i-expired")

    def test_finding_structure(self):
        """Finding dict has all expected keys."""
        yesterday = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        tags = {**make_resource()["tags"], "ExpiresAt": yesterday}
        r = make_resource(tags=tags)
        findings = scan_lifecycle_compliance([r])
        f = findings[0]
        assert "category" in f
        assert "severity" in f
        assert "resource_arn" in f
        assert "resource_type" in f
        assert "recommendation" in f
        assert "tags" in f

    def test_empty_resources(self):
        """Empty resource list produces no findings."""
        findings = scan_lifecycle_compliance([])
        assert findings == []

    def test_no_cost_in_message_when_none(self):
        """When estimated_monthly_cost is None, no cost appears in issue."""
        tags = {**make_resource()["tags"]}
        del tags["Lifecycle"]
        r = make_resource(
            tags=tags,
            resource_type="ec2:instance",
            running_duration_days=90,
            estimated_monthly_cost=None,
        )
        findings = scan_lifecycle_compliance([r])
        assert len(findings) == 1
        assert "$" not in findings[0]["issue"]
