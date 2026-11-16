"""
Write operations — create/delete overrides, abort destroys, extend grace,
approve teardowns, trigger early destruction.
"""

import json
import os
from datetime import UTC, datetime, timedelta

from data import invalidate_hk_caches, invalidate_overrides_cache

LOCAL_DATA_DIR = os.environ.get("LOCAL_DATA_DIR", "")

if LOCAL_DATA_DIR:
    dynamodb = None
    lambda_client = None
    scheduler = None
else:
    import boto3

    dynamodb = boto3.resource("dynamodb")
    lambda_client = boto3.client("lambda")
    scheduler = boto3.client("scheduler")

OVERRIDES_TABLE = os.environ.get("OVERRIDES_TABLE", "")
HK_STATE_TABLE = os.environ.get("HK_STATE_TABLE", "")
SCANNER_FUNCTION_NAME = os.environ.get("SCANNER_FUNCTION_NAME", "")
HK_FUNCTION_NAME = os.environ.get("HK_FUNCTION_NAME", "")
HK_EXECUTOR_FUNCTION_NAME = os.environ.get("HK_EXECUTOR_FUNCTION_NAME", "")
HK_EXECUTOR_ARMED = os.environ.get("HK_EXECUTOR_ARMED", "false").lower() == "true"


# Longest single grace-period extension an admin can grant, in hours.
MAX_EXTENSION_HOURS = 24 * 30


def _bounded_hours(value):
    """Clamp a requested extension to 1..MAX_EXTENSION_HOURS (default 24)."""
    try:
        hours = int(value)
    except (TypeError, ValueError):
        return 24
    return max(1, min(hours, MAX_EXTENSION_HOURS))


def _update_existing(
    table, resource_arn, update_expression, values, condition="attribute_exists(resource_arn)", names=None
):
    """Update a hunterkiller record only when the condition holds.

    Returns False when the record does not exist or the condition fails, so a
    dashboard action can change a record but never create one.
    """
    kwargs = {
        "Key": {"resource_arn": resource_arn},
        "UpdateExpression": update_expression,
        "ExpressionAttributeValues": values,
        "ConditionExpression": condition,
    }
    if names:
        kwargs["ExpressionAttributeNames"] = names
    try:
        table.update_item(**kwargs)
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return False
    return True


def create_override(
    resource_arn, override_type, reason, created_by, resource_type="", resource_name="", region="", expires_days=None
):
    """Mark a resource as false positive or exempt."""
    table = dynamodb.Table(OVERRIDES_TABLE)
    now = datetime.now(UTC)

    item = {
        "resource_arn": resource_arn,
        "override_type": override_type,
        "reason": reason,
        "created_by": created_by,
        "created_at": now.isoformat(),
        "resource_type": resource_type,
        "resource_name": resource_name,
        "region": region,
    }

    if expires_days:
        expires_at = now + timedelta(days=int(expires_days))
        item["expires_at"] = expires_at.isoformat()
        item["ttl_expires"] = int(expires_at.timestamp())
    else:
        # Default TTL: 365 days (overrides aren't permanent without review)
        item["ttl_expires"] = int((now + timedelta(days=365)).timestamp())

    table.put_item(Item=item)
    invalidate_overrides_cache()
    return item


def delete_override(resource_arn):
    """Remove a false positive/exemption override."""
    table = dynamodb.Table(OVERRIDES_TABLE)
    table.delete_item(Key={"resource_arn": resource_arn})
    invalidate_overrides_cache()


def abort_destroy(resource_arn, aborted_by):
    """
    Abort a pending destroy (escape hatch).
    Updates the hunterkiller state record to 'aborted' status.
    Also attempts to cancel the EventBridge schedule.
    """
    table = dynamodb.Table(HK_STATE_TABLE)
    now = datetime.now(UTC).isoformat()

    # Update status in DynamoDB. An abort also withdraws any approval, so the
    # resource cannot be torn down later on the strength of the old one.
    if not _update_existing(
        table,
        resource_arn,
        "SET #s = :status, updated_at = :now, aborted_by = :by, "
        "approval_status = :pending, approved_by = :empty, approved_at = :empty",
        {":status": "aborted", ":now": now, ":by": aborted_by, ":pending": "pending", ":empty": ""},
        names={"#s": "status"},
    ):
        return False

    # Try to cancel the EventBridge schedule (best-effort)
    _cancel_enforcement_schedule(resource_arn)

    invalidate_hk_caches()
    return True


def extend_grace_period(resource_arn, additional_hours, extended_by):
    """Extend the grace period for a warned resource."""
    table = dynamodb.Table(HK_STATE_TABLE)
    now = datetime.now(UTC)

    # Get current record to read existing deadline
    response = table.get_item(Key={"resource_arn": resource_arn})
    record = response.get("Item")
    if not record:
        return False

    current_deadline = record.get("grace_deadline", now.isoformat())
    try:
        deadline_dt = datetime.fromisoformat(current_deadline)
    except (ValueError, TypeError):
        deadline_dt = now

    new_deadline = deadline_dt + timedelta(hours=_bounded_hours(additional_hours))

    table.update_item(
        Key={"resource_arn": resource_arn},
        UpdateExpression="SET grace_deadline = :deadline, updated_at = :now, extended_by = :by",
        ExpressionAttributeValues={
            ":deadline": new_deadline.isoformat(),
            ":now": now.isoformat(),
            ":by": extended_by,
        },
    )

    invalidate_hk_caches()
    return True


def approve_teardown(resource_arn, approved_by):
    """
    Approve a resource for teardown (admin action).
    Sets approval_status=approved, approved_by, approved_at in hunterkiller DynamoDB.
    The executor checks these fields before proceeding with destruction.
    """
    table = dynamodb.Table(HK_STATE_TABLE)
    now = datetime.now(UTC).isoformat()

    # Only a resource the notifier warned about, still in that state, can be
    # approved. An approval never creates a record.
    approved = _update_existing(
        table,
        resource_arn,
        "SET approval_status = :status, approved_by = :by, approved_at = :at, updated_at = :now",
        {":status": "approved", ":by": approved_by, ":at": now, ":now": now, ":warned": "warned"},
        condition="attribute_exists(resource_arn) AND #s = :warned",
        names={"#s": "status"},
    )

    invalidate_hk_caches()
    return approved


def revoke_approval(resource_arn, revoked_by):
    """
    Revoke a previously approved teardown (reset to pending).
    """
    table = dynamodb.Table(HK_STATE_TABLE)
    now = datetime.now(UTC).isoformat()

    revoked = _update_existing(
        table,
        resource_arn,
        "SET approval_status = :status, approved_by = :empty, approved_at = :empty, updated_at = :now",
        {":status": "pending", ":empty": "", ":now": now},
    )

    invalidate_hk_caches()
    return revoked


def trigger_early_destroy(resource_arn, triggered_by):
    """
    Trigger early destruction by invoking the hunterkiller executor Lambda
    with mode=enforce_single for a specific resource.
    Requires executor_armed=true and approval_status=approved.
    """
    # Must use executor Lambda, not notifier
    function_name = HK_EXECUTOR_FUNCTION_NAME if HK_EXECUTOR_ARMED else ""
    if not function_name:
        return {"error": "Executor not armed — set executor_armed=true in Terraform"}

    payload = {
        "mode": "enforce_single",
        "resource_arn": resource_arn,
        "triggered_by": triggered_by,
    }

    try:
        response = lambda_client.invoke(
            FunctionName=function_name,
            InvocationType="Event",  # Async
            Payload=json.dumps(payload),
        )
    except Exception as e:
        error_type = type(e).__name__
        print(f"ERROR: Failed to invoke executor {function_name}: {error_type}: {e}")
        return {"error": f"Failed to invoke executor: {error_type}: {e}"}

    return {
        "status": "invoked",
        "status_code": response.get("StatusCode"),
    }


def trigger_scan(triggered_by):
    """Invoke the compliance scanner Lambda (async)."""
    if not SCANNER_FUNCTION_NAME:
        return {"error": "Scanner function name not configured"}

    payload = {"source": "dashboard", "triggered_by": triggered_by}

    try:
        response = lambda_client.invoke(
            FunctionName=SCANNER_FUNCTION_NAME,
            InvocationType="Event",
            Payload=json.dumps(payload),
        )
    except Exception as e:
        error_type = type(e).__name__
        print(f"ERROR: Failed to invoke scanner {SCANNER_FUNCTION_NAME}: {error_type}: {e}")
        return {"error": f"Failed to invoke scanner: {error_type}: {e}"}

    return {"status": "invoked", "status_code": response.get("StatusCode")}


def trigger_sprawl_test(triggered_by):
    """Invoke the HK sprawl test Lambda (async) — creates non-compliant test resources."""
    # Derive function name from convention (no env var needed — avoids 5120 byte limit)
    _project = os.environ.get("PROJECT", "")
    _environment = os.environ.get("ENVIRONMENT", "")
    function_name = f"{_project}-{_environment}-hk-sprawl-test" if _project and _environment else ""

    if not function_name:
        return {"error": "Sprawl test function name not configured (PROJECT/ENVIRONMENT missing)"}

    payload = {"source": "dashboard", "triggered_by": triggered_by}

    try:
        response = lambda_client.invoke(
            FunctionName=function_name,
            InvocationType="Event",
            Payload=json.dumps(payload),
        )
    except Exception as e:
        error_type = type(e).__name__
        print(f"ERROR: Failed to invoke sprawl test {function_name}: {error_type}: {e}")
        return {"error": f"Failed to invoke sprawl test: {error_type}: {e}"}

    return {"status": "invoked", "status_code": response.get("StatusCode")}


def trigger_enforcer(triggered_by, min_severity="HIGH"):
    """Invoke the hunterkiller notifier Lambda with the given severity level (async)."""
    if not HK_FUNCTION_NAME:
        return {"error": "Hunterkiller function name not configured"}

    scan_date = datetime.now(UTC).strftime("%Y-%m-%d")
    payload = {
        "mode": "warn",
        "scan_date": scan_date,
        "min_severity": min_severity,
        "triggered_by": triggered_by,
    }

    try:
        response = lambda_client.invoke(
            FunctionName=HK_FUNCTION_NAME,
            InvocationType="Event",
            Payload=json.dumps(payload),
        )
    except Exception as e:
        error_type = type(e).__name__
        print(f"ERROR: Failed to invoke enforcer {HK_FUNCTION_NAME}: {error_type}: {e}")
        return {"error": f"Failed to invoke enforcer: {error_type}: {e}"}

    return {"status": "invoked", "status_code": response.get("StatusCode")}


def abort_all(aborted_by):
    """Abort all warned (non-aborted) resources in bulk."""
    table = dynamodb.Table(HK_STATE_TABLE)
    now = datetime.now(UTC).isoformat()

    # Scan for all warned items
    response = table.scan()
    items = response.get("Items", [])
    while "LastEvaluatedKey" in response:
        response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        items.extend(response.get("Items", []))

    count = 0
    for item in items:
        if item.get("status") == "warned":
            arn = item["resource_arn"]
            table.update_item(
                Key={"resource_arn": arn},
                UpdateExpression="SET #s = :status, updated_at = :now, aborted_by = :by, "
                "approval_status = :pending, approved_by = :empty, approved_at = :empty",
                ExpressionAttributeValues={
                    ":status": "aborted",
                    ":now": now,
                    ":by": aborted_by,
                    ":pending": "pending",
                    ":empty": "",
                },
                ExpressionAttributeNames={"#s": "status"},
            )
            _cancel_enforcement_schedule(arn)
            count += 1

    invalidate_hk_caches()
    return count


def extend_all(additional_hours, extended_by):
    """Extend grace period for all warned resources in bulk."""
    table = dynamodb.Table(HK_STATE_TABLE)
    now = datetime.now(UTC)

    response = table.scan()
    items = response.get("Items", [])
    while "LastEvaluatedKey" in response:
        response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        items.extend(response.get("Items", []))

    count = 0
    for item in items:
        if item.get("status") == "warned":
            arn = item["resource_arn"]
            current_deadline = item.get("grace_deadline", now.isoformat())
            try:
                deadline_dt = datetime.fromisoformat(current_deadline)
            except (ValueError, TypeError):
                deadline_dt = now
            new_deadline = deadline_dt + timedelta(hours=_bounded_hours(additional_hours))

            table.update_item(
                Key={"resource_arn": arn},
                UpdateExpression="SET grace_deadline = :deadline, updated_at = :now, extended_by = :by",
                ExpressionAttributeValues={
                    ":deadline": new_deadline.isoformat(),
                    ":now": now.isoformat(),
                    ":by": extended_by,
                },
            )
            count += 1

    invalidate_hk_caches()
    return count


def approve_all(approved_by):
    """Approve all warned resources with pending approval in bulk."""
    table = dynamodb.Table(HK_STATE_TABLE)
    now = datetime.now(UTC).isoformat()

    response = table.scan()
    items = response.get("Items", [])
    while "LastEvaluatedKey" in response:
        response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        items.extend(response.get("Items", []))

    count = 0
    for item in items:
        if item.get("status") == "warned" and item.get("approval_status") != "approved":
            arn = item["resource_arn"]
            table.update_item(
                Key={"resource_arn": arn},
                UpdateExpression="SET approval_status = :status, approved_by = :by, approved_at = :at, updated_at = :now",
                ExpressionAttributeValues={
                    ":status": "approved",
                    ":by": approved_by,
                    ":at": now,
                    ":now": now,
                },
            )
            count += 1

    invalidate_hk_caches()
    return count


def _cancel_enforcement_schedule(resource_arn):
    """Best-effort cancellation of the EventBridge enforcement schedule."""
    project = os.environ.get("PROJECT", "")
    environment = os.environ.get("ENVIRONMENT", "")
    prefix = f"{project}-{environment}-hk-enforce-"

    try:
        # List schedules matching the prefix
        response = scheduler.list_schedules(
            NamePrefix=prefix,
            MaxResults=100,
        )
        for schedule in response.get("Schedules", []):
            schedule_name = schedule["Name"]
            # Try to get the schedule and check if it targets this resource
            try:
                detail = scheduler.get_schedule(Name=schedule_name)
                target_input = detail.get("Target", {}).get("Input", "")
                if resource_arn in target_input:
                    scheduler.delete_schedule(Name=schedule_name)
                    print(f"Cancelled enforcement schedule: {schedule_name}")
            except Exception:
                pass
    except Exception as e:
        print(f"Warning: could not cancel enforcement schedule: {e}")
