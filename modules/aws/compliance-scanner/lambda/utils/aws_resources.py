"""
AWS Resource Discovery and Enrichment

Two-pass approach:
  1. Resource Groups Tagging API (bulk — all tagged resources in one call)
  2. Service-specific APIs (fill gaps for untagged resources)

Each resource is enriched with:
  - created_at: from resource metadata
  - created_by: from CloudTrail (best-effort, 90-day lookback)
  - running_duration_days: calculated from created_at
  - estimated_monthly_cost: from Cost Explorer service-level data
  - inferred_purpose: from Name tag, type, connections
"""

import contextlib
import json
import os
from datetime import UTC, datetime, timedelta

import boto3
from compliance_schema import PRICE_TABLE_REGION

from utils.ec2_pricing import flush_cache, get_ec2_monthly_cost

# Resource types that have dedicated service-specific scanners.
# Resources of these types found by the tagging API but NOT confirmed
# by the service scanner are filtered out as ghost resources.
_TYPES_WITH_SERVICE_SCANNERS = {
    "ec2:instance",
    "ec2:vpc",
    "ec2:security-group",
    "ec2:volume",
    "ec2:eip",
    "ec2:natgateway",
    "ec2:vpc-endpoint",
    "ecs:cluster",
    "ecs:service",
    "rds:instance",
    "lambda:function",
    "elasticache:cluster",
    "efs:filesystem",
    "ecr:repository",
    "secretsmanager:secret",
    "dynamodb:table",
    "sns:topic",
    "sqs:queue",
    "apigateway:api",
    "logs:log-group",
    "events:rule",
    "acm:certificate",
    "kms:key",
    "ssm:parameter",
    "s3:bucket",
    "iam:role",
    "iam:user",
    "route53:hostedzone",
}


def discover_all_resources(active_regions, account_id):
    """
    Discover all resources across active regions.

    Returns list of resource dicts with standardized fields:
      {arn, resource_type, region, tags, name, created_at, created_by,
       running_duration_days, estimated_monthly_cost, inferred_purpose}
    """
    resources_by_arn = {}

    # Get service-level costs for estimation
    service_costs = get_service_costs()

    # Track ARNs confirmed by service-specific APIs (Pass 2 + global).
    # Used to filter ghost resources from the tagging API.
    service_api_arns = set()

    for region in active_regions:
        print(f"  Scanning region: {region}")

        # Pass 1: Resource Groups Tagging API
        tagged = _discover_tagged_resources(region)
        for r in tagged:
            resources_by_arn[r["arn"]] = r

        # Pass 2: Service-specific APIs — fill gaps and enrich existing resources
        service_resources = _discover_untagged_resources(region, account_id)
        for r in service_resources:
            service_api_arns.add(r["arn"])
            if r["arn"] not in resources_by_arn:
                resources_by_arn[r["arn"]] = r
            else:
                # Merge metadata from service API into tagged resource
                # Service APIs provide state, instance_type, size_gb, etc.
                _merge_metadata(resources_by_arn[r["arn"]], r)

    # Global services (IAM, S3, Route53)
    print("  Scanning global services...")
    global_resources = _discover_global_resources(account_id)
    for r in global_resources:
        service_api_arns.add(r["arn"])
        if r["arn"] not in resources_by_arn:
            resources_by_arn[r["arn"]] = r
        else:
            _merge_metadata(resources_by_arn[r["arn"]], r)

    # Filter ghost resources: tagging API can return deleted resources
    # (especially Secrets Manager secrets) that no longer exist.
    # If a resource type has a service scanner but the scanner didn't
    # find it, the resource is likely a ghost.
    ghost_count = 0
    for arn in list(resources_by_arn):
        r = resources_by_arn[arn]
        if r["resource_type"] in _TYPES_WITH_SERVICE_SCANNERS and arn not in service_api_arns:
            del resources_by_arn[arn]
            ghost_count += 1
    if ghost_count:
        print(f"  Filtered {ghost_count} ghost resource(s) (tagging API only, not confirmed by service API)")

    resources = list(resources_by_arn.values())

    # Enrich with cost estimates
    _enrich_costs(resources, service_costs)

    # Enrich with purpose inference
    for r in resources:
        r["inferred_purpose"] = _infer_purpose(r)

    print(f"  Total unique resources: {len(resources)}")
    return resources


def _discover_tagged_resources(region):
    """Use Resource Groups Tagging API to find all tagged resources."""
    resources = []
    try:
        client = boto3.client("resourcegroupstaggingapi", region_name=region)
        paginator = client.get_paginator("get_resources")

        for page in paginator.paginate():
            for item in page.get("ResourceTagMappingList", []):
                arn = item["ResourceARN"]
                rtype = _type_from_arn(arn)
                tags = {t["Key"]: t["Value"] for t in item.get("Tags", [])}

                # Global services (IAM, etc.) are reported in us-east-1 but
                # are not regional — label them "global" for clarity.
                effective_region = region
                if rtype.startswith("iam:"):
                    effective_region = "global"

                # For RDS snapshots, link to the parent DB instance so the
                # drift detector can inherit compliance from TF-managed parents.
                attached = None
                if rtype == "rds:snapshot":
                    attached = _rds_snapshot_parent_arn(arn)

                resources.append(
                    {
                        "arn": arn,
                        "resource_type": rtype,
                        "region": effective_region,
                        "tags": tags,
                        "name": tags.get("Name", _name_from_arn(arn)),
                        "created_at": None,
                        "created_by": None,
                        "running_duration_days": None,
                        "estimated_monthly_cost": None,
                        "inferred_purpose": None,
                        "attached_to": attached,
                    }
                )
    except Exception as e:
        print(f"    Tagging API error in {region}: {e}")

    return resources


def _rds_snapshot_parent_arn(snapshot_arn):
    """Derive the parent DB instance ARN from an RDS snapshot ARN.

    Automated snapshots: arn:aws:rds:REGION:ACCT:snapshot:rds:db-name-YYYY-MM-DD-HH-MM
    Manual snapshots:    arn:aws:rds:REGION:ACCT:snapshot:my-snapshot-name
    """
    if ":snapshot:" not in snapshot_arn:
        return None
    prefix, snap_id = snapshot_arn.split(":snapshot:", 1)
    # Automated snapshots have an "rds:" prefix in the snapshot identifier
    if not snap_id.startswith("rds:"):
        return None
    # Strip "rds:" and the trailing date: name-YYYY-MM-DD-HH-MM (5 hyphens)
    name_with_date = snap_id[4:]
    parts = name_with_date.rsplit("-", 5)
    if len(parts) < 6:
        return None
    db_name = "-".join(parts[:-5])
    return f"{prefix}:db:{db_name}"


def _discover_untagged_resources(region, account_id):
    """Service-specific APIs to find resources the Tagging API misses."""
    resources = []

    # EC2
    resources.extend(_scan_ec2(region, account_id))
    # ECS
    resources.extend(_scan_ecs(region, account_id))
    # RDS
    resources.extend(_scan_rds(region, account_id))
    # Lambda
    resources.extend(_scan_lambda(region, account_id))
    # ELB
    resources.extend(_scan_elb(region))
    # ElastiCache
    resources.extend(_scan_elasticache(region, account_id))
    # EFS
    resources.extend(_scan_efs(region, account_id))
    # ECR
    resources.extend(_scan_ecr(region, account_id))
    # Secrets Manager
    resources.extend(_scan_secrets_manager(region))
    # DynamoDB
    resources.extend(_scan_dynamodb(region, account_id))
    # SNS
    resources.extend(_scan_sns(region))
    # SQS
    resources.extend(_scan_sqs(region))
    # API Gateway v2
    resources.extend(_scan_apigateway(region, account_id))
    # CloudWatch Log Groups
    resources.extend(_scan_log_groups(region, account_id))
    # EventBridge Rules
    resources.extend(_scan_eventbridge(region, account_id))
    # ACM Certificates
    resources.extend(_scan_acm(region, account_id))
    # KMS Keys
    resources.extend(_scan_kms(region, account_id))
    # SSM Parameters
    resources.extend(_scan_ssm(region, account_id))

    return resources


def _discover_global_resources(account_id):
    """Discover global resources (IAM, S3, Route53)."""
    resources = []

    # S3 Buckets
    try:
        s3 = boto3.client("s3")
        buckets = s3.list_buckets().get("Buckets", [])

        # Resolve bucket regions and collect metadata
        bucket_data = []
        for b in buckets:
            arn = f"arn:aws:s3:::{b['Name']}"
            tags = {}
            try:
                tag_resp = s3.get_bucket_tagging(Bucket=b["Name"])
                tags = {t["Key"]: t["Value"] for t in tag_resp.get("TagSet", [])}
            except Exception:
                pass  # No tags or access denied

            # Get actual bucket region (None means us-east-1)
            bucket_region = "us-east-1"
            try:
                loc = s3.get_bucket_location(Bucket=b["Name"])
                bucket_region = loc.get("LocationConstraint") or "us-east-1"
            except Exception:
                pass

            created = b.get("CreationDate")
            created_str = created.isoformat() if created else None
            duration = (datetime.now(UTC) - created).days if created else None

            bucket_data.append(
                {
                    "arn": arn,
                    "name": b["Name"],
                    "tags": tags,
                    "region": bucket_region,
                    "created_at": created_str,
                    "duration": duration,
                }
            )

        # Batch-query CloudWatch BucketSizeBytes per region
        bucket_sizes = {}
        buckets_by_region = {}
        for bd in bucket_data:
            buckets_by_region.setdefault(bd["region"], []).append(bd["name"])

        for region, names in buckets_by_region.items():
            try:
                cw = boto3.client("cloudwatch", region_name=region)
                queries = [
                    {
                        "Id": f"s{i}",
                        "MetricStat": {
                            "Metric": {
                                "Namespace": "AWS/S3",
                                "MetricName": "BucketSizeBytes",
                                "Dimensions": [
                                    {"Name": "BucketName", "Value": name},
                                    {"Name": "StorageType", "Value": "StandardStorage"},
                                ],
                            },
                            "Period": 86400,
                            "Stat": "Average",
                        },
                    }
                    for i, name in enumerate(names)
                ]
                resp = cw.get_metric_data(
                    MetricDataQueries=queries,
                    StartTime=datetime.now(UTC) - timedelta(days=3),
                    EndTime=datetime.now(UTC),
                )
                for i, result in enumerate(resp.get("MetricDataResults", [])):
                    values = result.get("Values", [])
                    if values:
                        bucket_sizes[names[i]] = values[0]
            except Exception as e:
                print(f"    S3 CloudWatch metrics error in {region}: {e}")

        # Build resource entries with size data
        for bd in bucket_data:
            resources.append(
                {
                    "arn": bd["arn"],
                    "resource_type": "s3:bucket",
                    "region": bd["region"],
                    "tags": bd["tags"],
                    "name": bd["name"],
                    "created_at": bd["created_at"],
                    "created_by": None,
                    "running_duration_days": bd["duration"],
                    "estimated_monthly_cost": None,
                    "size_bytes": bucket_sizes.get(bd["name"]),
                    "inferred_purpose": None,
                }
            )
    except Exception as e:
        print(f"    S3 scan error: {e}")

    # IAM Roles
    try:
        iam = boto3.client("iam")
        paginator = iam.get_paginator("list_roles")
        for page in paginator.paginate():
            for role in page.get("Roles", []):
                arn = role["Arn"]
                created = role.get("CreateDate")
                created_str = created.isoformat() if created else None
                duration = (datetime.now(UTC) - created).days if created else None

                tags = {}
                try:
                    tag_resp = iam.list_role_tags(RoleName=role["RoleName"])
                    tags = {t["Key"]: t["Value"] for t in tag_resp.get("Tags", [])}
                except Exception:
                    pass

                resources.append(
                    {
                        "arn": arn,
                        "resource_type": "iam:role",
                        "region": "global",
                        "tags": tags,
                        "name": role["RoleName"],
                        "created_at": created_str,
                        "created_by": None,
                        "running_duration_days": duration,
                        "estimated_monthly_cost": 0,
                        "inferred_purpose": None,
                    }
                )
    except Exception as e:
        print(f"    IAM roles scan error: {e}")

    # IAM Users
    try:
        iam = boto3.client("iam")
        paginator = iam.get_paginator("list_users")
        for page in paginator.paginate():
            for user in page.get("Users", []):
                arn = user["Arn"]
                created = user.get("CreateDate")
                created_str = created.isoformat() if created else None
                duration = (datetime.now(UTC) - created).days if created else None

                tags = {}
                try:
                    tag_resp = iam.list_user_tags(UserName=user["UserName"])
                    tags = {t["Key"]: t["Value"] for t in tag_resp.get("Tags", [])}
                except Exception:
                    pass

                resources.append(
                    {
                        "arn": arn,
                        "resource_type": "iam:user",
                        "region": "global",
                        "tags": tags,
                        "name": user["UserName"],
                        "created_at": created_str,
                        "created_by": None,
                        "running_duration_days": duration,
                        "estimated_monthly_cost": 0,
                        "inferred_purpose": None,
                    }
                )
    except Exception as e:
        print(f"    IAM users scan error: {e}")

    # Route53 Hosted Zones
    try:
        r53 = boto3.client("route53")
        zones = r53.list_hosted_zones().get("HostedZones", [])
        for z in zones:
            zone_id = z["Id"].split("/")[-1]
            arn = f"arn:aws:route53:::hostedzone/{zone_id}"
            resources.append(
                {
                    "arn": arn,
                    "resource_type": "route53:hostedzone",
                    "region": "global",
                    "tags": {},
                    "name": z["Name"],
                    "created_at": None,
                    "created_by": None,
                    "running_duration_days": None,
                    "estimated_monthly_cost": 0.50,  # $0.50/zone/month
                    "inferred_purpose": f"DNS zone: {z['Name']}",
                }
            )
    except Exception as e:
        print(f"    Route53 scan error: {e}")

    return resources


# ===========================================================================
# Service-specific scanners
# ===========================================================================


def _scan_ec2(region, account_id):
    resources = []
    try:
        ec2 = boto3.client("ec2", region_name=region)

        # Instances — also build a name lookup for volume context
        instance_names = {}  # instance_id → human-readable name

        for page in ec2.get_paginator("describe_instances").paginate():
            for res in page.get("Reservations", []):
                for inst in res.get("Instances", []):
                    tags = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}
                    launched = inst.get("LaunchTime")
                    launched_str = launched.isoformat() if launched else None
                    duration = (datetime.now(UTC) - launched).days if launched else None

                    inst_id = inst["InstanceId"]
                    inst_name = tags.get("Name", inst_id)
                    instance_names[inst_id] = inst_name

                    resources.append(
                        {
                            "arn": f"arn:aws:ec2:{region}:{account_id}:instance/{inst_id}",
                            "resource_type": "ec2:instance",
                            "region": region,
                            "tags": tags,
                            "name": inst_name,
                            "created_at": launched_str,
                            "created_by": None,
                            "running_duration_days": duration,
                            "estimated_monthly_cost": None,
                            "inferred_purpose": None,
                            "state": inst.get("State", {}).get("Name"),
                            "instance_type": inst.get("InstanceType"),
                        }
                    )

        # VPCs
        vpcs = ec2.describe_vpcs().get("Vpcs", [])
        for vpc in vpcs:
            tags = {t["Key"]: t["Value"] for t in vpc.get("Tags", [])}
            resources.append(
                {
                    "arn": f"arn:aws:ec2:{region}:{account_id}:vpc/{vpc['VpcId']}",
                    "resource_type": "ec2:vpc",
                    "region": region,
                    "tags": tags,
                    "name": tags.get("Name", vpc["VpcId"]),
                    "created_at": None,
                    "created_by": None,
                    "running_duration_days": None,
                    "estimated_monthly_cost": 0,
                    "inferred_purpose": None,
                    "is_default": vpc.get("IsDefault", False),
                }
            )

        # Security Groups
        sgs = ec2.describe_security_groups().get("SecurityGroups", [])
        for sg in sgs:
            tags = {t["Key"]: t["Value"] for t in sg.get("Tags", [])}
            resources.append(
                {
                    "arn": f"arn:aws:ec2:{region}:{account_id}:security-group/{sg['GroupId']}",
                    "resource_type": "ec2:security-group",
                    "region": region,
                    "tags": tags,
                    "name": tags.get("Name", sg.get("GroupName", sg["GroupId"])),
                    "created_at": None,
                    "created_by": None,
                    "running_duration_days": None,
                    "estimated_monthly_cost": 0,
                    "inferred_purpose": sg.get("Description"),
                }
            )

        # EBS Volumes
        for page in ec2.get_paginator("describe_volumes").paginate():
            for vol in page.get("Volumes", []):
                tags = {t["Key"]: t["Value"] for t in vol.get("Tags", [])}
                created = vol.get("CreateTime")
                created_str = created.isoformat() if created else None
                duration = (datetime.now(UTC) - created).days if created else None

                # Capture attachment context with human-readable instance name
                attachments = vol.get("Attachments", [])
                attached_to = None
                attached_device = None
                if attachments:
                    attached_to = attachments[0].get("InstanceId")
                    attached_device = attachments[0].get("Device")

                purpose = None
                if attached_to:
                    # Resolve instance ID to its Name tag
                    inst_name = instance_names.get(attached_to, attached_to)
                    device_str = f", {attached_device}" if attached_device else ""
                    if inst_name != attached_to:
                        purpose = f"Attached to {inst_name} ({attached_to}{device_str})"
                    else:
                        purpose = f"Attached to {attached_to}{device_str}"
                elif vol.get("State") == "available":
                    purpose = "Unattached volume (orphaned)"

                resources.append(
                    {
                        "arn": f"arn:aws:ec2:{region}:{account_id}:volume/{vol['VolumeId']}",
                        "resource_type": "ec2:volume",
                        "region": region,
                        "tags": tags,
                        "name": tags.get("Name", vol["VolumeId"]),
                        "created_at": created_str,
                        "created_by": None,
                        "running_duration_days": duration,
                        "estimated_monthly_cost": None,
                        "inferred_purpose": purpose,
                        "size_gb": vol.get("Size"),
                        "state": vol.get("State"),
                        "attached_to": attached_to,
                        "attached_device": attached_device,
                    }
                )

        # Elastic IPs — resolve parent resource via ENI so the drift
        # detector can inherit compliance from TF-managed parents (ALB, etc.).
        eips = ec2.describe_addresses().get("Addresses", [])

        eni_ids = [eip["NetworkInterfaceId"] for eip in eips if eip.get("NetworkInterfaceId")]
        eni_parent = {}  # NetworkInterfaceId → parent ARN
        if eni_ids:
            try:
                enis_resp = ec2.describe_network_interfaces(NetworkInterfaceIds=eni_ids)["NetworkInterfaces"]
                for eni in enis_resp:
                    desc = eni.get("Description", "")
                    eni_id = eni["NetworkInterfaceId"]
                    # ALB/NLB ENIs: "ELB app/name/hex" or "ELB net/name/hex"
                    if desc.startswith("ELB "):
                        # "ELB app/name/hex" → "app/name/hex"
                        lb_path = desc[4:]
                        eni_parent[eni_id] = (
                            f"arn:aws:elasticloadbalancing:{region}:{account_id}:loadbalancer/{lb_path}"
                        )
                    # NAT Gateway ENIs
                    elif desc.startswith("Interface for NAT Gateway"):
                        eni_parent[eni_id] = desc
            except Exception:
                pass

        for eip in eips:
            tags = {t["Key"]: t["Value"] for t in eip.get("Tags", [])}
            arn = f"arn:aws:ec2:{region}:{account_id}:elastic-ip/{eip.get('AllocationId', 'unknown')}"
            attached = None
            if eip.get("InstanceId"):
                attached = f"arn:aws:ec2:{region}:{account_id}:instance/{eip['InstanceId']}"
            elif eip.get("NetworkInterfaceId"):
                attached = eni_parent.get(eip["NetworkInterfaceId"])

            resources.append(
                {
                    "arn": arn,
                    "resource_type": "ec2:eip",
                    "region": region,
                    "tags": tags,
                    "name": tags.get("Name", eip.get("PublicIp", "")),
                    "created_at": None,
                    "created_by": None,
                    "running_duration_days": None,
                    "estimated_monthly_cost": 3.65 if not eip.get("AssociationId") else 0,
                    "inferred_purpose": f"EIP: {eip.get('PublicIp', '')}",
                    "attached_to": attached,
                }
            )

        # NAT Gateways
        nats = ec2.describe_nat_gateways(Filter=[{"Name": "state", "Values": ["available", "pending"]}]).get(
            "NatGateways", []
        )
        for nat in nats:
            tags = {t["Key"]: t["Value"] for t in nat.get("Tags", [])}
            created = nat.get("CreateTime")
            created_str = created.isoformat() if created else None
            duration = (datetime.now(UTC) - created).days if created else None

            resources.append(
                {
                    "arn": f"arn:aws:ec2:{region}:{account_id}:natgateway/{nat['NatGatewayId']}",
                    "resource_type": "ec2:natgateway",
                    "region": region,
                    "tags": tags,
                    "name": tags.get("Name", nat["NatGatewayId"]),
                    "created_at": created_str,
                    "created_by": None,
                    "running_duration_days": duration,
                    "estimated_monthly_cost": 32.40 if region == PRICE_TABLE_REGION else None,  # ~$0.045/hr
                    "inferred_purpose": None,
                }
            )

        # VPC Endpoints
        vpce_paginator = ec2.get_paginator("describe_vpc_endpoints")
        for page in vpce_paginator.paginate():
            for ep in page.get("VpcEndpoints", []):
                ep_id = ep["VpcEndpointId"]
                ep_type = ep.get("VpcEndpointType", "Gateway")
                tags = {t["Key"]: t["Value"] for t in ep.get("Tags", [])}
                created = ep.get("CreationTimestamp")
                created_str = created.isoformat() if created else None
                duration = (datetime.now(UTC) - created).days if created else None

                # Interface endpoints: $0.011/hr per AZ in PRICE_TABLE_REGION
                # Gateway endpoints (S3, DynamoDB): free
                if ep_type != "Interface":
                    cost = 0
                elif region == PRICE_TABLE_REGION:
                    num_azs = len(ep.get("SubnetIds", [])) or 1
                    cost = round(0.011 * num_azs * 730, 2)
                else:
                    cost = None

                svc_name = ep.get("ServiceName", "")
                short_svc = svc_name.split(".")[-1] if svc_name else ep_id

                resources.append(
                    {
                        "arn": f"arn:aws:ec2:{region}:{account_id}:vpc-endpoint/{ep_id}",
                        "resource_type": "ec2:vpc-endpoint",
                        "region": region,
                        "tags": tags,
                        "name": tags.get("Name", f"{short_svc} ({ep_type.lower()})"),
                        "created_at": created_str,
                        "created_by": None,
                        "running_duration_days": duration,
                        "estimated_monthly_cost": cost,
                        "inferred_purpose": f"VPC {ep_type} endpoint: {short_svc}",
                    }
                )

    except Exception as e:
        print(f"    EC2 scan error in {region}: {e}")

    return resources


def _scan_ecs(region, account_id):
    resources = []
    try:
        ecs = boto3.client("ecs", region_name=region)
        clusters = ecs.list_clusters().get("clusterArns", [])

        if clusters:
            cluster_details = ecs.describe_clusters(clusters=clusters).get("clusters", [])
            for c in cluster_details:
                tags = {t["key"]: t["value"] for t in c.get("tags", [])}
                resources.append(
                    {
                        "arn": c["clusterArn"],
                        "resource_type": "ecs:cluster",
                        "region": region,
                        "tags": tags,
                        "name": c["clusterName"],
                        "created_at": None,
                        "created_by": None,
                        "running_duration_days": None,
                        "estimated_monthly_cost": None,
                        "inferred_purpose": f"ECS cluster ({c.get('runningTasksCount', 0)} tasks)",
                    }
                )

                # Services in this cluster (paginated — list_services returns max 10)
                svc_arns = []
                svc_paginator = ecs.get_paginator("list_services")
                for svc_page in svc_paginator.paginate(cluster=c["clusterArn"]):
                    svc_arns.extend(svc_page.get("serviceArns", []))
                if svc_arns:
                    # describe_services accepts max 10 at a time
                    svcs = []
                    for i in range(0, len(svc_arns), 10):
                        batch = svc_arns[i : i + 10]
                        svcs.extend(ecs.describe_services(cluster=c["clusterArn"], services=batch).get("services", []))
                    # Cache task definition lookups to avoid redundant API calls
                    td_cache = {}
                    for svc in svcs:
                        tags = {t["key"]: t["value"] for t in svc.get("tags", [])}
                        created = svc.get("createdAt")
                        created_str = created.isoformat() if created else None
                        duration = None
                        if created:
                            if created.tzinfo is None:
                                created = created.replace(tzinfo=UTC)
                            duration = (datetime.now(UTC) - created).days

                        # Look up task definition for CPU/memory (Fargate pricing)
                        td_cpu = None
                        td_memory = None
                        td_arn = svc.get("taskDefinition", "")
                        if td_arn:
                            if td_arn not in td_cache:
                                try:
                                    td = ecs.describe_task_definition(taskDefinition=td_arn).get("taskDefinition", {})
                                    td_cache[td_arn] = (td.get("cpu"), td.get("memory"))
                                except Exception:
                                    td_cache[td_arn] = (None, None)
                            td_cpu, td_memory = td_cache[td_arn]

                        resources.append(
                            {
                                "arn": svc["serviceArn"],
                                "resource_type": "ecs:service",
                                "region": region,
                                "tags": tags,
                                "name": svc["serviceName"],
                                "created_at": created_str,
                                "created_by": None,
                                "running_duration_days": duration,
                                "estimated_monthly_cost": None,
                                "inferred_purpose": f"ECS service ({svc.get('desiredCount', 0)} desired)",
                                "fargate_cpu": td_cpu,
                                "fargate_memory": td_memory,
                            }
                        )
    except Exception as e:
        print(f"    ECS scan error in {region}: {e}")

    return resources


def _scan_rds(region, account_id):
    resources = []
    try:
        rds = boto3.client("rds", region_name=region)
        instances = rds.describe_db_instances().get("DBInstances", [])

        for db in instances:
            arn = db["DBInstanceArn"]
            tags = {}
            try:
                tag_resp = rds.list_tags_for_resource(ResourceName=arn)
                tags = {t["Key"]: t["Value"] for t in tag_resp.get("TagList", [])}
            except Exception:
                pass

            created = db.get("InstanceCreateTime")
            created_str = created.isoformat() if created else None
            duration = (datetime.now(UTC) - created).days if created else None

            resources.append(
                {
                    "arn": arn,
                    "resource_type": "rds:instance",
                    "region": region,
                    "tags": tags,
                    "name": db["DBInstanceIdentifier"],
                    "created_at": created_str,
                    "created_by": None,
                    "running_duration_days": duration,
                    "estimated_monthly_cost": None,
                    "inferred_purpose": f"RDS {db.get('Engine', '')} ({db.get('DBInstanceClass', '')})",
                    "engine": db.get("Engine"),
                    "instance_class": db.get("DBInstanceClass"),
                    "multi_az": db.get("MultiAZ", False),
                    "status": db.get("DBInstanceStatus"),
                }
            )
    except Exception as e:
        print(f"    RDS scan error in {region}: {e}")

    return resources


def _scan_lambda(region, account_id):
    resources = []
    try:
        lam = boto3.client("lambda", region_name=region)
        paginator = lam.get_paginator("list_functions")

        for page in paginator.paginate():
            for fn in page.get("Functions", []):
                arn = fn["FunctionArn"]
                tags = {}
                with contextlib.suppress(Exception):
                    tags = lam.list_tags(Resource=arn).get("Tags", {})

                modified = fn.get("LastModified", "")
                resources.append(
                    {
                        "arn": arn,
                        "resource_type": "lambda:function",
                        "region": region,
                        "tags": tags,
                        "name": fn["FunctionName"],
                        "created_at": modified,
                        "created_by": None,
                        "running_duration_days": None,
                        "estimated_monthly_cost": None,
                        "inferred_purpose": fn.get("Description") or f"Lambda ({fn.get('Runtime', 'unknown')})",
                    }
                )
    except Exception as e:
        print(f"    Lambda scan error in {region}: {e}")

    return resources


def _scan_elb(region):
    resources = []
    try:
        elb = boto3.client("elbv2", region_name=region)
        lbs = []
        lb_paginator = elb.get_paginator("describe_load_balancers")
        for lb_page in lb_paginator.paginate():
            lbs.extend(lb_page.get("LoadBalancers", []))

        for lb in lbs:
            arn = lb["LoadBalancerArn"]
            tags = {}
            try:
                tag_resp = elb.describe_tags(ResourceArns=[arn])
                for desc in tag_resp.get("TagDescriptions", []):
                    tags = {t["Key"]: t["Value"] for t in desc.get("Tags", [])}
            except Exception:
                pass

            created = lb.get("CreatedTime")
            created_str = created.isoformat() if created else None
            duration = (datetime.now(UTC) - created).days if created else None

            resources.append(
                {
                    "arn": arn,
                    "resource_type": f"elbv2:{lb.get('Type', 'unknown')}",
                    "region": region,
                    "tags": tags,
                    "name": lb["LoadBalancerName"],
                    "created_at": created_str,
                    "created_by": None,
                    "running_duration_days": duration,
                    "estimated_monthly_cost": 16.20 if region == PRICE_TABLE_REGION else None,  # ~$0.0225/hr for ALB
                    "inferred_purpose": f"{lb.get('Type', '')} load balancer",
                }
            )
    except Exception as e:
        print(f"    ELB scan error in {region}: {e}")

    return resources


def _scan_elasticache(region, account_id):
    resources = []
    try:
        ec = boto3.client("elasticache", region_name=region)
        clusters = ec.describe_cache_clusters(ShowCacheNodeInfo=True).get("CacheClusters", [])

        for c in clusters:
            arn = c.get("ARN", f"arn:aws:elasticache:{region}:{account_id}:cluster:{c['CacheClusterId']}")
            tags = {}
            try:
                tag_resp = ec.list_tags_for_resource(ResourceName=arn)
                tags = {t["Key"]: t["Value"] for t in tag_resp.get("TagList", [])}
            except Exception:
                pass

            created = c.get("CacheClusterCreateTime")
            created_str = created.isoformat() if created else None
            duration = (datetime.now(UTC) - created).days if created else None

            resources.append(
                {
                    "arn": arn,
                    "resource_type": "elasticache:cluster",
                    "region": region,
                    "tags": tags,
                    "name": c["CacheClusterId"],
                    "created_at": created_str,
                    "created_by": None,
                    "running_duration_days": duration,
                    "estimated_monthly_cost": None,
                    "inferred_purpose": f"ElastiCache {c.get('Engine', '')} ({c.get('CacheNodeType', '')})",
                }
            )
    except Exception as e:
        print(f"    ElastiCache scan error in {region}: {e}")

    return resources


def _scan_efs(region, account_id):
    resources = []
    try:
        efs = boto3.client("efs", region_name=region)
        filesystems = efs.describe_file_systems().get("FileSystems", [])

        for fs in filesystems:
            arn = f"arn:aws:elasticfilesystem:{region}:{account_id}:file-system/{fs['FileSystemId']}"
            tags = {t["Key"]: t["Value"] for t in fs.get("Tags", [])}
            created = fs.get("CreationTime")
            created_str = created.isoformat() if created else None
            duration = (datetime.now(UTC) - created).days if created else None

            size_info = fs.get("SizeInBytes", {})
            resources.append(
                {
                    "arn": arn,
                    "resource_type": "efs:filesystem",
                    "region": region,
                    "tags": tags,
                    "name": tags.get("Name", fs["FileSystemId"]),
                    "created_at": created_str,
                    "created_by": None,
                    "running_duration_days": duration,
                    "estimated_monthly_cost": None,
                    "size_bytes": size_info.get("Value", 0),
                    "size_bytes_standard": size_info.get("ValueInStandard", 0),
                    "size_bytes_ia": size_info.get("ValueInIA", 0),
                    "inferred_purpose": None,
                }
            )
    except Exception as e:
        print(f"    EFS scan error in {region}: {e}")

    return resources


def _scan_ecr(region, account_id):
    resources = []
    try:
        ecr = boto3.client("ecr", region_name=region)
        repos = ecr.describe_repositories().get("repositories", [])

        for r in repos:
            arn = r["repositoryArn"]
            created = r.get("createdAt")
            created_str = created.isoformat() if created else None
            duration = (datetime.now(UTC) - created).days if created else None

            # Sum image sizes for storage cost estimation
            total_image_bytes = 0
            try:
                img_paginator = ecr.get_paginator("describe_images")
                for img_page in img_paginator.paginate(repositoryName=r["repositoryName"]):
                    for img in img_page.get("imageDetails", []):
                        total_image_bytes += img.get("imageSizeInBytes", 0)
            except Exception:
                pass  # Access denied or empty repo

            resources.append(
                {
                    "arn": arn,
                    "resource_type": "ecr:repository",
                    "region": region,
                    "tags": {},
                    "name": r["repositoryName"],
                    "created_at": created_str,
                    "created_by": None,
                    "running_duration_days": duration,
                    "estimated_monthly_cost": None,
                    "size_bytes": total_image_bytes,
                    "inferred_purpose": f"Container registry: {r['repositoryName']}",
                }
            )
    except Exception as e:
        print(f"    ECR scan error in {region}: {e}")

    return resources


def _scan_secrets_manager(region):
    resources = []
    try:
        sm = boto3.client("secretsmanager", region_name=region)
        paginator = sm.get_paginator("list_secrets")

        for page in paginator.paginate():
            for s in page.get("SecretList", []):
                arn = s["ARN"]
                tags = {t["Key"]: t["Value"] for t in s.get("Tags", [])}
                created = s.get("CreatedDate")
                created_str = created.isoformat() if created else None
                duration = (datetime.now(UTC) - created).days if created else None

                resources.append(
                    {
                        "arn": arn,
                        "resource_type": "secretsmanager:secret",
                        "region": region,
                        "tags": tags,
                        "name": s["Name"],
                        "created_at": created_str,
                        "created_by": None,
                        "running_duration_days": duration,
                        "estimated_monthly_cost": 0.40,  # $0.40/secret/month
                        "inferred_purpose": s.get("Description"),
                    }
                )
    except Exception as e:
        print(f"    Secrets Manager scan error in {region}: {e}")

    return resources


def _scan_dynamodb(region, account_id):
    resources = []
    try:
        ddb = boto3.client("dynamodb", region_name=region)
        tables = []
        ddb_paginator = ddb.get_paginator("list_tables")
        for ddb_page in ddb_paginator.paginate():
            tables.extend(ddb_page.get("TableNames", []))

        for table_name in tables:
            arn = f"arn:aws:dynamodb:{region}:{account_id}:table/{table_name}"
            tags = {}
            try:
                tag_resp = ddb.list_tags_of_resource(ResourceArn=arn)
                tags = {t["Key"]: t["Value"] for t in tag_resp.get("Tags", [])}
            except Exception:
                pass

            resources.append(
                {
                    "arn": arn,
                    "resource_type": "dynamodb:table",
                    "region": region,
                    "tags": tags,
                    "name": table_name,
                    "created_at": None,
                    "created_by": None,
                    "running_duration_days": None,
                    "estimated_monthly_cost": None,
                    "inferred_purpose": f"DynamoDB table: {table_name}",
                }
            )
    except Exception as e:
        print(f"    DynamoDB scan error in {region}: {e}")

    return resources


def _scan_sns(region):
    resources = []
    try:
        sns = boto3.client("sns", region_name=region)
        topics = sns.list_topics().get("Topics", [])

        for t in topics:
            arn = t["TopicArn"]
            name = arn.split(":")[-1]
            tags = {}
            try:
                tag_resp = sns.list_tags_for_resource(ResourceArn=arn)
                tags = {t["Key"]: t["Value"] for t in tag_resp.get("Tags", [])}
            except Exception:
                pass

            resources.append(
                {
                    "arn": arn,
                    "resource_type": "sns:topic",
                    "region": region,
                    "tags": tags,
                    "name": name,
                    "created_at": None,
                    "created_by": None,
                    "running_duration_days": None,
                    "estimated_monthly_cost": None,  # usage-based per-request pricing
                    "inferred_purpose": f"SNS topic: {name}",
                }
            )
    except Exception as e:
        print(f"    SNS scan error in {region}: {e}")

    return resources


def _scan_sqs(region):
    resources = []
    try:
        sqs = boto3.client("sqs", region_name=region)
        queues = sqs.list_queues().get("QueueUrls", [])

        for url in queues or []:
            name = url.split("/")[-1]
            try:
                attrs = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["QueueArn", "CreatedTimestamp"]).get(
                    "Attributes", {}
                )
                arn = attrs.get("QueueArn", "")
                created_ts = attrs.get("CreatedTimestamp")
                created_str = None
                duration = None
                if created_ts:
                    created_dt = datetime.fromtimestamp(int(created_ts), tz=UTC)
                    created_str = created_dt.isoformat()
                    duration = (datetime.now(UTC) - created_dt).days

                tags = sqs.list_queue_tags(QueueUrl=url).get("Tags", {})

                resources.append(
                    {
                        "arn": arn,
                        "resource_type": "sqs:queue",
                        "region": region,
                        "tags": tags,
                        "name": name,
                        "created_at": created_str,
                        "created_by": None,
                        "running_duration_days": duration,
                        "estimated_monthly_cost": None,  # usage-based per-request pricing
                        "inferred_purpose": f"SQS queue: {name}",
                    }
                )
            except Exception:
                pass
    except Exception as e:
        print(f"    SQS scan error in {region}: {e}")

    return resources


def _scan_apigateway(region, account_id):
    resources = []
    try:
        apigw = boto3.client("apigatewayv2", region_name=region)
        apis = apigw.get_apis().get("Items", [])

        for api in apis:
            arn = f"arn:aws:apigateway:{region}::/apis/{api['ApiId']}"
            tags = api.get("Tags", {})
            created = api.get("CreatedDate")
            created_str = created.isoformat() if created else None
            duration = None
            if created:
                if created.tzinfo is None:
                    created = created.replace(tzinfo=UTC)
                duration = (datetime.now(UTC) - created).days

            resources.append(
                {
                    "arn": arn,
                    "resource_type": "apigateway:api",
                    "region": region,
                    "tags": tags,
                    "name": api.get("Name", api["ApiId"]),
                    "created_at": created_str,
                    "created_by": None,
                    "running_duration_days": duration,
                    "estimated_monthly_cost": None,
                    "inferred_purpose": api.get("Description") or f"API Gateway: {api.get('Name', '')}",
                }
            )
    except Exception as e:
        print(f"    API Gateway scan error in {region}: {e}")

    return resources


def _scan_log_groups(region, account_id):
    resources = []
    try:
        logs = boto3.client("logs", region_name=region)
        paginator = logs.get_paginator("describe_log_groups")

        for page in paginator.paginate():
            for lg in page.get("logGroups", []):
                arn = lg.get("arn", "").rstrip("*").rstrip(":")
                if not arn:
                    arn = f"arn:aws:logs:{region}:{account_id}:log-group:{lg['logGroupName']}"

                tags = {}
                with contextlib.suppress(Exception):
                    tags = logs.list_tags_for_resource(resourceArn=arn).get("tags", {})

                created_ms = lg.get("creationTime")
                created_str = None
                duration = None
                if created_ms:
                    created_dt = datetime.fromtimestamp(created_ms / 1000, tz=UTC)
                    created_str = created_dt.isoformat()
                    duration = (datetime.now(UTC) - created_dt).days

                resources.append(
                    {
                        "arn": arn,
                        "resource_type": "logs:log-group",
                        "region": region,
                        "tags": tags,
                        "name": lg["logGroupName"],
                        "created_at": created_str,
                        "created_by": None,
                        "running_duration_days": duration,
                        "estimated_monthly_cost": None,
                        "stored_bytes": lg.get("storedBytes", 0),
                        "inferred_purpose": f"Log group: {lg['logGroupName']}",
                    }
                )
    except Exception as e:
        print(f"    CloudWatch Logs scan error in {region}: {e}")

    return resources


def _scan_eventbridge(region, account_id):
    resources = []
    try:
        eb = boto3.client("events", region_name=region)
        rules = eb.list_rules().get("Rules", [])

        for rule in rules:
            arn = rule["Arn"]
            tags = {}
            try:
                tag_resp = eb.list_tags_for_resource(ResourceARN=arn)
                tags = {t["Key"]: t["Value"] for t in tag_resp.get("Tags", [])}
            except Exception:
                pass

            resources.append(
                {
                    "arn": arn,
                    "resource_type": "events:rule",
                    "region": region,
                    "tags": tags,
                    "name": rule["Name"],
                    "created_at": None,
                    "created_by": None,
                    "running_duration_days": None,
                    "estimated_monthly_cost": 0,
                    "inferred_purpose": rule.get("Description") or f"EventBridge: {rule['Name']}",
                }
            )
    except Exception as e:
        print(f"    EventBridge scan error in {region}: {e}")

    return resources


def _scan_acm(region, account_id):
    resources = []
    try:
        acm = boto3.client("acm", region_name=region)
        certs = acm.list_certificates().get("CertificateSummaryList", [])

        for cert in certs:
            arn = cert["CertificateArn"]
            tags = {}
            try:
                tag_resp = acm.list_tags_for_certificate(CertificateArn=arn)
                tags = {t["Key"]: t["Value"] for t in tag_resp.get("Tags", [])}
            except Exception:
                pass

            resources.append(
                {
                    "arn": arn,
                    "resource_type": "acm:certificate",
                    "region": region,
                    "tags": tags,
                    "name": cert.get("DomainName", arn.split("/")[-1]),
                    "created_at": None,
                    "created_by": None,
                    "running_duration_days": None,
                    "estimated_monthly_cost": 0,
                    "inferred_purpose": f"TLS cert: {cert.get('DomainName', '')}",
                }
            )
    except Exception as e:
        print(f"    ACM scan error in {region}: {e}")

    return resources


def _scan_kms(region, account_id):
    resources = []
    try:
        kms = boto3.client("kms", region_name=region)
        keys = kms.list_keys().get("Keys", [])

        for key in keys:
            arn = key["KeyArn"]
            try:
                meta = kms.describe_key(KeyId=key["KeyId"])["KeyMetadata"]
                if meta.get("KeyManager") == "AWS":
                    continue  # Skip AWS-managed keys

                tags = {}
                try:
                    tag_resp = kms.list_resource_tags(KeyId=key["KeyId"])
                    tags = {t["TagKey"]: t["TagValue"] for t in tag_resp.get("Tags", [])}
                except Exception:
                    pass

                created = meta.get("CreationDate")
                created_str = created.isoformat() if created else None
                duration = (datetime.now(UTC) - created).days if created else None

                resources.append(
                    {
                        "arn": arn,
                        "resource_type": "kms:key",
                        "region": region,
                        "tags": tags,
                        "name": meta.get("Description") or key["KeyId"],
                        "created_at": created_str,
                        "created_by": None,
                        "running_duration_days": duration,
                        "estimated_monthly_cost": 1.00,  # $1/key/month
                        "inferred_purpose": meta.get("Description"),
                    }
                )
            except Exception:
                pass
    except Exception as e:
        print(f"    KMS scan error in {region}: {e}")

    return resources


def _scan_ssm(region, account_id):
    resources = []
    try:
        ssm = boto3.client("ssm", region_name=region)
        paginator = ssm.get_paginator("describe_parameters")

        for page in paginator.paginate():
            for p in page.get("Parameters", []):
                arn = f"arn:aws:ssm:{region}:{account_id}:parameter/{p['Name'].lstrip('/')}"
                tags = {}
                try:
                    tag_resp = ssm.list_tags_for_resource(ResourceType="Parameter", ResourceId=p["Name"])
                    tags = {t["Key"]: t["Value"] for t in tag_resp.get("TagList", [])}
                except Exception:
                    pass

                modified = p.get("LastModifiedDate")
                modified_str = modified.isoformat() if modified else None

                resources.append(
                    {
                        "arn": arn,
                        "resource_type": "ssm:parameter",
                        "region": region,
                        "tags": tags,
                        "name": p["Name"],
                        "created_at": modified_str,
                        "created_by": None,
                        "running_duration_days": None,
                        "estimated_monthly_cost": 0,
                        "inferred_purpose": p.get("Description") or f"SSM param: {p['Name']}",
                    }
                )
    except Exception as e:
        print(f"    SSM scan error in {region}: {e}")

    return resources


# ===========================================================================
# Enrichment helpers
# ===========================================================================


def get_service_costs():
    """Get per-service costs from Cost Explorer (30-day trailing)."""
    try:
        ce = boto3.client("ce")
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        response = ce.get_cost_and_usage(
            TimePeriod={"Start": start_date, "End": end_date},
            Granularity="MONTHLY",
            Metrics=["UnblendedCost"],
            GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
        )

        costs = {}
        for result in response.get("ResultsByTime", []):
            for group in result.get("Groups", []):
                service = group["Keys"][0]
                cost = float(group["Metrics"]["UnblendedCost"]["Amount"])
                costs[service] = costs.get(service, 0) + cost

        return costs
    except Exception as e:
        print(f"  Cost Explorer service costs error: {e}")
        return {}


def _enrich_costs(resources, service_costs):
    """Estimate per-resource monthly cost using instance-type pricing and resource metadata.

    Cost semantics (three-state):
        float > 0  — scanner has a pricing model; this is the estimated monthly rate
        float == 0 — genuinely free (config-only resources: IAM, SGs, ACM, etc.)
        None       — scanner has NO pricing model (usage-based costs like CloudTrail,
                     GuardDuty, SNS, SQS) or insufficient data to estimate
    """
    for r in resources:
        if r["estimated_monthly_cost"] is not None:
            continue  # Already has a known cost (set during discovery)

        rtype = r["resource_type"]

        # The static rates below are for PRICE_TABLE_REGION. A resource in another
        # region keeps cost None (unknown) unless its rate is the same everywhere
        # or, for EC2, the Pricing API returns one for its region.
        in_table_region = r.get("region") == PRICE_TABLE_REGION
        if rtype in _REGION_PRICED_TYPES and not in_table_region:
            continue

        # EC2 instances: on-demand Linux rate for the instance's own region
        if rtype == "ec2:instance":
            itype = r.get("instance_type", "")
            state = r.get("state", "")
            # Primary: real on-demand rate from the weekly-rebuilt Pricing-API cache
            # (the static table missed c7i/c6i families and defaulted them to $30).
            cost = get_ec2_monthly_cost(itype, r.get("region"), state)
            if cost is None and in_table_region:
                # Pricing API unavailable for this type — fall back to the static
                # seed table (or a t3.small-ish default) so we never regress to None.
                cost = 0 if state == "stopped" else _EC2_MONTHLY_COST.get(itype, 30.0)
            r["estimated_monthly_cost"] = cost

        # EBS volumes: estimate from size
        elif rtype == "ec2:volume":
            size_gb = r.get("size_gb", 20)
            r["estimated_monthly_cost"] = round(size_gb * 0.095, 2)  # gp3

        # NAT Gateway: fixed ~$32/mo + data
        elif rtype == "ec2:natgateway":
            r["estimated_monthly_cost"] = 32.40

        # EIP: $3.65/mo if not attached
        elif rtype in ("ec2:eip", "ec2:elastic-ip"):
            r["estimated_monthly_cost"] = 3.65

        # RDS: estimate from instance class (double for multi-AZ)
        # Scanner discovers as rds:db (via tagging API) or rds:instance (via service API)
        elif rtype in ("rds:instance", "rds:db"):
            iclass = r.get("instance_class", "")
            base = _RDS_MONTHLY_COST.get(iclass, 25.0)
            r["estimated_monthly_cost"] = base * 2 if r.get("multi_az") else base

        # ECS service: Fargate pricing from task definition CPU/memory
        # $0.04048/vCPU-hr + $0.004445/GB-hr
        elif rtype == "ecs:service":
            cpu_units = int(r.get("fargate_cpu") or 256)
            mem_mb = int(r.get("fargate_memory") or 512)
            vcpu_cost = (cpu_units / 1024) * 0.04048 * 730
            mem_cost = (mem_mb / 1024) * 0.004445 * 730
            r["estimated_monthly_cost"] = round(vcpu_cost + mem_cost, 2)

        # ElastiCache
        elif rtype == "elasticache:cluster":
            r["estimated_monthly_cost"] = 12.41  # cache.t3.micro

        # ALB/NLB — discovered as elasticloadbalancing:loadbalancer or elbv2:*
        elif rtype in ("elbv2:application", "elbv2:network", "elasticloadbalancing:loadbalancer"):
            r["estimated_monthly_cost"] = 16.20  # base cost

        # Secrets Manager: $0.40/secret/month
        elif rtype == "secretsmanager:secret":
            r["estimated_monthly_cost"] = 0.40

        # KMS key: $1/month
        elif rtype == "kms:key":
            r["estimated_monthly_cost"] = 1.00

        # Lambda: typically negligible unless high-traffic
        elif rtype == "lambda:function":
            r["estimated_monthly_cost"] = 0

        # CloudWatch Logs: storage cost from storedBytes
        elif rtype in ("cloudwatch:log_group", "logs:log-group"):
            stored_bytes = r.get("stored_bytes", 0) or 0
            size_gb = stored_bytes / (1024**3)
            r["estimated_monthly_cost"] = round(size_gb * 0.0314, 2)

        # DynamoDB: $0.25/WCU + $0.05/RCU (assume minimal)
        elif rtype == "dynamodb:table":
            r["estimated_monthly_cost"] = 0.50

        # EFS: storage cost from SizeInBytes (Standard + IA tiers)
        elif rtype == "efs:filesystem":
            std_bytes = r.get("size_bytes_standard", 0) or 0
            ia_bytes = r.get("size_bytes_ia", 0) or 0
            std_gb = std_bytes / (1024**3)
            ia_gb = ia_bytes / (1024**3)
            # Standard $0.33/GB, Infrequent Access $0.0205/GB
            r["estimated_monthly_cost"] = round(std_gb * 0.33 + ia_gb * 0.0205, 2)

        # ECR: storage cost from total image size
        elif rtype == "ecr:repository":
            size_bytes = r.get("size_bytes", 0) or 0
            size_gb = size_bytes / (1024**3)
            r["estimated_monthly_cost"] = round(size_gb * 0.10, 2)  # $0.10/GB/month

        # S3: storage cost from CloudWatch BucketSizeBytes metric
        elif rtype == "s3:bucket":
            size_bytes = r.get("size_bytes")
            if size_bytes is not None:
                size_gb = size_bytes / (1024**3)
                r["estimated_monthly_cost"] = round(size_gb * 0.0245, 2)  # Standard
            # else: leave as None — no size data means unknown cost, not free

    # Persist any EC2 rates freshly fetched from the Pricing API during this scan.
    flush_cache()


# Resource types whose static rate differs by region. Outside PRICE_TABLE_REGION
# their cost is reported as unknown (None), never as this region's number.
_REGION_PRICED_TYPES = frozenset(
    {
        "ec2:volume",
        "ec2:natgateway",
        "rds:instance",
        "rds:db",
        "ecs:service",
        "elasticache:cluster",
        "elbv2:application",
        "elbv2:network",
        "elasticloadbalancing:loadbalancer",
        "cloudwatch:log_group",
        "logs:log-group",
        "efs:filesystem",
        "s3:bucket",
    }
)

# Fallback seed table — used only when the Pricing API can't price a type.
# Common EC2 instance type monthly costs (PRICE_TABLE_REGION, on-demand, Linux)
_EC2_MONTHLY_COST = {
    "t3.micro": 10.51,
    "t3.small": 21.02,
    "t3.medium": 42.05,
    "t3.large": 84.10,
    "t3.xlarge": 168.19,
    "t3.2xlarge": 336.38,
    "t3a.micro": 9.49,
    "t3a.small": 18.98,
    "t3a.medium": 37.96,
    "t3a.large": 75.92,
    "t2.micro": 12.26,
    "t2.small": 24.53,
    "t2.medium": 49.06,
    "t2.large": 98.11,
    "m5.large": 100.37,
    "m5.xlarge": 200.75,
    "m5.2xlarge": 401.50,
    "m6i.large": 100.37,
    "m6i.xlarge": 200.75,
    "r5.large": 131.40,
    "r5.xlarge": 262.80,
    "c5.large": 88.32,
    "c5.xlarge": 176.66,
    # Graviton (t4g) — ARM-based, common for cost optimization
    "t4g.nano": 4.38,
    "t4g.micro": 8.76,
    "t4g.small": 17.52,
    "t4g.medium": 35.04,
    "t4g.large": 70.08,
    "t4g.xlarge": 140.16,
    # Graviton m6g/m7g
    "m6g.medium": 44.53,
    "m6g.large": 89.06,
    "m6g.xlarge": 178.12,
    "m7g.medium": 46.72,
    "m7g.large": 93.44,
    # Graviton c6g/c7g
    "c6g.medium": 39.05,
    "c6g.large": 78.11,
    "c6g.xlarge": 156.22,
    # Graviton r6g
    "r6g.medium": 58.40,
    "r6g.large": 116.80,
    "r6g.xlarge": 233.60,
    # Older/common types still in use
    "t2.nano": 6.13,
    "m5.4xlarge": 803.00,
    "r5.2xlarge": 525.60,
}

# Common RDS instance class monthly costs (PRICE_TABLE_REGION, on-demand, single-AZ)
_RDS_MONTHLY_COST = {
    "db.t3.micro": 13.14,
    "db.t3.small": 26.28,
    "db.t3.medium": 52.56,
    "db.t4g.micro": 11.68,
    "db.t4g.small": 23.36,
    "db.t4g.medium": 46.72,
    "db.r5.large": 175.20,
    "db.r5.xlarge": 350.40,
    "db.r6g.large": 157.68,
    "db.r6g.xlarge": 315.36,
    "db.m5.large": 136.51,
    "db.m5.xlarge": 273.02,
    # Graviton m6g/m7g
    "db.m6g.large": 122.64,
    "db.m6g.xlarge": 245.28,
    "db.m7g.large": 128.48,
    "db.m7g.xlarge": 256.96,
    # Graviton r7g
    "db.r7g.large": 175.20,
    "db.r7g.xlarge": 350.40,
    # Smaller/common tiers
    "db.t3.large": 105.12,
    "db.t4g.large": 93.44,
}


_purpose_hints_cache = None


def _purpose_hints():
    """Name fragment → purpose text, from the PURPOSE_HINTS environment variable (JSON object).

    Empty by default: the scanner knows no deployment's service names.
    """
    global _purpose_hints_cache
    if _purpose_hints_cache is None:
        try:
            hints = json.loads(os.environ.get("PURPOSE_HINTS") or "{}")
        except ValueError:
            print("WARNING: PURPOSE_HINTS is not valid JSON; ignoring it")
            hints = {}
        hints = hints if isinstance(hints, dict) else {}
        # Longest fragment first, so the match does not depend on the order of the input.
        ordered = sorted(hints.items(), key=lambda kv: (-len(str(kv[0])), str(kv[0])))
        _purpose_hints_cache = {str(k).lower(): str(v) for k, v in ordered}
    return _purpose_hints_cache


def _infer_purpose(resource):
    """Infer resource purpose from name, tags, type, and metadata."""
    if resource.get("inferred_purpose"):
        return resource["inferred_purpose"]

    name = resource.get("name", "")
    tags = resource.get("tags", {})
    rtype = resource.get("resource_type", "")

    # Check for descriptive tags
    for tag_key in ["Description", "Purpose", "Application", "Service", "Component"]:
        if tag_key in tags:
            return tags[tag_key]

    # Check Name patterns supplied by the deployment (PURPOSE_HINTS)
    name_lower = name.lower()
    for pattern, purpose in _purpose_hints().items():
        if pattern in name_lower:
            return purpose

    return f"{rtype}: {name}" if name else rtype


def _merge_metadata(existing, enriched):
    """Merge metadata fields from service-specific API into tagged resource.

    The Tagging API finds resources but lacks metadata (state, instance_type,
    size_gb, etc.). The service-specific API has this metadata. When both find
    the same resource (same ARN), merge the enriched fields into the existing entry.
    """
    metadata_fields = [
        "state",
        "instance_type",
        "instance_class",
        "size_gb",
        "size_bytes",
        "size_bytes_standard",
        "size_bytes_ia",
        "stored_bytes",
        "created_at",
        "created_by",
        "running_duration_days",
        "estimated_monthly_cost",
        "inferred_purpose",
        "is_default",
        "engine",
        "attached_to",
        "attached_device",
        "multi_az",
        "fargate_cpu",
        "fargate_memory",
    ]
    for field in metadata_fields:
        if field in enriched and enriched[field] is not None and existing.get(field) is None:
            existing[field] = enriched[field]


# ===========================================================================
# ARN parsing helpers
# ===========================================================================


def _type_from_arn(arn):
    """Extract resource type from ARN."""
    parts = arn.split(":")
    if len(parts) < 6:
        return "unknown"

    service = parts[2]
    resource = parts[5] if len(parts) > 5 else ""

    # S3 bucket ARNs: arn:aws:s3:::bucket-name (resource IS the bucket name, not a type)
    if service == "s3" and "/" not in resource:
        return "s3:bucket"

    # SNS topic ARNs: arn:aws:sns:region:account:topic-name (resource IS the topic name)
    if service == "sns" and "/" not in resource:
        return "sns:topic"

    # Handle resource-type/id format (strip leading / from apigateway-style ARNs)
    resource = resource.lstrip("/")
    if "/" in resource:
        resource_type = resource.split("/")[0]
        return f"{service}:{resource_type}"
    elif ":" in resource:
        resource_type = resource.split(":")[0]
        return f"{service}:{resource_type}"

    return f"{service}:{resource}" if resource else f"{service}:resource"


def _name_from_arn(arn):
    """Extract a human-readable name from ARN."""
    parts = arn.split(":")
    if len(parts) < 6:
        return arn

    resource = parts[-1]
    if "/" in resource:
        return resource.split("/")[-1]
    return resource
