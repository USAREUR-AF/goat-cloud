"""Detect resources managed by Pulumi (S3 backend).

Reads Pulumi checkpoint files from S3, extracts AWS ARNs from resource
outputs, and attributes them to their source repo via the IaC registry.
"""

import boto3
from utils.state_parser import read_json_object


def detect_pulumi_managed_resources(registry_entries, account_id):
    """Detect resources managed by Pulumi repos using S3 backend.

    For each registry entry with iac_type='pulumi' and
    state_location.type='pulumi_s3', reads the Pulumi checkpoint and
    extracts managed AWS ARNs.

    Args:
        registry_entries: Registry entries with iac_type='pulumi'
        account_id: AWS account ID (for filtering non-AWS resources)

    Returns:
        dict: {arn: {repo_url, iac_type, source, repo_slug}}
    """
    s3 = boto3.client("s3")
    managed_resources = {}

    for entry in registry_entries:
        loc = entry.get("state_location", {})
        if loc.get("type") != "pulumi_s3":
            print(
                f"WARNING: Pulumi entry {entry.get('repo_slug')} has unsupported "
                f"state_location.type: {loc.get('type')} (only pulumi_s3 supported)"
            )
            continue

        bucket = loc.get("bucket", "")
        project = loc.get("project", "")
        environment = entry.get("environment", "dev")

        if not bucket or not project:
            print(f"WARNING: Pulumi entry {entry.get('repo_slug')} missing bucket or project in state_location")
            continue

        try:
            arns = _load_pulumi_state(s3, bucket, project, environment)
            for arn in arns:
                managed_resources[arn] = {
                    "repo_url": entry.get("repo_url", ""),
                    "iac_type": "pulumi",
                    "source": f"pulumi:{project}",
                    "repo_slug": entry.get("repo_slug", project),
                }
            print(f"Pulumi {entry.get('repo_slug')}: found {len(arns)} managed resources")
        except Exception as e:
            print(f"WARNING: Failed to load Pulumi state for {entry.get('repo_slug')}: {e}")

    print(f"Pulumi detection: {len(managed_resources)} total managed resources")
    return managed_resources


def _load_pulumi_state(s3, bucket, project, stack):
    """Load a Pulumi checkpoint from S3 and extract AWS ARNs.

    Pulumi S3 backend stores state at:
    .pulumi/stacks/{project}/{stack}.json

    Returns a set of AWS ARN strings.
    """
    key = f".pulumi/stacks/{project}/{stack}.json"

    try:
        state = read_json_object(s3, bucket, key)
    except s3.exceptions.NoSuchKey:
        print(f"Pulumi state not found: s3://{bucket}/{key}")
        return set()
    except Exception as e:
        print(f"Failed to read Pulumi state s3://{bucket}/{key}: {e}")
        return set()

    return _extract_arns_from_checkpoint(state)


def _extract_arns_from_checkpoint(state):
    """Walk a Pulumi checkpoint and extract AWS ARNs from resource outputs.

    Pulumi checkpoint format (v3):
    {
        "version": 3,
        "checkpoint": {
            "stack": "dev",
            "latest": {
                "resources": [
                    {
                        "urn": "urn:pulumi:dev::project::aws:lambda/function:Function::name",
                        "type": "aws:lambda/function:Function",
                        "outputs": {
                            "arn": "arn:aws:lambda:...",
                            "id": "function-name"
                        }
                    }
                ]
            }
        }
    }
    """
    arns = set()

    # Navigate to resources list
    checkpoint = state.get("checkpoint", state)  # v3 wraps in checkpoint key
    latest = checkpoint.get("latest", {})
    resources = latest.get("resources", [])

    for resource in resources:
        resource_type = resource.get("type", "")

        # Only process AWS resources
        if not resource_type.startswith("aws:"):
            continue

        # Skip provider resources (aws:index:...)
        if resource_type.startswith("aws:index:"):
            continue

        outputs = resource.get("outputs", {})

        # Strategy 1: Direct ARN from outputs (most reliable)
        arn = outputs.get("arn")
        if arn and isinstance(arn, str) and arn.startswith("arn:aws:"):
            arns.add(arn)
            continue

        # Strategy 2: For S3 buckets, construct from bucket name
        if resource_type == "aws:s3/bucket:Bucket" or resource_type == "aws:s3/bucketV2:BucketV2":
            bucket_name = outputs.get("bucket") or outputs.get("id")
            if bucket_name:
                arns.add(f"arn:aws:s3:::{bucket_name}")
                continue

        # Strategy 3: For resources where ID is useful, log but skip
        # (most AWS Pulumi resources include 'arn' in outputs)
        resource_id = outputs.get("id")
        if resource_id and isinstance(resource_id, str) and resource_id.startswith("arn:aws:"):
            arns.add(resource_id)

    return arns
