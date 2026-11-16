"""
Drift Detector (Click-Ops Finder)

Compares discovered AWS resources against Terraform state to identify
resources that exist in AWS but are not managed by Terraform.
"""


def scan_drift(resources, tf_arns, exclusion_engine):
    """
    Detect resources not tracked in Terraform state.

    Args:
        resources: List of resource dicts from discovery
        tf_arns: Set of ARNs from Terraform state
        exclusion_engine: ExclusionEngine instance

    Returns:
        Dict with terraform_managed count, unmanaged count, and findings list
    """
    terraform_managed = 0
    unmanaged = 0
    findings = []

    for resource in resources:
        arn = resource.get("arn", "")
        resource_type = resource.get("resource_type", "")
        name = resource.get("name", "")

        # Check if excluded
        excluded, _reason = exclusion_engine.should_exclude(resource)
        if excluded:
            continue

        # Check if in Terraform state (directly or via a TF-managed parent)
        in_state = _is_in_terraform_state(arn, resource, tf_arns)
        if not in_state and resource.get("attached_to"):
            parent = {"arn": resource["attached_to"], "resource_type": "", "name": ""}
            in_state = _is_in_terraform_state(resource["attached_to"], parent, tf_arns)

        if in_state:
            terraform_managed += 1
            continue

        # Not in Terraform state — potential drift
        unmanaged += 1
        confidence = exclusion_engine.classify_confidence(resource, in_state)

        if confidence is None:
            continue

        findings.append(
            {
                "category": "drift_detection",
                "severity": confidence,
                "resource_arn": arn,
                "resource_type": resource_type,
                "resource_name": name,
                "region": resource.get("region", ""),
                "state": resource.get("state"),
                "issue": "Resource exists in AWS but not in Terraform state (possible click-ops)",
                "confidence": confidence,
                "tags": resource.get("tags", {}),
                "created_at": resource.get("created_at"),
                "created_by": resource.get("created_by"),
                "running_duration_days": resource.get("running_duration_days"),
                "estimated_monthly_cost": resource.get("estimated_monthly_cost"),
                "inferred_purpose": resource.get("inferred_purpose"),
                "attached_to": resource.get("attached_to"),
                "recommendation": _recommendation(resource_type, confidence),
            }
        )

    return {
        "terraform_managed": terraform_managed,
        "unmanaged": unmanaged,
        "findings": findings,
    }


def _is_in_terraform_state(arn, resource, tf_arns):
    """Check if a resource is tracked in Terraform state (by ARN or ID)."""
    if not tf_arns:
        return False  # No state loaded, can't determine

    # Direct ARN match
    if arn in tf_arns:
        return True

    # Normalized ARN match (strip trailing :* etc.)
    normalized = arn.rstrip("*").rstrip(":")
    if normalized in tf_arns:
        return True

    # Check by resource ID embedded in ARN
    # e.g., sg-abc123 from arn:aws:ec2:...:security-group/sg-abc123
    resource_id = _extract_id_from_arn(arn)
    if resource_id and resource_id in tf_arns:
        return True

    # Secrets Manager: AWS appends a random 6-char suffix to secret ARNs.
    # TF state stores one suffix, discovery may return a different one.
    # Additionally, TF modules using random_id add an 8-char hex prefix
    # before the AWS suffix (e.g., name-a1b2c3d4-XXXXXX).
    # The state_parser pre-stores both single and double-stripped prefixes.
    resource_type = resource.get("resource_type", "")
    if resource_type == "secretsmanager:secret" and ":secret:" in arn:
        secret_part = arn.split(":secret:")[-1]
        if "-" in secret_part:
            # Single-strip: remove AWS suffix (-XXXXXX)
            single = arn.rsplit("-", 1)[0]
            if single in tf_arns:
                return True
            # Double-strip: also remove random_id hex (-HEXHEXHX)
            double_part = single.split(":secret:")[-1]
            if "-" in double_part:
                double = single.rsplit("-", 1)[0]
                if double in tf_arns:
                    return True

    # Check by name for some resource types
    name = resource.get("name", "")

    # S3 buckets are often stored by name in state
    return bool(resource_type == "s3:bucket" and name and f"arn:aws:s3:::{name}" in tf_arns)


def _extract_id_from_arn(arn):
    """Extract resource ID from the end of an ARN."""
    parts = arn.split(":")
    if len(parts) < 6:
        return None

    resource = parts[-1]
    if "/" in resource:
        return resource.split("/")[-1]
    return None


def _recommendation(resource_type, confidence):
    """Generate actionable recommendation based on resource type and confidence."""
    if confidence == "HIGH":
        if resource_type.startswith("ec2:instance"):
            return "Investigate: running EC2 instance not in Terraform. Import with `terraform import` or terminate if unused."
        if resource_type.startswith("rds:"):
            return "Investigate: RDS instance not in Terraform. This has ongoing cost. Import or decommission."
        return "Investigate: this resource appears to be manually created. Import into Terraform state or remove if unused."

    if confidence == "MEDIUM":
        return "Review: this resource may have been created manually or by another automation tool. Verify ownership."

    return "Low priority: this resource is likely auto-created by AWS. Verify if needed."
