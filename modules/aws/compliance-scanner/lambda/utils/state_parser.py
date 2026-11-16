"""
Terraform State Parser

Reads Terraform state from S3 and extracts all managed resource ARNs.
Used by the drift detector to identify click-ops resources.
"""

import json
import os

import boto3

# Largest state object the scanner reads into memory. A state file is named by
# a registry entry, so its size is not under the scanner's control.
MAX_STATE_BYTES = int(os.environ.get("MAX_STATE_BYTES", str(50 * 1024 * 1024)))


class StateTooLarge(Exception):
    """A state object is larger than MAX_STATE_BYTES."""


def read_json_object(s3, bucket, key, max_bytes=None):
    """Read and parse a JSON object from S3, refusing one above the size limit."""
    limit = MAX_STATE_BYTES if max_bytes is None else max_bytes
    response = s3.get_object(Bucket=bucket, Key=key)
    size = response.get("ContentLength")
    if size is not None and size > limit:
        raise StateTooLarge(f"{size} bytes exceeds the {limit} byte limit")
    body = response["Body"].read(limit + 1)
    if len(body) > limit:
        raise StateTooLarge(f"object exceeds the {limit} byte limit")
    return json.loads(body.decode("utf-8"))


class StateUnreadable(Exception):
    """A state object could not be read or parsed."""


def load_terraform_state_arns(bucket, key, strict=False):
    """
    Download Terraform state from S3 and extract all managed ARNs.

    Returns:
        set: All ARNs found in the Terraform state

    With strict=True an unreadable state raises StateUnreadable instead of
    returning an empty set, so the caller can tell "manages nothing" from
    "could not be read".
    """
    s3 = boto3.client("s3")

    try:
        state = read_json_object(s3, bucket, key)
        if not isinstance(state, dict):
            raise ValueError("state is not a JSON object")
    except Exception as e:
        print(f"  Failed to load TF state from s3://{bucket}/{key}: {e}")
        if strict:
            raise StateUnreadable(f"{type(e).__name__}: {e}") from e
        return set()

    arns = set()
    _extract_arns_from_state(state, arns)

    return arns


def _extract_arns_from_state(state, arns):
    """Recursively extract ARNs from Terraform state structure."""
    version = state.get("version", 0)

    if version >= 4:
        # Terraform state v4 format
        for resource in state.get("resources", []):
            for instance in resource.get("instances", []):
                attrs = instance.get("attributes", {})
                _extract_arns_from_attributes(attrs, arns)
    elif version == 3:
        # Terraform state v3 format
        for module in state.get("modules", []):
            for _name, resource in module.get("resources", {}).items():
                primary = resource.get("primary", {})
                attrs = primary.get("attributes", {})
                _extract_arns_from_attributes(attrs, arns)


def _extract_arns_from_attributes(attrs, arns):
    """Extract ARN-like values from resource attributes."""
    # Common ARN attribute names
    arn_keys = [
        "arn",
        "id",
        "cluster_arn",
        "function_arn",
        "role_arn",
        "topic_arn",
        "queue_arn",
        "bucket_arn",
        "table_arn",
        "log_group_arn",
        "certificate_arn",
        "target_group_arn",
        "listener_arn",
        "security_group_id",
        "subnet_id",
        "vpc_id",
        "instance_id",
        "volume_id",
        "allocation_id",
        "nat_gateway_id",
        "file_system_id",
        "cache_cluster_id",
        "db_instance_identifier",
        "repository_url",
    ]

    for key in arn_keys:
        value = attrs.get(key, "")
        if isinstance(value, str) and value:
            if value.startswith("arn:"):
                # Normalize: strip trailing :* from log group ARNs
                normalized = value.rstrip("*").rstrip(":")
                arns.add(normalized)
                # Secrets Manager: store prefixes without random suffixes
                # so drift detector can match by prefix.
                # Two levels: AWS appends -XXXXXX, and TF random_id adds
                # -HEXHEXHEX before that. Store both single and double-stripped.
                if ":secret:" in normalized:
                    secret_part = normalized.split(":secret:")[-1]
                    if "-" in secret_part:
                        single = normalized.rsplit("-", 1)[0]
                        arns.add(single)
                        # Double-strip: also remove random_id hex prefix
                        double_part = single.split(":secret:")[-1]
                        if "-" in double_part:
                            arns.add(single.rsplit("-", 1)[0])
            elif _looks_like_resource_id(key, value):
                # Store raw IDs too for matching (sg-xxx, subnet-xxx, etc.)
                arns.add(value)

    # Also check nested attributes for ARNs
    for _key, value in attrs.items():
        if isinstance(value, str) and value.startswith("arn:"):
            normalized = value.rstrip("*").rstrip(":")
            arns.add(normalized)


def _looks_like_resource_id(key, value):
    """Check if a value looks like an AWS resource ID."""
    prefixes = [
        "sg-",
        "subnet-",
        "vpc-",
        "i-",
        "vol-",
        "eipalloc-",
        "nat-",
        "igw-",
        "rtb-",
        "eni-",
        "acl-",
        "fs-",
    ]
    return any(value.startswith(p) for p in prefixes)
