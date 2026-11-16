"""Stack Reaper — JIT precheck + teardown dispatch.

Invoked by EventBridge Scheduler at the scheduled ExpiresAt time.
  1. Load registry entry for the target stack
  2. JIT precheck: the entry's lifecycle and expiry, checked against the
     live tags of the stack's resources (expiry_decision.py)
  3. If expired → dispatch teardown via GitHub workflow_dispatch
  4. Record result in DynamoDB audit trail
  5. Send admin notification email
"""

import os
import time
from datetime import UTC, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import boto3
from compliance_schema import require_env
from expiry_decision import decide, resources_of_repository
from github_dispatch import dispatch_teardown
from iac_registry import load_iac_registry

# Environment
PROJECT = require_env("PROJECT")
ENVIRONMENT = os.environ.get("ENVIRONMENT", "dev")
STATE_TABLE = os.environ.get("STATE_TABLE", "")
SCHEDULE_GROUP = os.environ.get("SCHEDULE_GROUP", "")
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"
GITHUB_APP_SECRET_ARN = os.environ.get("GITHUB_APP_SECRET_ARN", "")
SES_SENDER_EMAIL = os.environ.get("SES_SENDER_EMAIL", "")
SES_ADMIN_EMAILS = [e.strip() for e in os.environ.get("SES_ADMIN_EMAILS", "").split(",") if e.strip()]
STATE_TTL_DAYS = int(os.environ.get("STATE_TTL_DAYS", "90"))

# Retry config
MAX_DISPATCH_RETRIES = 3
RETRY_BACKOFF_BASE = 2  # seconds


def handler(event, context):
    """Reaper entry point. Event from EventBridge Scheduler:
    {"stack_id": "example-repo", "scheduled_expires_at": "2026-03-01T..."}
    """
    stack_id = event.get("stack_id", "")
    scheduled_expires_at = event.get("scheduled_expires_at", "")

    print(f"Stack Reaper firing — stack_id={stack_id} scheduled_expires_at={scheduled_expires_at} dry_run={DRY_RUN}")

    if not stack_id:
        print("ERROR: No stack_id in event")
        return {"status": "error", "message": "No stack_id in event"}

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(STATE_TABLE)
    tagging = boto3.client("resourcegroupstaggingapi")

    # Step 1: Load registry entry
    registry = load_iac_registry(PROJECT, ENVIRONMENT)
    entry = next((e for e in registry if e.get("repo_slug") == stack_id), None)

    if not entry:
        msg = f"Stack {stack_id} not found in IaC registry — may have been deregistered"
        print(msg)
        _update_state(table, stack_id, {"status": "not_found", "message": msg})
        return {"status": "not_found", "message": msg}

    repo_url = entry.get("repo_url", "")
    iac_type = entry.get("iac_type", "unknown")

    # Step 2: JIT precheck — re-read current ExpiresAt from live tags
    print(f"JIT precheck: querying live tags for {repo_url}")
    precheck_result = _jit_precheck(tagging, entry, stack_id)

    if precheck_result["action"] == "skip":
        msg = precheck_result["message"]
        print(f"PRECHECK SKIP: {msg}")
        _update_state(
            table,
            stack_id,
            {
                "status": "precheck_skipped",
                "message": msg,
                "last_precheck": datetime.now(UTC).isoformat(),
            },
        )
        return {"status": "skipped", "message": msg}

    current_expires_at = precheck_result.get("expires_at")
    print(f"PRECHECK PASS: ExpiresAt={current_expires_at} — proceeding with teardown")

    # Step 3: Dry run check
    if DRY_RUN:
        msg = f"DRY RUN: Would dispatch teardown for {stack_id} (repo={repo_url}, iac_type={iac_type})"
        print(msg)
        _update_state(
            table,
            stack_id,
            {
                "status": "dry_run",
                "message": msg,
                "last_precheck": datetime.now(UTC).isoformat(),
                "expires_at": current_expires_at,
            },
        )
        _send_notification(stack_id, "dry_run", msg, repo_url, iac_type)
        return {"status": "dry_run", "message": msg}

    # Step 4: Dispatch teardown via GitHub workflow_dispatch
    result = _dispatch_with_retry(repo_url, stack_id)

    if result["success"]:
        msg = result["message"]
        print(f"DISPATCH SUCCESS: {msg}")
        _update_state(
            table,
            stack_id,
            {
                "status": "dispatched",
                "message": msg,
                "dispatched_at": datetime.now(UTC).isoformat(),
                "last_precheck": datetime.now(UTC).isoformat(),
                "expires_at": current_expires_at,
            },
        )
        _send_notification(stack_id, "dispatched", msg, repo_url, iac_type)

        # Delete the schedule (one-shot, fire-and-forget — ActionAfterCompletion
        # should handle this, but belt-and-suspenders)
        _delete_schedule(stack_id)

        return {"status": "dispatched", "message": msg}
    else:
        msg = result["message"]
        print(f"DISPATCH FAILED: {msg}")
        _update_state(
            table,
            stack_id,
            {
                "status": "dispatch_failed",
                "message": msg,
                "last_precheck": datetime.now(UTC).isoformat(),
                "expires_at": current_expires_at,
            },
        )
        _send_notification(stack_id, "dispatch_failed", msg, repo_url, iac_type)
        return {"status": "dispatch_failed", "message": msg}


def _jit_precheck(tagging, entry, stack_id):
    """Decide again, from the registry entry and live tags, just before dispatch.

    Proceeds only when the entry declares an expiring lifecycle, every live
    resource of the stack agrees, and the resulting expiry has passed.
    """
    now = datetime.now(UTC)
    try:
        decision = decide(entry, resources_of_repository(tagging, entry.get("repo_url", "")))
    except Exception as e:
        # On precheck error, err on the side of caution — skip
        return {"action": "skip", "message": f"Precheck error for {stack_id}: {e}. Skipping (safe default)."}

    if decision["status"] != "expires":
        return {"action": "skip", "message": f"{stack_id} is {decision['status']}: {decision['reason']}"}

    expires_at = decision["expires_at"]
    if expires_at > now:
        return {
            "action": "skip",
            "message": f"{stack_id} expires at {expires_at.isoformat()}, after now={now.isoformat()}.",
        }
    return {
        "action": "proceed",
        "expires_at": expires_at.isoformat(),
        "message": f"ExpiresAt={expires_at.isoformat()} is past now. Proceeding.",
    }


def _dispatch_with_retry(repo_url, stack_id):
    """Dispatch teardown with retry logic."""
    last_result = None

    for attempt in range(1, MAX_DISPATCH_RETRIES + 1):
        print(f"  Dispatch attempt {attempt}/{MAX_DISPATCH_RETRIES} for {stack_id}")

        result = dispatch_teardown(
            repo_url=repo_url,
            workflow_file="teardown.yml",
            github_app_secret_arn=GITHUB_APP_SECRET_ARN,
            triggered_by="stack-reaper",
        )
        last_result = result

        if result["success"]:
            return result

        # Don't retry on 4xx client errors (except 502/503/504)
        status = result.get("status_code", 0)
        if 400 <= status < 500 and status not in (429,):
            print(f"  Client error {status} — not retrying")
            return result

        if attempt < MAX_DISPATCH_RETRIES:
            wait = RETRY_BACKOFF_BASE**attempt
            print(f"  Retrying in {wait}s...")
            time.sleep(wait)

    return last_result


def _delete_schedule(stack_id):
    """Delete the one-shot schedule after successful dispatch."""
    schedule_name = f"reaper-{stack_id}"
    try:
        scheduler = boto3.client("scheduler")
        scheduler.delete_schedule(Name=schedule_name, GroupName=SCHEDULE_GROUP)
        print(f"  Deleted schedule {schedule_name}")
    except Exception as e:
        # Non-fatal — ActionAfterCompletion=DELETE should handle this
        print(f"  Schedule cleanup note: {e}")


def _update_state(table, stack_id, updates):
    """Write/update DynamoDB state entry."""
    now = datetime.now(UTC)
    ttl = int((now + timedelta(days=STATE_TTL_DAYS)).timestamp())

    item = {
        "stack_id": stack_id,
        "updated_at": now.isoformat(),
        "ttl_epoch": ttl,
    }
    item.update(updates)

    try:
        table.put_item(Item=item)
    except Exception as e:
        print(f"  ERROR updating state for {stack_id}: {e}")


def _send_notification(stack_id, status, message, repo_url, iac_type):
    """Send admin email notification about reaper action."""
    if not SES_SENDER_EMAIL or not SES_ADMIN_EMAILS:
        return

    subject_prefix = {
        "dry_run": "[DRY RUN]",
        "dispatched": "[TEARDOWN]",
        "dispatch_failed": "[FAILED]",
    }.get(status, "[REAPER]")

    # One line only: a stack id with CR/LF must not add a mail header.
    subject = " ".join(f"{subject_prefix} Stack Reaper: {stack_id}".split())

    body = f"""Stack Reaper — {status.upper()}

Stack:      {stack_id}
Repository: {repo_url}
IaC Type:   {iac_type}
Status:     {status}
Time:       {datetime.now(UTC).isoformat()}

{message}

---
Stack Reaper ({PROJECT}/{ENVIRONMENT})
"""

    try:
        ses = boto3.client("ses")
        msg = MIMEMultipart()
        msg["Subject"] = subject
        msg["From"] = SES_SENDER_EMAIL
        msg["To"] = ", ".join(SES_ADMIN_EMAILS)
        msg.attach(MIMEText(body, "plain"))

        ses.send_raw_email(
            Source=SES_SENDER_EMAIL,
            Destinations=SES_ADMIN_EMAILS,
            RawMessage={"Data": msg.as_string()},
        )
        print(f"  Notification sent to {SES_ADMIN_EMAILS}")
    except Exception as e:
        print(f"  WARNING: Failed to send notification: {e}")
