"""
Hunterkiller Executor — Destructive Compliance Enforcement

Separate Lambda entry point that handles enforce and enforce_single modes.
Only deployed when executor_armed=true (Terraform-gated).

Checks DynamoDB approval_status before proceeding with teardowns:
  - approval_status must be "approved" (set by admin in dashboard)
  - Approvals expire after 24 hours

Safety:
  - DRY_RUN=true by default — teardowns logged but NOT executed.
  - Production-protected resources (Environment=prod, Protected=true) are exempt,
    decided from current tags: the stored record, the fresh report's finding
    and a live tag read where the service allows one.
  - "Destroy now" acts only on a resource with a current HIGH finding.
  - DynamoDB approval gate: admin must approve each resource before teardown.
  - IAM tag conditions prevent touching Protected/prod resources (defense in depth).
"""

import json
import os
import time
from datetime import UTC, datetime, timedelta

from compliance_schema import PROTECTED_ENVIRONMENTS as _CONTRACT_PROTECTED_ENVIRONMENTS
from compliance_schema import TAG_KEYS
from executor import execute_teardowns
from notifier import send_enforcement_report
from state_manager import StateManager

# Environment variables
PROJECT = os.environ.get("PROJECT", "unknown")
ENVIRONMENT = os.environ.get("ENVIRONMENT", "unknown")
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"
# Recoverable removal is the default: final snapshots, recovery windows, and
# refusal of buckets and repositories that still hold data.
UNRECOVERABLE_TEARDOWN = os.environ.get("UNRECOVERABLE_TEARDOWN", "false").lower() == "true"
STATE_TABLE = os.environ.get("STATE_TABLE", "")
REPORTS_BUCKET = os.environ.get("REPORTS_BUCKET", "")
SES_SENDER_EMAIL = os.environ.get("SES_SENDER_EMAIL", "")
SES_ADMIN_EMAILS = [e.strip() for e in os.environ.get("SES_ADMIN_EMAILS", "").split(",") if e.strip()]
# An unset or empty variable falls back to the generated contract.
PROTECTED_ENVIRONMENTS = [
    e.strip() for e in os.environ.get("PROTECTED_ENVIRONMENTS", "").split(",") if e.strip()
] or list(_CONTRACT_PROTECTED_ENVIRONMENTS)
PROTECTED_TAG_KEY = os.environ.get("PROTECTED_TAG_KEY") or TAG_KEYS["protected"]
PROTECTED_RESOURCES_SSM = os.environ.get("PROTECTED_RESOURCES_SSM", "")
PROTECTED_RESOURCE_TYPES = {t.strip() for t in os.environ.get("PROTECTED_RESOURCE_TYPES", "").split(",") if t.strip()}
STATE_TTL_DAYS = int(os.environ.get("STATE_TTL_DAYS", "30"))
SCANNER_FUNCTION_NAME = os.environ.get("SCANNER_FUNCTION_NAME", "")

# Approval expiry: 24 hours
APPROVAL_EXPIRY_HOURS = 24
# An automatic approval (approved_by "auto:expired") is written by the notifier
# when the warning is sent, a grace period before the scheduled run. It holds
# for the grace period plus the normal window, so the run it was made for can
# use it.
GRACE_PERIOD_HOURS = float(os.environ.get("GRACE_PERIOD_HOURS", "24"))
AUTO_APPROVED_BY = "auto:expired"
# "Destroy now" uses the newest report no older than this, so it works before
# the day's scan has run.
REPORT_MAX_AGE_HOURS = float(os.environ.get("REPORT_MAX_AGE_HOURS", "36"))


def handler(event, context):
    """Executor Lambda handler — dispatches to enforce or enforce_single mode."""
    start_time = time.time()
    mode = event.get("mode", "enforce")
    scan_date = event.get("scan_date", datetime.now(UTC).strftime("%Y-%m-%d"))
    batch_id = event.get("batch_id", "")

    print(f"Executor invoked: mode={mode}, dry_run={DRY_RUN}, scan_date={scan_date}")

    state_mgr = StateManager(STATE_TABLE, STATE_TTL_DAYS)

    if mode == "enforce":
        all_batches = event.get("scope") == "all_warned"
        return _handle_enforce(state_mgr, scan_date, batch_id, start_time, all_batches=all_batches)
    elif mode == "enforce_single":
        resource_arn = event.get("resource_arn", "")
        triggered_by = event.get("triggered_by", "unknown")
        return _handle_enforce_single(state_mgr, resource_arn, triggered_by, start_time)
    else:
        print(f"Executor received unsupported mode: {mode}")
        return {"statusCode": 400, "body": f"Executor does not handle mode: {mode}"}


def _handle_enforce(state_mgr, scan_date, batch_id, start_time, all_batches=False):
    """
    Re-check warned resources against fresh compliance report.
    Only tear down resources with approval_status == "approved".
    all_batches (the dashboard's "Destroy All") reads every warned record.
    """
    print(f"=== EXECUTOR ENFORCE MODE === batch_id={batch_id} all_batches={all_batches}")

    # Step 1: Get the warned resources from DynamoDB (one batch, or every batch)
    warned_records = state_mgr.get_warned_records(batch_id, all_batches=all_batches)
    print(f"Found {len(warned_records)} warned resources for batch {batch_id}")

    if not warned_records:
        print("No warned resources found for this batch.")
        return _response("enforce", 0, 0, 0, 0, 0, 0, start_time, batch_id)

    # Step 2: Load today's fresh compliance report
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    report = _load_compliance_report(today)
    if not report:
        report = _load_compliance_report(scan_date)

    if not report:
        print("No compliance report found — cannot verify. Skipping enforcement.")
        return _response("enforce", len(warned_records), 0, 0, 0, 0, 0, start_time, batch_id)

    # Step 3: Current HIGH findings by ARN (their tags are this scan's tags)
    findings_by_arn = _high_findings_by_arn(report)

    # Step 4: Categorize each warned resource
    to_teardown = []
    resolved = []
    protected = []
    aborted = []
    extended = []
    not_approved = []

    now = datetime.now(UTC)

    for record in warned_records:
        arn = record["resource_arn"]

        # Escape hatch: aborted via dashboard
        if record.get("status") == "aborted":
            print(f"  ABORTED: {arn} — by {record.get('aborted_by', 'unknown')}")
            aborted.append(record)
            continue

        # Escape hatch: grace period extended
        grace_deadline = record.get("grace_deadline", "")
        if grace_deadline:
            try:
                deadline_dt = datetime.fromisoformat(grace_deadline)
                if now < deadline_dt:
                    hours_left = (deadline_dt - now).total_seconds() / 3600
                    print(f"  EXTENDED: {arn} — {hours_left:.1f}h remaining")
                    extended.append(record)
                    continue
            except (ValueError, TypeError):
                pass

        # Check if now compliant
        if arn not in findings_by_arn:
            print(f"  RESOLVED: {arn} — now compliant")
            state_mgr.update_status(arn, "resolved")
            resolved.append(record)
            continue

        # Protection from current tags: the fresh finding and a live read
        if _protected_now(record, findings_by_arn[arn]):
            print(f"  PROTECTED: {arn} — exempt from teardown")
            state_mgr.update_status(arn, "protected")
            protected.append(record)
            continue

        # APPROVAL GATE: approved, with a readable approval time inside the window
        approval_status, approval_detail = _approval_state(record, now)
        if approval_status == "expired":
            print(f"  APPROVAL EXPIRED: {arn} — {approval_detail}")
            state_mgr.update_approval_status(arn, "expired")
            not_approved.append(record)
            continue
        if approval_status != "approved":
            print(f"  NOT APPROVED: {arn} — approval_status={approval_status}, skipping")
            not_approved.append(record)
            continue

        to_teardown.append(record)

    print(
        f"Enforcement: {len(to_teardown)} to tear down, {len(resolved)} resolved, "
        f"{len(protected)} protected, {len(aborted)} aborted, {len(extended)} extended, "
        f"{len(not_approved)} not approved"
    )

    # Step 5: Execute teardowns
    results = []
    if to_teardown:
        results = execute_teardowns(to_teardown, dry_run=DRY_RUN, unrecoverable=UNRECOVERABLE_TEARDOWN)
        for r in results:
            state_mgr.update_status(r["resource_arn"], _result_status(r), teardown_result=r.get("message", ""))

    # Step 6: Calculate cost impact
    savings_monthly = sum(float(r.get("estimated_monthly_cost") or 0) for r in results if r.get("success"))
    protected_cost = sum(float(r.get("estimated_monthly_cost") or 0) for r in protected)
    print(f"Cost impact: ${savings_monthly:.2f}/mo savings, ${protected_cost:.2f}/mo protected")

    # Step 7: Send enforcement report email (with savings)
    if SES_SENDER_EMAIL:
        send_enforcement_report(
            results=results,
            resolved=resolved,
            protected=protected,
            sender_email=SES_SENDER_EMAIL,
            admin_emails=SES_ADMIN_EMAILS,
            dry_run=DRY_RUN,
            project=PROJECT,
            environment=ENVIRONMENT,
            batch_id=batch_id,
            savings_monthly=savings_monthly,
            protected_cost=protected_cost,
        )

    # Step 8: Re-invoke scanner for fresh post-teardown report
    _trigger_rescan()

    duration = round(time.time() - start_time, 1)
    print(f"Enforce complete in {duration}s")

    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "mode": "enforce",
                "batch_id": batch_id,
                "dry_run": DRY_RUN,
                "torn_down": len([r for r in results if r.get("success")]),
                "resolved": len(resolved),
                "protected": len(protected),
                "aborted": len(aborted),
                "extended": len(extended),
                "not_approved": len(not_approved),
                "errors": len([r for r in results if not r.get("success")]),
                "savings_monthly": savings_monthly,
                "protected_cost": protected_cost,
                "duration_seconds": duration,
            }
        ),
    }


def _handle_enforce_single(state_mgr, resource_arn, triggered_by, start_time):
    """
    Enforce a single resource immediately (dashboard 'Destroy Now' button).
    Still requires approval_status == "approved".
    """
    print(f"=== EXECUTOR ENFORCE SINGLE === resource={resource_arn}, triggered_by={triggered_by}")

    if not resource_arn:
        return {"statusCode": 400, "body": "resource_arn is required"}

    record = state_mgr.get_record(resource_arn)
    if not record:
        print(f"No state record for {resource_arn}")
        return {"statusCode": 404, "body": f"No record found for {resource_arn}"}

    # Only a resource that was warned, and is still in that state, can be torn
    # down: not one that was aborted, resolved, protected or already handled,
    # and not a record that never came from a warning.
    status = record.get("status", "")
    if status != "warned" or not record.get("warned_at") or not record.get("resource_type"):
        print(f"NOT ELIGIBLE: {resource_arn} — status={status or 'none'}")
        return {
            "statusCode": 409,
            "body": json.dumps(
                {
                    "status": "not_eligible",
                    "message": f"Only a warned resource can be torn down (current status: {status or 'none'})",
                }
            ),
        }

    # Still non-compliant now: a resource fixed since its warning is not torn down.
    report = _load_recent_report(REPORT_MAX_AGE_HOURS)
    findings = _high_findings_by_arn(report).get(resource_arn) if report else None
    if not findings:
        reason = "no current compliance report" if not report else "no current HIGH finding"
        print(f"NOT ELIGIBLE: {resource_arn} — {reason}")
        return {
            "statusCode": 409,
            "body": json.dumps({"status": "not_eligible", "message": f"Not torn down: {reason} for this resource"}),
        }

    # Protection from current tags: the fresh finding and a live read
    if _protected_now(record, findings):
        print(f"PROTECTED: {resource_arn} — exempt from teardown")
        state_mgr.update_status(resource_arn, "protected")
        return {"statusCode": 200, "body": json.dumps({"status": "protected"})}

    # APPROVAL GATE: approved, with a readable approval time inside the window
    approval_status, approval_detail = _approval_state(record, datetime.now(UTC))
    if approval_status == "expired":
        print(f"APPROVAL EXPIRED: {resource_arn} — {approval_detail}")
        state_mgr.update_approval_status(resource_arn, "expired")
        return {
            "statusCode": 403,
            "body": json.dumps(
                {
                    "status": "approval_expired",
                    "message": f"Approval expired ({approval_detail}, limit {APPROVAL_EXPIRY_HOURS}h)",
                }
            ),
        }
    if approval_status != "approved":
        print(f"NOT APPROVED: {resource_arn} — approval_status={approval_status}")
        return {
            "statusCode": 403,
            "body": json.dumps(
                {
                    "status": "not_approved",
                    "approval_status": approval_status,
                    "message": f"Resource must be approved before teardown (current: {approval_status})",
                }
            ),
        }

    # Execute teardown
    results = execute_teardowns([record], dry_run=DRY_RUN, unrecoverable=UNRECOVERABLE_TEARDOWN)
    for r in results:
        state_mgr.update_status(
            r["resource_arn"],
            _result_status(r),
            teardown_result=f"Early destroy by {triggered_by}: {r.get('message', '')}",
        )

    duration = round(time.time() - start_time, 1)
    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "mode": "enforce_single",
                "resource_arn": resource_arn,
                "triggered_by": triggered_by,
                "dry_run": DRY_RUN,
                "success": results[0]["success"] if results else False,
                "duration_seconds": duration,
            }
        ),
    }


def _result_status(result):
    """Record status after a teardown attempt.

    A resource whose recovery point is still being made stays "warned", so the
    next run (or "destroy now") finishes the removal while the approval holds.
    """
    if DRY_RUN:
        return "dry_run"
    if result.get("success"):
        return "torn_down"
    return "warned" if result.get("pending") else "error"


def _approval_state(record, now):
    """Return (state, detail) for a record's approval.

    "approved" only when approval_status is approved AND approved_at is a
    readable time no older than APPROVAL_EXPIRY_HOURS and not in the future.
    An approval with a missing or unreadable time counts as expired.
    """
    approval_status = record.get("approval_status", "pending")
    if approval_status != "approved":
        return approval_status, ""
    limit_hours = APPROVAL_EXPIRY_HOURS
    if record.get("approved_by") == AUTO_APPROVED_BY:
        limit_hours += GRACE_PERIOD_HOURS
    try:
        approved_dt = datetime.fromisoformat(record.get("approved_at") or "")
        if approved_dt.tzinfo is None:
            approved_dt = approved_dt.replace(tzinfo=UTC)
        hours_since = (now - approved_dt).total_seconds() / 3600
    except (ValueError, TypeError):
        return "expired", "approval time missing or unreadable"
    if hours_since > limit_hours:
        return "expired", f"approved {hours_since:.1f}h ago"
    if hours_since < -0.25:
        return "expired", "approval time is in the future"
    return "approved", ""


def _load_recent_report(max_age_hours, now=None):
    """The newest daily report generated no more than max_age_hours ago, or None.

    Today's report is tried first, then the reports of earlier days the age
    allows. A report whose generated_at is missing or unreadable is not used.
    """
    now = now or datetime.now(UTC)
    days = int(max_age_hours // 24) + 1
    for back in range(days + 1):
        report = _load_compliance_report((now - timedelta(days=back)).strftime("%Y-%m-%d"))
        if not isinstance(report, dict):
            continue
        try:
            generated = datetime.fromisoformat(str(report.get("generated_at", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=UTC)
        age_hours = (now - generated).total_seconds() / 3600
        if -0.25 <= age_hours <= max_age_hours:
            return report
    return None


def _load_compliance_report(date_str):
    """Load the compliance scanner JSON report from S3."""
    if not REPORTS_BUCKET:
        print("No reports bucket configured")
        return None

    import boto3

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


def _extract_all_findings(report):
    """Extract all findings from all sections."""
    all_findings = []
    for section_key in ("drift_detection", "tag_compliance", "schedule_compliance", "lifecycle_compliance"):
        section = report.get(section_key, {})
        all_findings.extend(section.get("findings", []))
    return all_findings


def _high_findings_by_arn(report):
    """Current HIGH findings of a report, as {arn: [finding, ...]}."""
    by_arn = {}
    for finding in _extract_all_findings(report or {}):
        if isinstance(finding, dict) and finding.get("severity") == "HIGH" and finding.get("resource_arn"):
            by_arn.setdefault(finding["resource_arn"], []).append(finding)
    return by_arn


class LiveTagsUnavailable(Exception):
    """The resource's tags could not be read from its service."""


def _live_tags(arn, resource_type):
    """Current tags of a resource as a dict, or None when no live read applies.

    IAM roles, users and policies are read from IAM; any other resource with a
    region in its ARN from the Resource Groups Tagging API. None means the
    resource has no live read here (the caller relies on the fresh report).
    Raises LiveTagsUnavailable when a read applies and fails.
    """
    import boto3

    try:
        if resource_type in ("iam:role", "iam:user", "iam:policy"):
            iam = boto3.client("iam")
            name = arn.rsplit("/", 1)[-1]
            if resource_type == "iam:role":
                tags = iam.list_role_tags(RoleName=name).get("Tags", [])
            elif resource_type == "iam:user":
                tags = iam.list_user_tags(UserName=name).get("Tags", [])
            else:
                tags = iam.list_policy_tags(PolicyArn=arn).get("Tags", [])
            return {t["Key"]: t.get("Value", "") for t in tags}
        parts = arn.split(":")
        region = parts[3] if len(parts) > 5 else ""
        if not region:
            return None
        tagging = boto3.client("resourcegroupstaggingapi", region_name=region)
        found = tagging.get_resources(ResourceARNList=[arn]).get("ResourceTagMappingList", [])
        for mapping in found:
            if mapping.get("ResourceARN") == arn:
                return {t["Key"]: t.get("Value", "") for t in mapping.get("Tags", [])}
        return None
    except Exception as e:
        raise LiveTagsUnavailable(str(e)) from e


def _protected_now(record, findings):
    """Protection decided from current tags, before a teardown.

    Protected when any of these protects: the record as warned, the tags of
    each current finding for the resource, or the tags read live from its
    service. A live read that applies and fails protects.
    """
    if _is_protected(record):
        return True
    for finding in findings or []:
        if _is_protected({**record, "tags": finding.get("tags") if "tags" in finding else {}}):
            return True
    arn = record.get("resource_arn", "")
    try:
        live = _live_tags(arn, record.get("resource_type", ""))
    except LiveTagsUnavailable as e:
        print(f"Live tags unreadable — treating {arn} as protected: {e}")
        return True
    return live is not None and _is_protected({**record, "tags": live})


def _is_protected(record):
    """Check if a resource is protected from teardown.

    Four layers of protection:
      1. Resource type is in protected_resource_types (e.g., s3:bucket)
      2. Environment tag matches protected_environments (e.g., prod)
      3. Protected tag is true/yes/1
      4. ARN matches protected_resources config (defense-in-depth from SSM)
    """
    resource_type = record.get("resource_type", "")
    if resource_type and resource_type in PROTECTED_RESOURCE_TYPES:
        return True

    tags = record.get("tags") or {}
    if not isinstance(tags, dict):
        # A record whose tags cannot be read cannot be shown to be unprotected.
        return True
    # Tag keys are matched without regard to case ("environment" protects too).
    lowered = {str(k).lower(): str(v).strip().lower() for k, v in tags.items()}

    if lowered.get(TAG_KEYS["environment"].lower(), "") in [e.lower() for e in PROTECTED_ENVIRONMENTS]:
        return True

    if lowered.get(PROTECTED_TAG_KEY.lower(), "") in ("true", "yes", "1"):
        return True

    # Defense-in-depth: check against protected resources config
    arn = record.get("resource_arn", "")
    return bool(arn and _match_protected_arn(arn))


class ProtectedConfigUnavailable(Exception):
    """The protected-resources list is configured but could not be read."""


def _load_protected_config():
    """Load protected resources config from SSM Parameter Store (no caching).

    Returns None when no list is configured. Raises ProtectedConfigUnavailable
    when one is configured and cannot be read or parsed.
    """
    if not PROTECTED_RESOURCES_SSM:
        return None
    try:
        import boto3

        ssm = boto3.client("ssm")
        resp = ssm.get_parameter(Name=PROTECTED_RESOURCES_SSM)
        config = json.loads(resp["Parameter"]["Value"])
        if not isinstance(config, dict):
            raise ValueError("protected resources config is not an object")
        entries = config.get("resources", [])
        if not isinstance(entries, list):
            raise ValueError("protected resources config: 'resources' is not a list")
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("arn_pattern"), str) or not entry["arn_pattern"]:
                raise ValueError("protected resources config: an entry has no arn_pattern")
        return config
    except Exception as e:
        raise ProtectedConfigUnavailable(str(e)) from e


def _match_protected_arn(arn):
    """Check if an ARN matches any protected resource pattern.

    When the list is configured but unreadable, every resource counts as
    protected: nothing is torn down against a list that cannot be checked.
    """
    try:
        config = _load_protected_config()
    except ProtectedConfigUnavailable as e:
        print(f"Protected resources list unavailable — treating {arn} as protected: {e}")
        return True
    if not config:
        return False

    import boto3

    account_id = boto3.client("sts").get_caller_identity()["Account"]

    for entry in config.get("resources", []):
        pattern = entry["arn_pattern"]
        resolved = pattern.replace("*", account_id) if "*" in pattern else pattern
        if arn == resolved or arn.startswith(resolved):
            return True
    return False


def _trigger_rescan():
    """Re-invoke the compliance scanner (async) for a fresh post-teardown report.

    Allows the dashboard to immediately show updated findings after enforcement.
    """
    if not SCANNER_FUNCTION_NAME:
        print("No scanner function configured — skipping post-teardown rescan")
        return

    try:
        import boto3

        lambda_client = boto3.client("lambda")
        lambda_client.invoke(
            FunctionName=SCANNER_FUNCTION_NAME,
            InvocationType="Event",  # Async — don't wait
            Payload=json.dumps({"triggered_by": "hunterkiller-executor"}),
        )
        print(f"Post-teardown rescan triggered: {SCANNER_FUNCTION_NAME}")
    except Exception as e:
        print(f"Warning: failed to trigger rescan (non-fatal): {e}")


def _response(mode, total, torn_down, resolved, protected, aborted, not_approved, start_time, batch_id=""):
    duration = round(time.time() - start_time, 1)
    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "mode": mode,
                "batch_id": batch_id,
                "dry_run": DRY_RUN,
                "total": total,
                "torn_down": torn_down,
                "resolved": resolved,
                "protected": protected,
                "aborted": aborted,
                "not_approved": not_approved,
                "duration_seconds": duration,
            }
        ),
    }
