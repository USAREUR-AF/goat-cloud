"""Tests for compliance-scanner/lambda/delta.py — pure report comparison logic."""

import pytest
from delta import _extract_findings, _finding_cost, _sum_burn, compute_delta
from factories import make_finding, make_report, make_resource


class TestComputeDelta:
    def test_identical_reports(self):
        report = make_report()
        delta = compute_delta(report, report)
        s = delta["summary"]
        assert s["new_count"] == 0
        assert s["resolved_count"] == 0
        assert s["persistent_count"] == s["today_total"]

    def test_new_finding(self):
        yesterday = make_report(num_drift=1, num_tag=0)
        today = make_report(num_drift=2, num_tag=0)
        delta = compute_delta(today, yesterday)
        assert delta["summary"]["new_count"] == 1
        assert len(delta["new_findings"]) == 1

    def test_resolved_finding(self):
        yesterday = make_report(num_drift=2, num_tag=0)
        today = make_report(num_drift=1, num_tag=0)
        delta = compute_delta(today, yesterday)
        assert delta["summary"]["resolved_count"] == 1
        assert len(delta["resolved_findings"]) == 1

    def test_severity_change(self):
        arn = "arn:aws:ec2:eu-central-1:123456789012:instance/i-shared"
        f_high = make_finding(resource_arn=arn, severity="HIGH")
        f_medium = make_finding(resource_arn=arn, severity="MEDIUM")
        yesterday = make_report(
            num_drift=0, num_tag=0, drift_detection={"findings": [f_high], "terraform_managed": 0, "unmanaged": 1}
        )
        today = make_report(
            num_drift=0, num_tag=0, drift_detection={"findings": [f_medium], "terraform_managed": 0, "unmanaged": 1}
        )
        delta = compute_delta(today, yesterday)
        assert len(delta["severity_changes"]) == 1
        assert delta["severity_changes"][0]["previous_severity"] == "HIGH"
        assert delta["severity_changes"][0]["severity"] == "MEDIUM"

    def test_multi_category_merge(self):
        arn = "arn:aws:ec2:eu-central-1:123456789012:instance/i-both"
        drift_f = make_finding(category="drift_detection", severity="MEDIUM", resource_arn=arn)
        tag_f = make_finding(category="tag_compliance", severity="HIGH", resource_arn=arn)
        report = make_report(
            num_drift=0,
            num_tag=0,
            drift_detection={"findings": [drift_f], "terraform_managed": 0, "unmanaged": 1},
            tag_compliance={"findings": [tag_f]},
        )
        findings = _extract_findings(report)
        assert arn in findings
        assert findings[arn]["severity"] == "HIGH"

    def test_both_empty(self):
        empty = make_report(num_drift=0, num_tag=0)
        empty["discovered_resources"] = []
        delta = compute_delta(empty, empty)
        s = delta["summary"]
        assert s["new_count"] == 0
        assert s["resolved_count"] == 0
        assert s["persistent_count"] == 0

    def test_empty_yesterday(self):
        today = make_report(num_drift=2, num_tag=1)
        empty = make_report(num_drift=0, num_tag=0)
        empty["discovered_resources"] = []
        delta = compute_delta(today, empty)
        assert delta["summary"]["new_count"] == 3
        assert delta["summary"]["resolved_count"] == 0

    def test_empty_today(self):
        yesterday = make_report(num_drift=2, num_tag=1)
        empty = make_report(num_drift=0, num_tag=0)
        empty["discovered_resources"] = []
        delta = compute_delta(empty, yesterday)
        assert delta["summary"]["resolved_count"] == 3
        assert delta["summary"]["new_count"] == 0

    def test_burn_excludes_inactive(self):
        f_running = make_finding(
            resource_arn="arn:aws:ec2:eu-central-1:123456789012:instance/i-running",
            state="running",
            estimated_monthly_cost=100.0,
        )
        f_terminated = make_finding(
            resource_arn="arn:aws:ec2:eu-central-1:123456789012:instance/i-terminated",
            state="terminated",
            estimated_monthly_cost=200.0,
        )
        findings = {
            f_running["resource_arn"]: f_running,
            f_terminated["resource_arn"]: f_terminated,
        }
        assert _sum_burn(findings) == 100.0

    def test_sort_order_cost_desc(self):
        f_cheap = make_finding(
            resource_arn="arn:aws:ec2:eu-central-1:123456789012:instance/i-cheap", estimated_monthly_cost=10.0
        )
        f_expensive = make_finding(
            resource_arn="arn:aws:ec2:eu-central-1:123456789012:instance/i-expensive", estimated_monthly_cost=500.0
        )
        today = make_report(
            num_drift=0,
            num_tag=0,
            drift_detection={"findings": [f_cheap, f_expensive], "terraform_managed": 0, "unmanaged": 2},
        )
        empty = make_report(num_drift=0, num_tag=0)
        empty["discovered_resources"] = []
        delta = compute_delta(today, empty)
        assert delta["new_findings"][0]["estimated_monthly_cost"] == 500.0

    def test_compliant_count(self):
        r1 = make_resource(arn="arn:aws:ec2:eu-central-1:123456789012:instance/i-clean")
        r2 = make_resource(arn="arn:aws:ec2:eu-central-1:123456789012:instance/i-dirty")
        f = make_finding(resource_arn=r2["arn"])

        today = make_report(
            num_drift=0,
            num_tag=0,
            discovered_resources=[r1, r2],
            drift_detection={"findings": [f], "terraform_managed": 0, "unmanaged": 1},
        )
        yesterday = make_report(num_drift=0, num_tag=0, discovered_resources=[])
        delta = compute_delta(today, yesterday)
        assert delta["summary"]["new_compliant_count"] == 1


class TestFindingCost:
    def test_float(self):
        assert _finding_cost({"estimated_monthly_cost": 42.5}) == 42.5

    def test_none(self):
        assert _finding_cost({"estimated_monthly_cost": None}) == 0.0

    def test_zero(self):
        assert _finding_cost({"estimated_monthly_cost": 0}) == 0.0

    def test_missing_key(self):
        assert _finding_cost({}) == 0.0

    @pytest.mark.parametrize(
        "cost",
        [None, 0, 0.0, -1, -99.9, 1e10, 1e-10, float("inf"), float("-inf"), 42, 99999],
    )
    def test_never_raises_for_numeric(self, cost):
        """_finding_cost should return a float and never raise for any numeric input."""
        result = _finding_cost({"estimated_monthly_cost": cost})
        assert isinstance(result, float)
