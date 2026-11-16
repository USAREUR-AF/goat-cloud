"""
Data layer core — boto3 clients, environment config, and caching infrastructure.

All other data_* modules import shared state from here.
"""

import os
import time
from decimal import Decimal

from compliance_schema import require_env

# Inputs with no default. Read at import so a missing one stops the cold start
# with its name.
PROJECT = require_env("PROJECT")
REGION = require_env("REGION")
REPOSITORY_URL = require_env("REPOSITORY_URL")
DOMAIN_NAME = require_env("DOMAIN_NAME")

LOCAL_DATA_DIR = os.environ.get("LOCAL_DATA_DIR", "")

if LOCAL_DATA_DIR:
    # Local mode — no AWS clients needed
    s3 = None
    dynamodb = None
    ce = None
    ssm = None
else:
    import boto3

    s3 = boto3.client("s3")
    dynamodb = boto3.resource("dynamodb")
    ce = boto3.client("ce")
    ssm = boto3.client("ssm")

REPORTS_BUCKET = os.environ.get("REPORTS_BUCKET", "")
HK_STATE_TABLE = os.environ.get("HK_STATE_TABLE", "")
REPORT_TABLE = os.environ.get("REPORT_TABLE", "")
OVERRIDES_TABLE = os.environ.get("OVERRIDES_TABLE", "")
TF_STATE_BUCKET = os.environ.get("TF_STATE_BUCKET", "")
TF_STATE_KEY = os.environ.get("TF_STATE_KEY", "")
IMAGE_VERSIONS_SSM = os.environ.get("IMAGE_VERSIONS_SSM", "")
PROTECTED_RESOURCES_SSM = os.environ.get("PROTECTED_RESOURCES_SSM", "")
_project = os.environ.get("PROJECT", "")
_environment = os.environ.get("ENVIRONMENT", "")
JOB_STATUS_SSM_PREFIX = f"/{_project}/{_environment}/job-status" if _project and _environment else ""


# ---------------------------------------------------------------------------
# Three-tier in-memory cache.
#
#   _report_cache: report_key -> report_dict  (IMMUTABLE — never expires)
#   _api_cache:    key -> {data, expires}     (TTL-based, short or long)
# ---------------------------------------------------------------------------
_report_cache = {}  # report_key -> report_dict (immutable, no TTL)
_api_cache = {}  # key -> {"data": ..., "expires": monotonic_time}

_CACHE_TTL_INDEX = 30  # 30s — index pointer / report dates list
_CACHE_TTL_HK = 30  # 30s — hunterkiller records
_CACHE_TTL_OVERRIDES = 30  # 30s — override records
_CACHE_TTL_TF_STATE = 300  # 5 min  — TF state changes on apply
_CACHE_TTL_SECURITY = 600  # 10 min — ECR scans change rarely
_CACHE_TTL_COST = 900  # 15 min — Cost Explorer changes daily


def _cache_get(key):
    """Return cached value if present and not expired, else None."""
    entry = _api_cache.get(key)
    if entry and time.monotonic() < entry["expires"]:
        return entry["data"]
    return None


def _cache_set(key, data, ttl):
    """Store a value in the API cache with a TTL."""
    _api_cache[key] = {"data": data, "expires": time.monotonic() + ttl}


def invalidate_hk_caches():
    """Clear HK/pending caches after a write action (approve, abort, extend)."""
    _api_cache.pop("hk_records", None)
    _api_cache.pop("pending_destroys", None)


def invalidate_overrides_cache():
    """Clear overrides cache after a write action (create, delete)."""
    _api_cache.pop("overrides", None)


def clear_all_caches():
    """Clear all cache tiers (admin action, also called on cold start)."""
    _report_cache.clear()
    _api_cache.clear()


def _decimal_to_float(obj):
    """Recursively convert Decimal values back to float for JSON serialization."""
    if isinstance(obj, Decimal):
        if obj == int(obj):
            return int(obj)
        return float(obj)
    elif isinstance(obj, dict):
        return {k: _decimal_to_float(v) for k, v in obj.items()}
    elif isinstance(obj, list | tuple):
        return [_decimal_to_float(item) for item in obj]
    return obj


# A scan older than this many hours is shown as stale (the scanner runs daily).
SCAN_STALE_HOURS = float(os.environ.get("SCAN_STALE_HOURS", "26") or 26)


def scan_freshness(report, now=None):
    """Say whether the latest report can be trusted to describe the account.

    Returns a dict:
      status  "ok"       last complete scan is recent
              "stale"    last complete scan is older than SCAN_STALE_HOURS
              "error"    the latest scan did not complete
              "unknown"  no report, or no readable scan time
      age_hours            hours since the last complete scan, or None
      last_successful_scan_at
    """
    from datetime import UTC, datetime

    report = report or {}
    last_ok = report.get("last_successful_scan_at")
    if last_ok is None:
        last_ok = report.get("generated_at", "") if report.get("scan_complete", True) else ""
    result = {"status": "unknown", "age_hours": None, "last_successful_scan_at": last_ok or ""}
    if not report.get("generated_at"):
        return result
    try:
        last_dt = datetime.fromisoformat(str(last_ok).replace("Z", "+00:00"))
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=UTC)
        result["age_hours"] = max(0.0, ((now or datetime.now(UTC)) - last_dt).total_seconds() / 3600)
    except (ValueError, TypeError):
        pass
    if report.get("scan_complete", True) is False:
        result["status"] = "error"
    elif result["age_hours"] is None:
        result["status"] = "unknown"
    elif result["age_hours"] > SCAN_STALE_HOURS:
        result["status"] = "stale"
    else:
        result["status"] = "ok"
    return result


def get_job_statuses():
    """Read execution status for background jobs from SSM.

    Returns dict keyed by job name with last_run, duration_seconds, status, summary.
    Scanner data comes from the compliance report (passed separately), so only
    hunterkiller and stack-reaper are read here.
    """
    import json as _json

    if not JOB_STATUS_SSM_PREFIX or not ssm:
        return {}

    cached = _cache_get("job_statuses")
    if cached is not None:
        return cached

    statuses = {}
    for job in ("hunterkiller", "stack-reaper"):
        try:
            resp = ssm.get_parameter(Name=f"{JOB_STATUS_SSM_PREFIX}/{job}")
            statuses[job] = _json.loads(resp["Parameter"]["Value"])
        except ssm.exceptions.ParameterNotFound:
            pass
        except Exception as e:
            print(f"Warning: failed to read job status for {job}: {e}")

    _cache_set("job_statuses", statuses, _CACHE_TTL_INDEX)
    return statuses
