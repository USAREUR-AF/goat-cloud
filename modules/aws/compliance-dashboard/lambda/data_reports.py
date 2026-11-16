"""
Data layer — report loading from DynamoDB/S3 with immutable caching.
"""

import json
import os

from data_core import (
    _CACHE_TTL_INDEX,
    LOCAL_DATA_DIR,
    REPORT_TABLE,
    REPORTS_BUCKET,
    _cache_get,
    _cache_set,
    _decimal_to_float,
    _report_cache,
    dynamodb,
    s3,
)


def get_report_dates(limit=30):
    """List available compliance report keys from DynamoDB, newest first.

    Returns timestamp-based keys (e.g., "2026-02-20T19:41:56") from the
    INDEX/REPORTS item. Falls back to S3 date-based listing if no DynamoDB
    table is configured (backward compatibility during migration).

    Cached 30s — only changes when a new scan completes.
    """
    if LOCAL_DATA_DIR:
        return ["local"]

    cached = _cache_get("report_dates")
    if cached is not None:
        return cached[:limit]

    if REPORT_TABLE:
        try:
            table = dynamodb.Table(REPORT_TABLE)
            response = table.get_item(Key={"PK": "INDEX", "SK": "REPORTS"})
            item = response.get("Item")
            if item:
                keys = item.get("report_keys", [])
                _cache_set("report_dates", keys, _CACHE_TTL_INDEX)
                return keys[:limit]
        except Exception as e:
            print(f"Error reading report index from DynamoDB: {e}")

    # Fallback: S3 listing (backward compatibility)
    try:
        response = s3.list_objects_v2(
            Bucket=REPORTS_BUCKET,
            Prefix="reports/",
            Delimiter="/",
        )
        prefixes = [p["Prefix"] for p in response.get("CommonPrefixes", [])]
        dates = []
        for p in prefixes:
            parts = p.strip("/").split("/")
            if len(parts) >= 2:
                dates.append(parts[1])
        dates.sort(reverse=True)
        _cache_set("report_dates", dates, _CACHE_TTL_INDEX)
        return dates[:limit]
    except Exception as e:
        print(f"Error listing report dates: {e}")
        return []


def get_latest_report_date():
    """Find the most recent compliance report key.

    Reads the INDEX/LATEST pointer from DynamoDB (single point read).
    Falls back to get_report_dates() if not available.

    Cached 30s — only changes when a new scan completes.
    """
    if LOCAL_DATA_DIR:
        return "local"

    cached = _cache_get("latest_report_date")
    if cached is not None:
        return cached

    if REPORT_TABLE:
        try:
            table = dynamodb.Table(REPORT_TABLE)
            response = table.get_item(Key={"PK": "INDEX", "SK": "LATEST"})
            item = response.get("Item")
            if item:
                key = item.get("report_key")
                _cache_set("latest_report_date", key, _CACHE_TTL_INDEX)
                return key
        except Exception as e:
            print(f"Error reading latest report pointer from DynamoDB: {e}")

    dates = get_report_dates(limit=1)
    result = dates[0] if dates else None
    if result:
        _cache_set("latest_report_date", result, _CACHE_TTL_INDEX)
    return result


def get_previous_report(current_date=None):
    """Load the report from the day before the given date.

    Finds the closest available prior date (not necessarily exactly -1 day).
    Returns (date_str, report_dict) or (None, None) if no prior report.
    """
    dates = get_report_dates(limit=30)
    if not current_date:
        current_date = dates[0] if dates else None
    if not current_date:
        return None, None

    # Find first date strictly before current_date
    for d in dates:
        if d < current_date:
            report = get_compliance_report(d)
            if report:
                return d, report
    return None, None


def get_24h_previous_report(current_date=None):
    """Load a report that is at least 24 hours before the given date.

    Prefers the most recent report that is >= 24h old. If no report is
    that old, falls back to the oldest available report (maximising the
    comparison window).

    Returns (date_str, report_dict) or (None, None) if no prior report.
    """
    from datetime import timedelta

    dates = get_report_dates(limit=30)
    if not current_date:
        current_date = dates[0] if dates else None
    if not current_date:
        return None, None

    # Parse current_date as datetime for age comparison
    current_dt = _parse_report_date(current_date)
    cutoff = current_dt - timedelta(hours=24) if current_dt else None

    prior_dates = [d for d in dates if d < current_date]
    if not prior_dates:
        return None, None

    if cutoff:
        # Find the most recent report that is >= 24h old
        for d in prior_dates:
            d_dt = _parse_report_date(d)
            if d_dt and d_dt <= cutoff:
                report = get_compliance_report(d)
                if report:
                    return d, report

    # No report >= 24h old (or unparseable dates) — use the oldest available
    oldest = prior_dates[-1]  # dates are newest-first
    report = get_compliance_report(oldest)
    if report:
        return oldest, report
    return None, None


def _parse_report_date(date_str):
    """Parse a report key into a datetime, or None if unparseable.

    Handles ISO timestamps (2026-02-20T19:41:56) and plain dates (2026-02-20).
    """
    from datetime import UTC, datetime

    if not date_str or date_str == "local":
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def get_month_start_report(current_date=None):
    """Load the monthly baseline report for destroyed-resource detection.

    Checks for a scanner-saved baseline at baselines/YYYY-MM.json first
    (the first scan of each month saves a copy there). Falls back to
    the earliest daily report in the current month if no baseline exists.

    Returns (date_str, report_dict) or (None, None) if no prior report.
    """
    dates = get_report_dates(limit=30)
    if not current_date:
        current_date = dates[0] if dates else None
    if not current_date:
        return None, None

    month_prefix = current_date[:7]

    # Try scanner-saved monthly baseline first (most complete)
    if REPORTS_BUCKET and not LOCAL_DATA_DIR:
        baseline_key = f"baselines/{month_prefix}.json"
        try:
            response = s3.get_object(Bucket=REPORTS_BUCKET, Key=baseline_key)
            report = json.loads(response["Body"].read().decode("utf-8"))
            return f"{month_prefix}-01", report
        except Exception:
            pass  # No baseline yet — fall back to daily reports

    # Fall back: earliest daily report in this month
    month_dates = [d for d in dates if d.startswith(month_prefix) and d < current_date]
    if month_dates:
        baseline_date = month_dates[-1]  # dates are newest-first, last = earliest
    else:
        # No earlier report this month — fall back to last report of prior month
        for d in dates:
            if d < current_date:
                baseline_date = d
                break
        else:
            return None, None

    report = get_compliance_report(baseline_date)
    if report:
        return baseline_date, report
    return None, None


_EMPTY_REPORT = {
    "scan_date": "N/A",
    "environment": "local",
    "summary": {"total_resources": 0, "total_findings": 0, "findings_by_severity": {}},
    "drift_detection": {"findings": []},
    "tag_compliance": {"findings": []},
    "schedule_compliance": {"findings": []},
    "lifecycle_compliance": {"findings": []},
}


def get_compliance_report(date=None):
    """Load the compliance report from DynamoDB (or local file).

    In local mode, returns an empty report if the file is missing — this lets
    developers work on visualizations with only a TF state file.

    In Lambda mode, reads from DynamoDB using a partition query on
    REPORT#{report_key}. Falls back to S3 if no DynamoDB table is configured.

    Report data is IMMUTABLE (each scan creates a unique partition), so reports
    are cached forever by their key. Only the first load per report_key per
    container lifecycle hits DynamoDB.
    """
    if LOCAL_DATA_DIR:
        path = os.path.join(LOCAL_DATA_DIR, "compliance-report.json")
        try:
            with open(path) as f:
                return json.load(f)
        except FileNotFoundError:
            print(f"No compliance report at {path} — using empty report")
            return dict(_EMPTY_REPORT)
        except Exception as e:
            print(f"Error loading local report {path}: {e}")
            return dict(_EMPTY_REPORT)

    if not date:
        date = get_latest_report_date()
    if not date:
        print("No report date found — returning empty report")
        return dict(_EMPTY_REPORT)

    # Check immutable report cache first
    if date in _report_cache:
        return _report_cache[date]

    # Try DynamoDB first
    if REPORT_TABLE:
        try:
            report = _load_report_from_dynamodb(date)
            if report:
                _report_cache[date] = report
                return report
        except Exception as e:
            print(f"Error loading report from DynamoDB for {date}: {e}")

    # Fallback: S3 (backward compatibility — handles pre-migration reports)
    # For timestamp-based keys (e.g., "2026-02-20T19:41:56"), extract the date
    s3_date = date[:10] if len(date) > 10 else date
    key = f"reports/{s3_date}/compliance-report.json"
    try:
        response = s3.get_object(Bucket=REPORTS_BUCKET, Key=key)
        report = json.loads(response["Body"].read().decode("utf-8"))
        _report_cache[date] = report
        return report
    except Exception as e:
        print(f"Error loading report s3://{REPORTS_BUCKET}/{key}: {e}")
        return dict(_EMPTY_REPORT)


def _load_report_from_dynamodb(report_key):
    """Load and reassemble a compliance report from DynamoDB items.

    Queries all items in the REPORT#{report_key} partition and reconstructs
    the original report dict. Returns None if the report doesn't exist.
    """
    from boto3.dynamodb.conditions import Key

    table = dynamodb.Table(REPORT_TABLE)
    pk = f"REPORT#{report_key}"

    response = table.query(KeyConditionExpression=Key("PK").eq(pk))
    items = response.get("Items", [])
    while "LastEvaluatedKey" in response:
        response = table.query(
            KeyConditionExpression=Key("PK").eq(pk),
            ExclusiveStartKey=response["LastEvaluatedKey"],
        )
        items.extend(response.get("Items", []))

    if not items:
        return None

    return _reassemble_report(items)


def _reassemble_report(items):
    """Reconstruct the original report dict from DynamoDB items.

    Converts Decimal back to float for JSON serialization compatibility.
    """
    report = {}
    finding_batches = {}  # cat_name -> [(batch_idx, findings, cat_meta)]
    resource_batches = []  # [(batch_idx, resources)]

    for item in items:
        sk = item.get("SK", "")

        if sk == "META":
            # Core metadata fields
            report["report_version"] = item.get("report_version", "1.0")
            report["generated_at"] = item.get("generated_at", "")
            report["account_id"] = item.get("account_id", "")
            report["project"] = item.get("project", "")
            report["environment"] = item.get("environment", "")
            report["scan_duration_seconds"] = item.get("scan_duration_seconds", 0)
            report["regions_scanned"] = item.get("regions_scanned", [])
            report["regions_with_resources"] = item.get("regions_with_resources", [])
            report["summary"] = _decimal_to_float(item.get("summary", {}))
            report["resource_type_counts"] = _decimal_to_float(item.get("resource_type_counts", {}))
            report["region_type_counts"] = _decimal_to_float(item.get("region_type_counts", {}))
            report["region_costs"] = _decimal_to_float(item.get("region_costs", {}))
            report["service_costs"] = _decimal_to_float(item.get("service_costs", {}))
            report["errors"] = item.get("errors", [])
            report["scan_complete"] = bool(item.get("scan_complete", True))
            report["last_successful_scan_at"] = item.get("last_successful_scan_at", "") or (
                report["generated_at"] if report["scan_complete"] else ""
            )

        elif sk.startswith("SECTION#"):
            section_data = _decimal_to_float(item.get("data", {}))
            report.update(section_data)

        elif sk.startswith("FINDING#"):
            parts = sk.split("#")  # FINDING#cat#0000
            if len(parts) >= 3:
                cat_name = parts[1]
                batch_idx = int(parts[2])
                findings = _decimal_to_float(item.get("findings", []))
                cat_meta = _decimal_to_float(item.get("category_meta", {}))
                if cat_name not in finding_batches:
                    finding_batches[cat_name] = []
                finding_batches[cat_name].append((batch_idx, findings, cat_meta))

        elif sk.startswith("RESOURCE#"):
            parts = sk.split("#")
            if len(parts) >= 2:
                batch_idx = int(parts[1])
                resources = _decimal_to_float(item.get("resources", []))
                resource_batches.append((batch_idx, resources))

    # Reassemble findings by category
    cat_key_map = {
        "drift": "drift_detection",
        "tag": "tag_compliance",
        "schedule": "schedule_compliance",
        "lifecycle": "lifecycle_compliance",
    }
    for cat_name, report_key in cat_key_map.items():
        batches = finding_batches.get(cat_name, [])
        batches.sort(key=lambda b: b[0])
        all_findings = []
        cat_meta = {}
        for _, findings, meta in batches:
            all_findings.extend(findings)
            if meta:
                cat_meta.update(meta)
        cat_meta["findings"] = all_findings
        report[report_key] = cat_meta

    # Ensure all finding categories exist
    for report_key in cat_key_map.values():
        if report_key not in report:
            report[report_key] = {"findings": []}

    # Reassemble discovered resources
    resource_batches.sort(key=lambda b: b[0])
    all_resources = []
    for _, resources in resource_batches:
        all_resources.extend(resources)
    report["discovered_resources"] = all_resources

    return report


def get_html_report(date=None):
    """Load the styled HTML compliance report from S3."""
    if LOCAL_DATA_DIR:
        path = os.path.join(LOCAL_DATA_DIR, "compliance-report.html")
        try:
            with open(path) as f:
                return f.read()
        except FileNotFoundError:
            return None
        except Exception as e:
            print(f"Error loading local HTML report {path}: {e}")
            return None

    if not date:
        date = get_latest_report_date()
    if not date:
        return None

    # S3 HTML reports use date-only prefix (YYYY-MM-DD), not full timestamps
    s3_date = date[:10] if len(date) > 10 else date
    key = f"reports/{s3_date}/compliance-report.html"
    try:
        response = s3.get_object(Bucket=REPORTS_BUCKET, Key=key)
        return response["Body"].read().decode("utf-8")
    except Exception as e:
        print(f"Error loading HTML report s3://{REPORTS_BUCKET}/{key}: {e}")
        return None
