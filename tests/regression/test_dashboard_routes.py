"""Regression: Dashboard route testing — every GET/POST returns expected status + content."""

import base64
import gzip
import os
import pathlib
import re
import urllib.parse
from unittest.mock import patch

import pytest
from factories import make_alb_event
from moto import mock_aws


def _invoke(moto_env, path, method="GET", email="admin@example.com", qs=None, body=None):
    """Invoke the dashboard handler with a crafted ALB event."""
    handler = moto_env["handler"]
    event = make_alb_event(path=path, method=method, email=email, qs=qs, body=body)
    return handler(event, None)


def _form_body(**fields):
    """Encode form fields as application/x-www-form-urlencoded body string."""
    return urllib.parse.urlencode(fields)


@mock_aws
class TestGetRoutes:
    """All GET routes return 200 with expected content markers."""

    def test_home(self, moto_env):
        resp = _invoke(moto_env, "/")
        assert resp["statusCode"] == 200

    def test_findings(self, moto_env):
        resp = _invoke(moto_env, "/findings")
        assert resp["statusCode"] == 200

    def test_findings_severity_filter(self, moto_env):
        resp = _invoke(moto_env, "/findings", qs={"severity": "HIGH"})
        assert resp["statusCode"] == 200

    def test_compliant(self, moto_env):
        resp = _invoke(moto_env, "/compliant")
        assert resp["statusCode"] == 200

    def test_changes(self, moto_env):
        resp = _invoke(moto_env, "/changes")
        assert resp["statusCode"] == 200

    def test_pending(self, moto_env):
        resp = _invoke(moto_env, "/pending")
        assert resp["statusCode"] == 200

    def test_overrides(self, moto_env):
        resp = _invoke(moto_env, "/overrides")
        assert resp["statusCode"] == 200

    def test_tree(self, moto_env):
        resp = _invoke(moto_env, "/tree")
        assert resp["statusCode"] == 200

    def test_burn(self, moto_env):
        resp = _invoke(moto_env, "/burn")
        assert resp["statusCode"] == 200

    def test_services(self, moto_env):
        resp = _invoke(moto_env, "/services")
        assert resp["statusCode"] == 200

    def test_security(self, moto_env):
        resp = _invoke(moto_env, "/security")
        assert resp["statusCode"] == 200

    def test_overview(self, moto_env):
        resp = _invoke(moto_env, "/overview")
        assert resp["statusCode"] == 200

    def test_removed_load_test_trigger_is_not_found(self, moto_env):
        resp = _invoke(moto_env, "/trigger/perf", method="POST")
        assert resp["statusCode"] in (404, 405)
        assert "/trigger/perf" not in _invoke(moto_env, "/self").get("body", "")

    def test_protected(self, moto_env):
        resp = _invoke(moto_env, "/protected")
        assert resp["statusCode"] == 200

    def test_guide(self, moto_env):
        resp = _invoke(moto_env, "/guide")
        assert resp["statusCode"] == 200

    def test_docs(self, moto_env):
        resp = _invoke(moto_env, "/docs")
        assert resp["statusCode"] == 200

    def test_self(self, moto_env):
        resp = _invoke(moto_env, "/self")
        assert resp["statusCode"] == 200

    def test_cure_with_arn(self, moto_env):
        arn = "arn:aws:ec2:eu-central-1:123456789012:instance/i-drift000000"
        resp = _invoke(moto_env, "/cure", qs={"arn": arn})
        assert resp["statusCode"] == 200

    def test_cure_missing_arn(self, moto_env):
        resp = _invoke(moto_env, "/cure", qs={})
        assert resp["statusCode"] == 400

    def test_report_download_admin(self, moto_env):
        auth_mod = moto_env["auth_mod"]
        with patch.object(auth_mod, "ADMIN_EMAILS", ["admin@example.com"]):
            resp = _invoke(moto_env, "/report/download", email="admin@example.com")
        assert resp["statusCode"] == 200
        assert "application/json" in resp["headers"]["Content-Type"]

    def test_report_download_non_admin(self, moto_env):
        auth_mod = moto_env["auth_mod"]
        with patch.object(auth_mod, "ADMIN_EMAILS", ["admin@example.com"]):
            resp = _invoke(moto_env, "/report/download", email="viewer@example.com")
        assert resp["statusCode"] == 403

    def test_404_unknown_path(self, moto_env):
        resp = _invoke(moto_env, "/nonexistent")
        assert resp["statusCode"] == 404

    def test_detail_route(self, moto_env):
        resp = _invoke(moto_env, "/detail/module.web")
        assert resp["statusCode"] == 200


@mock_aws
class TestPostRoutes:
    """POST routes: admin-gated, return redirects or errors."""

    def test_override_create_admin(self, moto_env):
        auth_mod = moto_env["auth_mod"]
        body = _form_body(
            resource_arn="arn:aws:ec2:eu-central-1:123456789012:instance/i-test",
            reason="False positive",
            override_type="false_positive",
        )
        with patch.object(auth_mod, "ADMIN_EMAILS", ["admin@example.com"]):
            resp = _invoke(moto_env, "/override", method="POST", email="admin@example.com", body=body)
        assert resp["statusCode"] in (200, 303)

    def test_override_create_non_admin(self, moto_env):
        auth_mod = moto_env["auth_mod"]
        body = _form_body(resource_arn="arn:aws:test", reason="test")
        with patch.object(auth_mod, "ADMIN_EMAILS", ["admin@example.com"]):
            resp = _invoke(moto_env, "/override", method="POST", email="viewer@example.com", body=body)
        assert resp["statusCode"] == 403

    def test_abort_admin(self, moto_env):
        auth_mod = moto_env["auth_mod"]
        arn = "arn:aws:ec2:eu-central-1:123456789012:instance/i-abort-route"
        moto_env["hk_table"].put_item(Item={"resource_arn": arn, "status": "warned", "approval_status": "pending"})
        body = _form_body(resource_arn=arn)
        with patch.object(auth_mod, "ADMIN_EMAILS", ["admin@example.com"]):
            resp = _invoke(moto_env, "/abort", method="POST", email="admin@example.com", body=body)
        assert resp["statusCode"] == 303

    def test_approve_admin(self, moto_env):
        auth_mod = moto_env["auth_mod"]
        arn = "arn:aws:ec2:eu-central-1:123456789012:instance/i-approve-route"
        moto_env["hk_table"].put_item(Item={"resource_arn": arn, "status": "warned", "approval_status": "pending"})
        body = _form_body(resource_arn=arn)
        with patch.object(auth_mod, "ADMIN_EMAILS", ["admin@example.com"]):
            resp = _invoke(moto_env, "/approve", method="POST", email="admin@example.com", body=body)
        assert resp["statusCode"] == 303

    def test_revoke_admin(self, moto_env):
        auth_mod = moto_env["auth_mod"]
        arn = "arn:aws:ec2:eu-central-1:123456789012:instance/i-revoke-route"
        moto_env["hk_table"].put_item(Item={"resource_arn": arn, "status": "warned", "approval_status": "approved"})
        body = _form_body(resource_arn=arn)
        with patch.object(auth_mod, "ADMIN_EMAILS", ["admin@example.com"]):
            resp = _invoke(moto_env, "/revoke", method="POST", email="admin@example.com", body=body)
        assert resp["statusCode"] == 303

    def test_cache_clear_admin(self, moto_env):
        auth_mod = moto_env["auth_mod"]
        with patch.object(auth_mod, "ADMIN_EMAILS", ["admin@example.com"]):
            resp = _invoke(moto_env, "/cache/clear", method="POST", email="admin@example.com")
        assert resp["statusCode"] == 303

    def test_trigger_scan_non_admin(self, moto_env):
        auth_mod = moto_env["auth_mod"]
        with patch.object(auth_mod, "ADMIN_EMAILS", ["admin@example.com"]):
            resp = _invoke(moto_env, "/trigger/scan", method="POST", email="viewer@example.com")
        assert resp["statusCode"] == 403

    def test_trigger_enforce_non_admin(self, moto_env):
        auth_mod = moto_env["auth_mod"]
        body = _form_body(min_severity="HIGH")
        with patch.object(auth_mod, "ADMIN_EMAILS", ["admin@example.com"]):
            resp = _invoke(moto_env, "/trigger/enforce", method="POST", email="viewer@example.com", body=body)
        assert resp["statusCode"] == 403

    def test_post_unknown_path(self, moto_env):
        auth_mod = moto_env["auth_mod"]
        with patch.object(auth_mod, "ADMIN_EMAILS", ["admin@example.com"]):
            resp = _invoke(moto_env, "/nonexistent", method="POST", email="admin@example.com")
        assert resp["statusCode"] == 404


@mock_aws
class TestMethodGuard:
    """Unsupported HTTP methods return 405."""

    def test_put_returns_405(self, moto_env):
        resp = _invoke(moto_env, "/", method="PUT")
        assert resp["statusCode"] == 405


@mock_aws
class TestViewerGate:
    """Viewer gate restricts access when enabled."""

    def test_viewer_gate_blocks_unauthorized(self, moto_env):
        auth_mod = moto_env["auth_mod"]
        with (
            patch.object(auth_mod, "ADMIN_EMAILS", []),
            patch.object(auth_mod, "VIEWER_EMAILS", []),
            patch.object(auth_mod, "ENABLE_VIEWER_GATE", True),
        ):
            resp = _invoke(moto_env, "/", email="stranger@example.com")
        assert resp["statusCode"] == 403

    def test_enroll_exempt_from_viewer_gate(self, moto_env):
        auth_mod = moto_env["auth_mod"]
        with patch.object(auth_mod, "ENABLE_VIEWER_GATE", True):
            resp = _invoke(moto_env, "/enroll", email="stranger@example.com")
        # Should not be 403 (might be 400 for missing token, which is fine)
        assert resp["statusCode"] != 403


# Pages rendered with no site directory must describe no particular deployment.
_DEFAULT_PAGES = [
    "/",
    "/findings",
    "/compliant",
    "/changes",
    "/pending",
    "/overrides",
    "/tree",
    "/burn",
    "/services",
    "/security",
    "/overview",
    "/protected",
    "/guide",
    "/docs",
    "/self",
    "/repos",
    "/network",
    "/architecture",
    "/architecture/custom",
    "/architecture/interactions",
    "/architecture/drawio",
    "/access",
]
# Words that name a particular deployment and must not appear on a default page.
# The list is not part of this repository: a maintainer keeps it outside the
# tree and points DASHBOARD_FORBIDDEN_WORDS at it (one regular expression per
# line). Without it the pages are still rendered and checked for the brand.
_ROOT = pathlib.Path(__file__).resolve().parents[2]
_WORDS_FILE = os.environ.get("DASHBOARD_FORBIDDEN_WORDS") or str(_ROOT / "oss" / "dashboard-forbidden-words.txt")


def _deployment_words():
    try:
        with open(_WORDS_FILE, encoding="utf-8") as fh:
            words = [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
    except OSError:
        return None
    return re.compile("|".join(words)) if words else None


_DEPLOYMENT_WORDS = _deployment_words()


class TestDefaultPagesNameNoDeployment:
    @pytest.mark.parametrize("path", _DEFAULT_PAGES)
    def test_page(self, moto_env, path):
        resp = _invoke(moto_env, path)
        assert resp["statusCode"] in (200, 302), path
        body = resp.get("body", "")
        if resp.get("isBase64Encoded"):
            body = gzip.decompress(base64.b64decode(body)).decode()
        assert len(body) > 500 or resp["statusCode"] == 302, path
        if resp["statusCode"] == 200 and "<html" in body:
            assert "goat-cloud" in body, path
            assert "__BRAND__" not in body, path
        if _DEPLOYMENT_WORDS is not None:
            found = sorted(set(_DEPLOYMENT_WORDS.findall(body)))
            assert found == [], f"{path} prints {len(found)} deployment word(s)"
