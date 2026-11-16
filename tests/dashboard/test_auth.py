"""Tests for compliance-dashboard/lambda/auth.py — who a request is from."""

import base64
import hashlib
import json
from unittest.mock import patch

import alb_jwt
import auth as auth_module
import pytest
from auth import get_user_email, has_valid_perf_token, is_admin, is_authorized, is_same_origin_post, require_admin
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from factories import TEST_ALB_ARN, alb_public_point, alb_signing_key, make_alb_event, make_alb_jwt

PROXY_USER = "arn:aws:iam::123456789012:user/dashboard-proxy"
PROXY_ROLE = "arn:aws:iam::123456789012:role/fronts/dashboard-proxy"
PROXY_SECRET = "s3cr3t-proxy-header-value"


def _alb(headers):
    return {"requestContext": {"elb": {}}, "headers": headers}


def _function_url(user_arn, headers):
    return {"requestContext": {"authorizer": {"iam": {"userArn": user_arn}}, "http": {}}, "headers": headers}


def _unsigned_jwt(claims):
    header_b64 = base64.urlsafe_b64encode(b'{"typ":"JWT"}').rstrip(b"=").decode()
    payload_b64 = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"{header_b64}.{payload_b64}.fakesig"


class TestLoadBalancerToken:
    def test_signed_token_gives_email(self):
        event = _alb({"x-amzn-oidc-data": make_alb_jwt({"email": "User@Example.com"})})
        assert get_user_email(event) == "user@example.com"

    def test_unsigned_token_rejected(self):
        event = _alb({"x-amzn-oidc-data": _unsigned_jwt({"email": "admin@example.com"})})
        assert get_user_email(event) == ""

    def test_token_signed_by_another_key_rejected(self):
        other = ec.generate_private_key(ec.SECP256R1())
        event = _alb({"x-amzn-oidc-data": make_alb_jwt({"email": "admin@example.com"}, key=other)})
        assert get_user_email(event) == ""

    def test_token_from_another_load_balancer_rejected(self):
        token = make_alb_jwt({"email": "admin@example.com"}, signer=TEST_ALB_ARN.replace("/test/", "/other/"))
        assert get_user_email(_alb({"x-amzn-oidc-data": token})) == ""

    def test_expired_token_rejected(self):
        token = make_alb_jwt({"email": "admin@example.com"}, exp_in=-3600)
        assert get_user_email(_alb({"x-amzn-oidc-data": token})) == ""

    def test_altered_payload_rejected(self):
        header, _payload, sig = make_alb_jwt({"email": "viewer@example.com"}).split(".")
        forged = base64.urlsafe_b64encode(json.dumps({"email": "admin@example.com", "exp": 9999999999}).encode())
        assert get_user_email(_alb({"x-amzn-oidc-data": f"{header}.{forged.decode()}.{sig}"})) == ""

    def test_other_algorithm_rejected(self):
        token = make_alb_jwt({"email": "admin@example.com"}, alg="none")
        assert get_user_email(_alb({"x-amzn-oidc-data": token})) == ""

    def test_no_expected_signer_rejects_every_token(self, monkeypatch):
        monkeypatch.setenv("ALB_ARN", "")
        assert get_user_email(_alb({"x-amzn-oidc-data": make_alb_jwt({"email": "a@example.com"})})) == ""

    def test_token_for_another_client_on_the_same_load_balancer_rejected(self):
        token = make_alb_jwt({"email": "admin@example.com"}, client="other-application")
        assert get_user_email(_alb({"x-amzn-oidc-data": token})) == ""

    def test_token_from_another_issuer_on_the_same_load_balancer_rejected(self):
        token = make_alb_jwt({"email": "admin@example.com"}, issuer="https://idp.other.example")
        assert get_user_email(_alb({"x-amzn-oidc-data": token})) == ""

    @pytest.mark.parametrize("name", ["ALB_OIDC_CLIENT_ID", "ALB_OIDC_ISSUER"])
    def test_no_expected_client_or_issuer_rejects_every_token(self, monkeypatch, name):
        monkeypatch.setenv(name, "")
        assert get_user_email(_alb({"x-amzn-oidc-data": make_alb_jwt({"email": "a@example.com"})})) == ""

    def test_token_ignored_when_request_did_not_come_from_a_load_balancer(self):
        event = {"headers": {"x-amzn-oidc-data": make_alb_jwt({"email": "admin@example.com"})}}
        assert get_user_email(event) == ""

    def test_malformed_token(self):
        assert get_user_email(_alb({"x-amzn-oidc-data": "not.a.jwt"})) == ""
        assert get_user_email(_alb({"x-amzn-oidc-data": "x" * 20000})) == ""

    def test_token_without_email(self):
        event = _alb({"x-amzn-oidc-data": make_alb_jwt({"sub": "u1"}), "x-amzn-oidc-identity": "id@example.com"})
        assert get_user_email(event) == ""


class TestPlainHeadersAreNotIdentity:
    """A header any client can send never names the user."""

    @pytest.mark.parametrize(
        "header",
        ["x-forwarded-email", "X-Forwarded-Email", "x-amzn-oidc-identity", "cf-access-authenticated-user-email"],
    )
    def test_header_alone_ignored(self, header):
        assert get_user_email({"headers": {header: "admin@example.com"}}) == ""
        assert get_user_email(_alb({header: "admin@example.com"})) == ""

    def test_forwarded_email_does_not_override_signed_token(self):
        event = make_alb_event(email="viewer@example.com")
        event["headers"]["x-forwarded-email"] = "admin@example.com"
        assert get_user_email(event) == "viewer@example.com"

    def test_empty_headers(self):
        assert get_user_email({"headers": {}}) == ""
        assert get_user_email({}) == ""


class TestTrustedProxy:
    def test_function_url_caller_in_list(self):
        with patch.object(auth_module, "TRUSTED_PROXY_PRINCIPAL_ARNS", [PROXY_USER]):
            event = _function_url(PROXY_USER, {"x-forwarded-email": "Admin@Example.com"})
            assert get_user_email(event) == "admin@example.com"

    def test_function_url_caller_not_in_list(self):
        with patch.object(auth_module, "TRUSTED_PROXY_PRINCIPAL_ARNS", [PROXY_USER]):
            event = _function_url("arn:aws:iam::123456789012:user/someone", {"x-forwarded-email": "admin@example.com"})
            assert get_user_email(event) == ""

    def test_function_url_no_list(self):
        event = _function_url(PROXY_USER, {"x-forwarded-email": "admin@example.com"})
        assert get_user_email(event) == ""

    def test_assumed_role_session_matches_role(self):
        session = "arn:aws:sts::123456789012:assumed-role/dashboard-proxy/i-0abc"
        with patch.object(auth_module, "TRUSTED_PROXY_PRINCIPAL_ARNS", [PROXY_ROLE]):
            assert get_user_email(_function_url(session, {"x-forwarded-email": "a@example.com"})) == "a@example.com"
            other = "arn:aws:sts::123456789012:assumed-role/another-role/i-0abc"
            assert get_user_email(_function_url(other, {"x-forwarded-email": "a@example.com"})) == ""

    def test_proxy_header_secret(self):
        digest = hashlib.sha256(PROXY_SECRET.encode()).hexdigest()
        with (
            patch.object(auth_module, "PROXY_AUTH_HEADER_NAME", "x-oauth2-proxy-auth"),
            patch.object(auth_module, "PROXY_AUTH_HEADER_SHA256", digest),
        ):
            ok = _alb({"X-OAuth2-Proxy-Auth": PROXY_SECRET, "x-forwarded-email": "admin@example.com"})
            assert get_user_email(ok) == "admin@example.com"
            wrong = _alb({"x-oauth2-proxy-auth": "guess", "x-forwarded-email": "admin@example.com"})
            assert get_user_email(wrong) == ""
            missing = _alb({"x-forwarded-email": "admin@example.com"})
            assert get_user_email(missing) == ""

    def test_value_that_is_not_one_address_rejected(self):
        with patch.object(auth_module, "TRUSTED_PROXY_PRINCIPAL_ARNS", [PROXY_USER]):
            for value in ["not-an-email", "a@example.com, b@example.com", "<script>@example.com", "a b@example.com"]:
                assert get_user_email(_function_url(PROXY_USER, {"x-forwarded-email": value})) == ""


class TestPerfToken:
    def test_matching_token(self):
        digest = hashlib.sha256(b"perf-token-value").hexdigest()
        with patch.object(auth_module, "PERF_TOKEN_SHA256", digest):
            assert has_valid_perf_token({"headers": {"x-perf-token": "perf-token-value"}}) is True
            assert has_valid_perf_token({"headers": {"x-perf-token": "anything"}}) is False

    def test_any_value_no_longer_passes_the_viewer_gate(self):
        with patch.object(auth_module, "ENABLE_VIEWER_GATE", True):
            assert is_authorized("", {"headers": {"x-perf-token": "anything"}}) is False
            digest = hashlib.sha256(b"perf-token-value").hexdigest()
            with patch.object(auth_module, "PERF_TOKEN_SHA256", digest):
                assert is_authorized("", {"headers": {"x-perf-token": "perf-token-value"}}) is True


class TestSameOriginPost:
    def _event(self, **headers):
        return {"headers": {"host": "compliance.test.example.com", **headers}}

    def test_fetch_metadata_same_origin(self):
        assert is_same_origin_post(self._event(**{"sec-fetch-site": "same-origin"})) is True

    @pytest.mark.parametrize("value", ["cross-site", "same-site", "none"])
    def test_fetch_metadata_other(self, value):
        event = self._event(**{"sec-fetch-site": value, "origin": "https://compliance.test.example.com"})
        assert is_same_origin_post(event) is False

    def test_origin_matches_host(self):
        assert is_same_origin_post(self._event(origin="https://compliance.test.example.com")) is True

    def test_origin_matches_forwarded_host(self):
        event = {
            "headers": {"host": "abc.lambda-url.example", "x-forwarded-host": "compliance.example"},
        }
        event["headers"]["origin"] = "https://compliance.example"
        assert is_same_origin_post(event) is True

    def test_origin_matches_trusted_origin(self):
        with patch.object(auth_module, "TRUSTED_ORIGINS", ["https://compliance.other.example"]):
            assert is_same_origin_post(self._event(origin="https://compliance.other.example")) is True

    @pytest.mark.parametrize(
        "origin", ["https://evil.example", "null", "https://compliance.test.example.com.evil.example"]
    )
    def test_foreign_origin(self, origin):
        assert is_same_origin_post(self._event(origin=origin)) is False

    def test_referer_used_when_no_origin(self):
        assert is_same_origin_post(self._event(referer="https://compliance.test.example.com/pending")) is True
        assert is_same_origin_post(self._event(referer="https://evil.example/x")) is False

    def test_no_source_header_refused(self):
        assert is_same_origin_post(self._event()) is False


class TestP256:
    def test_verifies_library_signature_and_rejects_changes(self):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

        key = alb_signing_key()
        for message in [b"", b"abc", b"x" * 1000]:
            r, s = decode_dss_signature(key.sign(message, ec.ECDSA(hashes.SHA256())))
            sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
            assert alb_jwt.verify_p256_sha256(alb_public_point(), message, sig) is True
            assert alb_jwt.verify_p256_sha256(alb_public_point(), message + b"!", sig) is False
            assert alb_jwt.verify_p256_sha256(alb_public_point(), message, sig[:-1] + bytes([sig[-1] ^ 1])) is False

    def test_rejects_degenerate_inputs(self):
        point = alb_public_point()
        assert alb_jwt.verify_p256_sha256(point, b"m", b"\x00" * 64) is False
        assert alb_jwt.verify_p256_sha256(point, b"m", b"\xff" * 64) is False
        assert alb_jwt.verify_p256_sha256(point, b"m", b"short") is False
        assert alb_jwt.verify_p256_sha256((1, 1), b"m", b"\x01" * 64) is False
        assert alb_jwt.verify_p256_sha256(None, b"m", b"\x01" * 64) is False

    def test_parses_published_key_format(self):
        pem = (
            alb_signing_key()
            .public_key()
            .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
            .decode()
        )
        assert alb_jwt.parse_p256_public_key_pem(pem) == alb_public_point()
        assert alb_jwt.parse_p256_public_key_pem("-----BEGIN PUBLIC KEY-----\nAAAA\n-----END PUBLIC KEY-----") is None
        assert alb_jwt.parse_p256_public_key_pem("not a key") is None


class TestIsAdmin:
    """Test is_admin — patches ADMIN_EMAILS since it's frozen at import time."""

    def test_admin_email_matches(self):
        with patch.object(auth_module, "ADMIN_EMAILS", ["admin@example.com"]):
            assert is_admin("admin@example.com") is True

    def test_case_insensitive(self):
        with patch.object(auth_module, "ADMIN_EMAILS", ["admin@example.com"]):
            assert is_admin("Admin@Example.Com") is True

    def test_non_admin(self):
        with patch.object(auth_module, "ADMIN_EMAILS", ["admin@example.com"]):
            assert is_admin("viewer@example.com") is False


class TestRequireAdmin:
    def test_admin_returns_none(self):
        with patch.object(auth_module, "ADMIN_EMAILS", ["admin@example.com"]):
            assert require_admin("admin@example.com") is None

    def test_non_admin_returns_message(self):
        with patch.object(auth_module, "ADMIN_EMAILS", ["admin@example.com"]):
            result = require_admin("viewer@example.com")
            assert result is not None
            assert "not in the admin list" in result

    def test_empty_email_returns_message(self):
        result = require_admin("")
        assert result is not None
        assert "Authentication required" in result
