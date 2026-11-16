"""Stack Reaper Reconciler — Scan IaC registry and manage teardown schedules.

Runs periodically (e.g. every 6 hours). For each IaC registry entry:
  1. Read the entry's lifecycle and expires_at; only a temporary or
     experiment entry with an expiry is scheduled
  2. Read the live tags of the stack's resources, which can only block or
     delay the teardown (expiry_decision.py)
  3. Create/update/delete EventBridge Scheduler entries as needed
  4. Retry any previously failed dispatches
  5. Clean up orphan schedules for stacks no longer in registry
"""

import json
import os
from datetime import UTC, datetime, timedelta

import boto3
from compliance_contract import parse_expires_at
from compliance_schema import repo_slug, require_env
from expiry_decision import decide, resources_of_repository
from iac_registry import load_iac_registry

# Environment
PROJECT = require_env("PROJECT")
ENVIRONMENT = os.environ.get("ENVIRONMENT", "dev")
STATE_TABLE = os.environ.get("STATE_TABLE", "")
SCHEDULE_GROUP = os.environ.get("SCHEDULE_GROUP", "")
# The repository that deploys this module; its own registry entry is never reaped.
PLATFORM_REPO_SLUG = repo_slug(require_env("REPOSITORY_URL"))
SCHEDULER_ROLE_ARN = os.environ.get("SCHEDULER_ROLE_ARN", "")
REAPER_FUNCTION_ARN = os.environ.get("REAPER_FUNCTION_ARN", "")
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"
STATE_TTL_DAYS = int(os.environ.get("STATE_TTL_DAYS", "90"))
SCHEDULE_PREFIX = "reaper-"


def handler(event, context):
    """Reconciler entry point."""
    print(f"Stack Reaper Reconciler — project={PROJECT} env={ENVIRONMENT} dry_run={DRY_RUN}")

    if not REAPER_FUNCTION_ARN:
        print(
            "WARNING: REAPER_FUNCTION_ARN not set — reaper not armed. "
            "Will scan and record state but cannot create schedules."
        )

    registry = load_iac_registry(PROJECT, ENVIRONMENT)
    if not registry:
        print("No IaC registry entries found. Nothing to do.")
        return {"scheduled": 0, "skipped": 0, "warnings": 0, "retried": 0, "orphans_cleaned": 0}

    tagging = boto3.client("resourcegroupstaggingapi")
    scheduler = boto3.client("scheduler")
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(STATE_TABLE)

    now = datetime.now(UTC)
    stats = {"scheduled": 0, "skipped": 0, "warnings": 0, "retried": 0, "orphans_cleaned": 0}
    active_schedule_names = set()

    for entry in registry:
        slug = entry.get("repo_slug", "unknown")
        repo_url = entry.get("repo_url", "")
        iac_type = entry.get("iac_type", "unknown")

        print(f"\n--- Processing: {slug} (iac_type={iac_type}) ---")

        # Skip the platform repository itself — it's persistent infrastructure
        if slug == PLATFORM_REPO_SLUG:
            print(f"  Skipping {slug} (self)")
            stats["skipped"] += 1
            continue

        # The registry entry decides whether the stack expires; live tags can
        # only block or delay that (expiry_decision.py).
        try:
            resources = resources_of_repository(tagging, repo_url)
        except Exception as e:
            print(f"  ERROR querying tags for {slug}: {e}")
            stats["warnings"] += 1
            continue
        decision = decide(entry, resources)
        status = decision["status"]

        if status != "expires":
            print(f"  {status}: {decision['reason']}")
            state = {"status": status, "iac_type": iac_type, "repo_url": repo_url, "message": decision["reason"]}
            if status == "blocked":
                state["problems"] = decision["problems"][:50]
                stats["warnings"] += 1
            else:
                stats["skipped"] += 1
            _update_state(table, slug, state)
            _delete_schedule_if_exists(scheduler, slug)
            continue

        expires_at = decision["expires_at"]
        print(f"  ExpiresAt: {expires_at.isoformat()}")

        # Determine schedule time
        schedule_name = f"{SCHEDULE_PREFIX}{slug}"
        active_schedule_names.add(schedule_name)

        if expires_at <= now:
            # Already expired — schedule for 5 minutes from now (or immediately re-schedule)
            schedule_at = now + timedelta(minutes=5)
            print(f"  Already expired! Scheduling reaper for {schedule_at.isoformat()}")
        else:
            schedule_at = expires_at
            print(f"  Scheduling reaper for {schedule_at.isoformat()}")

        # Create or update the schedule
        if REAPER_FUNCTION_ARN:
            _upsert_schedule(scheduler, schedule_name, schedule_at, slug, expires_at)
            stats["scheduled"] += 1
        else:
            print(f"  SKIP: Would schedule {slug} at {schedule_at} but reaper not armed")
            stats["skipped"] += 1

        _update_state(
            table,
            slug,
            {
                "status": "scheduled",
                "iac_type": iac_type,
                "repo_url": repo_url,
                "expires_at": expires_at.isoformat(),
                "scheduled_at": schedule_at.isoformat(),
            },
        )

    # Retry any dispatch_failed entries
    failed = _scan_failed_entries(table)
    for entry in failed:
        slug = entry["stack_id"]
        print(f"\n  Retrying failed dispatch for {slug}")
        schedule_name = f"{SCHEDULE_PREFIX}{slug}"
        active_schedule_names.add(schedule_name)

        if REAPER_FUNCTION_ARN:
            schedule_at = now + timedelta(minutes=5)
            _upsert_schedule(
                scheduler,
                schedule_name,
                schedule_at,
                slug,
                parse_expires_at(entry.get("expires_at")) or now,
            )
            _update_state(table, slug, {"status": "retry_scheduled"})
            stats["retried"] += 1

    # Clean up orphan schedules (schedules for stacks no longer in registry)
    orphans = _find_orphan_schedules(scheduler, active_schedule_names)
    for orphan_name in orphans:
        print(f"  Cleaning orphan schedule: {orphan_name}")
        _delete_schedule(scheduler, orphan_name)
        stats["orphans_cleaned"] += 1

    duration = round((datetime.now(UTC) - now).total_seconds(), 1)
    print(f"\nReconciler complete: {json.dumps(stats)}")
    _write_job_status(stats, duration)
    return stats


JOB_STATUS_SSM_PREFIX = os.environ.get("JOB_STATUS_SSM_PREFIX", "")


def _write_job_status(stats, duration):
    """Write execution summary to SSM for dashboard visibility."""
    if not JOB_STATUS_SSM_PREFIX:
        return
    try:
        ssm = boto3.client("ssm")
        parts = []
        if stats.get("scheduled"):
            parts.append(f"{stats['scheduled']} stacks tracked")
        if stats.get("retried"):
            parts.append(f"{stats['retried']} retried")
        if stats.get("warnings"):
            parts.append(f"{stats['warnings']} lifecycle warnings")
        if not parts:
            parts.append("no stacks pending teardown")
        if stats.get("orphans_cleaned"):
            parts.append(f"{stats['orphans_cleaned']} stale schedule(s) removed")
        ssm.put_parameter(
            Name=f"{JOB_STATUS_SSM_PREFIX}/stack-reaper",
            Value=json.dumps(
                {
                    "last_run": datetime.now(UTC).isoformat(),
                    "duration_seconds": duration,
                    "status": "ok",
                    "summary": ", ".join(parts),
                }
            ),
            Type="String",
            Overwrite=True,
        )
    except Exception as e:
        print(f"Warning: failed to write job status to SSM: {e}")


def _upsert_schedule(scheduler, schedule_name, schedule_at, stack_id, expires_at):
    """Create or update an EventBridge Scheduler one-time schedule."""
    # Format as EventBridge at() expression
    schedule_expr = f"at({schedule_at.strftime('%Y-%m-%dT%H:%M:%S')})"

    payload = json.dumps(
        {
            "stack_id": stack_id,
            "scheduled_expires_at": expires_at.isoformat(),
        }
    )

    try:
        scheduler.update_schedule(
            Name=schedule_name,
            GroupName=SCHEDULE_GROUP,
            ScheduleExpression=schedule_expr,
            ScheduleExpressionTimezone="UTC",
            FlexibleTimeWindow={"Mode": "OFF"},
            Target={
                "Arn": REAPER_FUNCTION_ARN,
                "RoleArn": SCHEDULER_ROLE_ARN,
                "Input": payload,
            },
            State="ENABLED",
            ActionAfterCompletion="DELETE",
        )
        print(f"  Updated schedule {schedule_name} → {schedule_expr}")
    except scheduler.exceptions.ResourceNotFoundException:
        scheduler.create_schedule(
            Name=schedule_name,
            GroupName=SCHEDULE_GROUP,
            ScheduleExpression=schedule_expr,
            ScheduleExpressionTimezone="UTC",
            FlexibleTimeWindow={"Mode": "OFF"},
            Target={
                "Arn": REAPER_FUNCTION_ARN,
                "RoleArn": SCHEDULER_ROLE_ARN,
                "Input": payload,
            },
            State="ENABLED",
            ActionAfterCompletion="DELETE",
        )
        print(f"  Created schedule {schedule_name} → {schedule_expr}")
    except Exception as e:
        print(f"  ERROR upserting schedule {schedule_name}: {e}")


def _delete_schedule_if_exists(scheduler, slug):
    """Delete a schedule if it exists (no-op if missing)."""
    _delete_schedule(scheduler, f"{SCHEDULE_PREFIX}{slug}")


def _delete_schedule(scheduler, schedule_name):
    """Delete an EventBridge schedule by name."""
    try:
        scheduler.delete_schedule(Name=schedule_name, GroupName=SCHEDULE_GROUP)
        print(f"  Deleted schedule {schedule_name}")
    except scheduler.exceptions.ResourceNotFoundException:
        pass
    except Exception as e:
        print(f"  ERROR deleting schedule {schedule_name}: {e}")


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


def _scan_failed_entries(table):
    """Find DynamoDB entries with status=dispatch_failed."""
    try:
        resp = table.scan(
            FilterExpression="attribute_exists(#s) AND #s = :failed",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":failed": "dispatch_failed"},
        )
        return resp.get("Items", [])
    except Exception as e:
        print(f"  ERROR scanning for failed entries: {e}")
        return []


def _find_orphan_schedules(scheduler, active_names):
    """List all schedules in our group and return names not in active_names."""
    orphans = []
    try:
        paginator = scheduler.get_paginator("list_schedules")
        for page in paginator.paginate(GroupName=SCHEDULE_GROUP):
            for sched in page.get("Schedules", []):
                name = sched["Name"]
                if name.startswith(SCHEDULE_PREFIX) and name not in active_names:
                    orphans.append(name)
    except Exception as e:
        print(f"  ERROR listing schedules for orphan cleanup: {e}")
    return orphans
