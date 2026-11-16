"""Tests for hunterkiller index.py — _extract_actionable_findings.

Uses hk_index fixture to avoid collision with scanner's index.py.
"""

from factories import make_finding


def _report_with_severities(*severities, states=None):
    """Build a report with findings at the given severities."""
    states = states or {}
    findings = []
    for i, sev in enumerate(severities):
        f = make_finding(
            severity=sev,
            resource_arn=f"arn:aws:ec2:eu-central-1:123456789012:instance/i-{i:06d}",
            state=states.get(i, "running"),
        )
        findings.append(f)
    return {
        "drift_detection": {"findings": findings},
        "tag_compliance": {"findings": []},
        "schedule_compliance": {"findings": []},
        "lifecycle_compliance": {"findings": []},
    }


class TestExtractActionableFindings:
    def test_high_only(self, hk_index):
        report = _report_with_severities("HIGH", "MEDIUM", "LOW")
        result = hk_index._extract_actionable_findings(report, min_severity="HIGH")
        assert len(result) == 1
        assert result[0]["severity"] == "HIGH"

    def test_medium_includes_high_and_medium(self, hk_index):
        report = _report_with_severities("HIGH", "MEDIUM", "LOW")
        result = hk_index._extract_actionable_findings(report, min_severity="MEDIUM")
        assert len(result) == 2
        severities = {f["severity"] for f in result}
        assert severities == {"HIGH", "MEDIUM"}

    def test_all_includes_everything(self, hk_index):
        report = _report_with_severities("HIGH", "MEDIUM", "LOW")
        result = hk_index._extract_actionable_findings(report, min_severity="ALL")
        assert len(result) == 3

    def test_inactive_states_filtered(self, hk_index):
        report = _report_with_severities(
            "HIGH",
            "HIGH",
            "HIGH",
            "HIGH",
            "HIGH",
            states={0: "terminated", 1: "stopped", 2: "deleted", 3: "deleting", 4: "shutting-down"},
        )
        result = hk_index._extract_actionable_findings(report, min_severity="HIGH")
        assert len(result) == 0

    def test_running_state_included(self, hk_index):
        report = _report_with_severities("HIGH", states={0: "running"})
        result = hk_index._extract_actionable_findings(report, min_severity="HIGH")
        assert len(result) == 1

    def test_empty_state_treated_as_active(self, hk_index):
        report = _report_with_severities("HIGH", states={0: ""})
        result = hk_index._extract_actionable_findings(report, min_severity="HIGH")
        assert len(result) == 1

    def test_all_four_categories_scanned(self, hk_index):
        report = {
            "drift_detection": {"findings": [make_finding(severity="HIGH", resource_arn="arn:1")]},
            "tag_compliance": {
                "findings": [make_finding(category="tag_compliance", severity="HIGH", resource_arn="arn:2")]
            },
            "schedule_compliance": {
                "findings": [make_finding(category="schedule_compliance", severity="HIGH", resource_arn="arn:3")]
            },
            "lifecycle_compliance": {
                "findings": [make_finding(category="lifecycle_compliance", severity="HIGH", resource_arn="arn:4")]
            },
        }
        result = hk_index._extract_actionable_findings(report, min_severity="HIGH")
        assert len(result) == 4

    def test_empty_report(self, hk_index):
        report = {
            "drift_detection": {"findings": []},
            "tag_compliance": {"findings": []},
            "schedule_compliance": {"findings": []},
            "lifecycle_compliance": {"findings": []},
        }
        result = hk_index._extract_actionable_findings(report, min_severity="ALL")
        assert len(result) == 0
