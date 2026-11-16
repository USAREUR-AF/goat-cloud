"""Tests for compliance-dashboard/lambda/views_repos.py — finding attribution and onboarding text."""

import re

import pytest
import views_repos
from compliance_schema import repo_slug
from data_core import REPOSITORY_URL


def _report(findings, entries):
    return {
        "iac_registry": {"entries": entries, "total_external_managed": 0},
        "tag_compliance": {"findings": findings},
        "discovered_resources": [],
    }


def _row(html, name):
    """Return the table row whose first cell holds ``name``."""
    for row in re.findall(r"<tr[^>]*>.*?</tr>", html, flags=re.S):
        first = re.search(r"<td[^>]*>(.*?)</td>", row, flags=re.S)
        if first and name in first.group(1):
            return row
    return None


@pytest.fixture()
def render(monkeypatch):
    def _render(findings, entries, config=None):
        monkeypatch.setattr(views_repos, "get_compliance_report", lambda: _report(findings, entries))
        monkeypatch.setattr(views_repos, "_load_platform_config", lambda: config or {})
        return views_repos.render_repos()

    return _render


class TestUnclaimedFindings:
    def test_finding_without_repository_goes_to_unclaimed_row(self, render):
        platform = repo_slug(REPOSITORY_URL)
        entries = [{"repo_slug": platform, "repo_url": REPOSITORY_URL, "iac_type": "terraform", "resource_count": 10}]
        findings = [{"managed_by_repo_slug": ""}, {"managed_by_repo_slug": ""}, {"managed_by_repo_slug": platform}]

        html = render(findings, entries)

        unclaimed = _row(html, "Unclaimed")
        assert unclaimed is not None
        assert re.findall(r'<td style="text-align:right">(\d+)</td>', unclaimed) == ["2"]
        platform_row = _row(html, platform)
        # resources, then findings: the platform repository keeps only its own finding
        assert re.findall(r'<td style="text-align:right">(\d+)</td>', platform_row) == ["10", "1"]

    def test_no_unclaimed_row_when_every_finding_has_a_repository(self, render):
        entries = [{"repo_slug": "org/app", "iac_type": "terraform", "resource_count": 3}]
        html = render([{"managed_by_repo_slug": "org/app"}], entries)
        assert _row(html, "Unclaimed") is None

    def test_unclaimed_row_shown_with_no_registered_repository(self, render):
        html = render([{}], [])
        assert _row(html, "Unclaimed") is not None


class TestOnboardingPaths:
    def test_unknown_state_bucket_is_a_placeholder(self, render, monkeypatch):
        monkeypatch.setattr(views_repos, "TF_STATE_BUCKET", "")
        html = render([], [])
        assert "s3://&lt;your-state-bucket&gt;/" in html
        assert "s3:///" not in html
        assert "//iac-registry" not in html

    def test_known_state_bucket_is_shown(self, render):
        html = render([], [], config={"state_bucket": "team-tf-state"})
        assert "s3://team-tf-state/&lt;repo&gt;/" in html
