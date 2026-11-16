"""Regression: who may act on the dashboard, and from where."""

import urllib.parse

from factories import make_alb_event
from moto import mock_aws

ARN = "arn:aws:ec2:eu-central-1:123456789012:instance/i-0123456789abcdef0"


def _post(moto_env, path, email="admin@example.com", headers=None, drop=(), **fields):
    event = make_alb_event(path=path, method="POST", email=email, body=urllib.parse.urlencode(fields))
    for name in drop:
        event["headers"].pop(name, None)
    event["headers"].update(headers or {})
    return moto_env["handler"](event, None)


@mock_aws
class TestPostOrigin:
    def test_same_site_post_accepted(self, moto_env):
        moto_env["hk_table"].put_item(Item={"resource_arn": ARN, "status": "warned", "approval_status": "pending"})
        resp = _post(moto_env, "/approve", resource_arn=ARN)
        assert resp["statusCode"] == 303

    def test_post_from_another_site_refused(self, moto_env):
        resp = _post(moto_env, "/approve", headers={"origin": "https://evil.example"}, resource_arn=ARN)
        assert resp["statusCode"] == 403

    def test_cross_site_fetch_metadata_refused(self, moto_env):
        resp = _post(moto_env, "/destroy-all", headers={"sec-fetch-site": "cross-site"})
        assert resp["statusCode"] == 403

    def test_post_without_origin_or_referer_refused(self, moto_env):
        resp = _post(moto_env, "/abort-all", drop=("origin",))
        assert resp["statusCode"] == 403


@mock_aws
class TestIdentity:
    def test_forwarded_email_header_does_not_make_an_admin(self, moto_env):
        resp = _post(
            moto_env,
            "/approve",
            email="viewer@example.com",
            headers={
                "x-forwarded-email": "admin@example.com",
                "cf-access-authenticated-user-email": "admin@example.com",
            },
            resource_arn=ARN,
        )
        assert resp["statusCode"] == 403

    def test_request_without_identity_cannot_act(self, moto_env):
        resp = _post(moto_env, "/approve", drop=("x-amzn-oidc-data",), resource_arn=ARN)
        assert resp["statusCode"] == 403


@mock_aws
class TestResponseHeaders:
    def test_pages_carry_security_headers(self, moto_env):
        resp = moto_env["handler"](make_alb_event(path="/"), None)
        headers = resp["headers"]
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["X-Frame-Options"] == "DENY"
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
        assert "form-action 'self'" in headers["Content-Security-Policy"]

    def test_cache_clear_redirect_stays_on_site(self, moto_env):
        for target in ["//evil.example/x", "https://evil.example", "/\\evil.example"]:
            resp = _post(moto_env, "/cache/clear", return_to=target)
            assert resp["statusCode"] == 303
            assert resp["headers"]["Location"] == "/self"
        resp = _post(moto_env, "/cache/clear", return_to="/burn")
        assert resp["headers"]["Location"] == "/burn"

    def test_report_download_is_sandboxed(self, moto_env):
        resp = moto_env["handler"](make_alb_event(path="/report/download", email="admin@example.com"), None)
        if resp["statusCode"] == 200:
            assert resp["headers"]["Content-Security-Policy"] == "sandbox"
            assert resp["headers"]["X-Content-Type-Options"] == "nosniff"


@mock_aws
class TestApprovalRecords:
    """A dashboard action changes a warning record; it never creates one."""

    def test_approve_unknown_resource_creates_nothing(self, moto_env):
        resp = _post(moto_env, "/approve", resource_arn=ARN)
        assert resp["statusCode"] == 409
        assert "Item" not in moto_env["hk_table"].get_item(Key={"resource_arn": ARN})

    def test_abort_and_revoke_unknown_resource_create_nothing(self, moto_env):
        assert _post(moto_env, "/abort", resource_arn=ARN)["statusCode"] == 404
        assert _post(moto_env, "/revoke", resource_arn=ARN)["statusCode"] == 404
        assert "Item" not in moto_env["hk_table"].get_item(Key={"resource_arn": ARN})

    def test_approve_requires_a_current_warning(self, moto_env):
        moto_env["hk_table"].put_item(Item={"resource_arn": ARN, "status": "aborted", "approval_status": "pending"})
        assert _post(moto_env, "/approve", resource_arn=ARN)["statusCode"] == 409
        item = moto_env["hk_table"].get_item(Key={"resource_arn": ARN})["Item"]
        assert item["approval_status"] == "pending"

    def test_abort_withdraws_the_approval(self, moto_env):
        moto_env["hk_table"].put_item(Item={"resource_arn": ARN, "status": "warned", "approval_status": "pending"})
        assert _post(moto_env, "/approve", resource_arn=ARN)["statusCode"] == 303
        assert moto_env["hk_table"].get_item(Key={"resource_arn": ARN})["Item"]["approval_status"] == "approved"
        assert _post(moto_env, "/abort", resource_arn=ARN)["statusCode"] == 303
        item = moto_env["hk_table"].get_item(Key={"resource_arn": ARN})["Item"]
        assert item["status"] == "aborted"
        assert item["approval_status"] == "pending"
        assert item["approved_at"] == ""

    def test_extension_is_bounded(self, moto_env):
        from datetime import datetime, timedelta

        moto_env["hk_table"].put_item(Item={"resource_arn": ARN, "status": "warned", "approval_status": "pending"})
        assert _post(moto_env, "/extend", resource_arn=ARN, hours="99999999")["statusCode"] == 303
        deadline = datetime.fromisoformat(
            moto_env["hk_table"].get_item(Key={"resource_arn": ARN})["Item"]["grace_deadline"]
        )
        assert deadline - datetime.now(deadline.tzinfo) < timedelta(days=31)
