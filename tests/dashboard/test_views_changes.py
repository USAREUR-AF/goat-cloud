"""Tests for compliance-dashboard/lambda/views_changes.py — pure rendering logic."""

from factories import make_finding
from views_changes import _delta_color, _format_delta, render_changes


def _make_delta(**overrides):
    """Build a minimal delta dict for testing render_changes."""
    base = {
        "new_findings": [],
        "resolved_findings": [],
        "persistent_findings": [],
        "severity_changes": [],
        "summary": {
            "new_count": 0,
            "resolved_count": 0,
            "persistent_count": 0,
            "severity_change_count": 0,
            "new_burn": 0,
            "resolved_burn": 0,
            "burn_delta": 0,
            "today_burn": 0,
            "yesterday_burn": 0,
            "today_total": 0,
            "yesterday_total": 0,
            "new_compliant_count": 0,
            "lost_compliant_count": 0,
            "today_discovered": 100,
            "yesterday_discovered": 100,
        },
    }
    base.update(overrides)
    return base


class TestRenderChanges:
    def test_none_delta_shows_no_prior(self):
        html = render_changes(None, "2026-02-20", "2026-02-19")
        assert "No prior report available" in html

    def test_valid_delta_shows_header(self):
        delta = _make_delta()
        html = render_changes(delta, "2026-02-20", "2026-02-19")
        assert "24-HOUR CHANGES" in html
        assert "2026-02-20" in html
        assert "2026-02-19" in html

    def test_resolved_compliant_badge(self):
        resolved = make_finding(
            resource_arn="arn:aws:ec2:eu-central-1:123456789012:instance/i-test",
        )
        resolved["resolution"] = "compliant"
        delta = _make_delta(
            resolved_findings=[resolved],
            summary={
                "new_count": 0,
                "resolved_count": 1,
                "persistent_count": 0,
                "severity_change_count": 0,
                "new_burn": 0,
                "resolved_burn": 21.02,
                "burn_delta": -21.02,
                "today_burn": 0,
                "yesterday_burn": 21.02,
                "today_total": 0,
                "yesterday_total": 1,
                "new_compliant_count": 0,
                "lost_compliant_count": 0,
                "today_discovered": 100,
                "yesterday_discovered": 100,
            },
        )
        html = render_changes(delta, "2026-02-20", "2026-02-19")
        assert "COMPLIANT" in html
        assert "DELETED" not in html

    def test_resolved_deleted_badge(self):
        resolved = make_finding(
            resource_arn="arn:aws:ec2:eu-central-1:123456789012:instance/i-gone",
        )
        resolved["resolution"] = "deleted"
        delta = _make_delta(
            resolved_findings=[resolved],
            summary={
                "new_count": 0,
                "resolved_count": 1,
                "persistent_count": 0,
                "severity_change_count": 0,
                "new_burn": 0,
                "resolved_burn": 21.02,
                "burn_delta": -21.02,
                "today_burn": 0,
                "yesterday_burn": 21.02,
                "today_total": 0,
                "yesterday_total": 1,
                "new_compliant_count": 0,
                "lost_compliant_count": 0,
                "today_discovered": 100,
                "yesterday_discovered": 100,
            },
        )
        html = render_changes(delta, "2026-02-20", "2026-02-19")
        assert "DELETED" in html


class TestDeltaColor:
    def test_positive_is_red(self):
        assert _delta_color(10) == "#ef4444"

    def test_negative_is_green(self):
        assert _delta_color(-5) == "#22c55e"

    def test_zero_is_gray(self):
        assert _delta_color(0) == "#6b7280"


class TestFormatDelta:
    def test_positive(self):
        assert _format_delta(42) == "+$42"

    def test_negative(self):
        assert _format_delta(-15) == "-$15"

    def test_zero(self):
        assert _format_delta(0) == "$0"
