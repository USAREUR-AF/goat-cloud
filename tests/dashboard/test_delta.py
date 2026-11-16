"""Tests for compliance-dashboard/lambda/delta.py — resolution field logic.

The dashboard delta.py is the SAME file as the scanner delta.py (shared code).
These tests focus on the 'resolution' field added for the /changes page:
  - resolved + ARN still in today_discovered → resolution="compliant"
  - resolved + ARN NOT in today_discovered → resolution="deleted"
"""

from factories import make_finding, make_resource


class TestResolvedResolution:
    def test_resolved_still_discovered_is_compliant(self, dashboard_delta):
        arn = "arn:aws:ec2:eu-central-1:123456789012:instance/i-compliant"
        yesterday = {
            "drift_detection": {"findings": [make_finding(resource_arn=arn)]},
            "tag_compliance": {"findings": []},
            "schedule_compliance": {"findings": []},
            "lifecycle_compliance": {"findings": []},
            "discovered_resources": [make_resource(arn=arn)],
        }
        today = {
            "drift_detection": {"findings": []},
            "tag_compliance": {"findings": []},
            "schedule_compliance": {"findings": []},
            "lifecycle_compliance": {"findings": []},
            "discovered_resources": [make_resource(arn=arn)],  # still discovered
        }
        delta = dashboard_delta.compute_delta(today, yesterday, include_resolution=True)
        assert len(delta["resolved_findings"]) == 1
        assert delta["resolved_findings"][0]["resolution"] == "compliant"

    def test_resolved_not_discovered_is_deleted(self, dashboard_delta):
        arn = "arn:aws:ec2:eu-central-1:123456789012:instance/i-deleted"
        yesterday = {
            "drift_detection": {"findings": [make_finding(resource_arn=arn)]},
            "tag_compliance": {"findings": []},
            "schedule_compliance": {"findings": []},
            "lifecycle_compliance": {"findings": []},
            "discovered_resources": [make_resource(arn=arn)],
        }
        today = {
            "drift_detection": {"findings": []},
            "tag_compliance": {"findings": []},
            "schedule_compliance": {"findings": []},
            "lifecycle_compliance": {"findings": []},
            "discovered_resources": [],  # not discovered → deleted
        }
        delta = dashboard_delta.compute_delta(today, yesterday, include_resolution=True)
        assert len(delta["resolved_findings"]) == 1
        assert delta["resolved_findings"][0]["resolution"] == "deleted"

    def test_mixed_compliant_and_deleted(self, dashboard_delta):
        arn_compliant = "arn:aws:ec2:eu-central-1:123456789012:instance/i-compliant"
        arn_deleted = "arn:aws:ec2:eu-central-1:123456789012:instance/i-deleted"
        yesterday = {
            "drift_detection": {
                "findings": [
                    make_finding(resource_arn=arn_compliant),
                    make_finding(resource_arn=arn_deleted),
                ]
            },
            "tag_compliance": {"findings": []},
            "schedule_compliance": {"findings": []},
            "lifecycle_compliance": {"findings": []},
            "discovered_resources": [
                make_resource(arn=arn_compliant),
                make_resource(arn=arn_deleted),
            ],
        }
        today = {
            "drift_detection": {"findings": []},
            "tag_compliance": {"findings": []},
            "schedule_compliance": {"findings": []},
            "lifecycle_compliance": {"findings": []},
            "discovered_resources": [make_resource(arn=arn_compliant)],  # only compliant remains
        }
        delta = dashboard_delta.compute_delta(today, yesterday, include_resolution=True)
        resolved = delta["resolved_findings"]
        assert len(resolved) == 2
        by_arn = {f["resource_arn"]: f for f in resolved}
        assert by_arn[arn_compliant]["resolution"] == "compliant"
        assert by_arn[arn_deleted]["resolution"] == "deleted"

    def test_info_findings_excluded_from_delta(self, dashboard_delta):
        """INFO findings (e.g. expiring-soon) are informational, not non-compliant."""
        arn_info = "arn:aws:ec2:eu-central-1:123456789012:instance/i-expiring"
        arn_high = "arn:aws:ec2:eu-central-1:123456789012:instance/i-expired"
        today = {
            "drift_detection": {"findings": []},
            "tag_compliance": {"findings": []},
            "schedule_compliance": {"findings": []},
            "lifecycle_compliance": {
                "findings": [
                    make_finding(
                        category="lifecycle_compliance",
                        severity="INFO",
                        resource_arn=arn_info,
                        issue="Resource expires in 1 day",
                    ),
                    make_finding(
                        category="lifecycle_compliance",
                        severity="HIGH",
                        resource_arn=arn_high,
                        issue="Resource expired 5 days ago",
                    ),
                ]
            },
            "discovered_resources": [
                make_resource(arn=arn_info),
                make_resource(arn=arn_high),
            ],
        }
        yesterday = {
            "drift_detection": {"findings": []},
            "tag_compliance": {"findings": []},
            "schedule_compliance": {"findings": []},
            "lifecycle_compliance": {"findings": []},
            "discovered_resources": [],
        }
        delta = dashboard_delta.compute_delta(today, yesterday)
        # Only the HIGH finding should appear; INFO is excluded
        assert len(delta["new_findings"]) == 1
        assert delta["new_findings"][0]["resource_arn"] == arn_high
        assert delta["summary"]["new_count"] == 1

    def test_new_finding_has_no_resolution(self, dashboard_delta):
        arn = "arn:aws:ec2:eu-central-1:123456789012:instance/i-new"
        today = {
            "drift_detection": {"findings": [make_finding(resource_arn=arn)]},
            "tag_compliance": {"findings": []},
            "schedule_compliance": {"findings": []},
            "lifecycle_compliance": {"findings": []},
            "discovered_resources": [make_resource(arn=arn)],
        }
        yesterday = {
            "drift_detection": {"findings": []},
            "tag_compliance": {"findings": []},
            "schedule_compliance": {"findings": []},
            "lifecycle_compliance": {"findings": []},
            "discovered_resources": [],
        }
        delta = dashboard_delta.compute_delta(today, yesterday, include_resolution=True)
        assert len(delta["new_findings"]) == 1
        assert "resolution" not in delta["new_findings"][0]
