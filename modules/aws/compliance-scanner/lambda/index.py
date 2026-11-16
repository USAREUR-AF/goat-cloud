"""
Compliance Scanner — Account-Wide Resource Discovery and Compliance Checking

Discovers ALL resources in the AWS account, checks tag compliance,
detects Terraform state drift (click-ops), and flags lifecycle/schedule violations.
Report-only — no destructive actions.

Runs nightly via EventBridge. Outputs:
  1. JSON report → S3 (machine-readable)
  2. Markdown report → S3 (human-readable)
  3. SNS email summary → cloud admins
"""

import json
import os
import time
import traceback
from datetime import UTC, datetime

import boto3
from compliance_schema import REQUIRED_TAGS as _SCHEMA_REQUIRED_TAGS
from compliance_schema import repo_slug, require_env, validate_report
from iac_registry import load_iac_registry
from reporters.html_email import send_html_email
from reporters.json_report import generate_json_report
from reporters.markdown_report import generate_markdown_report
from scanners.drift_detector import scan_drift
from scanners.lifecycle_compliance import scan_lifecycle_compliance
from scanners.multi_state_detector import analyze_discovered_states, scan_multi_state
from scanners.registry_state import resources_of_entry, unreadable_state_finding
from scanners.schedule_compliance import scan_schedule_compliance
from scanners.tag_compliance import scan_tag_compliance
from utils.aws_resources import discover_all_resources, get_service_costs
from utils.exclusions import ExclusionEngine
from utils.region_discovery import discover_active_regions
from utils.state_parser import load_terraform_state_arns

# Inputs with no default. Checked at import so a missing one stops the cold
# start with its name instead of failing part-way through a scan.
for _name in ("PROJECT", "ENVIRONMENT", "REPOSITORY_URL", "AWS_REGION"):
    require_env(_name)

# Lazy imports for IaC state detectors (only used if registry entries exist)
# from scanners.cfn_state_detector import detect_cfn_managed_resources
# from scanners.pulumi_state_detector import detect_pulumi_managed_resources

# CloudTrail event names that indicate resource destruction
_TERMINATION_EVENTS = {
    "TerminateInstances",
    "DeleteVolume",
    "DeleteSecurityGroup",
    "ReleaseAddress",
    "DeleteNatGateway",
    "DeleteVpc",
    "DeleteSubnet",
    "DeleteDBInstance",
    "DeleteDBCluster",
    "DeleteCacheCluster",
    "DeleteLoadBalancer",
    "DeleteTargetGroup",
    "DeleteFunction20150331",  # Lambda DeleteFunction
    "DeleteService",  # ECS
    "DeleteBucket",
    "DeleteTable",  # DynamoDB
}


def _get_termination_events(scan_time):
    """Query CloudTrail for resource termination events this billing month.

    Returns a list of {arn, event_time, event_name} dicts. Used by the
    dashboard to calculate accurate prorated costs for destroyed resources.
    """
    ct = boto3.client("cloudtrail")
    month_start = scan_time.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    events = []
    seen = set()  # dedupe by ARN

    for event_name in _TERMINATION_EVENTS:
        try:
            paginator = ct.get_paginator("lookup_events")
            for page in paginator.paginate(
                LookupAttributes=[
                    {
                        "AttributeKey": "EventName",
                        "AttributeValue": event_name,
                    }
                ],
                StartTime=month_start,
                EndTime=scan_time,
            ):
                for event in page.get("Events", []):
                    for resource in event.get("Resources", []):
                        arn = resource.get("ResourceName", "")
                        if arn and arn not in seen:
                            seen.add(arn)
                            events.append(
                                {
                                    "arn": arn,
                                    "event_time": event["EventTime"].isoformat(),
                                    "event_name": event_name,
                                }
                            )
        except Exception:
            continue  # Skip individual event types that fail

    return events


# Environment variables
PROJECT = os.environ.get("PROJECT", "unknown")
ENVIRONMENT = os.environ.get("ENVIRONMENT", "unknown")
REPORTS_BUCKET = os.environ.get("REPORTS_BUCKET", "")
SCAN_METRIC_NAMESPACE = os.environ.get("SCAN_METRIC_NAMESPACE", "ComplianceScanner")
TF_STATE_BUCKET = os.environ.get("TF_STATE_BUCKET", "")
TF_STATE_KEY = os.environ.get("TF_STATE_KEY", "")
REQUIRED_TAGS = [
    t.strip() for t in (os.environ.get("REQUIRED_TAGS") or ",".join(_SCHEMA_REQUIRED_TAGS)).split(",") if t.strip()
]
EXCLUSION_ARNS = [a.strip() for a in os.environ.get("EXCLUSION_ARNS", "").split(",") if a.strip()]
EXCLUSION_PATTERNS = [p.strip() for p in os.environ.get("EXCLUSION_PATTERNS", "").split(",") if p.strip()]
SES_SENDER_EMAIL = os.environ.get("SES_SENDER_EMAIL", "")
SES_RECIPIENT_EMAILS = [e.strip() for e in os.environ.get("SES_RECIPIENT_EMAILS", "").split(",") if e.strip()]
HUNTERKILLER_FUNCTION_NAME = os.environ.get("HUNTERKILLER_FUNCTION_NAME", "")
OVERRIDES_TABLE = os.environ.get("OVERRIDES_TABLE", "")
PROTECTED_RESOURCES_SSM = os.environ.get("PROTECTED_RESOURCES_SSM", "")
REPORT_TABLE = os.environ.get("REPORT_TABLE", "")
# This repo's own URL — used to tell our IaC-registry entry from a sister repo's.
REPOSITORY_URL = os.environ.get("REPOSITORY_URL", "")


def handler(event, context):
    """Main Lambda handler — orchestrates the full compliance scan."""
    start_time = time.time()
    scan_time = datetime.now(UTC)
    print(f"Starting compliance scan for {PROJECT}-{ENVIRONMENT} at {scan_time.isoformat()}")

    account_id = boto3.client("sts").get_caller_identity()["Account"]

    results = {
        "report_version": "1.0",
        "generated_at": scan_time.isoformat(),
        "scan_duration_seconds": 0,
        "account_id": account_id,
        "project": PROJECT,
        "environment": ENVIRONMENT,
        "regions_scanned": [],
        "regions_with_resources": [],
        "summary": {
            "total_resources_discovered": 0,
            "total_findings": 0,
            "findings_by_severity": {"HIGH": 0, "MEDIUM": 0, "LOW": 0},
            "findings_by_category": {},
        },
        "tag_compliance": {"findings": []},
        "drift_detection": {"terraform_managed": 0, "unmanaged": 0, "findings": []},
        "schedule_compliance": {"findings": []},
        "lifecycle_compliance": {"findings": []},
        "errors": [],
    }

    try:
        # Step 1: Discover active regions via Cost Explorer
        print("Step 1: Discovering active regions...")
        active_regions, all_regions_cost = discover_active_regions()
        results["regions_scanned"] = active_regions
        results["region_costs"] = all_regions_cost
        results["service_costs"] = get_service_costs()
        print(f"  Active regions: {active_regions}")

        # Step 2: Discover all resources across active regions
        print("Step 2: Discovering resources...")
        resources = discover_all_resources(active_regions, account_id)
        results["summary"]["total_resources_discovered"] = len(resources)
        results["regions_with_resources"] = list(set(r.get("region", "global") for r in resources))
        # Resource type and region summaries for charts
        type_counts = {}
        region_type_counts = {}
        for r in resources:
            rtype = r.get("resource_type", "unknown")
            region = r.get("region", "global")
            type_counts[rtype] = type_counts.get(rtype, 0) + 1
            if region not in region_type_counts:
                region_type_counts[region] = {}
            region_type_counts[region][rtype] = region_type_counts[region].get(rtype, 0) + 1
        results["resource_type_counts"] = type_counts
        results["region_type_counts"] = region_type_counts
        # Persist trimmed resource inventory for dashboard viz
        # Include sizing fields so dashboard can estimate "when-running" cost
        # for stopped/destroyed resources (scanner sets cost=0 for stopped EC2).
        results["discovered_resources"] = [
            {
                "arn": r.get("arn", ""),
                "resource_type": r.get("resource_type", ""),
                "region": r.get("region", "global"),
                "name": r.get("name", ""),
                "state": r.get("state", ""),
                "tags": r.get("tags", {}),
                "estimated_monthly_cost": r.get("estimated_monthly_cost"),
                "instance_type": r.get("instance_type", ""),
                "instance_class": r.get("instance_class", ""),
            }
            for r in resources
            if r.get("arn")  # skip resources without ARNs
        ]
        print(f"  Discovered {len(resources)} resources")

        # Step 3: Load Terraform state for drift detection
        # Loads the primary state AND every other state file in the same bucket
        # (other roots and workspaces of the platform), so a resource managed by
        # any of them is not flagged as drift.
        print("Step 3: Loading Terraform state...")
        tf_arns = set()
        tf_arns_by_workspace = {}  # workspace path -> ARN count (for registry reporting)
        multi_state = {"state_files": [], "lock_tables": [], "notes": []}
        if TF_STATE_BUCKET and TF_STATE_KEY:
            tf_arns = load_terraform_state_arns(TF_STATE_BUCKET, TF_STATE_KEY)
            print(f"  Loaded {len(tf_arns)} ARNs from primary state")

            # Discover and load all other state files in our bucket
            try:
                multi_state = scan_multi_state(TF_STATE_BUCKET, TF_STATE_KEY)
                our_extra_states = [
                    sf for sf in multi_state.get("state_files", []) if sf.get("bucket") == TF_STATE_BUCKET
                ]
                for sf in our_extra_states:
                    extra_arns = load_terraform_state_arns(sf["bucket"], sf["key"])
                    workspace = sf["key"].replace("terraform.tfstate", "").strip("/")
                    tf_arns_by_workspace[f"{sf['bucket']}/{sf['key']}"] = len(extra_arns)
                    print(f"  Loaded {len(extra_arns)} ARNs from workspace {workspace}")
                    tf_arns |= extra_arns
                print(f"  Total: {len(tf_arns)} ARNs across {1 + len(our_extra_states)} state files")
            except Exception as e:
                print(f"  Multi-state discovery error (non-fatal): {e}")
        else:
            print("  Skipping — no TF state bucket configured")

        # Step 3b: Load IaC registry and external managed resources
        # Discovers resources managed by other IaC repos (Serverless/CDK via
        # CloudFormation, Pulumi via S3 state) so they aren't flagged as drift.
        iac_registry = []
        external_managed = {}  # arn -> {repo_url, iac_type, source, repo_slug}
        unreadable_states = []  # (registry entry, error) for registered states that could not be read
        try:
            iac_registry = load_iac_registry(PROJECT, ENVIRONMENT)
            if iac_registry:
                # CloudFormation-based repos (Serverless, CDK, SAM, Copilot, raw CFN)
                _CFN_IAC_TYPES = {"serverless", "cdk", "cloudformation", "sam", "copilot"}
                cfn_entries = [e for e in iac_registry if str(e.get("iac_type") or "").lower() in _CFN_IAC_TYPES]
                if cfn_entries:
                    from scanners.cfn_state_detector import detect_cfn_managed_resources

                    cfn_managed, unregistered_stacks = detect_cfn_managed_resources(
                        cfn_entries, active_regions, account_id
                    )
                    external_managed.update(cfn_managed)
                    if unregistered_stacks:
                        results["unregistered_cfn_stacks"] = unregistered_stacks

                # Pulumi-based repos (S3 backend)
                pulumi_entries = [e for e in iac_registry if e.get("iac_type") == "pulumi"]
                if pulumi_entries:
                    from scanners.pulumi_state_detector import detect_pulumi_managed_resources

                    pulumi_managed = detect_pulumi_managed_resources(pulumi_entries, account_id)
                    external_managed.update(pulumi_managed)

                # Terraform-based repos (S3 backend) — read .tfstate and extract ARNs
                tf_entries = [e for e in iac_registry if e.get("iac_type") == "terraform"]
                if tf_entries:
                    # NOTE: load_terraform_state_arns is imported at module level
                    # (top of file). It must NOT be re-imported here — a function-
                    # local import makes the name local to the ENTIRE handler, so
                    # the earlier use at Step 3 (line ~208) throws UnboundLocalError,
                    # aborting state + IaC-registry loading (0 registered repos).
                    own_state_key = os.environ.get("TF_STATE_KEY", "")
                    own_state_bucket = os.environ.get("TF_STATE_BUCKET", "")
                    for entry in tf_entries:
                        loc = entry.get("state_location", {}) or {}
                        # state_location.type is optional — iac_type already says
                        # "terraform", so an s3 bucket+key IS an s3_tfstate. Only
                        # skip if type is EXPLICITLY something else. Requiring
                        # type=="s3_tfstate" would drop registered states that omit
                        # the field and report their resources as drift.
                        if loc.get("type") and loc.get("type") != "s3_tfstate":
                            continue
                        bucket = loc.get("bucket", "")
                        key = loc.get("key", "")
                        # Skip our own state — it's already loaded into tf_arns
                        if bucket == own_state_bucket and key == own_state_key:
                            continue
                        if not bucket or not key:
                            continue
                        try:
                            ext_arns = load_terraform_state_arns(bucket, key, strict=True)
                            for arn in ext_arns:
                                external_managed[arn] = {
                                    "repo_url": entry.get("repo_url", ""),
                                    "iac_type": "terraform",
                                    "source": "iac-registry",
                                    "repo_slug": entry.get("repo_slug", ""),
                                }
                            print(
                                f"  IaC registry [terraform]: {entry.get('repo_slug')} → {len(ext_arns)} ARNs from s3://{bucket}/{key}"
                            )
                        except Exception as e:
                            # Registered but unreadable: one finding on the entry,
                            # and the stack's tagged resources are not reported
                            # as unmanaged in this scan.
                            print(f"  WARNING: failed to load TF state for {entry.get('repo_slug')}: {e}")
                            unreadable_states.append((entry, e))
                            for arn in resources_of_entry(resources, entry):
                                external_managed[arn] = {
                                    "repo_url": entry.get("repo_url", ""),
                                    "iac_type": "terraform",
                                    "source": "iac-registry:state-unreadable",
                                    "repo_slug": entry.get("repo_slug", ""),
                                }

                # Registry SSM parameters themselves are managed resources
                # (created by each repo's CI pipeline or Terraform)
                lambda_region = require_env("AWS_REGION")
                for entry in iac_registry:
                    ssm_name = entry.get("_ssm_name", "")
                    if ssm_name:
                        registry_arn = f"arn:aws:ssm:{lambda_region}:{account_id}:parameter/{ssm_name.lstrip('/')}"
                        external_managed[registry_arn] = {
                            "repo_url": entry.get("repo_url", ""),
                            "iac_type": entry.get("iac_type", ""),
                            "source": "iac-registry",
                            "repo_slug": entry.get("repo_slug", ""),
                        }

                print(f"  IaC registry: {len(external_managed)} externally managed resources")
        except Exception as e:
            print(f"  IaC registry loading error (non-fatal): {e}")
            results["errors"].append(f"IaC registry loading failed: {e}")

        # Unified set of all managed ARNs (our TF state + external IaC)
        all_managed_arns = tf_arns | set(external_managed.keys())

        # Step 4: Initialize exclusion engine
        exclusion_engine = ExclusionEngine(
            explicit_arns=EXCLUSION_ARNS,
            patterns=EXCLUSION_PATTERNS,
            tf_arns=all_managed_arns,
            account_id=account_id,
        )

        # Load dashboard overrides (false positives / exemptions)
        if OVERRIDES_TABLE:
            exclusion_engine.load_overrides(OVERRIDES_TABLE)

        # Step 5: Pre-filter excluded resources so ALL scanners respect exclusions.
        # The exclusion engine handles: auto-created AWS resources, EBS root volumes,
        # dashboard overrides (false positives), explicit ARN exclusions, custom patterns.
        included_resources = []
        excluded_count = 0
        for r in resources:
            excluded, _reason = exclusion_engine.should_exclude(r)
            if excluded:
                excluded_count += 1
            else:
                included_resources.append(r)
        print(f"  Excluded {excluded_count} resources from compliance checks")
        results["summary"]["excluded_resources"] = excluded_count

        # Run scanners (each independently so one failure doesn't block others)
        tag_findings = []
        drift_results = results["drift_detection"]
        schedule_findings = []
        lifecycle_findings = []

        try:
            print("Step 4: Running tag compliance scan...")
            tag_findings = scan_tag_compliance(included_resources, REQUIRED_TAGS)
            results["tag_compliance"]["findings"] = tag_findings
        except Exception as e:
            results["errors"].append(f"Tag compliance scan failed: {e}")
            print(f"  Tag compliance scan error (non-fatal): {e}")

        try:
            print("Step 5: Running drift detection...")
            drift_results = scan_drift(included_resources, all_managed_arns, exclusion_engine)
            lambda_region = os.environ.get("AWS_REGION", "")
            for entry, error in unreadable_states:
                ssm_name = entry.get("_ssm_name", "")
                registry_arn = f"arn:aws:ssm:{lambda_region}:{account_id}:parameter/{ssm_name.lstrip('/')}"
                drift_results.setdefault("findings", []).append(
                    unreadable_state_finding(entry, registry_arn, lambda_region, error)
                )
            results["drift_detection"] = drift_results
        except Exception as e:
            results["errors"].append(f"Drift detection failed: {e}")
            print(f"  Drift detection error (non-fatal): {e}")

        try:
            print("Step 6: Running schedule compliance scan...")
            schedule_findings = scan_schedule_compliance(included_resources)
            results["schedule_compliance"]["findings"] = schedule_findings
        except Exception as e:
            results["errors"].append(f"Schedule compliance scan failed: {e}")
            print(f"  Schedule compliance error (non-fatal): {e}")

        try:
            print("Step 7: Running lifecycle compliance scan...")
            lifecycle_findings = scan_lifecycle_compliance(included_resources)
            results["lifecycle_compliance"]["findings"] = lifecycle_findings
        except Exception as e:
            results["errors"].append(f"Lifecycle compliance scan failed: {e}")
            print(f"  Lifecycle compliance error (non-fatal): {e}")

        # Step 8: Multi-state report (reuses discovery from step 3)
        try:
            results["multi_state_detection"] = multi_state
            state_count = len(multi_state.get("state_files", []))
            lock_count = len(multi_state.get("lock_tables", []))
            print(f"Step 8: Multi-state summary: {state_count} state files, {lock_count} lock tables")

            # Step 9: Cross-reference other state files with discovered resources
            if multi_state.get("state_files"):
                print("Step 9: Analyzing other Terraform state files...")
                # Ownership follows IaC-registry registration before bucket, so a
                # sister repo sharing our state bucket is not reported as ours.
                registry_state_paths = {}
                for entry in iac_registry:
                    loc = entry.get("state_location", {}) or {}
                    e_bucket, e_key = loc.get("bucket", ""), loc.get("key", "")
                    if e_bucket and e_key:
                        registry_state_paths[f"{e_bucket}/{e_key}"] = entry.get("repo_url", "")
                state_analyses = analyze_discovered_states(
                    multi_state["state_files"],
                    resources,
                    known_state_bucket=TF_STATE_BUCKET,
                    registry_state_paths=registry_state_paths,
                    own_repo_url=REPOSITORY_URL,
                )
                results["other_iac_analysis"] = state_analyses
                for sa in state_analyses:
                    print(
                        f"  {sa['state_path']}: {sa.get('visible_count', 0)} visible resources, "
                        f"${sa.get('total_cost', 0):.2f}/mo"
                    )
        except Exception as e:
            results["errors"].append(f"Multi-state analysis failed: {e}")
            print(f"  Multi-state analysis error (non-fatal): {e}")

        # Step 10: Annotate drift findings managed by external state files.
        # Resources in other repos' TF state are real drift from THIS repo's
        # perspective, but we label them so the dashboard can distinguish them.
        try:
            external_arns = {}  # arn -> state_path
            for sa in results.get("other_iac_analysis", []):
                if sa.get("is_ours"):
                    continue  # Our workspaces are already loaded into tf_arns
                state_path = sa.get("state_path", "")
                for overlap in sa.get("overlapping", []):
                    overlap_arn = overlap.get("arn", "")
                    if overlap_arn:
                        external_arns[overlap_arn] = state_path
            if external_arns:
                ext_annotated = 0
                for f in drift_results.get("findings", []):
                    f_arn = f.get("resource_arn", "")
                    if f_arn in external_arns:
                        f["external_iac"] = True
                        f["external_iac_state"] = external_arns[f_arn]
                        ext_annotated += 1
                if ext_annotated:
                    print(f"  Annotated {ext_annotated} drift findings as managed by external IaC")

            # Enrich with IaC registry attribution (repo URL, IaC type)
            if external_managed:
                registry_annotated = 0
                for f in drift_results.get("findings", []):
                    f_arn = f.get("resource_arn", "")
                    if f_arn in external_managed:
                        info = external_managed[f_arn]
                        f["external_iac"] = True
                        f["managed_by_repo"] = info.get("repo_url", "")
                        f["managed_by_iac_type"] = info.get("iac_type", "")
                        f["managed_by_source"] = info.get("source", "")
                        f["managed_by_repo_slug"] = info.get("repo_slug", "")
                        registry_annotated += 1
                if registry_annotated:
                    print(f"  Annotated {registry_annotated} findings with IaC registry attribution")
        except Exception as e:
            print(f"  External IaC annotation error (non-fatal): {e}")

        # Add IaC registry summary to report
        if iac_registry:
            registry_summary = []
            for entry in iac_registry:
                slug = entry.get("repo_slug", "")
                loc = entry.get("state_location", {})

                if entry.get("iac_type") == "terraform" and loc.get("type") == "s3_tfstate":
                    # Terraform repos: count from per-workspace state data
                    state_path = f"{loc.get('bucket', '')}/{loc.get('key', '')}"
                    if slug == repo_slug(require_env("REPOSITORY_URL")):
                        repo_resources = len(tf_arns)
                    else:
                        repo_resources = tf_arns_by_workspace.get(state_path, 0)
                else:
                    # Non-Terraform repos (CFN/Pulumi): count from external_managed
                    repo_resources = sum(1 for _arn, info in external_managed.items() if info.get("repo_slug") == slug)

                registry_summary.append(
                    {
                        "repo_slug": slug,
                        "repo_url": entry.get("repo_url", ""),
                        "iac_type": entry.get("iac_type", ""),
                        "last_deploy": entry.get("last_deploy", ""),
                        "owner": entry.get("owner"),
                        "contact": entry.get("contact"),
                        "resource_count": repo_resources,
                    }
                )
            results["iac_registry"] = {
                "schema_version": "1.0",
                "entries": registry_summary,
                "total_external_managed": len(external_managed),
            }

        # Aggregate findings
        all_findings = tag_findings + drift_results.get("findings", []) + schedule_findings + lifecycle_findings

        # Annotate protected resources — still flagged for visibility, but
        # marked so hunterkiller and dashboard can distinguish them
        protected_config = _load_protected_resources()
        protected_count = 0
        if protected_config:
            for f in all_findings:
                match = _match_protected(f.get("resource_arn", ""), protected_config, account_id)
                if match:
                    f["protected"] = True
                    f["protection_category"] = match.get("category", "")
                    f["protection_reason"] = match.get("reason", "")
                    f["protection_name"] = match.get("name", "")
                    protected_count += 1
            results["protected_resources"] = {
                "config": protected_config,
                "findings_annotated": protected_count,
            }
            print(f"  Annotated {protected_count} findings as protected")

        results["summary"]["total_findings"] = len(all_findings)
        for f in all_findings:
            severity = f.get("severity", "LOW")
            results["summary"]["findings_by_severity"][severity] = (
                results["summary"]["findings_by_severity"].get(severity, 0) + 1
            )
        results["summary"]["findings_by_category"] = {
            "tag_compliance": len(tag_findings),
            "drift_detection": len(drift_results.get("findings", [])),
            "schedule_compliance": len(schedule_findings),
            "lifecycle_compliance": len(lifecycle_findings),
        }

    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        print(f"Error during scan: {error_msg}")
        results["errors"].append(error_msg)
        # The scan stopped part-way: this report does not describe the account.
        results["scan_complete"] = False

    results.setdefault("scan_complete", True)

    # Record duration
    results["scan_duration_seconds"] = round(time.time() - start_time, 1)
    print(f"Scan completed in {results['scan_duration_seconds']}s")

    # Step 6b: Look up resource termination events from CloudTrail
    # Used by the dashboard to calculate accurate prorated costs for destroyed resources
    try:
        print("Looking up CloudTrail termination events...")
        termination_events = _get_termination_events(scan_time)
        results["termination_events"] = termination_events
        print(f"  Found {len(termination_events)} termination events this month")
    except Exception as e:
        print(f"  CloudTrail lookup failed (non-fatal): {e}")
        results["termination_events"] = []

    # Self-check: validate report against the shared schema contract
    schema_issues = validate_report(results)
    if schema_issues:
        print(f"WARNING: Report schema validation found {len(schema_issues)} issue(s):")
        for issue in schema_issues[:10]:
            print(f"  - {issue}")
        results["errors"].append(f"Schema validation: {len(schema_issues)} issues")

    # Step 7: Generate and upload reports (each step isolated so one failure
    # doesn't prevent the others from completing)
    date_prefix = scan_time.strftime("%Y-%m-%d")
    s3 = boto3.client("s3")

    # When the account was last scanned completely: this scan, or the value the
    # previous report carried. A reader uses it to tell a clean account from a
    # scanner that has stopped producing complete scans.
    results["last_successful_scan_at"] = _last_successful_scan_at(results, s3, date_prefix)
    report_stored = not REPORTS_BUCKET

    if REPORTS_BUCKET:
        # JSON report
        try:
            json_report = generate_json_report(results)
            s3.put_object(
                Bucket=REPORTS_BUCKET,
                Key=f"reports/{date_prefix}/compliance-report.json",
                Body=json_report,
                ContentType="application/json",
            )
            print(f"JSON report uploaded to s3://{REPORTS_BUCKET}/reports/{date_prefix}/compliance-report.json")
            report_stored = True

            # Save monthly baseline — first report of each month is preserved
            # so the dashboard can detect ALL resources destroyed this month.
            month_prefix = scan_time.strftime("%Y-%m")
            baseline_key = f"baselines/{month_prefix}.json"
            try:
                s3.head_object(Bucket=REPORTS_BUCKET, Key=baseline_key)
            except s3.exceptions.ClientError:
                # No baseline yet for this month — save one
                s3.put_object(
                    Bucket=REPORTS_BUCKET,
                    Key=baseline_key,
                    Body=json_report,
                    ContentType="application/json",
                )
                print(f"Monthly baseline saved to s3://{REPORTS_BUCKET}/{baseline_key}")
        except Exception as e:
            error_msg = f"JSON report upload failed: {e}\n{traceback.format_exc()}"
            print(error_msg)
            results["errors"].append(error_msg)

        # DynamoDB report (for dashboard — fast, consistent reads)
        if REPORT_TABLE:
            try:
                from reporters.dynamodb_report import write_report_to_dynamodb

                write_report_to_dynamodb(REPORT_TABLE, results["generated_at"], results)
            except Exception as e:
                error_msg = f"DynamoDB report write failed: {e}\n{traceback.format_exc()}"
                print(error_msg)
                results["errors"].append(error_msg)

        # Markdown report
        try:
            md_report = generate_markdown_report(results)
            s3.put_object(
                Bucket=REPORTS_BUCKET,
                Key=f"reports/{date_prefix}/compliance-report.md",
                Body=md_report,
                ContentType="text/markdown",
            )
            print(f"Markdown report uploaded to s3://{REPORTS_BUCKET}/reports/{date_prefix}/compliance-report.md")
        except Exception as e:
            error_msg = f"Markdown report upload failed: {e}\n{traceback.format_exc()}"
            print(error_msg)
            results["errors"].append(error_msg)

    # Compute 24-hour delta (load yesterday's report for comparison)
    delta = None
    if REPORTS_BUCKET:
        try:
            from delta import compute_delta

            yesterday_report = _load_previous_report(s3, REPORTS_BUCKET, date_prefix)
            if yesterday_report:
                delta = compute_delta(results, yesterday_report)
                ds = delta["summary"]
                print(
                    f"Delta: +{ds['new_count']} new, -{ds['resolved_count']} resolved, "
                    f"burn delta: {ds['burn_delta']:+.0f}/mo"
                )
        except Exception as e:
            print(f"Warning: delta computation failed (non-fatal): {e}")

    # HTML report (same styled report that gets emailed, includes delta)
    if REPORTS_BUCKET:
        try:
            from reporters.html_email import _build_full_html_report

            html_report = _build_full_html_report(results, REPORTS_BUCKET, date_prefix, delta=delta)
            s3.put_object(
                Bucket=REPORTS_BUCKET,
                Key=f"reports/{date_prefix}/compliance-report.html",
                Body=html_report,
                ContentType="text/html",
            )
            print(f"HTML report uploaded to s3://{REPORTS_BUCKET}/reports/{date_prefix}/compliance-report.html")
        except Exception as e:
            error_msg = f"HTML report upload failed: {e}\n{traceback.format_exc()}"
            print(error_msg)
            results["errors"].append(error_msg)

    # HTML email via SES
    try:
        if SES_SENDER_EMAIL and SES_RECIPIENT_EMAILS:
            send_html_email(results, SES_SENDER_EMAIL, SES_RECIPIENT_EMAILS, REPORTS_BUCKET, date_prefix, delta=delta)
    except Exception as e:
        error_msg = f"SES email failed: {e}\n{traceback.format_exc()}"
        print(error_msg)
        results["errors"].append(error_msg)

    # Step 10: Invoke hunterkiller (warn + reconcile stale warnings)
    hunterkiller_result = None
    if HUNTERKILLER_FUNCTION_NAME:
        try:
            has_high = results["summary"]["findings_by_severity"].get("HIGH", 0) > 0
            print(f"Step 10: Invoking hunterkiller ({HUNTERKILLER_FUNCTION_NAME}) in warn mode...")
            lambda_client = boto3.client("lambda")
            hk_payload = {
                "mode": "warn",
                "scan_date": scan_time.strftime("%Y-%m-%d"),
            }
            hk_response = lambda_client.invoke(
                FunctionName=HUNTERKILLER_FUNCTION_NAME,
                InvocationType="Event",  # Async — don't wait for completion
                Payload=json.dumps(hk_payload),
            )
            print(f"  Hunterkiller invoked (async): StatusCode={hk_response['StatusCode']}")
            hunterkiller_result = "invoked" if has_high else "invoked (reconcile only)"
        except Exception as e:
            print(f"  Hunterkiller invocation failed (non-fatal): {e}")
            results["errors"].append(f"Hunterkiller invocation failed: {e}")
            hunterkiller_result = f"error: {e}"

    scan_succeeded = bool(results["scan_complete"] and report_stored)
    _emit_scan_metric(scan_succeeded)

    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "scan_succeeded": scan_succeeded,
                "last_successful_scan_at": results["last_successful_scan_at"],
                "resources_discovered": results["summary"]["total_resources_discovered"],
                "total_findings": results["summary"]["total_findings"],
                "findings_by_severity": results["summary"]["findings_by_severity"],
                "scan_duration_seconds": results["scan_duration_seconds"],
                "errors": len(results["errors"]),
                "hunterkiller": hunterkiller_result,
            }
        ),
    }


def _last_successful_scan_at(results, s3, date_prefix):
    """Time of the last complete scan: this one, else what the previous report recorded."""
    if results.get("scan_complete"):
        return results["generated_at"]
    if not REPORTS_BUCKET:
        return ""
    try:
        previous = _load_previous_report(s3, REPORTS_BUCKET, date_prefix) or {}
    except Exception as e:
        print(f"  Could not read the previous report for the last successful scan time: {e}")
        return ""
    if "last_successful_scan_at" in previous:
        return previous["last_successful_scan_at"] or ""
    # A report written before this field existed: complete unless it says otherwise.
    return previous.get("generated_at", "") if previous.get("scan_complete", True) else ""


def _emit_scan_metric(succeeded):
    """Write the scan outcome as CloudWatch metrics (embedded metric format).

    ScanSucceeded and ScanFailed are read from the function's log, so no
    metrics permission is needed. The module's alarms watch them: a scan that
    fails, or no successful scan for the configured number of hours.
    """
    function_name = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "compliance-scanner")
    print(
        json.dumps(
            {
                "_aws": {
                    "Timestamp": int(time.time() * 1000),
                    "CloudWatchMetrics": [
                        {
                            "Namespace": SCAN_METRIC_NAMESPACE,
                            "Dimensions": [["FunctionName"]],
                            "Metrics": [
                                {"Name": "ScanSucceeded", "Unit": "Count"},
                                {"Name": "ScanFailed", "Unit": "Count"},
                            ],
                        }
                    ],
                },
                "FunctionName": function_name,
                "ScanSucceeded": 1 if succeeded else 0,
                "ScanFailed": 0 if succeeded else 1,
            }
        )
    )


def _load_previous_report(s3, bucket, current_date):
    """Load the most recent report before current_date from S3."""
    response = s3.list_objects_v2(Bucket=bucket, Prefix="reports/", Delimiter="/")
    dates = []
    for p in response.get("CommonPrefixes", []):
        parts = p["Prefix"].strip("/").split("/")
        if len(parts) >= 2:
            dates.append(parts[1])
    dates.sort(reverse=True)

    for d in dates:
        if d < current_date:
            key = f"reports/{d}/compliance-report.json"
            obj = s3.get_object(Bucket=bucket, Key=key)
            return json.loads(obj["Body"].read().decode("utf-8"))
    return None


def _load_protected_resources():
    """Load protected resources config from SSM Parameter Store.

    Returns the parsed config dict or None if not configured.
    Reads fresh from SSM every invocation — no caching.
    """
    if not PROTECTED_RESOURCES_SSM:
        return None
    try:
        ssm = boto3.client("ssm")
        resp = ssm.get_parameter(Name=PROTECTED_RESOURCES_SSM)
        return json.loads(resp["Parameter"]["Value"])
    except Exception as e:
        print(f"Warning: could not load protected resources from SSM: {e}")
        return None


def _match_protected(arn, config, account_id):
    """Check if an ARN matches any protected resource pattern.

    Returns the matching protected resource entry or None.
    Supports * wildcard in the account-ID position of ARN patterns.
    """
    if not arn or not config:
        return None

    for entry in config.get("resources", []):
        pattern = entry.get("arn_pattern", "")
        if not pattern:
            continue
        # Resolve * wildcard in account-ID position
        resolved = pattern.replace("*", account_id) if "*" in pattern else pattern
        if arn == resolved or arn.startswith(resolved):
            return entry
    return None
