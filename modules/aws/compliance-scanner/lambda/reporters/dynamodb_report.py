"""
DynamoDB Report Writer

Writes compliance report data to DynamoDB as batched items for fast,
consistent reads by the dashboard. Each scan gets a unique timestamp-based
partition — no overwrites, full history preserved.

Table schema (single-table design):
  PK (String) + SK (String)

  REPORT#{timestamp} / META          — summary, metadata, costs
  REPORT#{timestamp} / SECTION#...   — large sections (multi_state, protected, etc.)
  REPORT#{timestamp} / FINDING#cat#N — batches of 25 findings per category
  REPORT#{timestamp} / RESOURCE#N    — batches of 50 discovered resources
  INDEX / REPORTS                    — sorted list of all report keys (max 90)
  INDEX / LATEST                     — pointer to newest report
"""

import math
import time
from decimal import Decimal


def _float_to_decimal(obj):
    """Recursively convert floats to Decimal for DynamoDB compatibility."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return Decimal("0")
        return Decimal(str(obj))
    elif isinstance(obj, dict):
        return {k: _float_to_decimal(v) for k, v in obj.items()}
    elif isinstance(obj, list | tuple):
        return [_float_to_decimal(item) for item in obj]
    return obj


def _batch_items(items, batch_size):
    """Split a list into batches of batch_size."""
    for i in range(0, len(items), batch_size):
        yield items[i : i + batch_size]


def _ttl_timestamp(days=90):
    """Return a TTL epoch timestamp N days from now."""
    return int(time.time()) + (days * 86400)


def write_report_to_dynamodb(table_name, generated_at, results):
    """Write compliance report to DynamoDB as batched items.

    Args:
        table_name: DynamoDB table name
        generated_at: ISO 8601 timestamp string from the scanner
        results: Full results dict from the scanner
    """
    import boto3

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(table_name)

    # Truncate to seconds for the report key
    report_key = generated_at[:19]  # "2026-02-20T19:41:56"
    pk = f"REPORT#{report_key}"
    ttl = _ttl_timestamp(90)

    # 1. Write META item — summary + small metadata
    summary = results.get("summary", {})
    meta_item = _float_to_decimal(
        {
            "PK": pk,
            "SK": "META",
            "report_key": report_key,
            "generated_at": generated_at,
            "report_version": results.get("report_version", "1.0"),
            "account_id": results.get("account_id", ""),
            "project": results.get("project", ""),
            "environment": results.get("environment", ""),
            "scan_duration_seconds": results.get("scan_duration_seconds", 0),
            "regions_scanned": results.get("regions_scanned", []),
            "regions_with_resources": results.get("regions_with_resources", []),
            "summary": summary,
            "resource_type_counts": results.get("resource_type_counts", {}),
            "region_type_counts": results.get("region_type_counts", {}),
            "region_costs": results.get("region_costs", {}),
            "service_costs": results.get("service_costs", {}),
            "errors": results.get("errors", []),
            "scan_complete": bool(results.get("scan_complete", True)),
            "last_successful_scan_at": results.get("last_successful_scan_at", ""),
            "ttl_expires": ttl,
        }
    )
    table.put_item(Item=meta_item)
    print(f"  DynamoDB: wrote META item for {report_key}")

    # 2. Write SECTION items for large report sections
    sections = {
        "multi_state": {
            "multi_state_detection": results.get("multi_state_detection", {}),
            "other_iac_analysis": results.get("other_iac_analysis", {}),
        },
        "protected": {
            "protected_resources": results.get("protected_resources", {}),
        },
        "termination_events": {
            "termination_events": results.get("termination_events", []),
        },
        "iac_registry": {
            "iac_registry": results.get("iac_registry", {}),
        },
    }
    for section_name, section_data in sections.items():
        # Skip empty sections
        if not any(section_data.values()):
            continue
        table.put_item(
            Item={
                "PK": pk,
                "SK": f"SECTION#{section_name}",
                "data": _float_to_decimal(section_data),
                "ttl_expires": ttl,
            }
        )
    print("  DynamoDB: wrote SECTION items")

    # 3. Write FINDING batches (groups of 25 per category)
    finding_categories = {
        "drift": results.get("drift_detection", {}),
        "tag": results.get("tag_compliance", {}),
        "schedule": results.get("schedule_compliance", {}),
        "lifecycle": results.get("lifecycle_compliance", {}),
    }
    total_findings = 0
    for cat_name, cat_data in finding_categories.items():
        findings = cat_data.get("findings", [])
        if not findings:
            continue
        # Store category-level metadata (managed/unmanaged counts for drift)
        cat_meta = {k: v for k, v in cat_data.items() if k != "findings"}
        for batch_idx, batch in enumerate(_batch_items(findings, 25)):
            table.put_item(
                Item={
                    "PK": pk,
                    "SK": f"FINDING#{cat_name}#{batch_idx:04d}",
                    "findings": _float_to_decimal(batch),
                    "category_meta": _float_to_decimal(cat_meta) if batch_idx == 0 else {},
                    "ttl_expires": ttl,
                }
            )
            total_findings += len(batch)
    print(f"  DynamoDB: wrote {total_findings} findings in batches")

    # 4. Write RESOURCE batches (groups of 50)
    resources = results.get("discovered_resources", [])
    for batch_idx, batch in enumerate(_batch_items(resources, 50)):
        table.put_item(
            Item={
                "PK": pk,
                "SK": f"RESOURCE#{batch_idx:04d}",
                "resources": _float_to_decimal(batch),
                "ttl_expires": ttl,
            }
        )
    print(f"  DynamoDB: wrote {len(resources)} resources in {math.ceil(len(resources) / 50)} batches")

    # 5. Update INDEX/LATEST pointer
    table.put_item(
        Item={
            "PK": "INDEX",
            "SK": "LATEST",
            "report_key": report_key,
        }
    )

    # 6. Update INDEX/REPORTS list (prepend, trim to 90)
    _update_reports_index(table, report_key)

    print(f"  DynamoDB: report {report_key} written successfully")


def _update_reports_index(table, new_key):
    """Prepend a report key to the INDEX/REPORTS list, keeping max 90 entries."""
    try:
        response = table.get_item(Key={"PK": "INDEX", "SK": "REPORTS"})
        existing = response.get("Item", {}).get("report_keys", [])
    except Exception:
        existing = []

    # Prepend new key, deduplicate, trim
    keys = [new_key] + [k for k in existing if k != new_key]
    keys = keys[:90]

    table.put_item(
        Item={
            "PK": "INDEX",
            "SK": "REPORTS",
            "report_keys": keys,
        }
    )
