"""Output encoding of the hunterkiller emails: HTML bodies, links, headers and recipients."""

from email import message_from_string

import notifier
import pytest
from contact_resolver import resolve_contact
from factories import make_finding
from notifier import _clean_recipients, _enforcement_body, _safe_url, _warning_body, send_warning_emails

SCRIPT = '"><script>alert(1)</script>'
ATTR = "x' onmouseover='alert(1)"
HEADER = "a@b.c\r\nBcc: x@evil.example"


def _hostile_finding(payload):
    return make_finding(
        resource_name=payload,
        resource_type=payload,
        region=payload,
        issue=payload,
        contact_email=payload,
        contact_source=payload,
        message=payload,
        success=False,
    )


@pytest.mark.parametrize("payload", [SCRIPT, ATTR])
def test_warning_body_escapes_every_value(payload):
    out = _warning_body([_hostile_finding(payload)], payload, payload, 24, False, payload, payload)
    assert "<script" not in out
    assert "' onmouseover=" not in out


@pytest.mark.parametrize("payload", [SCRIPT, ATTR])
def test_enforcement_body_escapes_every_value(payload):
    item = _hostile_finding(payload)
    out = _enforcement_body([item, {**item, "success": True}], [item], [item], False, payload, payload, payload)
    assert "<script" not in out
    assert "' onmouseover=" not in out


def test_benign_warning_body_unchanged():
    finding = make_finding(contact_email="user@example.com", contact_source="tag:Contact")
    out = _warning_body([finding], "user@example.com", "2026-02-21 03:00 UTC", 24, False, "acme", "dev")
    assert "acme / dev" in out
    assert "(tag:Contact) " in out
    assert "(24 hours from now)" in out


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,x", "' onclick='alert(1)"])
def test_dashboard_link_is_http_only(url, monkeypatch):
    assert _safe_url(url) == ""
    monkeypatch.setattr(notifier, "DASHBOARD_URL", url)
    warning = _warning_body([make_finding()], "user@example.com", "soon", 24, False, "acme", "dev")
    report = _enforcement_body([], [], [], False, "acme", "dev", "batch")
    assert "<a href" not in warning
    assert "<a href" not in report


def test_dashboard_link_kept_for_https(monkeypatch):
    monkeypatch.setattr(notifier, "DASHBOARD_URL", "https://dashboard.example.com")
    warning = _warning_body([make_finding()], "user@example.com", "soon", 24, False, "acme", "dev")
    assert "<a href='https://dashboard.example.com/pending'" in warning


def test_recipient_must_be_one_plain_address():
    given = [HEADER, " ok@example.com ", "Name <a@b.c>", "a@b.c;d@e.f", "a@b.c d@e.f", "no-at-sign", ""]
    assert _clean_recipients(given) == ["ok@example.com"]


def test_contact_tag_with_a_header_payload_falls_back_to_admins():
    email, source = resolve_contact({"Contact": HEADER}, ["Contact"], ["admin@example.com"])
    assert (email, source) == ("admin@example.com", "admin_fallback")


class _Ses:
    def __init__(self):
        self.sent = []

    def send_raw_email(self, **kwargs):
        self.sent.append(kwargs)


def test_warning_headers_stay_clean(monkeypatch):
    ses = _Ses()
    monkeypatch.setattr(notifier.boto3, "client", lambda *a, **k: ses)
    by_recipient = {HEADER: [make_finding()], "owner@example.com": [make_finding()]}

    sent = send_warning_emails(by_recipient, "sender@example.com", 24, False, HEADER, "dev")

    assert sent == 1
    assert ses.sent[0]["Destinations"] == ["owner@example.com"]
    message = message_from_string(ses.sent[0]["RawMessage"]["Data"])
    assert message["Bcc"] is None
    assert message["To"] == "owner@example.com"
    assert "Bcc: x@evil.example" in message["Subject"].replace("\n", "")
