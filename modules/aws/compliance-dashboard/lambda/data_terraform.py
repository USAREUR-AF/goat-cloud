"""
Data layer — Terraform state, module tree, infra graph, and import hints.
"""

import json
import os
import re

import boto3
from data_core import (
    _CACHE_TTL_TF_STATE,
    LOCAL_DATA_DIR,
    PROJECT,
    REGION,
    REPOSITORY_URL,
    TF_STATE_BUCKET,
    TF_STATE_KEY,
    _cache_get,
    _cache_set,
    s3,
)


def _load_module_file_map():
    """Top-level module address → source file path, from MODULE_FILE_MAP (JSON object).

    Empty by default; a deployment passes its own map to get source links.
    """
    try:
        mapping = json.loads(os.environ.get("MODULE_FILE_MAP") or "{}")
    except ValueError:
        print("WARNING: MODULE_FILE_MAP is not valid JSON; ignoring it")
        return {}
    return {str(k): str(v) for k, v in mapping.items()} if isinstance(mapping, dict) else {}


_MODULE_FILE_MAP = _load_module_file_map()

_REPO_URL = REPOSITORY_URL


def _repo_file_url(repo_base, file_path):
    """Link to file_path in a repository; "" unless the base (a tag value) is an http(s) URL."""
    from views_base import _safe_url

    return _safe_url(f"{repo_base}/blob/main/{file_path}") if file_path else ""


def get_tf_resource_info(resource_arn):
    """Look up a resource in Terraform state and return its module path and file location.

    Returns dict with: module_path, tf_address, resource_type, file_path, repo_url, found
    """
    state = _load_tf_state()
    if not state:
        return {"found": False, "reason": "TF state not configured or unavailable"}

    # Search managed resources for matching ARN (skip data sources)
    for resource in state.get("resources", []):
        if resource.get("mode") == "data":
            continue
        module = resource.get("module", "")
        rtype = resource.get("type", "")
        rname = resource.get("name", "")

        for inst in resource.get("instances", []):
            attrs = inst.get("attributes", {})
            arn = attrs.get("arn", "")
            # Also check id for resources like EBS volumes that use volume ID
            rid = attrs.get("id", "")

            if arn == resource_arn or (rid and resource_arn.endswith(f"/{rid}")):
                tf_address = f"{module}.{rtype}.{rname}" if module else f"{rtype}.{rname}"

                # Find the file from the top-level module
                top_module = module.split(".")[0] + "." + module.split(".")[1] if "." in module else module
                file_path = _MODULE_FILE_MAP.get(top_module, "")

                # Collect current tags from state
                tags = attrs.get("tags", {}) or {}

                return {
                    "found": True,
                    "module_path": module,
                    "tf_address": tf_address,
                    "tf_type": rtype,
                    "tf_name": rname,
                    "file_path": file_path,
                    "repo_url": _repo_file_url(tags.get("Repository", _REPO_URL), file_path),
                    "tags": tags,
                    "top_module": top_module,
                }

    return {"found": False, "reason": "Resource not found in Terraform state"}


def _load_tf_state():
    """Load TF state from S3 (or local file). Returns parsed JSON or None.

    Cached 5 min — TF state only changes on terraform apply.
    """
    if LOCAL_DATA_DIR:
        path = os.path.join(LOCAL_DATA_DIR, "terraform.tfstate")
        try:
            with open(path) as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading local TF state {path}: {e}")
            return None

    cached = _cache_get("tf_state")
    if cached is not None:
        return cached

    if not TF_STATE_BUCKET or not TF_STATE_KEY:
        return None
    try:
        response = s3.get_object(Bucket=TF_STATE_BUCKET, Key=TF_STATE_KEY)
        state = json.loads(response["Body"].read().decode("utf-8"))
        _cache_set("tf_state", state, _CACHE_TTL_TF_STATE)
        return state
    except Exception as e:
        print(f"Error loading TF state s3://{TF_STATE_BUCKET}/{TF_STATE_KEY}: {e}")
        return None


_EMPTY_TF_STATE = {"resources": []}


def get_volume_parent_instance(resource_arn):
    """Trace an EBS volume to its parent EC2 instance in TF state.

    Searches all aws_instance resources for root_block_device or ebs_block_device
    whose volume_id matches. Returns dict with instance info or None.
    """
    vol_id = resource_arn.split("/")[-1] if "/" in resource_arn else ""
    if not vol_id or not vol_id.startswith("vol-"):
        return None

    state = _load_tf_state()
    if not state:
        return None

    for resource in state.get("resources", []):
        if resource.get("type") != "aws_instance":
            continue
        module = resource.get("module", "")
        rname = resource.get("name", "")

        for inst in resource.get("instances", []):
            attrs = inst.get("attributes", {})

            # Check root_block_device
            for dev in attrs.get("root_block_device", []):
                if dev.get("volume_id") == vol_id:
                    return _build_instance_result(module, rname, attrs, dev, is_root=True)

            # Check ebs_block_device
            for dev in attrs.get("ebs_block_device", []):
                if dev.get("volume_id") == vol_id:
                    return _build_instance_result(module, rname, attrs, dev, is_root=False)

    return None


def _build_instance_result(module, rname, attrs, device, is_root):
    """Build the result dict for a volume's parent instance."""
    tf_address = f"{module}.aws_instance.{rname}" if module else f"aws_instance.{rname}"
    top_module = module.split(".")[0] + "." + module.split(".")[1] if "." in module else module
    file_path = _MODULE_FILE_MAP.get(top_module, "")

    return {
        "instance_id": attrs.get("id", ""),
        "instance_arn": attrs.get("arn", ""),
        "tf_address": tf_address,
        "top_module": top_module,
        "file_path": file_path,
        "repo_url": _repo_file_url((attrs.get("tags") or {}).get("Repository", _REPO_URL), file_path),
        "tags": attrs.get("tags", {}) or {},
        "is_root_volume": is_root,
        "device_name": device.get("device_name", ""),
    }


def get_volume_live_context(volume_id, region=None):
    """Look up an EBS volume via EC2 API and return attachment + instance info.

    Returns dict with volume state, attachment info, and parent instance details
    (name, state, tags) — or None if the volume can't be found.
    """
    try:
        ec2 = boto3.client("ec2", region_name=region)
        vols = ec2.describe_volumes(VolumeIds=[volume_id])
        volumes = vols.get("Volumes", [])
        if not volumes:
            return None

        vol = volumes[0]
        result = {
            "volume_state": vol.get("State", ""),
            "volume_size_gb": vol.get("Size", 0),
            "volume_type": vol.get("VolumeType", ""),
            "attached": False,
            "instance": None,
        }

        attachments = vol.get("Attachments", [])
        if not attachments:
            return result

        att = attachments[0]
        instance_id = att.get("InstanceId", "")
        result["attached"] = True
        result["device_name"] = att.get("Device", "")
        result["delete_on_termination"] = att.get("DeleteOnTermination", False)

        if not instance_id:
            return result

        # Fetch the parent instance
        instances = ec2.describe_instances(InstanceIds=[instance_id])
        for res in instances.get("Reservations", []):
            for inst in res.get("Instances", []):
                inst_tags = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}
                result["instance"] = {
                    "instance_id": inst.get("InstanceId", ""),
                    "state": inst.get("State", {}).get("Name", ""),
                    "name": inst_tags.get("Name", ""),
                    "instance_type": inst.get("InstanceType", ""),
                    "tags": inst_tags,
                    "launch_time": str(inst.get("LaunchTime", "")),
                }
                break
            break

        return result
    except Exception as e:
        print(f"Error looking up volume {volume_id}: {e}")
        return None


def _infer_arch_meta(resources):
    """Derive architecture visualization metadata from resource tags.

    Uses majority-vote across a module's resources for ArchZone, ArchRole,
    ArchVisible defaults to True unless any resource explicitly sets it to "false".
    """
    zone_votes = {}
    role_votes = {}
    visible = True
    for r in resources:
        tags = r.get("tags") or {}
        az = tags.get("ArchZone", "")
        if az:
            zone_votes[az] = zone_votes.get(az, 0) + 1
        ar = tags.get("ArchRole", "")
        if ar:
            role_votes[ar] = role_votes.get(ar, 0) + 1
        if tags.get("ArchVisible", "").lower() == "false":
            visible = False
    zone = max(zone_votes, key=zone_votes.get) if zone_votes else None
    role = max(role_votes, key=role_votes.get) if role_votes else None
    return {"zone": zone, "role": role, "visible": visible}


def get_tf_module_tree(report):
    """Parse TF state into a module hierarchy with compliance cross-reference.

    Returns a list of top-level module dicts, each containing:
      - module: top-level module name (e.g., "module.web")
      - file_path: source file in the repo
      - resources: list of resource dicts with compliance status
      - children: dict of child module names → resource lists
      - stats: { total, compliant, non_compliant, cost }
      - arch: { zone, role, visible } from ArchZone/ArchRole/ArchVisible tags
    """
    state = _load_tf_state()
    if not state:
        return []

    # Build set of non-compliant ARNs from compliance report
    nc_arns = set()
    if report:
        for category in ["drift_detection", "tag_compliance", "schedule_compliance", "lifecycle_compliance"]:
            for f in report.get(category, {}).get("findings", []):
                nc_arns.add(f.get("resource_arn", ""))

    # Build ARN → estimated monthly cost lookup from discovered resources.
    # Values are float | None.  None means "scanner has no pricing model for
    # this resource type" — semantically different from 0 ("genuinely free").
    cost_by_arn = {}
    if report:
        for r in report.get("discovered_resources", []):
            arn = r.get("arn", "")
            if arn:
                raw = r.get("estimated_monthly_cost")
                cost_by_arn[arn] = float(raw) if raw is not None else None

    # Parse every resource from state into module buckets
    modules = {}  # top_module -> { children: { full_module -> [resources] }, ... }

    for resource in state.get("resources", []):
        if resource.get("mode") == "data":
            continue  # Skip data sources — not managed resources
        module = resource.get("module", "")
        rtype = resource.get("type", "")
        rname = resource.get("name", "")

        # Derive top-level module, stripping count/for_each index (e.g. [0])
        parts = module.split(".") if module else []
        if len(parts) >= 2:
            top_module = f"{parts[0]}.{parts[1]}"
        elif module:
            top_module = module
        else:
            top_module = "(root)"
        # Normalize: module.web[0] → module.web
        top_module = re.sub(r"\[\d+\]$", "", top_module)

        for inst in resource.get("instances", []):
            attrs = inst.get("attributes", {})
            arn = attrs.get("arn", "")
            rid = attrs.get("id", "")
            tags = attrs.get("tags", {}) or {}
            name = tags.get("Name", "") or rname

            # Check compliance by ARN match only (compliance report uses ARNs)
            # Resources without ARNs (data sources, attachments) are treated as
            # compliant — the scanner doesn't produce findings for them.
            compliant = True
            if arn:
                # Direct match
                if arn in nc_arns:
                    compliant = False
                else:
                    # Normalized match (strip trailing :* from log group ARNs)
                    normalized = arn.rstrip("*").rstrip(":")
                    if normalized in nc_arns:
                        compliant = False

            res_entry = {
                "tf_address": f"{module}.{rtype}.{rname}" if module else f"{rtype}.{rname}",
                "type": rtype,
                "name": name,
                "arn": arn,
                "id": rid,
                "compliant": compliant,
                "tags": tags,
                "estimated_monthly_cost": cost_by_arn.get(arn),  # None = no model
            }

            if top_module not in modules:
                modules[top_module] = {"children": {}}
            child_key = module or "(root)"
            if child_key not in modules[top_module]["children"]:
                modules[top_module]["children"][child_key] = []
            modules[top_module]["children"][child_key].append(res_entry)

    # Build output sorted by module name
    result = []
    for top_module in sorted(modules.keys()):
        data = modules[top_module]
        all_resources = []
        children = {}
        for child_key, resources in sorted(data["children"].items()):
            all_resources.extend(resources)
            # Only show child if different from top module
            if child_key != top_module:
                children[child_key] = resources

        total = len(all_resources)
        compliant_count = sum(1 for r in all_resources if r["compliant"])
        non_compliant = total - compliant_count
        # Sum only known (non-None) costs.  Track how many are unknown.
        known_costs = [
            r["estimated_monthly_cost"] for r in all_resources if r.get("estimated_monthly_cost") is not None
        ]
        mod_cost = sum(known_costs)
        cost_unknown = total - len(known_costs)

        result.append(
            {
                "module": top_module,
                "file_path": _MODULE_FILE_MAP.get(top_module, ""),
                "resources": all_resources,
                "children": children,
                "stats": {
                    "total": total,
                    "compliant": compliant_count,
                    "non_compliant": non_compliant,
                    "cost": round(mod_cost, 2),
                    "cost_unknown": cost_unknown,
                },
                "arch": _infer_arch_meta(all_resources),
            }
        )

    return result


def get_full_infra_graph(report):
    """Build a complete infra graph combining TF state + all discovered resources.

    Returns a dict with nodes, links, common_tags, and stats for D3 rendering.
    Node types: root, module, service_group, resource.
    Resources are linked to their TF module (if managed) or service group (if unmanaged).
    """
    from views_base import _console_url

    state = _load_tf_state()

    # 1. Build TF-managed ARN → module mapping from state
    tf_managed = {}  # arn -> {module, tf_address}
    if state:
        for resource in state.get("resources", []):
            if resource.get("mode") == "data":
                continue
            module = resource.get("module", "")
            rtype = resource.get("type", "")
            rname = resource.get("name", "")
            parts = module.split(".") if module else []
            top_module = f"{parts[0]}.{parts[1]}" if len(parts) >= 2 else (module or "(root)")
            for inst in resource.get("instances", []):
                attrs = inst.get("attributes", {})
                arn = attrs.get("arn", "")
                if arn:
                    tf_managed[arn] = {
                        "module": top_module,
                        "tf_address": f"{module}.{rtype}.{rname}" if module else f"{rtype}.{rname}",
                    }

    # 2. Build non-compliant ARN set from findings
    nc_arns = set()
    if report:
        for cat in ["drift_detection", "tag_compliance", "schedule_compliance", "lifecycle_compliance"]:
            for f in report.get(cat, {}).get("findings", []):
                nc_arns.add(f.get("resource_arn", ""))

    # 3. Process all discovered resources
    discovered = report.get("discovered_resources", []) if report else []
    project = report.get("project", PROJECT) if report else PROJECT
    environment = report.get("environment", "dev") if report else "dev"

    nodes = []
    links = []
    modules_seen = {}  # module_name -> {total, compliant}
    service_groups_seen = {}  # service -> {total, compliant}
    all_tag_dicts = []
    managed_count = 0

    # Root node
    nodes.append(
        {
            "id": "root",
            "type": "root",
            "name": f"{project}-{environment}",
        }
    )

    for r in discovered:
        arn = r.get("arn", "")
        if not arn:
            continue

        tags = r.get("tags", {}) or {}
        all_tag_dicts.append(tags)
        managed = arn in tf_managed
        compliant = arn not in nc_arns
        resource_type = r.get("resource_type", "")
        region = r.get("region") or REGION
        cost = r.get("estimated_monthly_cost")
        console_url = _console_url(arn, region)

        if managed:
            managed_count += 1

        node = {
            "id": arn,
            "type": "resource",
            "name": r.get("name", ""),
            "resource_type": resource_type,
            "arn": arn,
            "tags": tags,
            "managed": managed,
            "compliant": compliant,
            "cost": cost,
            "region": region,
            "console_url": console_url,
        }

        if managed:
            tf_info = tf_managed[arn]
            mod = tf_info["module"]
            node["module"] = mod
            node["tf_address"] = tf_info["tf_address"]

            if mod not in modules_seen:
                modules_seen[mod] = {"total": 0, "compliant": 0}
            modules_seen[mod]["total"] += 1
            if compliant:
                modules_seen[mod]["compliant"] += 1
        else:
            # Group unmanaged resources by Service tag, then by AWS service type
            service_tag = tags.get("Service", "")
            if service_tag:
                group_key = f"svc:{service_tag}"
                group_name = service_tag.title()
            else:
                svc_prefix = resource_type.split(":")[0] if ":" in resource_type else "other"
                group_key = f"aws:{svc_prefix}"
                group_name = svc_prefix.upper()
            node["group"] = group_key

            if group_key not in service_groups_seen:
                service_groups_seen[group_key] = {"name": group_name, "total": 0, "compliant": 0}
            service_groups_seen[group_key]["total"] += 1
            if compliant:
                service_groups_seen[group_key]["compliant"] += 1

        nodes.append(node)

    # 4. Add module nodes and links
    for mod, stats in sorted(modules_seen.items()):
        pct = round(100 * stats["compliant"] / stats["total"]) if stats["total"] else 100
        nodes.append(
            {
                "id": f"module:{mod}",
                "type": "module",
                "name": mod,
                "total": stats["total"],
                "compliant": stats["compliant"],
                "pct": pct,
                "file_path": _MODULE_FILE_MAP.get(mod, ""),
            }
        )
        links.append({"source": "root", "target": f"module:{mod}", "link_type": "root-module"})

    # Link managed resources to their modules
    for n in nodes:
        if n["type"] == "resource" and n.get("managed"):
            links.append(
                {
                    "source": f"module:{n['module']}",
                    "target": n["id"],
                    "link_type": "module-resource",
                }
            )

    # 5. Add service group nodes and links
    for gkey, gdata in sorted(service_groups_seen.items()):
        pct = round(100 * gdata["compliant"] / gdata["total"]) if gdata["total"] else 100
        nodes.append(
            {
                "id": f"group:{gkey}",
                "type": "service_group",
                "name": gdata["name"],
                "total": gdata["total"],
                "compliant": gdata["compliant"],
                "pct": pct,
            }
        )
        links.append({"source": "root", "target": f"group:{gkey}", "link_type": "root-group"})

    # Link unmanaged resources to their groups
    for n in nodes:
        if n["type"] == "resource" and not n.get("managed") and n.get("group"):
            links.append(
                {
                    "source": f"group:{n['group']}",
                    "target": n["id"],
                    "link_type": "group-resource",
                }
            )

    # 6. Compute common tags (present in >80% of resources with same key:value)
    common_tags = _compute_common_tags(all_tag_dicts)

    # Mark unique tags on each resource node (tags not in common set)
    for n in nodes:
        if n["type"] == "resource":
            n["unique_tags"] = {
                k: v for k, v in n.get("tags", {}).items() if k not in common_tags or common_tags[k] != v
            }

    unmanaged = len(discovered) - managed_count
    return {
        "nodes": nodes,
        "links": links,
        "common_tags": common_tags,
        "stats": {
            "total": len(discovered),
            "managed": managed_count,
            "unmanaged": unmanaged,
            "compliant": len(discovered) - len([a for a in nc_arns if any(r.get("arn") == a for r in discovered)]),
            "non_compliant": len([a for a in nc_arns if any(r.get("arn") == a for r in discovered)]),
        },
    }


def _compute_common_tags(tag_dicts):
    """Find tags (key:value pairs) present in >80% of resources."""
    if not tag_dicts:
        return {}
    counts = {}
    for tags in tag_dicts:
        for k, v in tags.items():
            pair = (k, v)
            counts[pair] = counts.get(pair, 0) + 1
    threshold = len(tag_dicts) * 0.8
    return {k: v for (k, v), count in counts.items() if count >= threshold}


def get_import_hint(resource_arn, resource_type=""):
    """Generate a terraform import command hint for an untracked resource."""
    # Map ARN patterns to TF resource types and import IDs
    parts = resource_arn.split(":")
    if len(parts) < 6:
        return None

    resource_part = parts[5]  # e.g., "volume/vol-xxx", "instance/i-xxx"
    parts[3]
    parts[4]

    hints = {
        "volume": ("aws_ebs_volume", resource_part.split("/")[-1]),
        "instance": ("aws_instance", resource_part.split("/")[-1]),
        "security-group": ("aws_security_group", resource_part.split("/")[-1]),
        "subnet": ("aws_subnet", resource_part.split("/")[-1]),
        "vpc": ("aws_vpc", resource_part.split("/")[-1]),
        "db": ("aws_db_instance", resource_part.split(":")[-1] if ":" in resource_part else resource_part),
        "cluster": ("aws_elasticache_cluster", resource_part.split(":")[-1] if ":" in resource_part else resource_part),
    }

    # Match the resource type prefix
    prefix = resource_part.split("/")[0] if "/" in resource_part else resource_part.split(":")[0]
    if prefix in hints:
        tf_type, import_id = hints[prefix]
        return {
            "tf_type": tf_type,
            "import_id": import_id,
            "command": f"terraform import {tf_type}.RESOURCE_NAME {import_id}",
        }

    return None
