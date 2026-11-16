"""Tests for hunterkiller notifier.py — email template helpers."""

from factories import make_finding
from notifier import _extract_short_id, _html_escape, _warning_body, _warning_subject


class TestWarningSubject:
    def test_includes_count_and_cost(self):
        findings = [make_finding(estimated_monthly_cost=10), make_finding(estimated_monthly_cost=20)]
        subject = _warning_subject(findings, "acme", "dev", dry_run=False)
        assert "2 resources" in subject
        assert "$30/mo" in subject
        assert "[acme]" in subject

    def test_dry_run_prefix(self):
        findings = [make_finding()]
        subject = _warning_subject(findings, "acme", "dev", dry_run=True)
        assert subject.startswith("[DRY RUN]")

    def test_no_dry_run_prefix(self):
        findings = [make_finding()]
        subject = _warning_subject(findings, "acme", "dev", dry_run=False)
        assert "[DRY RUN]" not in subject


class TestWarningBody:
    def test_contains_resource_info(self):
        findings = [
            make_finding(
                resource_name="orphan-instance",
                estimated_monthly_cost=42.50,
                resource_type="ec2:instance",
                contact_email="user@example.com",
                contact_source="tag:Contact",
            )
        ]
        html = _warning_body(findings, "user@example.com", "2026-02-21 03:00 UTC", 24, False, "acme", "dev")
        assert "orphan-instance" in html
        assert "2026-02-21 03:00 UTC" in html
        assert "24" in html

    def test_dry_run_banner(self):
        findings = [make_finding()]
        html = _warning_body(findings, "user@example.com", "2026-02-21 03:00 UTC", 24, True, "acme", "dev")
        assert "DRY RUN" in html

    def test_no_dry_run_banner_when_live(self):
        findings = [make_finding()]
        html = _warning_body(findings, "user@example.com", "2026-02-21 03:00 UTC", 24, False, "acme", "dev")
        # Banner specifically mentions "DRY RUN MODE"
        assert "DRY RUN MODE" not in html


class TestHtmlEscape:
    def test_ampersand(self):
        assert "&amp;" in _html_escape("A & B")

    def test_angle_brackets(self):
        assert "&lt;" in _html_escape("<script>")
        assert "&gt;" in _html_escape("</script>")

    def test_quotes(self):
        assert "&quot;" in _html_escape('"quoted"')

    def test_empty_string(self):
        assert _html_escape("") == ""

    def test_none_returns_empty(self):
        assert _html_escape(None) == ""


class TestExtractShortId:
    def test_slash_separator(self):
        assert _extract_short_id("arn:aws:ec2:eu-central-1:123456789012:instance/i-abc123") == "i-abc123"

    def test_colon_separator(self):
        assert _extract_short_id("arn:aws:rds:eu-central-1:123456789012:db:mydb") == "mydb"

    def test_empty_arn(self):
        assert _extract_short_id("") == ""

    def test_long_arn_fallback(self):
        # No slash or colon beyond standard — returns last 40 chars
        short = _extract_short_id("some-string-without-proper-arn-format")
        assert len(short) <= 40
