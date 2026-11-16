"""Tests for scanners/tag_compliance.py — pure tag validation logic."""

import pytest
from compliance_schema import LIFECYCLE_VALUES, REPOSITORY_PATTERN, REQUIRED_TAGS
from factories import make_resource
from scanners.tag_compliance import scan_tag_compliance


class TestScanTagCompliance:
    def test_all_tags_present(self):
        r = make_resource()
        findings = scan_tag_compliance([r], REQUIRED_TAGS)
        assert len(findings) == 0

    def test_missing_all_tags(self):
        r = make_resource(tags={})
        findings = scan_tag_compliance([r], REQUIRED_TAGS)
        assert len(findings) > 0
        f = findings[0]
        assert f["severity"] == "HIGH"
        assert f["category"] == "tag_compliance"

    def test_missing_one_critical_tag(self):
        tags = dict(make_resource()["tags"])
        del tags["ManagedBy"]
        r = make_resource(tags=tags)
        findings = scan_tag_compliance([r], REQUIRED_TAGS)
        assert len(findings) > 0

    def test_managed_by_manual_on_secret_is_ok(self):
        tags = dict(make_resource()["tags"])
        tags["ManagedBy"] = "manual"
        r = make_resource(
            resource_type="secretsmanager:secret",
            arn="arn:aws:secretsmanager:eu-central-1:123456789012:secret:my-secret-AbCdEf",
            tags=tags,
        )
        findings = scan_tag_compliance([r], REQUIRED_TAGS)
        managed_by_findings = [f for f in findings if "ManagedBy" in f.get("issue", "")]
        assert len(managed_by_findings) == 0

    def test_managed_by_invalid_on_ec2_is_finding(self):
        """ManagedBy value not in VALID_MANAGED_BY produces a finding."""
        tags = dict(make_resource()["tags"])
        tags["ManagedBy"] = "clickops"
        r = make_resource(tags=tags)
        findings = scan_tag_compliance([r], REQUIRED_TAGS)
        managed_by_findings = [
            f for f in findings if "ManagedBy" in f.get("issue", "") and "clickops" in f.get("issue", "").lower()
        ]
        assert len(managed_by_findings) >= 1

    @pytest.mark.parametrize(
        "rtype",
        [
            "ec2:route-table",
            "ec2:network-interface",
            "ec2:internet-gateway",
            "route53:hostedzone",
        ],
    )
    def test_untaggable_types_skipped(self, rtype):
        r = make_resource(
            resource_type=rtype,
            tags={},
            arn=f"arn:aws:{rtype.replace(':', ':')}:eu-central-1:123456789012:{rtype}/r-test",
        )
        findings = scan_tag_compliance([r], REQUIRED_TAGS)
        assert len(findings) == 0

    def test_empty_resources(self):
        findings = scan_tag_compliance([], REQUIRED_TAGS)
        assert len(findings) == 0

    def test_finding_has_expected_keys(self):
        r = make_resource(tags={})
        findings = scan_tag_compliance([r], REQUIRED_TAGS)
        assert len(findings) > 0
        f = findings[0]
        for key in ("category", "severity", "resource_arn", "resource_type", "issue"):
            assert key in f, f"Missing key: {key}"

    def test_repository_not_url(self):
        tags = dict(make_resource()["tags"])
        tags["Repository"] = "platform-infra"
        r = make_resource(tags=tags)
        findings = scan_tag_compliance([r], REQUIRED_TAGS)
        repo_findings = [f for f in findings if "Repository" in f.get("issue", "")]
        assert len(repo_findings) >= 1

    def test_repository_finding_names_contract_pattern(self):
        """The repository check uses the pattern generated from the contract."""
        tags = dict(make_resource()["tags"])
        tags["Repository"] = "ftp://example.com/repo.git"
        findings = scan_tag_compliance([make_resource(tags=tags)], REQUIRED_TAGS)
        repo_findings = [f for f in findings if "Repository" in f.get("issue", "")]
        assert len(repo_findings) == 1
        assert repo_findings[0]["severity"] == "MEDIUM"
        assert REPOSITORY_PATTERN in repo_findings[0]["issue"]

    @pytest.mark.parametrize("url", ["https://example.com/org/repo", "git@example.com:org/repo.git"])
    def test_repository_matching_pattern_is_clean(self, url):
        tags = dict(make_resource()["tags"])
        tags["Repository"] = url
        findings = scan_tag_compliance([make_resource(tags=tags)], REQUIRED_TAGS)
        assert findings == []

    def test_lifecycle_invalid_is_medium_finding(self):
        """A Lifecycle value outside the contract is a MEDIUM tag_compliance finding."""
        tags = dict(make_resource()["tags"])
        tags["Lifecycle"] = "forever"
        findings = scan_tag_compliance([make_resource(tags=tags)], REQUIRED_TAGS)
        lifecycle_findings = [f for f in findings if "Lifecycle" in f.get("issue", "")]
        assert len(lifecycle_findings) == 1
        finding = lifecycle_findings[0]
        assert finding["category"] == "tag_compliance"
        assert finding["severity"] == "MEDIUM"
        assert "forever" in finding["issue"]
        for value in LIFECYCLE_VALUES:
            assert value in finding["issue"]

    @pytest.mark.parametrize("value", LIFECYCLE_VALUES)
    def test_lifecycle_contract_values_are_clean(self, value):
        tags = dict(make_resource()["tags"])
        tags["Lifecycle"] = value
        findings = scan_tag_compliance([make_resource(tags=tags)], REQUIRED_TAGS)
        assert [f for f in findings if "Lifecycle" in f.get("issue", "")] == []

    def test_lifecycle_value_is_case_sensitive(self):
        tags = dict(make_resource()["tags"])
        tags["Lifecycle"] = "Persistent"
        findings = scan_tag_compliance([make_resource(tags=tags)], REQUIRED_TAGS)
        assert len([f for f in findings if "Lifecycle" in f.get("issue", "")]) == 1

    @pytest.mark.parametrize(
        "value",
        ["2026-12-01", "2026-12-01T00:00:00", "20261201t0000z", "2026-12-01T00:00:00+00:00", "next week"],
    )
    def test_expires_at_outside_the_contract_form_is_medium_finding(self, value):
        """A date alone, a timestamp without Z, the compact form and free text are all reported."""
        tags = {**make_resource()["tags"], "Lifecycle": "temporary", "ExpiresAt": value}
        findings = scan_tag_compliance([make_resource(tags=tags)], REQUIRED_TAGS)
        expiry_findings = [f for f in findings if f["issue"].startswith("ExpiresAt tag")]
        assert len(expiry_findings) == 1
        assert expiry_findings[0]["category"] == "tag_compliance"
        assert expiry_findings[0]["severity"] == "MEDIUM"
        assert value in expiry_findings[0]["issue"]

    @pytest.mark.parametrize("value", ["2026-12-01T00:00:00Z", "2026-12-01T00:00Z"])
    def test_expires_at_in_the_contract_form_is_clean(self, value):
        tags = {**make_resource()["tags"], "Lifecycle": "temporary", "ExpiresAt": value}
        assert scan_tag_compliance([make_resource(tags=tags)], REQUIRED_TAGS) == []

    def test_bad_expires_at_is_reported_when_required_tags_are_missing_too(self):
        """The expiry form is checked whatever else the resource carries."""
        findings = scan_tag_compliance([make_resource(tags={"ExpiresAt": "2026-12-01"})], REQUIRED_TAGS)
        assert [f["severity"] for f in findings if f["issue"].startswith("ExpiresAt tag")] == ["MEDIUM"]
        assert any("Missing required tags" in f["issue"] for f in findings)

    def test_date_only_expiry_is_never_silent(self):
        """A date-only expiry in the past is reported by both scans: wrong form, and expired."""
        from scanners.lifecycle_compliance import scan_lifecycle_compliance

        tags = {**make_resource()["tags"], "Lifecycle": "temporary", "ExpiresAt": "2020-12-01"}
        resource = make_resource(tags=tags)
        assert [f["severity"] for f in scan_tag_compliance([resource], REQUIRED_TAGS)] == ["MEDIUM"]
        assert [f["severity"] for f in scan_lifecycle_compliance([resource])] == ["HIGH"]

    def test_any_environment_is_accepted_when_the_contract_lists_none(self):
        tags = {**make_resource()["tags"], "Environment": "anything"}
        assert scan_tag_compliance([make_resource(tags=tags)], REQUIRED_TAGS) == []

    def test_environment_outside_the_contract_list_is_medium_finding(self, monkeypatch):
        monkeypatch.setattr("scanners.tag_compliance.ENVIRONMENT_ALLOWED", ["dev", "live"])
        tags = {**make_resource()["tags"], "Environment": "sandbox"}
        findings = scan_tag_compliance([make_resource(tags=tags)], REQUIRED_TAGS)
        assert len(findings) == 1
        assert findings[0]["severity"] == "MEDIUM"
        assert "Environment tag is 'sandbox'" in findings[0]["issue"]
        tags["Environment"] = "dev"
        assert scan_tag_compliance([make_resource(tags=tags)], REQUIRED_TAGS) == []

    @pytest.mark.parametrize("value", ["opentofu", "infrastructure-manager"])
    def test_managed_by_values_added_in_1_0(self, value):
        tags = {**make_resource()["tags"], "ManagedBy": value}
        assert scan_tag_compliance([make_resource(tags=tags)], REQUIRED_TAGS) == []

    def test_deployment_manager_is_not_a_managed_by_value(self):
        tags = {**make_resource()["tags"], "ManagedBy": "deployment-manager"}
        assert len(scan_tag_compliance([make_resource(tags=tags)], REQUIRED_TAGS)) == 1
