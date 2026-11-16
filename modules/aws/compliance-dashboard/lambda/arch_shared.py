"""
Shared architecture metadata — dynamic service discovery from TF state.

get_module_meta() discovers services from the live Terraform state, enriched
with display metadata from _ENRICHMENT.  CONNECTIONS defines service-to-service
edges (filtered to active services via get_active_connections()).

SUBMODULE_DISPLAY maps child module name segments to human-readable labels.
COST_TIERS classifies TF resource types by cost impact for visualization.

Renderer-specific visual properties (SVG icons, shapes) are NOT stored
here — each renderer maintains its own local mapping from the canonical
``icon`` key to its visual representation.
"""

import os
import re

import site_config
from data_core import DOMAIN_NAME, REGION

_ENV = os.environ.get("ENVIRONMENT", "dev")
_DOMAIN = DOMAIN_NAME
_REGION = REGION

# ─────────────────────────────────────────────────────────────────────────────
# Enrichment — optional display overrides for data that can't be auto-derived.
#
# Keys are TF module addresses.  Only fields present here override auto-detected
# values.  Modules not listed still appear — they just get auto-generated names
# and descriptions.
# ─────────────────────────────────────────────────────────────────────────────

_BUILTIN_ENRICHMENT = {
    "module.alb": {
        "name": "ALB",
        "desc": "Application Load Balancer — routes all HTTPS traffic to backend services",
        "icon": "alb",
    },
    "module.compliance_scanner": {
        "name": "Scanner",
        "desc": "Nightly compliance scanning — writes reports to DynamoDB + S3",
    },
    "module.hunterkiller": {
        "name": "Hunterkiller",
        "desc": "Automated compliance enforcement",
    },
    "module.compliance_dashboard": {
        "name": "Dashboard",
        "desc": "Compliance dashboard — reads report data from DynamoDB via ALB",
    },
    "module.cloudtrail": {
        "name": "CloudTrail",
        "tier": "platform",
        "desc": "Audit logging with S3 storage",
        "icon": "trail",
        "console": f"https://{_REGION}.console.aws.amazon.com/cloudtrailv2/home?region={_REGION}#/trails",
    },
    "module.governance": {
        "name": "Governance",
        "tier": "platform",
        "desc": "Tag policies and governance alerts via SNS",
        "icon": "shield",
        "console": f"https://{_REGION}.console.aws.amazon.com/sns/v3/home?region={_REGION}#/topics",
    },
    "module.monitoring": {
        "name": "CloudWatch",
        "tier": "platform",
        "desc": "Alarms, VPC flow logs, and metrics",
        "icon": "monitoring",
        "console": f"https://{_REGION}.console.aws.amazon.com/cloudwatch/home?region={_REGION}#alarmsV2:",
    },
    "module.guardduty": {
        "name": "GuardDuty",
        "tier": "platform",
        "desc": "Threat detection and alerting",
        "icon": "guardduty",
        "console": f"https://{_REGION}.console.aws.amazon.com/guardduty/home?region={_REGION}#/findings",
    },
    "module.vpc": {
        "name": "VPC",
        "tier": "platform",
        "desc": "Network backbone with public and private subnets",
        "icon": "vpc",
        "console": f"https://{_REGION}.console.aws.amazon.com/vpcconsole/home?region={_REGION}#vpcs:",
    },
    "module.ecs": {
        "name": "ECS Cluster",
        "tier": "platform",
        "desc": "Fargate cluster providing shared compute capacity",
        "console": f"https://{_REGION}.console.aws.amazon.com/ecs/v2/clusters?region={_REGION}",
    },
    "module.waf": {
        "name": "WAF",
        "tier": "platform",
        "desc": "OWASP and managed rule groups on ALB",
        "icon": "waf",
        "console": f"https://us-east-1.console.aws.amazon.com/wafv2/homev2/web-acls?region={_REGION}",
    },
    "module.ecr": {
        "name": "ECR",
        "tier": "platform",
        "desc": "Container registry for Docker images",
        "console": f"https://{_REGION}.console.aws.amazon.com/ecr/repositories?region={_REGION}",
    },
    "module.primary_certificate": {
        "name": "Primary Cert",
        "tier": "platform",
        "desc": f"ACM wildcard certificate for *.{_ENV}.{_DOMAIN}",
        "icon": "shield",
        "console": f"https://{_REGION}.console.aws.amazon.com/acm/home?region={_REGION}#/certificates/list",
    },
}

# Built-in entries with the deployment's own (site.json) merged over them.
_ENRICHMENT = {
    addr: {**_BUILTIN_ENRICHMENT.get(addr, {}), **site_config.get("module_enrichment").get(addr, {})}
    for addr in [*_BUILTIN_ENRICHMENT, *site_config.get("module_enrichment")]
}

# ─────────────────────────────────────────────────────────────────────────────
# Auto-detection: resource type → service type label and icon key
# ─────────────────────────────────────────────────────────────────────────────

_TYPE_PRIORITY = [
    # (resource type, display type, icon) — first match wins
    ("aws_ecs_service", "ECS Fargate", "ecs"),
    ("aws_lambda_function", "Lambda", "lambda"),
    ("aws_instance", "EC2", "ec2"),
    ("aws_elasticache_replication_group", "ElastiCache", "redis"),
    ("aws_elasticache_cluster", "ElastiCache", "redis"),
    ("aws_lb", "ALB", "alb"),
    ("aws_wafv2_web_acl", "WAF", "waf"),
    ("aws_cloudtrail", "CloudTrail", "trail"),
    ("aws_guardduty_detector", "GuardDuty", "guardduty"),
    ("aws_vpc", "VPC", "vpc"),
    ("aws_ecr_repository", "ECR", "ecs"),
    ("aws_acm_certificate", "ACM", "shield"),
    ("aws_cloudwatch_metric_alarm", "CloudWatch", "monitoring"),
    ("aws_sns_topic", "SNS + Lambda", "shield"),
    ("aws_dynamodb_table", "DynamoDB", "lambda"),
    ("aws_s3_bucket", "S3", "lambda"),
]

# ─────────────────────────────────────────────────────────────────────────────
# Module metadata — dynamically discovered from TF state.
# ─────────────────────────────────────────────────────────────────────────────

_module_meta_cache = None
_module_meta_ts = 0
_MODULE_META_TTL = 300  # 5 min — same as TF state cache


def get_module_meta():
    """Discover services from TF state, enriched with _ENRICHMENT overrides.

    Returns a dict keyed by module address (e.g., "module.alb"), each value
    containing: name, zone, icon, type, desc, host, auth, tier, notes, console.

    Cached for 5 minutes (same TTL as TF state).
    """
    import time

    global _module_meta_cache, _module_meta_ts
    now = time.time()
    if _module_meta_cache is not None and (now - _module_meta_ts) < _MODULE_META_TTL:
        return _module_meta_cache

    result = _discover_from_state()
    _module_meta_cache = result
    _module_meta_ts = now
    return result


def _discover_from_state():
    """Build module metadata from live TF state."""
    from data_terraform import _load_tf_state

    state = _load_tf_state()
    if not state:
        return {}

    # Pass 1: group resources by top-level module
    modules = {}  # top_module -> list of resource dicts
    for resource in state.get("resources", []):
        if resource.get("mode") == "data":
            continue
        module = resource.get("module", "")
        rtype = resource.get("type", "")

        parts = module.split(".") if module else []
        if len(parts) >= 2:
            top_module = f"{parts[0]}.{parts[1]}"
        elif module:
            top_module = module
        else:
            top_module = "(root)"
        top_module = re.sub(r"\[\d+\]$", "", top_module)

        if top_module == "(root)":
            continue

        if top_module not in modules:
            modules[top_module] = {"types": set(), "resources": [], "hosts": set(), "has_oidc": False}

        modules[top_module]["types"].add(rtype)

        for inst in resource.get("instances", []):
            attrs = inst.get("attributes", {})
            tags = attrs.get("tags", {}) or {}
            modules[top_module]["resources"].append({"type": rtype, "tags": tags, "attrs": attrs, "module": module})

            # Extract hostnames from listener rules
            if rtype == "aws_lb_listener_rule":
                for cond in attrs.get("condition", []):
                    hh = cond.get("host_header")
                    if isinstance(hh, dict):
                        for h in hh.get("values", []):
                            if not h.startswith("*"):
                                modules[top_module]["hosts"].add(h)
                    elif isinstance(hh, list):
                        for item in hh:
                            if isinstance(item, dict):
                                for h in item.get("values", []):
                                    if not h.startswith("*"):
                                        modules[top_module]["hosts"].add(h)
                # Check for OIDC auth
                actions = attrs.get("action", [])
                if any(a.get("type") == "authenticate-oidc" for a in actions):
                    modules[top_module]["has_oidc"] = True

            # Extract hostnames from Route53 records within the module
            if rtype == "aws_route53_record":
                fqdn = attrs.get("fqdn", "") or attrs.get("name", "")
                if fqdn and not fqdn.startswith("_"):
                    modules[top_module]["hosts"].add(fqdn.rstrip("."))

    # Pass 2: build metadata for each module
    result = {}
    for module_addr, mod_data in sorted(modules.items()):
        enrich = _ENRICHMENT.get(module_addr, {})
        short_name = module_addr.replace("module.", "")

        # Type detection
        svc_type, icon = _detect_type(mod_data["types"])
        icon = enrich.get("icon", icon)
        svc_type = enrich.get("type", svc_type)

        # Zone from ArchZone tags (majority vote)
        zone = _detect_zone(mod_data["resources"])
        zone = enrich.get("zone", zone) or zone

        # Hostname — first non-wildcard host found, or enrichment override
        host = ""
        if mod_data["hosts"]:
            host = sorted(mod_data["hosts"])[0]
        host = enrich.get("host", host)

        # Auth — auto-detect OIDC, allow enrichment override
        auth = ""
        if mod_data["has_oidc"]:
            auth = f"{site_config.get('oidc_provider_name')} OIDC".strip()
        auth = enrich.get("auth", auth) or auth

        # Tier — enrichment or infer from zone
        tier = enrich.get("tier", "")
        if not tier:
            tier = "platform" if zone in ("security", "infra") and not host else "hosted"

        # Display name — enrichment or humanize module name
        name = enrich.get("name", short_name.replace("_", " ").title())

        result[module_addr] = {
            "name": name,
            "zone": zone,
            "icon": icon,
            "type": svc_type,
            "host": host,
            "auth": auth,
            "desc": enrich.get("desc", ""),
            "tier": tier,
            "notes": enrich.get("notes", ""),
            "console": enrich.get("console", ""),
        }

    return result


def _detect_type(resource_types):
    """Determine primary service type and icon from resource types in a module."""
    for tf_type, label, icon in _TYPE_PRIORITY:
        if tf_type in resource_types:
            return label, icon
    return "Other", "ecs"


def _detect_zone(resources):
    """Detect architectural zone from ArchZone tags (majority vote)."""
    votes = {}
    for r in resources:
        az = r.get("tags", {}).get("ArchZone", "")
        if az:
            votes[az] = votes.get(az, 0) + 1
    if votes:
        return max(votes, key=votes.get)
    return "infra"


def invalidate_module_meta_cache():
    """Force re-discovery on next get_module_meta() call."""
    global _module_meta_cache, _module_meta_ts
    _module_meta_cache = None
    _module_meta_ts = 0


# ─────────────────────────────────────────────────────────────────────────────
# Service connections — directed edges between services.
#
# Each edge is a dict with:
#   from     — source service ID (short form, no "module." prefix)
#   to       — target service ID
#   type     — edge type for visual styling: http | auth | data | event | api | message
#   label    — short human-readable label shown on hover / in legends
#   protocol — (optional) protocol detail
#   async    — (optional) True if the interaction is asynchronous
#   weight   — (optional) 0=auxiliary, 1=normal (default), 2=critical path
#
# IDs are the short form (module address with "module." prefix stripped),
# plus the id of the deployment's edge node, when it declares one.
# ─────────────────────────────────────────────────────────────────────────────

# Edge type visual styles — consumed by renderers for color/dash/arrow
EDGE_STYLES = {
    "http": {"color": "#3b82f6", "dash": "", "label": "HTTP"},
    "auth": {"color": "#a855f7", "dash": "6,3", "label": "Auth"},
    "data": {"color": "#22c55e", "dash": "3,3", "label": "Data"},
    "event": {"color": "#f97316", "dash": "6,3", "label": "Event"},
    "api": {"color": "#f59e0b", "dash": "", "label": "API"},
    "message": {"color": "#06b6d4", "dash": "", "label": "Message"},
}

# The edges between this tooling's own modules. A deployment's site.json
# replaces the list with its full topology.
_BUILTIN_CONNECTIONS = [
    {
        "from": "compliance_scanner",
        "to": "compliance_dashboard",
        "type": "data",
        "label": "S3 reports + DynamoDB",
        "async": True,
        "weight": 0,
    },
    {
        "from": "hunterkiller",
        "to": "compliance_scanner",
        "type": "event",
        "label": "Reads scan reports",
        "async": True,
        "weight": 0,
    },
]

CONNECTIONS = site_config.get("connections") or _BUILTIN_CONNECTIONS

# The deployment's internet edge (site.json, ``edge_node``), a node that is not
# a Terraform module. None when the deployment declares none.
EDGE_NODE = site_config.get("edge_node")
EDGE_ID = EDGE_NODE["id"] if EDGE_NODE else None

# Special non-module node IDs that are always considered active.
_SPECIAL_NODES = {EDGE_ID} if EDGE_ID else set()


def get_active_connections():
    """Return CONNECTIONS filtered to only include edges where both endpoints
    exist in the current TF state (or are special nodes).
    """
    meta = get_module_meta()
    active_ids = {addr.replace("module.", "") for addr in meta}
    active_ids |= _SPECIAL_NODES
    return [c for c in CONNECTIONS if c["from"] in active_ids and c["to"] in active_ids]


def get_connection_pair(conn):
    """Extract (source, target) from a connection (dict or legacy tuple)."""
    if isinstance(conn, dict):
        return conn["from"], conn["to"]
    return conn[0], conn[1]


def get_connections_for(short_name):
    """Return (inbound, outbound) connection lists for a service.

    Only includes connections where both endpoints are active.
    """
    active = get_active_connections()
    inbound = [c for c in active if get_connection_pair(c)[1] == short_name]
    outbound = [c for c in active if get_connection_pair(c)[0] == short_name]
    return inbound, outbound


def get_service_subgraph(short_name):
    """Return (node_set, edge_list) for a service-perspective subgraph.

    Algorithm:
      1. Focal service + all 1-hop neighbors (direct connections)
      2. Upstream path: if ALB is a neighbor, include the edge node
      3. Auth chain: if an identity node is a neighbor,
         follow its auth/data edges one more hop
      4. Include all edges between the final node set

    This avoids the ALB-hub problem (2-hop BFS through ALB gives the
    entire graph) by only extending through identity/upstream paths.
    """
    active = get_active_connections()

    # 1. Direct connections → initial node set
    inbound, outbound = get_connections_for(short_name)
    nodes = {short_name}
    for conn in inbound + outbound:
        src, tgt = get_connection_pair(conn)
        nodes.add(src)
        nodes.add(tgt)

    # 2. Upstream path: edge → alb (if alb is in the neighborhood)
    if EDGE_ID and "alb" in nodes and short_name != EDGE_ID:
        nodes.add(EDGE_ID)

    # 3. Auth chain extension from identity-stack nodes
    identity_nodes = set(site_config.get("identity_nodes"))
    for id_node in list(identity_nodes & nodes):
        for conn in active:
            src, tgt = get_connection_pair(conn)
            edge_type = conn.get("type", "http") if isinstance(conn, dict) else "http"
            if edge_type not in ("auth", "data"):
                continue
            if src == id_node and tgt not in nodes:
                nodes.add(tgt)

    # 4. All edges between final node set
    edges = []
    for conn in active:
        src, tgt = get_connection_pair(conn)
        if src in nodes and tgt in nodes:
            edges.append(conn)

    return nodes, edges


# ─────────────────────────────────────────────────────────────────────────────
# Sub-module display names — maps the last segment of a child module address
# to a human-readable name.  E.g., "module.wiki.module.database"
# extracts "database" and maps to "Database (RDS)".
# ─────────────────────────────────────────────────────────────────────────────
SUBMODULE_DISPLAY = {
    "database": "Database (RDS)",
    "service": "ECS Service",
    "ses_user": "Email (SES)",
    "certificate": "Certificate (ACM)",
    "alias_certificate": "Alias Certificate (ACM)",
    "redis": "Redis (ElastiCache)",
    "proxy": "Reverse Proxy",
    "efs": "Persistent Storage (EFS)",
    "ecr": "Container Registry (ECR)",
}


def get_submodule_display_name(child_module_address):
    """Extract a human-readable name from a child module address.

    E.g., ``module.wiki.module.database`` -> ``Database (RDS)``.
    Falls back to title-casing the last segment.
    """
    parts = child_module_address.split(".")
    last_name = parts[-1] if parts else child_module_address
    for i in range(len(parts) - 1, -1, -1):
        if parts[i] == "module" and i + 1 < len(parts):
            last_name = parts[i + 1]
            break
    last_name = re.sub(r"\[\d+\]$", "", last_name)
    return SUBMODULE_DISPLAY.get(last_name, last_name.replace("_", " ").title())


# ─────────────────────────────────────────────────────────────────────────────
# Cost tier classification — maps TF resource types to cost impact tiers
# for color-coding in architecture diagrams.
# ─────────────────────────────────────────────────────────────────────────────
COST_TIERS = {
    "free": {
        "label": "Free / Config",
        "color": "#6b7280",
        "types": {
            "aws_iam_role",
            "aws_iam_policy",
            "aws_iam_role_policy",
            "aws_iam_role_policy_attachment",
            "aws_iam_user",
            "aws_iam_user_policy_attachment",
            "aws_iam_access_key",
            "aws_security_group",
            "aws_security_group_rule",
            "aws_route53_record",
            "aws_acm_certificate",
            "aws_acm_certificate_validation",
            "aws_secretsmanager_secret",
            "aws_secretsmanager_secret_version",
            "aws_lb_listener_rule",
            "aws_lb_target_group",
            "aws_lb_listener_certificate",
            "aws_ecs_task_definition",
            "aws_db_subnet_group",
            "aws_elasticache_subnet_group",
            "random_password",
            "random_id",
            "aws_s3_bucket_public_access_block",
            "aws_s3_bucket_server_side_encryption_configuration",
            "aws_s3_bucket_cors_configuration",
        },
    },
    "near_zero": {
        "label": "Near-Zero (~$0/mo)",
        "color": "#22c55e",
        "types": {
            "aws_lambda_function",
            "aws_lambda_permission",
            "aws_lambda_layer_version",
            "aws_dynamodb_table",
            "aws_apigatewayv2_api",
            "aws_apigatewayv2_stage",
            "aws_apigatewayv2_route",
            "aws_apigatewayv2_integration",
            "aws_cloudwatch_event_rule",
            "aws_cloudwatch_event_target",
        },
    },
    "low": {
        "label": "Low (~$1-5/mo)",
        "color": "#3b82f6",
        "types": {
            "aws_s3_bucket",
            "aws_ecr_repository",
            "aws_cloudwatch_log_group",
            "aws_efs_file_system",
            "aws_efs_mount_target",
        },
    },
    "moderate": {
        "label": "Moderate (~$15-50/mo)",
        "color": "#f59e0b",
        "types": {
            "aws_ecs_service",
            "aws_elasticache_replication_group",
            "aws_elasticache_cluster",
            "aws_nat_gateway",
            "aws_lb",
        },
    },
    "high": {
        "label": "High ($50+/mo)",
        "color": "#ef4444",
        "types": {
            "aws_instance",
            "aws_db_instance",
        },
    },
}


def get_cost_tier(tf_resource_type):
    """Return ``(tier_key, color)`` for a Terraform resource type."""
    for tier_key, tier_data in COST_TIERS.items():
        if tf_resource_type in tier_data.get("types", set()):
            return tier_key, tier_data.get("color", "#6b7280")
    return "free", "#6b7280"


# Cost formatting delegated to the canonical cost_display module.
# Re-exported here for backward compatibility with existing imports.


ZONE_COLORS = {
    "public": "#22c55e",
    "apps": "#3b82f6",
    "identity": "#a855f7",
    "serverless": "#f97316",
    "security": "#ef4444",
    "infra": "#6b7280",
}


# ─────────────────────────────────────────────────────────────────────────────
# Notable resource types — maps TF resource types to display labels and
# descriptions for the infrastructure component visualization.
# Only "interesting" resource types are included — IAM roles, SGs, etc. skipped.
# ─────────────────────────────────────────────────────────────────────────────
NOTABLE_RESOURCE_TYPES = {
    "aws_ecs_service": {"label": "ECS Service", "desc": "Container service running on Fargate"},
    "aws_ecs_task_definition": {"label": "Task Def", "desc": "ECS task definition (container config)"},
    "aws_db_instance": {"label": "RDS", "desc": "Managed PostgreSQL database instance"},
    "aws_elasticache_replication_group": {"label": "ElastiCache", "desc": "Managed Redis cluster for caching/sessions"},
    "aws_elasticache_cluster": {"label": "ElastiCache", "desc": "Managed Redis cluster for caching/sessions"},
    "aws_lambda_function": {"label": "Lambda", "desc": "Serverless function (pay-per-invocation)"},
    "aws_instance": {"label": "EC2", "desc": "Virtual machine instance"},
    "aws_s3_bucket": {"label": "S3", "desc": "Object storage bucket"},
    "aws_efs_file_system": {"label": "EFS", "desc": "Elastic file system (persistent storage)"},
    "aws_dynamodb_table": {"label": "DynamoDB", "desc": "NoSQL key-value table"},
    "aws_apigatewayv2_api": {"label": "API Gateway", "desc": "HTTP API endpoint (Lambda integration)"},
    "aws_ecr_repository": {"label": "ECR", "desc": "Docker container image registry"},
    "aws_lb": {"label": "Load Balancer", "desc": "Application Load Balancer"},
    "aws_lb_target_group": {"label": "Target Group", "desc": "ALB target group for routing"},
    "aws_cloudwatch_log_group": {"label": "Log Group", "desc": "CloudWatch log group"},
    "aws_cloudtrail": {"label": "CloudTrail", "desc": "API audit logging trail"},
    "aws_guardduty_detector": {"label": "GuardDuty", "desc": "Threat detection service"},
    "aws_sns_topic": {"label": "SNS Topic", "desc": "Notification topic for alerts"},
    "aws_cloudwatch_metric_alarm": {"label": "Alarm", "desc": "CloudWatch metric alarm"},
    "aws_secretsmanager_secret": {"label": "Secret", "desc": "Secrets Manager secret (credentials)"},
    "aws_acm_certificate": {"label": "Certificate", "desc": "ACM TLS/SSL certificate"},
    "aws_route53_record": {"label": "DNS Record", "desc": "Route53 DNS record"},
    "aws_wafv2_web_acl": {"label": "WAF ACL", "desc": "Web Application Firewall rule set"},
    "aws_vpc": {"label": "VPC", "desc": "Virtual Private Cloud network"},
    "aws_subnet": {"label": "Subnet", "desc": "VPC subnet (public or private)"},
    "aws_nat_gateway": {"label": "NAT Gateway", "desc": "NAT gateway for private subnet egress"},
    "aws_lb_listener_rule": {"label": "Listener Rule", "desc": "ALB listener routing rule"},
}

# Component display colors by label — used in interaction graph child ring.
COMPONENT_COLORS = {
    "ECS Service": "#3b82f6",
    "Task Def": "#3b82f6",
    "RDS": "#3b82f6",
    "ElastiCache": "#ef4444",
    "Lambda": "#f97316",
    "EC2": "#f59e0b",
    "S3": "#22c55e",
    "EFS": "#22c55e",
    "DynamoDB": "#f97316",
    "API Gateway": "#a855f7",
    "ECR": "#6b7280",
    "Load Balancer": "#22c55e",
    "Target Group": "#22c55e",
    "Log Group": "#6b7280",
    "CloudTrail": "#ef4444",
    "GuardDuty": "#ef4444",
    "SNS Topic": "#f97316",
    "Alarm": "#ef4444",
    "Secret": "#a855f7",
    "Certificate": "#6b7280",
    "DNS Record": "#6b7280",
    "WAF ACL": "#ef4444",
    "VPC": "#22c55e",
    "Subnet": "#22c55e",
    "NAT Gateway": "#f59e0b",
    "Listener Rule": "#22c55e",
}
