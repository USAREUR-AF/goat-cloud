"""
Authentication and authorization helpers.

This function does not sign users in. A front does, and tells the function
who the user is. An identity is accepted only from a front the deployment
names, and only in a form the function can check:

  A. Identity-aware proxy (for example OAuth2 Proxy). The proxy signs the user
     in and forwards the request with X-Forwarded-Email. The header is used
     only when the request provably came through the proxy:
       - Function URL with AWS_IAM auth: the SigV4 caller recorded by the
         Lambda service (requestContext.authorizer.iam.userArn) is one of
         TRUSTED_PROXY_PRINCIPAL_ARNS; or
       - load balancer bypass rule: the request carries the proxy's static
         header and its value hashes to PROXY_AUTH_HEADER_SHA256.
     The proxy must overwrite any X-Forwarded-Email a client sends.

  B. Load balancer authenticate-oidc. The load balancer adds a signed token
     in x-amzn-oidc-data. Its signature, signer (ALB_ARN), client and issuer
     (ALB_OIDC_CLIENT_ID, ALB_OIDC_ISSUER) and expiry are verified
     (alb_jwt.py) before the email claim is used. The module sets these only
     when it creates the authenticate-oidc rule itself.

Any other request has no identity: it can read pages when the viewer gate is
off and can perform no admin action.

Destructive actions (abort, destroy, extend, override) require admin email.
Viewer gate (optional): all GET routes restricted to authorized viewers.
"""

import hashlib
import hmac
import os
import re
import time
import urllib.parse

from alb_jwt import verified_claims

ADMIN_EMAILS = [e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()]
VIEWER_EMAILS = [e.strip().lower() for e in os.environ.get("VIEWER_EMAILS", "").split(",") if e.strip()]
ENABLE_VIEWER_GATE = os.environ.get("ENABLE_VIEWER_GATE", "false").lower() == "true"

# Fronts allowed to assert an identity (see module docstring).
TRUSTED_PROXY_PRINCIPAL_ARNS = [
    a.strip() for a in os.environ.get("TRUSTED_PROXY_PRINCIPAL_ARNS", "").split(",") if a.strip()
]
PROXY_AUTH_HEADER_NAME = os.environ.get("PROXY_AUTH_HEADER_NAME", "").strip().lower()
PROXY_AUTH_HEADER_SHA256 = os.environ.get("PROXY_AUTH_HEADER_SHA256", "").strip().lower()
PERF_TOKEN_SHA256 = os.environ.get("PERF_TOKEN_SHA256", "").strip().lower()
# Extra origins (scheme://host) allowed to submit forms, beyond the request's own host.
TRUSTED_ORIGINS = [o.strip().lower() for o in os.environ.get("TRUSTED_ORIGINS", "").split(",") if o.strip()]

_EMAIL_RE = re.compile(r"^[^@\s<>\"'`,;]+@[^@\s<>\"'`,;]+$")
_ASSUMED_ROLE_RE = re.compile(r"^arn:([^:]+):sts::(\d+):assumed-role/(.+)/[^/]+$")

# Warm-container cache for dynamic viewers/admins, reloaded from DynamoDB once
# it is older than _ROLE_CACHE_SECONDS. A demotion or revocation made through
# another container is seen within that time; every admin action also re-reads
# the caller's admin record with a consistent read (require_admin).
_ROLE_CACHE_SECONDS = 60
_dynamic_viewers = None
_dynamic_viewers_loaded_at = None
_dynamic_admins = None
_dynamic_admins_loaded_at = None


def _cache_fresh(loaded_at):
    return loaded_at is not None and time.monotonic() - loaded_at < _ROLE_CACHE_SECONDS


def _get_header(headers, mvh, name):
    """Read a header from either single- or multi-value form (any case)."""
    for key, val in (headers or {}).items():
        if key.lower() == name and val:
            return val
    for key, mv in (mvh or {}).items():
        if key.lower() == name and isinstance(mv, list) and mv:
            return mv[0]
    return ""


def _event_header(event, name):
    return _get_header(event.get("headers") or {}, event.get("multiValueHeaders") or {}, name)


def _hash_matches(value, expected_sha256):
    """Constant-time check that sha256(value) equals the configured digest."""
    if not value or not expected_sha256:
        return False
    return hmac.compare_digest(hashlib.sha256(value.encode()).hexdigest(), expected_sha256)


def _caller_principal(event):
    """IAM principal the Lambda service authenticated for a Function URL call.

    An assumed-role session ARN is reduced to its role ARN (role path excluded).
    """
    iam = ((event.get("requestContext") or {}).get("authorizer") or {}).get("iam") or {}
    arn = iam.get("userArn") or ""
    m = _ASSUMED_ROLE_RE.match(arn)
    if m:
        return f"arn:{m.group(1)}:iam::{m.group(2)}:role/{m.group(3)}"
    return arn


def _without_role_path(arn):
    """Drop the path from a role ARN (a session ARN never carries it)."""
    head, sep, tail = arn.partition(":role/")
    return head + sep + tail.rsplit("/", 1)[-1] if sep else arn


def _via_trusted_proxy(event):
    """True when the request provably came through a front that sets identity."""
    principal = _caller_principal(event)
    if principal and principal in {_without_role_path(a) for a in TRUSTED_PROXY_PRINCIPAL_ARNS}:
        return True
    return bool(
        PROXY_AUTH_HEADER_NAME and _hash_matches(_event_header(event, PROXY_AUTH_HEADER_NAME), PROXY_AUTH_HEADER_SHA256)
    )


def _clean_email(value):
    value = (value or "").strip().lower()
    if len(value) > 254 or not _EMAIL_RE.match(value):
        return ""
    return value


def get_user_email(event):
    """Return the verified email of the signed-in user, or "".

    See the module docstring for which fronts are accepted.
    """
    if _via_trusted_proxy(event):
        return _clean_email(_event_header(event, "x-forwarded-email"))

    if "elb" in (event.get("requestContext") or {}):
        claims = verified_claims(
            _event_header(event, "x-amzn-oidc-data"),
            os.environ.get("ALB_ARN", "").strip(),
            os.environ.get("AWS_REGION", ""),
            expected_client=os.environ.get("ALB_OIDC_CLIENT_ID", "").strip(),
            expected_issuer=os.environ.get("ALB_OIDC_ISSUER", "").strip(),
        )
        if claims:
            return _clean_email(str(claims.get("email", "")))

    return ""


def has_valid_perf_token(event):
    """True when the request carries the perf-test token of this deployment."""
    return _hash_matches(_event_header(event or {}, "x-perf-token"), PERF_TOKEN_SHA256)


def is_same_origin_post(event):
    """True when a state-changing request was sent by a page of this site.

    Browsers attach Sec-Fetch-Site and Origin to form posts; a page on another
    site cannot forge either. A request with neither header and no Referer is
    refused.
    """
    fetch_site = _event_header(event, "sec-fetch-site").strip().lower()
    if fetch_site:
        return fetch_site == "same-origin"

    source = _event_header(event, "origin") or _event_header(event, "referer")
    try:
        parsed = urllib.parse.urlsplit(source.strip())
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False
    source_host = parsed.netloc.lower()

    allowed_hosts = set()
    for name in ("host", "x-forwarded-host"):
        value = _event_header(event, name).split(",")[0].strip().lower()
        if value:
            allowed_hosts.add(value)
    for configured in [os.environ.get("DASHBOARD_URL", ""), *TRUSTED_ORIGINS]:
        host = urllib.parse.urlsplit(configured.strip()).netloc.lower()
        if host:
            allowed_hosts.add(host)
    return source_host in allowed_hosts


def is_admin(email):
    """Check if the email is in the static admin list or dynamic admin list."""
    e = email.lower()
    return e in ADMIN_EMAILS or e in _get_dynamic_admins()


def _is_dynamic_admin_now(email):
    """Consistent read of the caller's admin record. Any error means not admin."""
    table_name = os.environ.get("OVERRIDES_TABLE", "")
    if not table_name:
        return False
    try:
        import boto3

        table = boto3.resource("dynamodb").Table(table_name)
        item = table.get_item(Key={"resource_arn": f"ADMIN#{email.lower()}"}, ConsistentRead=True).get("Item")
    except Exception as e:
        print(f"WARN: Failed to read admin record: {e}")
        return False
    return bool(item)


def require_admin(email):
    """Return an error message if not admin, or None if authorized.

    Used on every admin route. A promoted (non-static) admin is checked
    against the current admin record with a consistent read, so a demotion is
    effective on every container at once.
    """
    if not email:
        return "Authentication required. No user identity found."
    e = email.lower()
    if e not in ADMIN_EMAILS and not _is_dynamic_admin_now(e):
        return f"Admin access required. {email} is not in the admin list."
    return None


def is_authorized(email, event=None):
    """Check if email is authorized to view the dashboard.

    When viewer gate is disabled, all authenticated users can view.
    When enabled, checks: admin list, static viewer list, dynamic DynamoDB
    viewers.
    """
    if not ENABLE_VIEWER_GATE:
        return True
    # The perf-test token is checked here as well as at the load balancer:
    # other listener rules forward the header whatever its value.
    if event and has_valid_perf_token(event):
        return True
    if not email:
        return False
    e = email.lower()
    if e in ADMIN_EMAILS:
        return True
    if e in VIEWER_EMAILS:
        return True
    return e in _get_dynamic_viewers()


def _get_dynamic_viewers():
    """Load enrolled viewers from DynamoDB (cached for _ROLE_CACHE_SECONDS)."""
    global _dynamic_viewers, _dynamic_viewers_loaded_at
    if _cache_fresh(_dynamic_viewers_loaded_at):
        return _dynamic_viewers

    import boto3

    table_name = os.environ.get("OVERRIDES_TABLE", "")
    if not table_name:
        _dynamic_viewers = set()
        _dynamic_viewers_loaded_at = time.monotonic()
        return _dynamic_viewers

    try:
        ddb = boto3.resource("dynamodb")
        table = ddb.Table(table_name)
        resp = table.scan(
            FilterExpression="begins_with(resource_arn, :prefix)",
            ExpressionAttributeValues={":prefix": "VIEWER#"},
            ProjectionExpression="resource_arn",
        )
        _dynamic_viewers = {item["resource_arn"].split("#", 1)[1].lower() for item in resp.get("Items", [])}
    except Exception as e:
        print(f"WARN: Failed to load dynamic viewers: {e}")
        _dynamic_viewers = set()

    _dynamic_viewers_loaded_at = time.monotonic()
    return _dynamic_viewers


def get_all_viewers():
    """Return combined set of all authorized viewer emails (for admin display)."""
    viewers = set(ADMIN_EMAILS) | set(VIEWER_EMAILS) | _get_dynamic_viewers()
    return sorted(viewers)


def add_dynamic_viewer(email, enrolled_by=""):
    """Add a viewer to DynamoDB and the in-memory cache."""
    global _dynamic_viewers

    import boto3

    table_name = os.environ.get("OVERRIDES_TABLE", "")
    if not table_name:
        return

    ddb = boto3.resource("dynamodb")
    table = ddb.Table(table_name)
    table.put_item(
        Item={
            "resource_arn": f"VIEWER#{email.lower()}",
            "email": email.lower(),
            "enrolled_at": int(time.time()),
            "enrolled_by": enrolled_by,
        }
    )

    # Update in-memory cache immediately
    if _dynamic_viewers is not None:
        _dynamic_viewers.add(email.lower())


def revoke_dynamic_viewer(email):
    """Remove a dynamic viewer from DynamoDB and clear the in-memory cache."""
    global _dynamic_viewers

    import boto3

    table_name = os.environ.get("OVERRIDES_TABLE", "")
    if not table_name:
        return

    ddb = boto3.resource("dynamodb")
    table = ddb.Table(table_name)
    table.delete_item(Key={"resource_arn": f"VIEWER#{email.lower()}"})

    # Also remove their visit record
    import contextlib

    with contextlib.suppress(Exception):
        table.delete_item(Key={"resource_arn": f"VISIT#{email.lower()}"})

    # Update in-memory cache
    if _dynamic_viewers is not None:
        _dynamic_viewers.discard(email.lower())


# ── Dynamic admins ─────────────────────────────────────────────────────────


def _get_dynamic_admins():
    """Load promoted admins from DynamoDB (cached for _ROLE_CACHE_SECONDS)."""
    global _dynamic_admins, _dynamic_admins_loaded_at
    if _cache_fresh(_dynamic_admins_loaded_at):
        return _dynamic_admins

    import boto3

    table_name = os.environ.get("OVERRIDES_TABLE", "")
    if not table_name:
        _dynamic_admins = set()
        _dynamic_admins_loaded_at = time.monotonic()
        return _dynamic_admins

    try:
        ddb = boto3.resource("dynamodb")
        table = ddb.Table(table_name)
        resp = table.scan(
            FilterExpression="begins_with(resource_arn, :prefix)",
            ExpressionAttributeValues={":prefix": "ADMIN#"},
            ProjectionExpression="resource_arn",
        )
        _dynamic_admins = {item["resource_arn"].split("#", 1)[1].lower() for item in resp.get("Items", [])}
    except Exception as e:
        print(f"WARN: Failed to load dynamic admins: {e}")
        _dynamic_admins = set()

    _dynamic_admins_loaded_at = time.monotonic()
    return _dynamic_admins


def promote_to_admin(email, promoted_by=""):
    """Promote a viewer to admin in DynamoDB and update in-memory cache."""
    global _dynamic_admins

    import boto3

    table_name = os.environ.get("OVERRIDES_TABLE", "")
    if not table_name:
        return

    ddb = boto3.resource("dynamodb")
    table = ddb.Table(table_name)
    table.put_item(
        Item={
            "resource_arn": f"ADMIN#{email.lower()}",
            "email": email.lower(),
            "promoted_at": int(time.time()),
            "promoted_by": promoted_by,
        }
    )

    if _dynamic_admins is not None:
        _dynamic_admins.add(email.lower())


def demote_from_admin(email):
    """Remove dynamic admin status from DynamoDB and update in-memory cache."""
    global _dynamic_admins

    import boto3

    table_name = os.environ.get("OVERRIDES_TABLE", "")
    if not table_name:
        return

    ddb = boto3.resource("dynamodb")
    table = ddb.Table(table_name)
    table.delete_item(Key={"resource_arn": f"ADMIN#{email.lower()}"})

    if _dynamic_admins is not None:
        _dynamic_admins.discard(email.lower())


# ── Last-visited tracking ──────────────────────────────────────────────────
# Throttle writes: at most once per 5 minutes per user per warm container.

_last_visited_written = {}  # email -> monotonic timestamp of last DDB write
_LAST_VISITED_THROTTLE = 300  # seconds


def record_last_visited(email):
    """Record the user's last access timestamp in DynamoDB (throttled)."""
    if not email:
        return

    now_mono = time.monotonic()
    last_write = _last_visited_written.get(email, 0)
    if now_mono - last_write < _LAST_VISITED_THROTTLE:
        return

    table_name = os.environ.get("OVERRIDES_TABLE", "")
    if not table_name:
        return

    try:
        import boto3

        ddb = boto3.resource("dynamodb")
        table = ddb.Table(table_name)
        table.put_item(
            Item={
                "resource_arn": f"VISIT#{email.lower()}",
                "email": email.lower(),
                "last_visited": int(time.time()),
            }
        )
        _last_visited_written[email] = now_mono
    except Exception as e:
        print(f"WARN: Failed to record last visited for {email}: {e}")


# ── Access list (admin page) ──────────────────────────────────────────────


def get_access_list():
    """Build a complete access list for the admin /access page.

    Returns a list of dicts with: email, role, source, enrolled_at, last_visited.
    """
    import boto3

    result = []
    seen = set()

    # Tier 1: Admins (static config)
    for email in ADMIN_EMAILS:
        seen.add(email)
        result.append(
            {
                "email": email,
                "role": "admin",
                "source": "static-config",
                "enrolled_at": None,
                "last_visited": None,
            }
        )

    # Tier 2: Static viewers (excluding those already listed as admin)
    for email in VIEWER_EMAILS:
        if email in seen:
            continue
        seen.add(email)
        result.append(
            {
                "email": email,
                "role": "viewer",
                "source": "static-config",
                "enrolled_at": None,
                "last_visited": None,
            }
        )

    # Tier 3: Dynamic viewers + visit records from DynamoDB
    table_name = os.environ.get("OVERRIDES_TABLE", "")
    if not table_name:
        return result

    try:
        ddb = boto3.resource("dynamodb")
        table = ddb.Table(table_name)

        # Scan for ADMIN# items (dynamic admin promotions)
        resp_admin = table.scan(
            FilterExpression="begins_with(resource_arn, :prefix)",
            ExpressionAttributeValues={":prefix": "ADMIN#"},
        )
        admin_map = {item.get("email", "").lower(): item for item in resp_admin.get("Items", [])}

        # Upgrade static viewers who were dynamically promoted to admin
        for entry in result:
            if entry["email"] in admin_map and entry["role"] != "admin":
                promo = admin_map[entry["email"]]
                entry["role"] = "admin"
                entry["source"] = f"promoted:{promo.get('promoted_by', '')}"
                entry["enrolled_at"] = promo.get("promoted_at", entry.get("enrolled_at"))

        # Scan for VIEWER# items (enrollment records)
        resp = table.scan(
            FilterExpression="begins_with(resource_arn, :prefix)",
            ExpressionAttributeValues={":prefix": "VIEWER#"},
        )
        viewer_items = resp.get("Items", [])

        # Scan for VISIT# items (last-visited records)
        resp2 = table.scan(
            FilterExpression="begins_with(resource_arn, :prefix)",
            ExpressionAttributeValues={":prefix": "VISIT#"},
        )
        visit_map = {
            item["resource_arn"].split("#", 1)[1].lower(): int(item.get("last_visited", 0))
            for item in resp2.get("Items", [])
        }

        # Add dynamic viewers (or dynamic admins who aren't in static config)
        for item in viewer_items:
            email = item.get("email", "").lower()
            if email in seen:
                continue
            seen.add(email)
            is_promoted = email in admin_map
            promo = admin_map.get(email, {})
            result.append(
                {
                    "email": email,
                    "role": "admin" if is_promoted else "viewer",
                    "source": f"promoted:{promo.get('promoted_by', '')}"
                    if is_promoted
                    else item.get("enrolled_by", "unknown"),
                    "enrolled_at": promo.get("promoted_at") if is_promoted else item.get("enrolled_at"),
                    "last_visited": None,
                }
            )

        # Dynamic admins who aren't viewers or static users (edge case: promoted externally)
        for email, promo in admin_map.items():
            if email in seen:
                continue
            seen.add(email)
            result.append(
                {
                    "email": email,
                    "role": "admin",
                    "source": f"promoted:{promo.get('promoted_by', '')}",
                    "enrolled_at": promo.get("promoted_at"),
                    "last_visited": None,
                }
            )

        # Merge last-visited timestamps into all entries
        for entry in result:
            lv = visit_map.get(entry["email"])
            if lv:
                entry["last_visited"] = lv

    except Exception as e:
        print(f"WARN: Failed to load access list from DynamoDB: {e}")

    return result
