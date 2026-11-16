"""Root conftest — shared fixtures and environment defaults for all compliance tests."""

import os

import pytest
from factories import TEST_ALB_ARN, TEST_ALB_KID, TEST_OIDC_CLIENT_ID, TEST_OIDC_ISSUER, TEST_REGION, alb_public_point

# Inputs the Lambdas require; a module that reads one at import stops when it is unset.
REQUIRED_ENV = {
    "AWS_DEFAULT_REGION": TEST_REGION,
    "AWS_REGION": TEST_REGION,
    "REGION": TEST_REGION,
    "PROJECT": "test-project",
    "ENVIRONMENT": "test",
    "REPOSITORY_URL": "https://github.com/acme/platform-infra",
    "DOMAIN_NAME": "example.com",
}

# Set critical env vars BEFORE any module imports happen (collection time).
# data.py creates boto3 clients at import time when LOCAL_DATA_DIR is empty.
for _name, _value in REQUIRED_ENV.items():
    os.environ.setdefault(_name, _value)
os.environ.setdefault("LOCAL_DATA_DIR", "/tmp/test-compliance-local")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Set safe defaults for all Lambda environment variables."""
    defaults = {
        **REQUIRED_ENV,
        "REPORTS_BUCKET": "",
        "REPORT_TABLE": "",
        "TF_STATE_BUCKET": "",
        "TF_STATE_KEY": "",
        "REQUIRED_TAGS": "",  # empty: the Lambdas fall back to the generated contract
        "EXCLUSION_ARNS": "",
        "EXCLUSION_PATTERNS": "",
        "SES_SENDER_EMAIL": "",
        "SES_RECIPIENT_EMAILS": "",
        "SES_ADMIN_EMAILS": "",
        "ADMIN_EMAILS": "admin@example.com",
        "VIEWER_EMAILS": "",
        "ENABLE_VIEWER_GATE": "false",
        "STATE_TABLE": "",
        "HK_STATE_TABLE": "",
        "OVERRIDES_TABLE": "",
        "DRY_RUN": "true",
        "GRACE_PERIOD_HOURS": "24",
        "PROTECTED_ENVIRONMENTS": "prod,production",
        "PROTECTED_TAG_KEY": "Protected",
        "PROTECTED_RESOURCE_TYPES": "route53:hostedzone,payments:payment-instrument",
        "CONTACT_TAG_KEYS": "Contact,OwnerEmail,Owner",
        "DASHBOARD_URL": "https://compliance.test.example.com",
        "SCANNER_FUNCTION_NAME": "",
        "HK_FUNCTION_NAME": "",
        "HK_EXECUTOR_FUNCTION_NAME": "",
        "EXECUTOR_ARMED": "false",
        "PROTECTED_RESOURCES_SSM": "",
        "LOCAL_DATA_DIR": "",
        "IMAGE_VERSIONS_JSON": "{}",
        "STATE_TTL_DAYS": "30",
        "LAMBDA_FUNCTION_NAME": "",
        "HUNTERKILLER_FUNCTION_NAME": "",
    }
    for key, val in defaults.items():
        monkeypatch.setenv(key, val)


@pytest.fixture(autouse=True)
def _alb_identity(monkeypatch):
    """Stand in for the load balancer: its ARN and its published signing key.

    Only the key published for TEST_ALB_KID is the test key, so a token signed
    by any other key, or naming any other signer, is rejected as in production.
    """
    import alb_jwt

    def fetch(region, kid):
        return alb_public_point() if kid == TEST_ALB_KID else None

    monkeypatch.setenv("ALB_ARN", TEST_ALB_ARN)
    monkeypatch.setenv("ALB_OIDC_CLIENT_ID", TEST_OIDC_CLIENT_ID)
    monkeypatch.setenv("ALB_OIDC_ISSUER", TEST_OIDC_ISSUER)
    monkeypatch.setattr(alb_jwt, "_fetch_public_key", fetch)
    alb_jwt._verified_cache.clear()
