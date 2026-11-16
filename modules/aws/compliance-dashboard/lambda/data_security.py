"""
Data layer — Security posture analysis (exposure, auth gaps, monitoring, container images, ECR).
"""

import json
import os
from datetime import UTC, datetime

import boto3
import site_config
from data_core import (
    _CACHE_TTL_SECURITY,
    IMAGE_VERSIONS_SSM,
    LOCAL_DATA_DIR,
    _cache_get,
    _cache_set,
    ssm,
)
from data_terraform import _load_tf_state


def get_security_posture():
    """Aggregate security posture from TF state, MODULE_META,
    image-versions.json, and ECR.

    Returns dict with:
      - exposure_map: list of services reachable from the internet
      - auth_gaps: per-service auth analysis with gap levels
      - monitoring: foundation checks + per-service alarm coverage
      - ecr_findings: per-container ECR scan results with drift detection
      - container_inventory: ECR repositories joined with image-versions
      - image_versions: raw image-versions.json data

    Cached 10 min — ECR scan findings and TF state change rarely.
    """
    cached = _cache_get("security_posture")
    if cached is not None:
        return cached

    state = _load_tf_state()
    exposure = _build_exposure_map(state)
    auth_gaps = _build_auth_gaps()
    monitoring = _build_monitoring_gaps(state)
    image_versions = _load_image_versions()
    inventory = _build_container_inventory(image_versions, state)
    try:
        ecr_findings = _fetch_ecr_findings(inventory)
    except Exception as e:
        print(f"ECR findings unavailable: {e}")
        ecr_findings = {}
    result = {
        "exposure_map": exposure,
        "auth_gaps": auth_gaps,
        "monitoring": monitoring,
        "ecr_findings": ecr_findings,
        "container_inventory": inventory,
        "image_versions": image_versions,
    }
    _cache_set("security_posture", result, _CACHE_TTL_SECURITY)
    return result


def _build_exposure_map(state):
    """Build internet exposure map from MODULE_META + TF state."""
    from arch_shared import get_module_meta

    cf_records = {}
    alb_oidc_hosts = set()
    has_waf = False

    if state:
        for resource in state.get("resources", []):
            rtype = resource.get("type", "")
            if rtype == "cloudflare_record":
                for inst in resource.get("instances", []):
                    attrs = inst.get("attributes", {})
                    hostname = attrs.get("hostname", "")
                    if hostname:
                        cf_records[hostname] = attrs.get("proxied", False)
            elif rtype == "aws_lb_listener_rule":
                for inst in resource.get("instances", []):
                    attrs = inst.get("attributes", {})
                    actions = attrs.get("action", [])
                    if any(a.get("type") == "authenticate-oidc" for a in actions):
                        for cond in attrs.get("condition", []):
                            hh = cond.get("host_header")
                            if isinstance(hh, dict):
                                for h in hh.get("values", []):
                                    alb_oidc_hosts.add(h)
                            elif isinstance(hh, list):
                                for item in hh:
                                    if isinstance(item, dict):
                                        for h in item.get("values", []):
                                            alb_oidc_hosts.add(h)
            elif rtype in ("aws_wafv2_web_acl", "aws_wafv2_web_acl_association"):
                has_waf = True

    exposure = []
    for module_addr, meta in get_module_meta().items():
        host = meta.get("host", "")
        if not host or host.startswith("*"):
            continue

        auth = meta.get("auth", "")
        has_alb_oidc = host in alb_oidc_hosts
        cf_proxied = cf_records.get(host)

        if has_alb_oidc and auth:
            auth_layer = f"ALB OIDC + {auth}"
        elif has_alb_oidc:
            auth_layer = "ALB OIDC"
        elif auth:
            auth_layer = auth
        else:
            auth_layer = "None"

        exposure.append(
            {
                "hostname": host,
                "service": meta["name"],
                "module": module_addr,
                "protocol": "HTTPS",
                "auth_layer": auth_layer,
                "cf_proxied": cf_proxied,
                "waf": has_waf,
                "has_auth": bool(auth) or has_alb_oidc,
            }
        )

    return sorted(exposure, key=lambda e: (e["has_auth"], e["hostname"]))


# Built-in classification: the two sign-in mechanisms this module itself sets
# up. The keyword "OIDC" is the label arch_shared gives a listener rule with an
# authenticate-oidc action, so label and rule change together.
AUTH_RULES = [
    {
        "match": ["OIDC"],
        "identity_provider": "OIDC provider",
        "auth_layer": "ALB OIDC",
        "registration": "Controlled (identity provider)",
        "gap_level": "ok",
    },
    {
        "match": ["IAM"],
        "identity_provider": "AWS IAM",
        "auth_layer": "Function URL (IAM)",
        "registration": "IAM policy",
        "gap_level": "ok",
    },
]


def classify_auth(auth):
    """Return (identity provider, auth layer, registration, gap level) for a service's auth text."""
    for rule in site_config.get("auth_rules") or AUTH_RULES:
        if any(keyword in auth for keyword in rule.get("match", [])):
            return (
                rule.get("identity_provider", ""),
                rule.get("auth_layer", ""),
                rule.get("registration", ""),
                rule.get("gap_level", "ok"),
            )
    return "None", "None", "Open", "critical"


def _build_auth_gaps():
    """Analyze auth coverage from MODULE_META annotations."""
    from arch_shared import get_module_meta

    # Services that ARE auth infrastructure — shown but not evaluated as consumers
    _AUTH_INFRA = set(site_config.get("auth_infra_modules"))
    # Services intentionally public by design
    _PUBLIC_BY_DESIGN = set(site_config.get("public_by_design_modules"))

    gaps = []
    for module_addr, meta in get_module_meta().items():
        host = meta.get("host", "")
        tier = meta.get("tier", "hosted")
        if tier == "platform" or not host or host.startswith("*"):
            continue

        auth = meta.get("auth", "")
        name = meta["name"]

        # Identity infrastructure provides auth — display with special status
        if module_addr in _AUTH_INFRA:
            gaps.append(
                {
                    "service": name,
                    "module": module_addr,
                    "hostname": host,
                    "identity_provider": "Self (identity provider)",
                    "auth_layer": auth or "Internal",
                    "registration": "Identity infrastructure",
                    "gap_level": "identity",
                }
            )
            continue

        # Intentionally public services get "info" level, not "critical"
        if module_addr in _PUBLIC_BY_DESIGN:
            gaps.append(
                {
                    "service": name,
                    "module": module_addr,
                    "hostname": host,
                    "identity_provider": "None (by design)",
                    "auth_layer": "None",
                    "registration": "Public by design",
                    "gap_level": "info",
                }
            )
            continue

        idp, layer, reg, level = classify_auth(auth)

        gaps.append(
            {
                "service": name,
                "module": module_addr,
                "hostname": host,
                "identity_provider": idp,
                "auth_layer": layer,
                "registration": reg,
                "gap_level": level,
            }
        )

    return sorted(
        gaps,
        key=lambda g: {"critical": 0, "warning": 1, "ok": 2}.get(g["gap_level"], 3),
    )


def _build_monitoring_gaps(state):
    """Analyze monitoring coverage from TF state."""
    has_cloudtrail = False
    has_guardduty = False
    has_vpc_flow_logs = False
    alarms = []
    alarm_service_names = set()

    if state:
        for resource in state.get("resources", []):
            rtype = resource.get("type", "")
            if rtype == "aws_cloudtrail":
                has_cloudtrail = True
            elif rtype == "aws_guardduty_detector":
                has_guardduty = True
            elif rtype == "aws_flow_log":
                has_vpc_flow_logs = True
            elif rtype == "aws_cloudwatch_metric_alarm":
                for inst in resource.get("instances", []):
                    attrs = inst.get("attributes", {})
                    dimensions = attrs.get("dimensions", {}) or {}
                    alarms.append(
                        {
                            "name": attrs.get("alarm_name", ""),
                            "namespace": attrs.get("namespace", ""),
                            "metric": attrs.get("metric_name", ""),
                            "dimensions": dimensions,
                        }
                    )
                    svc_name = dimensions.get("ServiceName", "")
                    if svc_name:
                        alarm_service_names.add(svc_name.lower())

    from arch_shared import get_module_meta

    services = []
    for module_addr, meta in get_module_meta().items():
        if meta.get("tier") == "platform":
            continue
        name = meta["name"]
        module_short = module_addr.replace("module.", "")
        has_alarm = any(module_short in sn for sn in alarm_service_names)

        # Health check: True (ALB health check via hostname), False (has host
        # but missing check), None (no ALB endpoint — not applicable)
        svc_type = meta.get("type", "")
        svc_host = meta.get("host", "")
        if "Lambda" in svc_type or "ElastiCache" in svc_type:
            has_health_check = None  # Managed runtime health
            hc_reason = "managed"
        elif not svc_host:
            has_health_check = None  # No ALB endpoint
            hc_reason = "no_alb"
        else:
            has_health_check = True  # Has ALB hostname → ALB health check
            hc_reason = "alb"

        services.append(
            {
                "service": name,
                "module": module_addr,
                "type": svc_type,
                "has_alarm": has_alarm,
                "has_health_check": has_health_check,
                "hc_reason": hc_reason,
            }
        )

    return {
        "cloudtrail": has_cloudtrail,
        "guardduty": has_guardduty,
        "vpc_flow_logs": has_vpc_flow_logs,
        "alarms": alarms,
        "alarm_count": len(alarms),
        "services": services,
    }


def _load_image_versions():
    """Load deployed image version data from SSM Parameter Store.

    Falls back to reading image-versions.json from repo root in local dev.
    """
    if IMAGE_VERSIONS_SSM and ssm:
        try:
            resp = ssm.get_parameter(Name=IMAGE_VERSIONS_SSM)
            return json.loads(resp["Parameter"]["Value"])
        except Exception as e:
            print(f"Warning: could not load image versions from SSM: {e}")

    # Fallback: read from repo root (local dev mode)
    if LOCAL_DATA_DIR:
        candidate = os.path.normpath(os.path.join(LOCAL_DATA_DIR, "..", "image-versions.json"))
        if os.path.isfile(candidate):
            try:
                with open(candidate) as f:
                    return json.load(f)
            except Exception as e:
                print(f"Error loading {candidate}: {e}")

    return {}


def _discover_ecr_repos(state):
    """Return set of ECR repository names from TF state."""
    repos = set()
    if not state:
        return repos
    for resource in state.get("resources", []):
        if resource.get("type") != "aws_ecr_repository":
            continue
        for inst in resource.get("instances", []):
            name = inst.get("attributes", {}).get("name", "")
            if name:
                repos.add(name)
    return repos


def _build_container_inventory(image_versions, state):
    """Build container inventory from live ECR repos in TF state.

    The primary container list comes from ``aws_ecr_repository`` resources
    discovered in TF state — this ensures only containers that actually exist
    show up.  ``image-versions.json`` provides annotations (pinned digests)
    but cannot add containers that don't exist in ECR.
    """
    ecr_repos = _discover_ecr_repos(state)

    # Build a lookup from ECR repo name -> image-versions key
    # image-versions.json uses logical names (e.g. "wiki") with an
    # ecr_repo field (e.g. "wiki") that matches the ECR repo name.
    iv_by_ecr_repo = {}
    for iv_name, iv_info in image_versions.items():
        ecr_repo = iv_info.get("ecr_repo", iv_name)
        iv_by_ecr_repo[ecr_repo] = (iv_name, iv_info)

    inventory = {}
    for repo_name in sorted(ecr_repos):
        # Match to image-versions entry by ECR repo name
        iv_name, iv_info = iv_by_ecr_repo.get(repo_name, (repo_name, {}))

        base = iv_info.get("upstream", "")

        inventory[iv_name] = {
            "base": base,
            "upstream_tag": iv_info.get("upstream_tag", ""),
            "ecr_repo": iv_info.get("ecr_repo", repo_name),
            "ecr_digest": iv_info.get("ecr_digest"),
            "notes": iv_info.get("_note", ""),
            "in_image_versions": iv_name in image_versions,
        }

    return inventory


def _get_scan_findings(ecr_client, repo_name, digest):
    """Fetch scan findings for a specific image digest.  Returns dict or None."""
    try:
        scan_response = ecr_client.describe_image_scan_findings(
            repositoryName=repo_name,
            imageId={"imageDigest": digest},
        )
        scan_data = scan_response.get("imageScanFindings", {})
        counts = scan_data.get("findingSeverityCounts", {})
        return {
            "scan_date": str(scan_data.get("imageScanCompletedAt", "")),
            "critical": counts.get("CRITICAL", 0),
            "high": counts.get("HIGH", 0),
            "medium": counts.get("MEDIUM", 0),
            "low": counts.get("LOW", 0),
            "informational": counts.get("INFORMATIONAL", 0),
            "total": sum(counts.values()),
        }
    except Exception as e:
        error_code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
        if error_code == "ScanNotFoundException":
            return None
        raise


def _fetch_ecr_findings(container_inventory):
    """Fetch ECR scan findings with deployed-vs-latest drift detection.

    For each container in *container_inventory*, queries ECR for both the
    deployed digest (from image-versions.json) and the latest pushed image.
    Returns backward-compatible dict plus new ``deployed_findings``,
    ``latest_findings``, and ``drift`` fields.
    """
    if LOCAL_DATA_DIR:
        return {}

    try:
        ecr_client = boto3.client("ecr")
    except Exception:
        return {}

    findings = {}
    for container_name, info in container_inventory.items():
        ecr_repo = info.get("ecr_repo", "")
        deployed_digest = info.get("ecr_digest")

        # Build candidate repo names (prefer ecr_repo from image-versions)
        candidates = []
        if ecr_repo:
            candidates.append(ecr_repo)
        candidates.extend(
            [
                container_name,
                container_name.replace("-", "_"),
                container_name.replace("_", "-"),
            ]
        )
        seen = set()
        candidates = [c for c in candidates if c and c not in seen and not seen.add(c)]

        for repo_name in candidates:
            try:
                response = ecr_client.describe_images(
                    repositoryName=repo_name,
                    filter={"tagStatus": "TAGGED"},
                    maxResults=10,
                )
                details = response.get("imageDetails", [])
                if not details:
                    continue

                details.sort(
                    key=lambda d: str(d.get("imagePushedAt", "")),
                    reverse=True,
                )
                latest = details[0]
                latest_digest = latest["imageDigest"]
                latest_pushed = latest.get("imagePushedAt")

                # Scan findings for the latest image
                latest_findings = _get_scan_findings(ecr_client, repo_name, latest_digest)

                # Scan findings for the deployed digest (if different)
                deployed_findings = None
                deployed_pushed = None
                if deployed_digest and deployed_digest != latest_digest:
                    deployed_findings = _get_scan_findings(ecr_client, repo_name, deployed_digest)
                    for d in details:
                        if d["imageDigest"] == deployed_digest:
                            deployed_pushed = d.get("imagePushedAt")
                            break
                    if deployed_pushed is None:
                        try:
                            dep_resp = ecr_client.describe_images(
                                repositoryName=repo_name,
                                imageIds=[{"imageDigest": deployed_digest}],
                            )
                            dep_details = dep_resp.get("imageDetails", [])
                            if dep_details:
                                deployed_pushed = dep_details[0].get("imagePushedAt")
                        except Exception:
                            pass
                elif deployed_digest:
                    deployed_findings = latest_findings
                    deployed_pushed = latest_pushed

                # Compute drift
                now = datetime.now(UTC)
                has_update = bool(deployed_digest and deployed_digest != latest_digest)
                deployed_age_days = None
                if deployed_pushed:
                    if isinstance(deployed_pushed, str):
                        try:
                            deployed_pushed = datetime.fromisoformat(deployed_pushed.replace("Z", "+00:00"))
                        except ValueError:
                            deployed_pushed = None
                    if deployed_pushed:
                        deployed_age_days = (now - deployed_pushed).days

                if deployed_age_days is None:
                    staleness = "unknown"
                elif deployed_age_days < 7:
                    staleness = "fresh"
                elif deployed_age_days < 30:
                    staleness = "aging"
                else:
                    staleness = "stale"

                # Backward-compatible top-level fields use latest findings
                lf = latest_findings or {}
                findings[container_name] = {
                    "repo": repo_name,
                    "tags": latest.get("imageTags", []),
                    "scan_date": lf.get("scan_date", ""),
                    "critical": lf.get("critical", 0),
                    "high": lf.get("high", 0),
                    "medium": lf.get("medium", 0),
                    "low": lf.get("low", 0),
                    "informational": lf.get("informational", 0),
                    "total": lf.get("total", 0),
                    # New fields
                    "deployed_digest": deployed_digest,
                    "latest_digest": latest_digest,
                    "deployed_findings": deployed_findings,
                    "latest_findings": latest_findings,
                    "drift": {
                        "has_update": has_update,
                        "deployed_age_days": deployed_age_days,
                        "staleness": staleness,
                    },
                }
                break

            except Exception as e:
                error_code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
                if error_code == "RepositoryNotFoundException":
                    continue
                elif error_code == "ScanNotFoundException":
                    findings[container_name] = {
                        "repo": repo_name,
                        "scan_date": "",
                        "error": "No scan results",
                        "deployed_digest": deployed_digest,
                        "drift": {
                            "has_update": False,
                            "deployed_age_days": None,
                            "staleness": "unknown",
                        },
                    }
                    break
                else:
                    print(f"ECR error for {repo_name}: {e}")
                    continue

    return findings
