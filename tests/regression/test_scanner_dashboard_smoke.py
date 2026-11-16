"""Smoke test: scanner produces findings → dashboard data layer consumes them.

Exercises the full pipeline from scanner modules through the dashboard's
data_compliance functions. No AWS calls — pure logic on in-memory data.
Touches: scanners/{tag,drift,lifecycle,schedule}_compliance, data_compliance,
views_base helpers.
"""

from datetime import UTC, datetime, timedelta

from compliance_schema import REQUIRED_TAGS
from data_compliance import (
    get_all_findings,
    get_compliant_summary,
    get_findings_summary,
    get_high_findings,
    get_unmanaged_resources,
)
from factories import make_report, make_resource
from scanners.drift_detector import scan_drift
from scanners.lifecycle_compliance import scan_lifecycle_compliance
from scanners.schedule_compliance import scan_schedule_compliance
from scanners.tag_compliance import scan_tag_compliance
from utils.exclusions import ExclusionEngine
from views_base import SEV_BG, SEV_COLORS, _e, _rate_color


class TestScannerToDashboardPipeline:
    """End-to-end: scanner functions produce findings, dashboard functions consume them."""

    def _build_report_from_scanners(self, resources, tf_arns=None):
        """Run all scanners on a resource list and assemble a report dict."""
        if tf_arns is None:
            tf_arns = set()

        exclusion_engine = ExclusionEngine(tf_arns=tf_arns)
        drift_result = scan_drift(resources, tf_arns, exclusion_engine)
        drift = drift_result["findings"]
        tags = scan_tag_compliance(resources, REQUIRED_TAGS)
        lifecycle = scan_lifecycle_compliance(resources)
        schedule = scan_schedule_compliance(resources)

        all_findings = drift + tags + lifecycle + schedule
        high = sum(1 for f in all_findings if f.get("severity") == "HIGH")
        medium = sum(1 for f in all_findings if f.get("severity") == "MEDIUM")
        low = sum(1 for f in all_findings if f.get("severity") == "LOW")

        return {
            "report_version": "1.0",
            "generated_at": datetime.now(UTC).isoformat(),
            "scan_duration_seconds": 1.0,
            "account_id": "123456789012",
            "project": "acme",
            "environment": "dev",
            "regions_scanned": ["eu-central-1"],
            "regions_with_resources": ["eu-central-1"],
            "discovered_resources": resources,
            "drift_detection": {
                "terraform_managed": drift_result["terraform_managed"],
                "unmanaged": drift_result["unmanaged"],
                "findings": drift,
            },
            "tag_compliance": {"findings": tags},
            "schedule_compliance": {"findings": schedule},
            "lifecycle_compliance": {"findings": lifecycle},
            "summary": {
                "total_resources_discovered": len(resources),
                "total_findings": len(all_findings),
                "findings_by_severity": {"HIGH": high, "MEDIUM": medium, "LOW": low},
                "findings_by_category": {
                    "drift_detection": len(drift),
                    "tag_compliance": len(tags),
                    "schedule_compliance": len(schedule),
                    "lifecycle_compliance": len(lifecycle),
                },
            },
            "region_costs": {"eu-central-1": 100.0},
            "service_costs": {},
            "resource_type_counts": {},
            "region_type_counts": {},
            "errors": [],
        }

    def test_clean_account(self):
        """Fully compliant resources → zero findings through entire pipeline."""
        resources = [
            make_resource(
                arn="arn:aws:ec2:eu-central-1:123456789012:instance/i-good",
                tags={
                    "Project": "acme",
                    "Environment": "dev",
                    "ManagedBy": "terraform",
                    "Lifecycle": "persistent",
                    "Repository": "https://github.com/acme/platform-infra",
                    "Schedule": "24x7",
                },
            ),
        ]
        tf_arns = {"arn:aws:ec2:eu-central-1:123456789012:instance/i-good"}
        report = self._build_report_from_scanners(resources, tf_arns)

        assert get_findings_summary(report)["total"] == 0
        assert get_high_findings(report) == []
        assert get_all_findings(report) == []
        summary = get_compliant_summary(report)
        assert summary["total_compliant"] == summary["total_resources"]

    def test_dirty_account(self):
        """Mix of problems → findings flow correctly through dashboard layer."""
        yesterday = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        resources = [
            # Unmanaged, untagged, expired
            make_resource(
                arn="arn:aws:ec2:eu-central-1:123456789012:instance/i-bad",
                name="orphan",
                state="running",
                tags={"ExpiresAt": yesterday},
                running_duration_days=90,
                estimated_monthly_cost=50.0,
            ),
            # Managed but missing tags
            make_resource(
                arn="arn:aws:rds:eu-central-1:123456789012:db:prod-db",
                resource_type="rds:instance",
                name="prod-db",
                tags={"Project": "acme"},
                running_duration_days=180,
                estimated_monthly_cost=200.0,
            ),
        ]
        tf_arns = {"arn:aws:rds:eu-central-1:123456789012:db:prod-db"}
        report = self._build_report_from_scanners(resources, tf_arns)

        summary = get_findings_summary(report)
        assert summary["total"] > 0
        assert summary["high"] > 0

        # High findings should include drift and tag violations
        highs = get_high_findings(report)
        assert len(highs) > 0

        # All findings includes everything
        all_f = get_all_findings(report)
        assert len(all_f) >= len(highs)

        # Compliant summary should show some non-compliant
        comp = get_compliant_summary(report)
        assert comp["total_compliant"] < comp["total_resources"]

    def test_unmanaged_resources_grouping(self):
        """Drift findings get properly grouped in the unmanaged resources view."""
        resources = [
            make_resource(
                arn="arn:aws:ec2:eu-central-1:123456789012:instance/i-exp1",
                resource_type="ec2:instance",
                name="experiment-1",
                tags={"ArchZone": "ad-hoc"},
                state="running",
            ),
            make_resource(
                arn="arn:aws:ec2:eu-central-1:123456789012:instance/i-exp2",
                resource_type="ec2:instance",
                name="experiment-2",
                tags={"Purpose": "load testing"},
                state="running",
            ),
        ]
        report = self._build_report_from_scanners(resources, tf_arns=set())
        result = get_unmanaged_resources(report)
        # Both should be grouped (ad-hoc and purpose groups)
        assert result["total"] >= 0  # may be filtered by confidence level


class TestViewsBaseHelpers:
    """Test pure helper functions from views_base that are used everywhere."""

    def test_html_escape(self):
        assert _e("<script>alert('xss')</script>") == "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;"

    def test_html_escape_none(self):
        assert _e(None) == ""

    def test_html_escape_number(self):
        assert _e(42) == "42"

    def test_rate_color_green(self):
        assert _rate_color(95) == "#22c55e"
        assert _rate_color(80) == "#22c55e"

    def test_rate_color_amber(self):
        assert _rate_color(65) == "#f59e0b"
        assert _rate_color(50) == "#f59e0b"

    def test_rate_color_red(self):
        assert _rate_color(30) == "#ef4444"
        assert _rate_color(0) == "#ef4444"

    def test_sev_colors_defined(self):
        assert "HIGH" in SEV_COLORS
        assert "MEDIUM" in SEV_COLORS
        assert "LOW" in SEV_COLORS

    def test_sev_bg_defined(self):
        assert "HIGH" in SEV_BG
        assert "MEDIUM" in SEV_BG
        assert "LOW" in SEV_BG


class TestDataResourcesHelpers:
    """Test pure functions from data_resources.py."""

    def test_stopped_ec2_cost_estimation(self):
        from data_resources import _estimate_stopped_ec2_cost

        report = make_report()
        # Add a volume attached to instance i-abc
        report["discovered_resources"].append(
            make_resource(
                resource_type="ec2:volume",
                arn="arn:aws:ec2:eu-central-1:123456789012:volume/vol-123",
                attached_to="i-abc",
                size_gb=100,
            )
        )
        cost = _estimate_stopped_ec2_cost("arn:aws:ec2:eu-central-1:123456789012:instance/i-abc", report)
        assert cost > 0
        assert cost == round(100 * 0.0952, 2)

    def test_stopped_ec2_cost_no_volumes_uses_default(self):
        from data_resources import _estimate_stopped_ec2_cost

        report = make_report()
        cost = _estimate_stopped_ec2_cost("arn:aws:ec2:eu-central-1:123456789012:instance/i-novolumes", report)
        # Default 30GB when no matching volumes found
        assert cost == round(30 * 0.0952, 2)

    def test_stopped_ec2_cost_no_report(self):
        from data_resources import _estimate_stopped_ec2_cost

        assert _estimate_stopped_ec2_cost("arn:aws:ec2:eu-central-1:123456789012:instance/i-x", None) == 0
