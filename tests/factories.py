"""
Shared test data factories for compliance reports, findings, resources,
and DynamoDB records.

Every factory returns a dict with sensible defaults. Override any field via kwargs.
"""

import base64
import json
from datetime import UTC, datetime, timedelta

# Region used by fixtures. The static price tables describe this region, so cost
# tests use it.
TEST_REGION = "eu-central-1"  # oss-allow: fixture value, not a default


def make_resource(**overrides):
    """Build a discovered resource dict."""
    base = {
        "arn": "arn:aws:ec2:eu-central-1:123456789012:instance/i-0abc123def456",
        "resource_type": "ec2:instance",
        "region": "eu-central-1",
        "name": "test-instance",
        "state": "running",
        "tags": {
            "Project": "acme",
            "Environment": "dev",
            "ManagedBy": "terraform",
            "Lifecycle": "persistent",
            "Repository": "https://github.com/acme/platform-infra",
        },
        "estimated_monthly_cost": 21.02,
        "instance_type": "t3.small",
        "created_at": "2025-01-15T10:00:00+00:00",
        "created_by": None,
        "running_duration_days": 90,
        "inferred_purpose": "Test instance",
        "attached_to": None,
        "is_default": False,
    }
    base.update(overrides)
    return base


def make_finding(category="drift_detection", severity="HIGH", **overrides):
    """Build a compliance finding dict."""
    base = {
        "category": category,
        "severity": severity,
        "resource_arn": "arn:aws:ec2:eu-central-1:123456789012:instance/i-0abc123def456",
        "resource_type": "ec2:instance",
        "resource_name": "orphan-instance",
        "region": "eu-central-1",
        "state": "running",
        "issue": "Resource exists in AWS but not in Terraform state (possible click-ops)",
        "tags": {},
        "created_at": "2025-01-15T10:00:00+00:00",
        "created_by": None,
        "running_duration_days": 90,
        "estimated_monthly_cost": 21.02,
        "inferred_purpose": None,
        "attached_to": None,
        "recommendation": "Investigate: import into Terraform or terminate.",
    }
    base.update(overrides)
    return base


def make_tag_finding(**overrides):
    """Build a tag compliance finding."""
    defaults = {
        "category": "tag_compliance",
        "severity": "HIGH",
        "issue": "Missing required tags: Project, Environment, ManagedBy",
        "missing_tags": ["Project", "Environment", "ManagedBy"],
        "existing_tags": {},
    }
    defaults.update(overrides)
    return make_finding(**defaults)


def make_report(num_drift=2, num_tag=1, num_schedule=0, num_lifecycle=0, **overrides):
    """Build a structurally complete compliance report."""
    resources = [
        make_resource(
            arn=f"arn:aws:ec2:eu-central-1:123456789012:instance/i-{i:012d}",
            name=f"instance-{i}",
        )
        for i in range(5)
    ]

    drift_findings = [
        make_finding(
            resource_arn=f"arn:aws:ec2:eu-central-1:123456789012:instance/i-drift{i:06d}",
            resource_name=f"drift-{i}",
        )
        for i in range(num_drift)
    ]

    tag_findings = [
        make_tag_finding(
            resource_arn=f"arn:aws:ec2:eu-central-1:123456789012:volume/vol-tag{i:06d}",
            resource_name=f"untagged-vol-{i}",
            resource_type="ec2:volume",
        )
        for i in range(num_tag)
    ]

    schedule_findings = [
        make_finding(
            category="schedule_compliance",
            severity="MEDIUM",
            resource_arn=f"arn:aws:ec2:eu-central-1:123456789012:instance/i-sched{i:06d}",
            resource_name=f"no-schedule-{i}",
            issue="No schedule tag found",
        )
        for i in range(num_schedule)
    ]

    lifecycle_findings = [
        make_finding(
            category="lifecycle_compliance",
            severity="LOW",
            resource_arn=f"arn:aws:ec2:eu-central-1:123456789012:volume/vol-life{i:06d}",
            resource_name=f"no-lifecycle-{i}",
            resource_type="ec2:volume",
            issue="No Lifecycle tag",
        )
        for i in range(num_lifecycle)
    ]

    all_findings = drift_findings + tag_findings + schedule_findings + lifecycle_findings
    high = sum(1 for f in all_findings if f["severity"] == "HIGH")
    medium = sum(1 for f in all_findings if f["severity"] == "MEDIUM")
    low = sum(1 for f in all_findings if f["severity"] == "LOW")

    base = {
        "report_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "scan_duration_seconds": 42.5,
        "account_id": "123456789012",
        "project": "acme",
        "environment": "dev",
        "regions_scanned": ["eu-central-1", "us-east-1"],
        "regions_with_resources": ["eu-central-1"],
        "discovered_resources": resources,
        "drift_detection": {
            "terraform_managed": 100,
            "unmanaged": len(drift_findings),
            "findings": drift_findings,
        },
        "tag_compliance": {"findings": tag_findings},
        "schedule_compliance": {"findings": schedule_findings},
        "lifecycle_compliance": {"findings": lifecycle_findings},
        "summary": {
            "total_resources_discovered": len(resources),
            "total_findings": len(all_findings),
            "findings_by_severity": {"HIGH": high, "MEDIUM": medium, "LOW": low},
            "findings_by_category": {
                "drift_detection": len(drift_findings),
                "tag_compliance": len(tag_findings),
                "schedule_compliance": len(schedule_findings),
                "lifecycle_compliance": len(lifecycle_findings),
            },
        },
        "region_costs": {"eu-central-1": 450.00, "us-east-1": 12.00},
        "service_costs": {"Amazon Elastic Compute Cloud": 300.00},
        "resource_type_counts": {"ec2:instance": 5},
        "region_type_counts": {"eu-central-1": {"ec2:instance": 5}},
        "errors": [],
    }
    base.update(overrides)
    return base


def make_hk_record(status="warned", **overrides):
    """Build a hunterkiller DynamoDB state record."""
    now = datetime.now(UTC)
    base = {
        "resource_arn": "arn:aws:ec2:eu-central-1:123456789012:instance/i-0abc123def456",
        "status": status,
        "batch_id": "20260220T030000",
        "warned_at": now.isoformat(),
        "grace_deadline": (now + timedelta(hours=24)).isoformat(),
        "contact_email": "admin@example.com",
        "contact_source": "admin_fallback",
        "resource_type": "ec2:instance",
        "resource_name": "orphan-instance",
        "region": "eu-central-1",
        "estimated_monthly_cost": "21.02",
        "issues": ["Resource exists in AWS but not in Terraform state"],
        "scan_date": now.strftime("%Y-%m-%d"),
        "tags": {},
        "approval_status": "pending",
        "approved_by": "",
        "approved_at": "",
    }
    base.update(overrides)
    return base


TEST_ALB_ARN = "arn:aws:elasticloadbalancing:eu-central-1:123456789012:loadbalancer/app/test/0123456789abcdef"
TEST_ALB_KID = "11111111-2222-3333-4444-555555555555"
TEST_OIDC_CLIENT_ID = "compliance-dashboard"
TEST_OIDC_ISSUER = "https://idp.test.example.com"
_alb_signing_key = None


def alb_signing_key():
    """P-256 key standing in for the load balancer's signing key (generated once per run)."""
    global _alb_signing_key
    if _alb_signing_key is None:
        from cryptography.hazmat.primitives.asymmetric import ec

        _alb_signing_key = ec.generate_private_key(ec.SECP256R1())
    return _alb_signing_key


def alb_public_point(key=None):
    """(x, y) of the test signing key, as alb_jwt returns it for a fetched key."""
    numbers = (key or alb_signing_key()).public_key().public_numbers()
    return numbers.x, numbers.y


def make_alb_jwt(
    claims,
    signer=TEST_ALB_ARN,
    kid=TEST_ALB_KID,
    alg="ES256",
    key=None,
    exp_in=300,
    client=TEST_OIDC_CLIENT_ID,
    issuer=TEST_OIDC_ISSUER,
):
    """Build an x-amzn-oidc-data token signed the way a load balancer signs it."""
    import time

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    exp = int(time.time()) + exp_in
    header = {"typ": "JWT", "alg": alg, "kid": kid, "signer": signer, "iss": issuer, "client": client, "exp": exp}
    body = {"exp": exp, **claims}
    # The load balancer keeps base64 padding in its tokens.
    header_b64 = base64.urlsafe_b64encode(json.dumps(header).encode()).decode()
    payload_b64 = base64.urlsafe_b64encode(json.dumps(body).encode()).decode()
    der = (key or alb_signing_key()).sign(f"{header_b64}.{payload_b64}".encode(), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    signature = base64.urlsafe_b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big")).decode()
    return f"{header_b64}.{payload_b64}.{signature}"


def make_alb_event(path="/", method="GET", email="viewer@example.com", qs=None, body=None, **overrides):
    """Build an ALB Lambda event dict with a signed OIDC identity token."""
    jwt = make_alb_jwt({"email": email, "sub": email, "iss": "https://idp.test.example.com"})

    base = {
        "requestContext": {
            "elb": {"targetGroupArn": "arn:aws:elasticloadbalancing:eu-central-1:123456789012:targetgroup/test/abc"}
        },
        "httpMethod": method,
        "path": path,
        "headers": {
            "x-amzn-oidc-data": jwt,
            "x-amzn-oidc-identity": email,
            "host": "compliance.test.example.com",
            "origin": "https://compliance.test.example.com",
            "accept-encoding": "gzip",
        },
        "queryStringParameters": qs or {},
        "body": body,
        "isBase64Encoded": False,
    }
    base.update(overrides)
    return base
