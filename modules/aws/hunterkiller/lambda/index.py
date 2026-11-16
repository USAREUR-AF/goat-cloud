"""
Hunterkiller Notifier — Compliance Warning System

This is the notifier Lambda entry point. It handles warn mode only:
  - Reads compliance findings, sends 24-hr warning emails, writes DynamoDB state,
    schedules a deferred enforcement invocation.

For enforce/enforce_single modes:
  - When executor_armed=true: Returns instructions to invoke the executor Lambda.
  - When executor_armed=false: Returns "executor not armed" (no destructive Lambda exists).

Safety:
  - This Lambda has NO destructive permissions whatsoever.
  - DRY_RUN=true by default — included in emails for transparency.
  - Production-protected resources (Environment=prod, Protected=true) are exempt.
"""

import json
import os
import time
import traceback
from datetime import UTC, datetime

import boto3
from compliance_schema import INACTIVE_STATES, TAG_KEYS
from compliance_schema import PROTECTED_ENVIRONMENTS as _CONTRACT_PROTECTED_ENVIRONMENTS
from contact_resolver import resolve_contact
from notifier import send_warning_emails
from state_manager import StateManager

# Environment variables
PROJECT = os.environ.get("PROJECT", "unknown")
ENVIRONMENT = os.environ.get("ENVIRONMENT", "unknown")
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"
GRACE_PERIOD_HOURS = int(os.environ.get("GRACE_PERIOD_HOURS", "24"))
STATE_TABLE = os.environ.get("STATE_TABLE", "")
REPORTS_BUCKET = os.environ.get("REPORTS_BUCKET", "")
SES_SENDER_EMAIL = os.environ.get("SES_SENDER_EMAIL", "")
SES_ADMIN_EMAILS = [e.strip() for e in os.environ.get("SES_ADMIN_EMAILS", "").split(",") if e.strip()]
# An unset or empty variable falls back to the generated contract.
PROTECTED_ENVIRONMENTS = [
    e.strip() for e in os.environ.get("PROTECTED_ENVIRONMENTS", "").split(",") if e.strip()
] or list(_CONTRACT_PROTECTED_ENVIRONMENTS)
PROTECTED_TAG_KEY = os.environ.get("PROTECTED_TAG_KEY") or TAG_KEYS["protected"]
CONTACT_TAG_KEYS = [k.strip() for k in os.environ.get("CONTACT_TAG_KEYS", "").split(",") if k.strip()]
STATE_TTL_DAYS = int(os.environ.get("STATE_TTL_DAYS", "30"))
LAMBDA_FUNCTION_NAME = os.environ.get("LAMBDA_FUNCTION_NAME", "")
EXECUTOR_FUNCTION_NAME = os.environ.get("EXECUTOR_FUNCTION_NAME", "")
# Role the enforcement schedule runs as: it may invoke the executor and nothing else.
SCHEDULER_ROLE_ARN = os.environ.get("SCHEDULER_ROLE_ARN", "")
EXECUTOR_ARMED = os.environ.get("EXECUTOR_ARMED", "false").lower() == "true"
AUTO_APPROVE_EXPIRED = os.environ.get("AUTO_APPROVE_EXPIRED", "false").lower() == "true"
PROTECTED_RESOURCES_SSM = os.environ.get("PROTECTED_RESOURCES_SSM", "")
PROTECTED_RESOURCE_TYPES = {t.strip() for t in os.environ.get("PROTECTED_RESOURCE_TYPES", "").split(",") if t.strip()}


def handler(event, context):
    """Notifier Lambda handler — handles warn mode, rejects enforce modes."""
    start_time = time.time()
    mode = event.get("mode", "warn")
    scan_date = event.get("scan_date", datetime.now(UTC).strftime("%Y-%m-%d"))

    min_severity = event.get("min_severity", "HIGH")
    triggered_by = event.get("triggered_by", "")

    print(
        f"Notifier invoked: mode={mode}, min_severity={min_severity}, "
        f"dry_run={DRY_RUN}, executor_armed={EXECUTOR_ARMED}"
        + (f", triggered_by={triggered_by}" if triggered_by else "")
    )

    if mode in ("enforce", "enforce_single"):
        if not EXECUTOR_ARMED:
            print(
                f"Executor not armed — cannot handle mode={mode}. "
                "Set executor_armed=true in Terraform to create the executor Lambda."
            )
            return {
                "statusCode": 200,
                "body": json.dumps(
                    {
                        "mode": mode,
                        "status": "executor_not_armed",
                        "message": "Executor Lambda does not exist. Set executor_armed=true in Terraform to enable enforcement.",
                    }
                ),
            }
        else:
            print(
                f"Executor is armed but enforce mode was sent to the notifier Lambda. "
                f"Invoke the executor Lambda directly for mode={mode}."
            )
            return {
                "statusCode": 400,
                "body": json.dumps(
                    {
                        "mode": mode,
                        "status": "wrong_lambda",
                        "message": "Enforce modes must be sent to the executor Lambda, not the notifier.",
                    }
                ),
            }

    if mode != "warn":
        print(f"Unknown mode: {mode}")
        return {"statusCode": 400, "body": f"Unknown mode: {mode}"}

    state_mgr = StateManager(STATE_TABLE, STATE_TTL_DAYS)
    return _handle_warn(state_mgr, scan_date, start_time, min_severity=min_severity)


def _handle_warn(state_mgr, scan_date, start_time, min_severity="HIGH"):
    """
    Read compliance report, filter actionable findings, send warnings, schedule enforcement.
    """
    print(f"=== WARN MODE (min_severity={min_severity}) ===")

    # Step 1: Load latest compliance report from S3
    report = _load_compliance_report(scan_date)
    if not report:
        print("No compliance report found — nothing to warn about.")
        return _response(0, 0, 0, start_time)

    # Step 2: Extract findings that are actionable at the requested severity level
    findings = _extract_actionable_findings(report, min_severity=min_severity)
    print(f"Found {len(findings)} actionable findings (min_severity={min_severity})")

    # Reconcile: auto-resolve stale warnings for resources no longer flagged
    current_arns = {f.get("resource_arn", "") for f in findings}
    resolved = state_mgr.reconcile_stale_warnings(current_arns)
    if resolved:
        print(f"Reconciled {resolved} stale warning(s)")

    if not findings:
        print("No actionable findings — nothing to warn about.")
        return _response(0, 0, 0, start_time)

    # Step 2b: Filter out protected resources (SSM config + tags)
    protected_config = _load_protected_config()
    if protected_config:
        account_id = boto3.client("sts").get_caller_identity()["Account"]
        before = len(findings)
        findings = [f for f in findings if not _is_protected(f, protected_config, account_id)]
        skipped = before - len(findings)
        if skipped:
            print(f"  Filtered {skipped} protected resources (SSM config + tags)")

    if not findings:
        print("All actionable findings are protected — nothing to warn about.")
        return _response(0, 0, 0, start_time)

    # Step 3: Check DynamoDB for already-warned resources (skip duplicates)
    new_findings = []
    for f in findings:
        arn = f.get("resource_arn", "")
        existing = state_mgr.get_record(arn)
        if existing and existing.get("status") in ("warned", "enforcing"):
            print(f"  Skipping {arn} — already warned ({existing['status']})")
            continue
        new_findings.append(f)

    print(f"{len(new_findings)} new findings to warn about ({len(findings) - len(new_findings)} already warned)")

    if not new_findings:
        return _response(len(findings), 0, 0, start_time)

    # Step 4: Resolve contacts and group by recipient. The order is the tag
    # on the resource, then the registry entry of its repository, then the
    # administrators.
    registry_entries = (report.get("iac_registry") or {}).get("entries", [])
    by_recipient = {}
    for f in new_findings:
        arn = f.get("resource_arn", "")
        tags = f.get("tags", {})
        contact_email, contact_source = resolve_contact(
            tags, CONTACT_TAG_KEYS, SES_ADMIN_EMAILS, registry_entries=registry_entries
        )

        f["contact_email"] = contact_email
        f["contact_source"] = contact_source

        if contact_email not in by_recipient:
            by_recipient[contact_email] = []
        by_recipient[contact_email].append(f)

    # Step 5: Write DynamoDB records (with approval_status=pending)
    batch_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    for f in new_findings:
        state_mgr.write_warning(
            resource_arn=f["resource_arn"],
            batch_id=batch_id,
            scan_date=scan_date,
            contact_email=f["contact_email"],
            contact_source=f["contact_source"],
            resource_type=f.get("resource_type", ""),
            resource_name=f.get("resource_name", ""),
            region=f.get("region", ""),
            estimated_monthly_cost=f.get("estimated_monthly_cost", 0),
            issues=[i for i in [f.get("issue", "")] if i],
            grace_period_hours=GRACE_PERIOD_HOURS,
            tags=f.get("tags", {}),
        )

    # Step 5b: Auto-approve expired resources (dev/staging only)
    auto_approved = 0
    if AUTO_APPROVE_EXPIRED:
        for f in new_findings:
            tags = f.get("tags", {})
            expires_at = tags.get("ExpiresAt", "")
            if not expires_at:
                continue
            try:
                expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                if expiry < datetime.now(UTC):
                    arn = f["resource_arn"]
                    state_mgr.update_approval_status(arn, "approved", approved_by="auto:expired")
                    auto_approved += 1
                    print(f"  Auto-approved (expired): {arn}")
            except (ValueError, TypeError):
                pass
        if auto_approved:
            print(f"Auto-approved {auto_approved} expired resources (AUTO_APPROVE_EXPIRED=true)")

    # Step 6: Send warning emails
    warnings_sent = 0
    if SES_SENDER_EMAIL:
        warnings_sent = send_warning_emails(
            by_recipient=by_recipient,
            sender_email=SES_SENDER_EMAIL,
            grace_period_hours=GRACE_PERIOD_HOURS,
            dry_run=DRY_RUN,
            project=PROJECT,
            environment=ENVIRONMENT,
        )
    else:
        print("SES not configured — skipping warning emails")

    # Step 7: Schedule enforcement invocation after grace period
    _schedule_enforcement(batch_id, scan_date)

    print(
        f"Warn complete: {len(new_findings)} resources warned, {warnings_sent} emails sent, "
        f"enforcement scheduled in {GRACE_PERIOD_HOURS}h"
    )

    return _response(len(findings), len(new_findings), warnings_sent, start_time)


def _load_compliance_report(date_str):
    """Load the compliance scanner JSON report from S3."""
    if not REPORTS_BUCKET:
        print("No reports bucket configured")
        return None

    s3 = boto3.client("s3")
    key = f"reports/{date_str}/compliance-report.json"

    try:
        response = s3.get_object(Bucket=REPORTS_BUCKET, Key=key)
        return json.loads(response["Body"].read().decode("utf-8"))
    except s3.exceptions.NoSuchKey:
        print(f"Report not found: s3://{REPORTS_BUCKET}/{key}")
        return None
    except Exception as e:
        print(f"Error loading report: {e}")
        return None


def _extract_actionable_findings(report, min_severity="HIGH"):
    """
    Extract findings at or above the requested severity level on active resources.
    These are candidates for 24-hr warnings.

    min_severity controls which findings are included:
      "HIGH"   — only HIGH (default, standard nightly behavior)
      "MEDIUM" — HIGH + MEDIUM
      "ALL"    — HIGH + MEDIUM + LOW (aggressive mode)
    """
    if min_severity == "HIGH":
        allowed = {"HIGH"}
    elif min_severity == "MEDIUM":
        allowed = {"HIGH", "MEDIUM"}
    else:  # "ALL" or "LOW"
        allowed = {"HIGH", "MEDIUM", "LOW"}

    actionable = []

    for section_key in ("drift_detection", "tag_compliance", "schedule_compliance", "lifecycle_compliance"):
        section = report.get(section_key, {})
        findings = section.get("findings", [])
        for f in findings:
            if f.get("aws_managed"):
                continue  # AWS-managed resources are expected, not actionable
            if f.get("severity") not in allowed:
                continue
            state = (f.get("state") or "").lower()
            if state in INACTIVE_STATES:
                continue
            actionable.append(f)

    return actionable


def _schedule_enforcement(batch_id, scan_date):
    """
    Create a one-shot EventBridge Scheduler schedule to invoke hunterkiller
    after GRACE_PERIOD_HOURS. Uses the Scheduler API (supports at() expressions).
    The schedule auto-deletes after firing (NONE FlexibleTimeWindow + DELETE after completion).
    """
    if not EXECUTOR_FUNCTION_NAME or not SCHEDULER_ROLE_ARN:
        print("Executor not armed (no executor function or scheduler role) — enforcement not scheduled")
        return

    from datetime import timedelta

    scheduler = boto3.client("scheduler")
    lambda_client = boto3.client("lambda")

    enforce_at = datetime.now(UTC) + timedelta(hours=GRACE_PERIOD_HOURS)
    schedule_name = f"{PROJECT}-{ENVIRONMENT}-hk-enforce-{batch_id}"

    try:
        # The schedule runs as the scheduler role, never as the executor's role.
        fn = lambda_client.get_function(FunctionName=EXECUTOR_FUNCTION_NAME)
        lambda_arn = fn["Configuration"]["FunctionArn"]

        # EventBridge Scheduler at() format: at(yyyy-mm-ddThh:mm:ss)
        schedule_expr = f"at({enforce_at.strftime('%Y-%m-%dT%H:%M:%S')})"

        scheduler.create_schedule(
            Name=schedule_name,
            ScheduleExpression=schedule_expr,
            ScheduleExpressionTimezone="UTC",
            FlexibleTimeWindow={"Mode": "OFF"},
            ActionAfterCompletion="DELETE",
            Target={
                "Arn": lambda_arn,
                "RoleArn": SCHEDULER_ROLE_ARN,
                "Input": json.dumps(
                    {
                        "mode": "enforce",
                        "batch_id": batch_id,
                        "scan_date": scan_date,
                    }
                ),
            },
            Description=f"Hunterkiller enforcement for batch {batch_id}",
        )

        print(f"Scheduled enforcement: schedule={schedule_name}, at={enforce_at.isoformat()}")

    except Exception as e:
        print(f"Failed to schedule enforcement: {e}\n{traceback.format_exc()}")


def _load_protected_config():
    """Load protected resources config from SSM Parameter Store."""
    if not PROTECTED_RESOURCES_SSM:
        return None
    try:
        ssm = boto3.client("ssm")
        resp = ssm.get_parameter(Name=PROTECTED_RESOURCES_SSM)
        return json.loads(resp["Parameter"]["Value"])
    except Exception as e:
        print(f"Warning: could not load protected resources from SSM: {e}")
        return None


def _is_protected(finding, config, account_id):
    """Check if a finding's resource is protected (type, tags, or SSM ARN patterns)."""
    # Resource-type-level protection (e.g., route53:hostedzone)
    resource_type = finding.get("resource_type", "")
    if resource_type and resource_type in PROTECTED_RESOURCE_TYPES:
        return True

    tags = finding.get("tags", {})

    # Tag-based protection
    env = tags.get(TAG_KEYS["environment"], "")
    if env.lower() in [e.lower() for e in PROTECTED_ENVIRONMENTS]:
        return True
    protected_val = tags.get(PROTECTED_TAG_KEY, "")
    if protected_val.lower() in ("true", "yes", "1"):
        return True

    # ARN-based protection (SSM config)
    arn = finding.get("resource_arn", "")
    if arn and config:
        for entry in config.get("resources", []):
            pattern = entry.get("arn_pattern", "")
            if not pattern:
                continue
            resolved = pattern.replace("*", account_id) if "*" in pattern else pattern
            if arn == resolved or arn.startswith(resolved):
                return True

    return False


def _response(total_findings, warned, emails_sent, start_time):
    duration = round(time.time() - start_time, 1)
    _write_job_status(total_findings, warned, emails_sent, duration)
    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "mode": "warn",
                "dry_run": DRY_RUN,
                "executor_armed": EXECUTOR_ARMED,
                "total_findings": total_findings,
                "new_warnings": warned,
                "emails_sent": emails_sent,
                "duration_seconds": duration,
            }
        ),
    }


JOB_STATUS_SSM_PREFIX = os.environ.get("JOB_STATUS_SSM_PREFIX", "")


def _write_job_status(total_findings, warned, emails_sent, duration):
    """Write execution summary to SSM for dashboard visibility."""
    if not JOB_STATUS_SSM_PREFIX:
        return
    try:
        parts = []
        if total_findings:
            parts.append(f"{total_findings} non-compliant resources")
        if warned:
            parts.append(f"{warned} warned")
        if emails_sent:
            parts.append(f"{emails_sent} emails sent")
        if not parts:
            parts.append("no non-compliant resources found")

        ssm = boto3.client("ssm")
        ssm.put_parameter(
            Name=f"{JOB_STATUS_SSM_PREFIX}/hunterkiller",
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
