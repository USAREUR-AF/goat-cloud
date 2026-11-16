"""
Data layer for per-repo resource views.

Filters the compliance report's discovered_resources by Repository tag
to extract resources belonging to a specific IaC repo, then groups them
into module-like tree structures and D3 graph data.
"""

from data_core import REGION
from data_reports import get_compliance_report
from views_base import _console_url


def get_repo_info(slug):
    """Return the IaC registry entry for a given repo slug, or None."""
    report = get_compliance_report()
    if not report:
        return None
    registry = report.get("iac_registry", {})
    for entry in registry.get("entries", []):
        if entry.get("repo_slug") == slug:
            return entry
    return None


def get_repo_resources(slug):
    """Return all discovered resources belonging to a repo slug.

    Matches resources whose Repository tag URL ends with the slug.
    Also cross-references findings to mark compliance status.
    """
    report = get_compliance_report()
    if not report:
        return []

    # Find the repo URL from registry
    info = get_repo_info(slug)
    repo_url = info.get("repo_url", "") if info else ""

    # Build non-compliant ARN set
    nc_arns = set()
    for cat in ("tag_compliance", "drift_detection", "schedule_compliance", "lifecycle_compliance"):
        for f in report.get(cat, {}).get("findings", []):
            nc_arns.add(f.get("resource_arn", ""))

    # Filter discovered resources by Repository tag
    resources = []
    for r in report.get("discovered_resources", []):
        tags = r.get("tags") or {}
        res_repo = tags.get("Repository", "")
        # Match by URL or by slug at end of URL
        if not _matches_repo(res_repo, slug, repo_url):
            continue
        arn = r.get("arn", "")
        resources.append(
            {
                "arn": arn,
                "name": r.get("name", ""),
                "resource_type": r.get("resource_type", ""),
                "region": r.get("region") or REGION,
                "tags": tags,
                "compliant": arn not in nc_arns,
                "estimated_monthly_cost": r.get("estimated_monthly_cost"),
                "console_url": _console_url(arn, r.get("region")),
            }
        )

    return resources


def _matches_repo(res_repo_tag, slug, repo_url):
    """Check if a resource's Repository tag matches the target repo."""
    if not res_repo_tag:
        return False
    # Exact URL match
    if repo_url and res_repo_tag == repo_url:
        return True
    # Slug at end of URL path
    return res_repo_tag.rstrip("/").endswith("/" + slug)


# ── Service type grouping ──

_SERVICE_TYPE_MAP = {
    "lambda": {"label": "Lambda Functions", "icon": "lambda", "zone": "compute"},
    "apigateway": {"label": "API Gateway", "icon": "alb", "zone": "api"},
    "s3": {"label": "S3 Buckets", "icon": "ecs", "zone": "storage"},
    "dynamodb": {"label": "DynamoDB Tables", "icon": "redis", "zone": "storage"},
    "iam": {"label": "IAM Roles & Policies", "icon": "shield", "zone": "security"},
    "logs": {"label": "CloudWatch Logs", "icon": "monitoring", "zone": "monitoring"},
    "cloudwatch": {"label": "CloudWatch", "icon": "monitoring", "zone": "monitoring"},
    "events": {"label": "EventBridge", "icon": "monitoring", "zone": "monitoring"},
    "ssm": {"label": "SSM Parameters", "icon": "shield", "zone": "security"},
    "sqs": {"label": "SQS Queues", "icon": "ecs", "zone": "messaging"},
    "sns": {"label": "SNS Topics", "icon": "ecs", "zone": "messaging"},
}


def _service_from_type(resource_type):
    """Extract the AWS service prefix from a resource type string.

    resource_type may be 'lambda:function', 'apigateway:restapi',
    'arn:aws:lambda:...', or a Terraform type like 'aws_lambda_function'.
    """
    if ":" in resource_type:
        return resource_type.split(":")[0]
    if resource_type.startswith("aws_"):
        # aws_lambda_function -> lambda
        parts = resource_type.split("_")
        return parts[1] if len(parts) > 1 else resource_type
    return resource_type


def get_repo_tree(slug):
    """Build a module tree for a repo, grouped by AWS service type.

    Returns a list of module dicts compatible with views_tree.render_tree():
      - module: service group name (e.g., "Lambda Functions")
      - file_path: "" (no TF file mapping for external repos)
      - resources: list of resource dicts with compliance status
      - children: {} (flat structure)
      - stats: { total, compliant, non_compliant, cost, cost_unknown }
    """
    resources = get_repo_resources(slug)
    if not resources:
        return []

    # Group by service type
    groups = {}  # service_key -> [resources]
    for r in resources:
        svc = _service_from_type(r["resource_type"])
        # Group EC2 instances by their Service tag (functional relatedness) within
        # the repo/stack — e.g. all "web" hosts in one box, all
        # "worker" hosts in another — instead of one big "EC2" card or one box
        # per host. Falls back to the instance name when there's no Service tag.
        # Other ec2:* types (volumes, SGs, VPCs) stay aggregated under their service.
        if r["resource_type"] == "ec2:instance":
            service_tag = (r.get("tags") or {}).get("Service") or r.get("name") or "instance"
            svc = f"ec2:instance::{service_tag}"
        if svc not in groups:
            groups[svc] = []
        groups[svc].append(
            {
                "tf_address": r["arn"],
                "type": r["resource_type"],
                "name": r["name"],
                "arn": r["arn"],
                "id": r["arn"].split("/")[-1] if "/" in r["arn"] else r["arn"].split(":")[-1],
                "compliant": r["compliant"],
                "tags": r["tags"],
                "estimated_monthly_cost": r.get("estimated_monthly_cost"),
            }
        )

    # Build tree nodes
    result = []
    for svc in sorted(groups.keys()):
        svc_resources = groups[svc]
        if svc.startswith("ec2:instance::"):
            # Functional-group box (by Service tag): e.g. "web" holding
            # its regional hosts. Label is the Service group; icon/zone from EC2.
            ec2_meta = _SERVICE_TYPE_MAP.get("ec2", {"label": "EC2", "icon": "ec2", "zone": "compute"})
            meta = {
                "label": svc.split("::", 1)[1],
                "icon": ec2_meta.get("icon", "ec2"),
                "zone": ec2_meta.get("zone", "compute"),
            }
        else:
            meta = _SERVICE_TYPE_MAP.get(svc, {"label": svc.upper(), "icon": "ecs", "zone": "other"})
        total = len(svc_resources)
        compliant_count = sum(1 for r in svc_resources if r["compliant"])
        known_costs = [
            r["estimated_monthly_cost"] for r in svc_resources if r.get("estimated_monthly_cost") is not None
        ]

        result.append(
            {
                "module": meta["label"],
                "file_path": "",
                "resources": svc_resources,
                "children": {},
                "stats": {
                    "total": total,
                    "compliant": compliant_count,
                    "non_compliant": total - compliant_count,
                    "cost": round(sum(known_costs), 2) if known_costs else 0,
                    "cost_unknown": total - len(known_costs),
                },
                "arch": {"zone": meta.get("zone", "other"), "role": None, "visible": True},
            }
        )

    return result


def get_repo_graph(slug):
    """Build a D3 force-directed graph for a repo's resources.

    Returns the same schema as data_terraform.get_full_infra_graph():
    { nodes, links, common_tags, stats }
    """
    resources = get_repo_resources(slug)
    info = get_repo_info(slug)
    repo_name = info.get("repo_slug", slug) if info else slug

    if not resources:
        return {
            "nodes": [{"id": "root", "type": "root", "name": repo_name}],
            "links": [],
            "common_tags": {},
            "stats": {"total": 0, "managed": 0, "unmanaged": 0},
        }

    nodes = [{"id": "root", "type": "root", "name": repo_name}]
    links = []
    groups_seen = {}
    all_tag_dicts = []
    nc_count = 0

    for r in resources:
        arn = r["arn"]
        svc = _service_from_type(r["resource_type"])
        meta = _SERVICE_TYPE_MAP.get(svc, {"label": svc.upper()})
        group_key = f"svc:{svc}"
        group_name = meta["label"]

        all_tag_dicts.append(r["tags"])
        if not r["compliant"]:
            nc_count += 1

        nodes.append(
            {
                "id": arn,
                "type": "resource",
                "name": r["name"],
                "resource_type": r["resource_type"],
                "arn": arn,
                "tags": r["tags"],
                "managed": True,
                "compliant": r["compliant"],
                "cost": r.get("estimated_monthly_cost"),
                "region": r["region"],
                "console_url": r.get("console_url", ""),
            }
        )

        if group_key not in groups_seen:
            groups_seen[group_key] = {"name": group_name, "total": 0, "compliant": 0}
        groups_seen[group_key]["total"] += 1
        if r["compliant"]:
            groups_seen[group_key]["compliant"] += 1

    # Add group nodes
    for gkey, gdata in sorted(groups_seen.items()):
        pct = round(100 * gdata["compliant"] / gdata["total"]) if gdata["total"] else 100
        nodes.append(
            {
                "id": f"group:{gkey}",
                "type": "module",
                "name": gdata["name"],
                "total": gdata["total"],
                "compliant": gdata["compliant"],
                "pct": pct,
                "file_path": "",
            }
        )
        links.append({"source": "root", "target": f"group:{gkey}", "link_type": "root-module"})

    # Link resources to groups
    for n in nodes:
        if n["type"] == "resource":
            svc = _service_from_type(n["resource_type"])
            gkey = f"svc:{svc}"
            links.append(
                {
                    "source": f"group:{gkey}",
                    "target": n["id"],
                    "link_type": "module-resource",
                }
            )

    # Common tags
    common_tags = _compute_common_tags(all_tag_dicts)
    for n in nodes:
        if n["type"] == "resource":
            n["unique_tags"] = {
                k: v for k, v in n.get("tags", {}).items() if k not in common_tags or common_tags[k] != v
            }

    total = len(resources)
    return {
        "nodes": nodes,
        "links": links,
        "common_tags": common_tags,
        "stats": {
            "total": total,
            "managed": total,
            "unmanaged": 0,
            "compliant": total - nc_count,
            "non_compliant": nc_count,
        },
    }


def _compute_common_tags(tag_dicts):
    """Find tags present in >80% of resources."""
    if not tag_dicts:
        return {}
    counts = {}
    for tags in tag_dicts:
        for k, v in tags.items():
            pair = (k, v)
            counts[pair] = counts.get(pair, 0) + 1
    threshold = len(tag_dicts) * 0.8
    return {k: v for (k, v), count in counts.items() if count >= threshold}
