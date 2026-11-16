"""
Multi-State Detector

Scans S3 buckets and DynamoDB tables for traces of Terraform state files
and lock tables beyond the known primary state. Detects evidence of other
Terraform management repos or forgotten infrastructure-as-code.

Signals to look for:
  - S3 objects with .tfstate extension or containing "terraform" in key
  - S3 buckets with names containing "tfstate", "terraform-state", or "tf-state"
  - DynamoDB tables with a "LockID" hash key (Terraform's lock table schema)
  - DynamoDB table names containing "tf-lock", "terraform-lock", or "tfstate"
"""

import re

import boto3
from compliance_schema import same_repository

# Patterns that suggest a bucket holds Terraform state
_STATE_BUCKET_PATTERNS = [
    re.compile(r"tfstate", re.IGNORECASE),
    re.compile(r"terraform.?state", re.IGNORECASE),
    re.compile(r"tf.?state", re.IGNORECASE),
]

# Patterns for state file keys within a bucket
_STATE_KEY_PATTERNS = [
    re.compile(r"\.tfstate$"),
    re.compile(r"\.tfstate\.backup$"),
    re.compile(r"terraform\.tfstate"),
    re.compile(r"env:/"),  # Terraform workspace prefix
]

# Patterns that suggest a DynamoDB table is a Terraform lock table
_LOCK_TABLE_PATTERNS = [
    re.compile(r"tf.?lock", re.IGNORECASE),
    re.compile(r"terraform.?lock", re.IGNORECASE),
    re.compile(r"tfstate", re.IGNORECASE),
    re.compile(r"terraform.?state", re.IGNORECASE),
]


def scan_multi_state(known_state_bucket, known_state_key):
    """
    Scan the account for Terraform state files and lock tables.

    Args:
        known_state_bucket: The known/expected TF state bucket (to exclude from findings)
        known_state_key: The known/expected TF state key (to exclude from findings)

    Returns:
        dict with state_files, lock_tables, and notes lists
    """
    state_files = []
    lock_tables = []
    notes = []

    # Scan S3 for state files
    try:
        s3_findings = _scan_s3_for_state(known_state_bucket, known_state_key)
        state_files.extend(s3_findings)
    except Exception as e:
        notes.append(f"S3 scan error: {e}")

    # Scan DynamoDB for lock tables
    try:
        dynamo_findings = _scan_dynamodb_for_locks(known_state_bucket)
        lock_tables.extend(dynamo_findings)
    except Exception as e:
        notes.append(f"DynamoDB scan error: {e}")

    if not state_files and not lock_tables:
        notes.append("No additional Terraform state traces found beyond the known state.")

    return {
        "state_files": state_files,
        "lock_tables": lock_tables,
        "notes": notes,
    }


def analyze_discovered_states(
    state_files, discovered_resources, known_state_bucket="", registry_state_paths=None, own_repo_url=""
):
    """
    Read each discovered state file and cross-reference with discovered resources.

    Args:
        state_files: List of discovered state file dicts from scan_multi_state
        discovered_resources: List of all discovered resource dicts
        known_state_bucket: Our own TF state bucket name
        registry_state_paths: {"bucket/key": repo_url} from the IaC registry. A
            state file another repo has registered is that repo's, even when it
            shares our bucket — sharing a bucket is a backend convenience, not
            ownership. Without this, another repository's state reads as "our
            workspace, managed by this repo" purely because its key sits in
            this repository's bucket.
        own_repo_url: This repo's URL, so its own registration still reads as ours

    Returns list of analysis dicts:
      {state_path, last_modified, managed_count, overlapping: [{arn, type, name, cost}],
       total_cost, recommendation, is_ours: bool}
    """
    from utils.state_parser import load_terraform_state_arns

    # Build lookup from discovered resources by ARN
    resource_by_arn = {}
    for r in discovered_resources:
        arn = r.get("arn", "")
        if arn:
            resource_by_arn[arn] = r

    registry_state_paths = registry_state_paths or {}

    analyses = []
    for sf in state_files:
        bucket = sf.get("bucket", "")
        key = sf.get("key", "")
        state_path = f"s3://{bucket}/{key}"
        claimed_by = registry_state_paths.get(f"{bucket}/{key}", "")
        # Ownership follows registration, then bucket. A sister repo that has
        # registered this state owns it regardless of which bucket it lives in.
        if claimed_by:
            is_ours = same_repository(claimed_by, own_repo_url)
        else:
            is_ours = bucket == known_state_bucket

        try:
            managed_arns = load_terraform_state_arns(bucket, key)
        except Exception as e:
            analyses.append(
                {
                    "state_path": state_path,
                    "last_modified": sf.get("last_modified", ""),
                    "error": str(e),
                    "managed_count": 0,
                    "overlapping": [],
                    "total_cost": 0,
                    "recommendation": "Unable to read state file",
                    "is_ours": is_ours,
                }
            )
            continue

        # Cross-reference with discovered resources
        overlapping = []
        total_cost = 0.0
        for arn in managed_arns:
            if arn in resource_by_arn:
                r = resource_by_arn[arn]
                cost = r.get("estimated_monthly_cost") or 0
                overlapping.append(
                    {
                        "arn": arn,
                        "resource_type": r.get("resource_type", ""),
                        "resource_name": r.get("resource_name", ""),
                        "estimated_monthly_cost": cost,
                        "region": r.get("region", ""),
                    }
                )
                total_cost += cost

        # Sort by cost descending
        overlapping.sort(key=lambda x: -(x.get("estimated_monthly_cost") or 0))

        # Generate recommendation based on ownership
        if is_ours:
            workspace = key.replace("terraform.tfstate", "").strip("/")
            rec = f"Our workspace ({workspace}). Managed by this repo."
        elif claimed_by:
            rec = f"Registered IaC. Managed by {claimed_by}."
        elif not overlapping:
            rec = "State file exists but manages no currently visible resources. May be stale."
        elif total_cost > 50:
            rec = "Active resources with significant cost. Identify owning team or import into primary IaC."
        elif total_cost > 0:
            rec = "Active resources with low cost. Consider importing or scheduling for teardown."
        else:
            rec = "Manages resources with no direct cost. Review for consolidation."

        analyses.append(
            {
                "state_path": state_path,
                "last_modified": sf.get("last_modified", ""),
                "managed_count": len(managed_arns),
                "visible_count": len(overlapping),
                "overlapping": overlapping,
                "total_cost": round(total_cost, 2),
                "recommendation": rec,
                "is_ours": is_ours,
            }
        )

    return analyses


def _scan_s3_for_state(known_bucket, known_key):
    """Scan S3 buckets for Terraform state files."""
    s3 = boto3.client("s3")
    findings = []

    try:
        buckets = s3.list_buckets().get("Buckets", [])
    except Exception as e:
        print(f"  Failed to list S3 buckets: {e}")
        return findings

    for bucket in buckets:
        bucket_name = bucket["Name"]

        # Check if this bucket's name suggests it holds TF state
        name_matches = any(p.search(bucket_name) for p in _STATE_BUCKET_PATTERNS)

        if name_matches:
            # Search this bucket for .tfstate files
            try:
                state_objects = _list_state_objects(s3, bucket_name)
                for obj in state_objects:
                    key = obj.get("Key", "")
                    # Skip the known state file
                    if bucket_name == known_bucket and key == known_key:
                        continue
                    # Skip .tfstate.backup files in the known bucket
                    if bucket_name == known_bucket and key.startswith(known_key):
                        continue
                    findings.append(
                        {
                            "bucket": bucket_name,
                            "key": key,
                            "size_bytes": obj.get("Size", 0),
                            "last_modified": str(obj.get("LastModified", "")),
                        }
                    )
            except Exception as e:
                print(f"  Failed to list objects in {bucket_name}: {e}")

    return findings


def _list_state_objects(s3, bucket_name):
    """List objects in a bucket that look like Terraform state files."""
    state_objects = []
    paginator = s3.get_paginator("list_objects_v2")

    try:
        for page in paginator.paginate(Bucket=bucket_name, MaxKeys=1000):
            for obj in page.get("Contents", []):
                key = obj.get("Key", "")
                if any(p.search(key) for p in _STATE_KEY_PATTERNS):
                    state_objects.append(obj)
    except Exception:
        pass  # Access denied or empty bucket

    return state_objects


def _scan_dynamodb_for_locks(known_state_bucket):
    """Scan DynamoDB tables for Terraform lock tables."""
    dynamodb = boto3.client("dynamodb")
    findings = []

    try:
        tables = []
        paginator = dynamodb.get_paginator("list_tables")
        for page in paginator.paginate():
            tables.extend(page.get("TableNames", []))
    except Exception as e:
        print(f"  Failed to list DynamoDB tables: {e}")
        return findings

    for table_name in tables:
        # Check if table name matches lock table patterns
        name_matches = any(p.search(table_name) for p in _LOCK_TABLE_PATTERNS)

        if not name_matches:
            # Also check if the table has a LockID hash key (Terraform convention)
            try:
                desc = dynamodb.describe_table(TableName=table_name)
                key_schema = desc.get("Table", {}).get("KeySchema", [])
                has_lock_id = any(k.get("AttributeName") == "LockID" and k.get("KeyType") == "HASH" for k in key_schema)
                if has_lock_id:
                    name_matches = True
            except Exception:
                continue

        if name_matches:
            try:
                desc = dynamodb.describe_table(TableName=table_name)
                table = desc.get("Table", {})
                region = dynamodb.meta.region_name
                findings.append(
                    {
                        "table_name": table_name,
                        "region": region,
                        "item_count": table.get("ItemCount", 0),
                        "arn": table.get("TableArn", ""),
                        "status": table.get("TableStatus", ""),
                    }
                )
            except Exception:
                pass

    return findings
