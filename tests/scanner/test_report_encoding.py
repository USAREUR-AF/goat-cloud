"""Output encoding of the scanner reports: HTML email, stored HTML report and Markdown report."""

import re
from email import message_from_string

import pytest
from factories import make_finding
from reporters import html_email
from reporters.html_email import _build_email_body, _build_full_html_report, _html_escape, _safe_url
from reporters.markdown_report import generate_markdown_report

SCRIPT = '"><script>alert(1)</script>'
ATTR = "x' onmouseover='alert(1)"
HEADER = "a@b.c\r\nBcc: x@evil.example"


def _hostile_results(payload):
    finding = make_finding(
        resource_name=payload,
        resource_type=payload,
        region=payload,
        state=payload,
        issue=payload,
        recommendation=payload,
        inferred_purpose=payload,
    )
    stopped = make_finding(
        resource_arn="arn:aws:ec2:eu-central-1:123456789012:instance/i-0stopped",
        resource_name=payload,
        resource_type=payload,
        state="terminated",
        attached_to=payload,
    )
    iac = {
        "state_path": payload,
        "last_modified": payload,
        "recommendation": payload,
        "overlapping": [{"resource_name": payload, "resource_type": payload, "region": payload}],
    }
    return {
        "project": payload,
        "environment": payload,
        "generated_at": payload,
        "account_id": payload,
        "summary": {"findings_by_category": {payload: 1}, "findings_by_severity": {"HIGH": 2}},
        "region_costs": {payload: 12.0},
        "resource_type_counts": {payload: 3},
        "drift_detection": {"findings": [finding, stopped]},
        "multi_state_detection": {
            "state_files": [{"bucket": payload, "key": payload, "last_modified": payload}],
            "lock_tables": [{"table_name": payload, "region": payload}],
            "notes": [payload],
        },
        "other_iac_analysis": [{**iac, "is_ours": True}, {**iac, "is_ours": False}],
        "errors": [payload],
    }


def _hostile_delta(payload):
    finding = make_finding(resource_name=payload, resource_type=payload, issue=payload, severity=payload)
    return {
        "summary": {"burn_delta": 0, "new_count": 1, "resolved_count": 1, "persistent_count": 0},
        "new_findings": [finding],
        "resolved_findings": [finding],
    }


@pytest.mark.parametrize("build", [_build_full_html_report, _build_email_body])
def test_html_never_carries_a_script_element(build):
    out = build(_hostile_results(SCRIPT), "bucket", "2026-01-01", delta=_hostile_delta(SCRIPT))
    assert "<script" not in out
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out


@pytest.mark.parametrize("build", [_build_full_html_report, _build_email_body])
def test_html_never_lets_a_value_add_an_attribute(build):
    out = build(_hostile_results(ATTR), "bucket", "2026-01-01", delta=_hostile_delta(ATTR))
    assert "' onmouseover=" not in out


def test_benign_values_render_unchanged():
    assert _html_escape("orphan-instance") == "orphan-instance"
    out = _build_full_html_report({"drift_detection": {"findings": [make_finding()]}}, "", "2026-01-01")
    assert "orphan-instance</a> <code>ec2:instance</code>" in out
    assert "State: <strong>running</strong>" in out


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,<script>alert(1)</script>", " JaVaScRiPt:x"])
def test_only_http_urls_become_links(url):
    assert _safe_url(url) == ""


def test_http_url_is_kept_and_attribute_escaped():
    assert _safe_url("https://example.com/a?b=1&c=2") == "https://example.com/a?b=1&amp;c=2"
    assert '"' not in _safe_url('https://example.com/"onclick="x')


@pytest.mark.parametrize("build", [_build_full_html_report, _build_email_body])
def test_repository_link_refuses_a_script_url(build, monkeypatch):
    monkeypatch.setenv("REPOSITORY_URL", "javascript:alert(1)")
    out = build({}, "", "2026-01-01")
    assert "javascript:" not in out


def test_subject_cannot_add_a_mail_header(monkeypatch):
    sent = {}

    class _Ses:
        def send_raw_email(self, **kwargs):
            sent.update(kwargs)

    monkeypatch.setattr(html_email.boto3, "client", lambda *a, **k: _Ses())
    html_email.send_html_email({"project": HEADER}, "sender@example.com", ["admin@example.com"], "", "2026-01-01")

    message = message_from_string(sent["RawMessage"]["Data"])
    assert message["Bcc"] is None
    assert "\n" not in message["Subject"].replace("\n ", "")
    assert "a@b.c Bcc: x@evil.example" in message["Subject"].replace("\n", "")
    assert sent["Destinations"] == ["admin@example.com"]


def test_markdown_never_carries_raw_html():
    out = generate_markdown_report(_hostile_results(SCRIPT))
    assert "<script" not in out


def _outside_code_spans(markdown):
    """The report with escaped backticks and every code span removed."""
    return re.sub(r"`[^`\n]*`", "", markdown.replace("\\`", ""))


def test_markdown_value_cannot_become_a_link():
    out = generate_markdown_report(_hostile_results("[click](javascript:alert(1))"))
    assert "[click](javascript:" not in _outside_code_spans(out)
    assert "\\[click\\](javascript:alert(1))" in out


def test_markdown_value_cannot_leave_its_code_span_or_line():
    out = generate_markdown_report(_hostile_results("`\n[x]: javascript:alert(1)"))
    assert "\n[x]: javascript:" not in out
    assert "[x]: javascript:" not in _outside_code_spans(out)
    assert "- **\\` \\[x\\]: javascript:alert(1)** `' [x]: javascript:alert(1)`" in out


def test_markdown_benign_finding_unchanged():
    out = generate_markdown_report({"drift_detection": {"findings": [make_finding()]}})
    assert "- **[orphan-instance](https://eu-central-1.console.aws.amazon.com/ec2/home" in out
    assert "  Resource exists in AWS but not in Terraform state (possible click-ops)" in out
