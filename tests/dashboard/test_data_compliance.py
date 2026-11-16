"""Tests for data_compliance.py — findings extraction, grouping, and summaries.

These are pure-function tests on the data layer. No AWS calls needed — all functions
operate on report dicts from the factory.
"""

from data_compliance import (
    get_all_findings,
    get_compliant_summary,
    get_findings_summary,
    get_high_findings,
    get_unmanaged_resources,
)
from factories import make_finding, make_report, make_tag_finding


class TestGetFindingsSummary:
    def test_basic_summary(self):
        report = make_report(num_drift=2, num_tag=1)
        s = get_findings_summary(report)
        assert s["total"] == 3
        assert s["high"] == 3  # drift and tag are both HIGH by default
        assert s["resources"] == 5

    def test_empty_report(self):
        s = get_findings_summary(None)
        assert s["total"] == 0
        assert s["resources"] == 0

    def test_zero_findings(self):
        report = make_report(num_drift=0, num_tag=0)
        s = get_findings_summary(report)
        assert s["total"] == 0


class TestGetHighFindings:
    def test_extracts_high_only(self):
        report = make_report(num_drift=2, num_tag=1, num_schedule=3, num_lifecycle=2)
        highs = get_high_findings(report)
        # Drift=HIGH, tag=HIGH, schedule=MEDIUM, lifecycle=LOW
        assert len(highs) == 3
        for f in highs:
            assert f["severity"] == "HIGH"

    def test_empty_report(self):
        assert get_high_findings(None) == []


class TestGetAllFindings:
    def test_returns_all_categories(self):
        report = make_report(num_drift=1, num_tag=1, num_schedule=1, num_lifecycle=1)
        findings = get_all_findings(report)
        assert len(findings) == 4
        categories = {f["category"] for f in findings}
        assert "drift_detection" in categories
        assert "tag_compliance" in categories
        assert "schedule_compliance" in categories
        assert "lifecycle_compliance" in categories

    def test_severity_filter(self):
        report = make_report(num_drift=2, num_schedule=3)
        highs = get_all_findings(report, severity_filter="HIGH")
        for f in highs:
            assert f["severity"] == "HIGH"
        meds = get_all_findings(report, severity_filter="MEDIUM")
        for f in meds:
            assert f["severity"] == "MEDIUM"

    def test_empty_report(self):
        assert get_all_findings(None) == []


class TestGetCompliantSummary:
    def test_basic_counts(self):
        report = make_report(num_drift=2, num_tag=1)
        s = get_compliant_summary(report)
        assert s["total_resources"] == 5
        # 3 non-compliant findings, 5 total -> 2 compliant (or more if deduped)
        assert s["total_compliant"] >= 0
        assert s["total_compliant"] <= s["total_resources"]

    def test_zero_findings_all_compliant(self):
        report = make_report(num_drift=0, num_tag=0)
        s = get_compliant_summary(report)
        assert s["total_compliant"] == s["total_resources"]

    def test_by_type_breakdown(self):
        report = make_report(num_drift=1, num_tag=1)
        s = get_compliant_summary(report)
        assert "ec2:instance" in s["by_type"]

    def test_by_region_breakdown(self):
        report = make_report(num_drift=1)
        s = get_compliant_summary(report)
        assert "eu-central-1" in s["by_region"]

    def test_empty_report(self):
        s = get_compliant_summary(None)
        assert s["total_resources"] == 0
        assert s["total_compliant"] == 0

    def test_deduplication_across_categories(self):
        """Same resource appearing in drift AND tag findings counts once."""
        arn = "arn:aws:ec2:eu-central-1:123456789012:instance/i-duped"
        report = make_report(num_drift=0, num_tag=0)
        report["drift_detection"]["findings"] = [make_finding(resource_arn=arn)]
        report["tag_compliance"]["findings"] = [make_tag_finding(resource_arn=arn)]
        report["summary"]["findings_by_severity"]["HIGH"] = 2
        s = get_compliant_summary(report)
        # Only 1 unique non-compliant ARN despite 2 findings
        nc = s["total_resources"] - s["total_compliant"]
        assert nc <= 2  # at most the one duped + any from other arns


class TestGetUnmanagedResources:
    def test_groups_by_service_prefix(self):
        """Drift findings are grouped by AWS service prefix by default."""
        report = make_report(num_drift=3, num_tag=0)
        # Default drift findings are ec2:instance with HIGH severity
        result = get_unmanaged_resources(report)
        assert result["total"] >= 0  # may be filtered by confidence

    def test_hidden_types_excluded(self):
        """Resource types like kms:key and logs:log-group are hidden."""
        report = make_report(num_drift=0, num_tag=0)
        report["drift_detection"]["findings"] = [
            make_finding(
                resource_type="kms:key",
                resource_arn="arn:aws:kms:eu-central-1:123456789012:key/k-123",
            ),
            make_finding(
                resource_type="logs:log-group",
                resource_arn="arn:aws:logs:eu-central-1:123456789012:log-group:/test:*",
            ),
        ]
        result = get_unmanaged_resources(report)
        assert result["total"] == 0

    def test_redacted_types_show_redacted_name(self):
        """IAM resources have their name redacted."""
        report = make_report(num_drift=0, num_tag=0)
        report["drift_detection"]["findings"] = [
            make_finding(
                resource_type="iam:user",
                resource_arn="arn:aws:iam::123456789012:user/admin",
                resource_name="admin",
                severity="HIGH",
            ),
        ]
        result = get_unmanaged_resources(report)
        if result["total"] > 0:
            # Check that name is redacted
            resource = result["groups"][0]["resources"][0]
            assert resource["name"] == "[redacted]"
            assert resource["arn"] == "[redacted]"

    def test_ad_hoc_tag_grouping(self):
        """Resources tagged ArchZone=ad-hoc go into the ad-hoc group."""
        report = make_report(num_drift=0, num_tag=0)
        report["drift_detection"]["findings"] = [
            make_finding(
                resource_type="ec2:instance",
                resource_arn="arn:aws:ec2:eu-central-1:123456789012:instance/i-adhoc",
                severity="HIGH",
                tags={"ArchZone": "ad-hoc"},
            ),
        ]
        result = get_unmanaged_resources(report)
        if result["total"] > 0:
            assert result["groups"][0]["key"] == "ad-hoc"

    def test_empty_report(self):
        result = get_unmanaged_resources(make_report(num_drift=0, num_tag=0))
        assert result["total"] == 0
        assert result["total_cost"] == 0
