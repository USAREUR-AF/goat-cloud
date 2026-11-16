"""Which Function URL callers the dashboard trusts to assert a user identity.

Permission to invoke the Function URL and permission to assert an identity are
two inputs. A front that only signs requests (and forwards client headers)
must not make every client an admin.
"""

import re
from pathlib import Path
from unittest.mock import patch

import auth as auth_module
from factories import make_alb_event

_MODULE = Path(__file__).resolve().parents[2] / "modules" / "aws" / "compliance-dashboard"


def _env_value(name):
    source = (_MODULE / "main.tf").read_text()
    match = re.search(rf"^\s*{name}\s*=\s*(.+)$", source, re.MULTILINE)
    assert match, f"{name} not set in main.tf"
    return match.group(1)


def test_trusted_proxies_come_from_the_identity_input_not_the_invoke_list():
    value = _env_value("TRUSTED_PROXY_PRINCIPAL_ARNS")
    assert "var.function_url_identity_proxy_principal_arns" in value
    assert "function_url_invoke_principal_arns" not in value


def test_identity_input_defaults_to_empty():
    source = (_MODULE / "variables.tf").read_text()
    block = re.search(r'variable "function_url_identity_proxy_principal_arns" \{(.*?)\n\}', source, re.DOTALL)
    assert block, "input missing"
    assert re.search(r"default\s*=\s*\[\]", block.group(1))


def _function_url_event(caller_arn, email):
    event = make_alb_event("/approve-all", method="POST", headers={"x-forwarded-email": email})
    event["requestContext"] = {"authorizer": {"iam": {"userArn": caller_arn}}}
    return event


def test_invoker_not_named_as_identity_proxy_is_anonymous():
    invoker = "arn:aws:iam::123456789012:user/signing-proxy"
    with patch.object(auth_module, "TRUSTED_PROXY_PRINCIPAL_ARNS", []):
        assert auth_module.get_user_email(_function_url_event(invoker, "admin@example.com")) == ""


def test_named_identity_proxy_asserts_identity():
    proxy = "arn:aws:iam::123456789012:user/sign-in-proxy"
    with patch.object(auth_module, "TRUSTED_PROXY_PRINCIPAL_ARNS", [proxy]):
        assert auth_module.get_user_email(_function_url_event(proxy, "admin@example.com")) == "admin@example.com"


def test_load_balancer_signer_is_set_only_with_the_modules_own_sign_in_rule():
    source = (_MODULE / "main.tf").read_text()
    rule = re.search(r"^\s*alb_oidc_rule\s*=\s*(.+)$", source, re.MULTILINE).group(1)
    assert "var.create_public_listener_rule" in rule and "var.alb_oidc_config != null" in rule
    assert re.search(r"^\s*alb_arn\s*=\s*local\.alb_oidc_rule\b", source, re.MULTILINE)
    assert "local.alb_oidc_rule ? var.alb_oidc_config.client_id" in _env_value("ALB_OIDC_CLIENT_ID")
    assert "local.alb_oidc_rule ? var.alb_oidc_config.issuer" in _env_value("ALB_OIDC_ISSUER")
