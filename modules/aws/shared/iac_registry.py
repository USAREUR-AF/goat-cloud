"""Load IaC registry entries from SSM Parameter Store.

One loader for every Lambda that reads the registry (the scanner and the stack
reaper); each build copies this file into its package.

Each repo that deploys to this AWS account registers itself under
/{project}/{environment}/iac-registry/{repo-slug} as a JSON SSM parameter.

IAC_REGISTRY_PREFIX may name environments literally, but a `*` in the
environment position (`/{project}/*/iac-registry`) makes coverage DERIVED:
every environment that has registered is read, so a new environment is
attributed the moment it applies, with no change here. An environment missing
from a hand-kept list has its Terraform-managed resources reported as suspected
click-ops.
"""

import json
import os

import boto3

# Optional fields of a registry entry: the team or role that owns what the
# repository deploys, where to reach it, the lifecycle of what it deploys and
# when that expires. Absent, empty or not a string reads as None.
OPTIONAL_TEXT_FIELDS = ("owner", "contact", "lifecycle", "expires_at")


def normalize_entry(value):
    """Return a registry entry with its optional text fields as a string or None."""
    for field in OPTIONAL_TEXT_FIELDS:
        text = value.get(field)
        value[field] = text.strip() or None if isinstance(text, str) else None
    return value


def discover_registry_prefixes(ssm, pattern):
    """Expand a `*` in the environment position of an iac-registry prefix.

    `/acme/*/iac-registry` becomes one concrete prefix per environment
    that has actually registered — `/acme/prod/iac-registry`,
    `/acme/dev/iac-registry`, and so on.

    Returns [] on any error, so discovery never fails the scan.
    """
    head, _, tail = pattern.partition("*")
    if not head.endswith("/"):
        head += "/"
    tail = tail.strip("/")
    if not tail:
        return []

    found = set()
    try:
        paginator = ssm.get_paginator("get_parameters_by_path")
        for page in paginator.paginate(Path=head.rstrip("/"), Recursive=True, WithDecryption=False):
            for param in page.get("Parameters", []):
                name = param.get("Name", "")
                if not name.startswith(head):
                    continue
                # <env>/<tail>/<repo-slug> — require a slug so an empty
                # registry path never yields a prefix.
                segments = name[len(head) :].split("/")
                if len(segments) >= 3 and segments[1] == tail and segments[2]:
                    found.add(f"{head}{segments[0]}/{tail}")
    except Exception as e:
        print(f"WARNING: Failed to discover IaC registry prefixes under {head}: {e}")
        return []

    return sorted(found)


def load_iac_registry(project=None, environment=None):
    """Read all SSM parameters under the IaC registry prefix.

    Returns a list of parsed registry entry dicts, each enriched with
    'repo_slug' (extracted from the parameter name).

    Returns [] if the prefix doesn't exist or on any error (graceful no-op).
    """
    # IAC_REGISTRY_PREFIX is a comma-separated list so one scanner attributes
    # EVERY environment registering in this AWS account, not just its own. An
    # entry may name an environment literally, or carry a `*` in the environment
    # position to derive coverage from what has actually registered.
    raw = os.environ.get("IAC_REGISTRY_PREFIX", "")
    prefixes = [p.strip() for p in raw.split(",") if p.strip()]
    if not prefixes and project and environment:
        prefixes = [f"/{project}/{environment}/iac-registry"]
    if not prefixes:
        return []

    ssm = boto3.client("ssm")

    expanded = []
    for prefix in prefixes:
        if "*" in prefix:
            expanded.extend(discover_registry_prefixes(ssm, prefix))
        else:
            expanded.append(prefix)
    # Preserve order, drop duplicates (a literal prefix may also be discovered).
    prefixes = list(dict.fromkeys(expanded))
    if not prefixes:
        return []

    entries = []
    seen = set()

    for prefix in prefixes:
        # Ensure trailing slash for GetParametersByPath
        if not prefix.endswith("/"):
            prefix += "/"
        try:
            paginator = ssm.get_paginator("get_parameters_by_path")
            for page in paginator.paginate(Path=prefix, Recursive=True, WithDecryption=False):
                for param in page.get("Parameters", []):
                    if param["Name"] in seen:
                        continue
                    seen.add(param["Name"])
                    try:
                        value = json.loads(param["Value"])
                        if not isinstance(value, dict):
                            print(f"WARNING: IaC registry entry {param['Name']} is not a JSON object; skipped")
                            continue
                        value = normalize_entry(value)
                        # Extract repo slug from parameter name
                        # e.g. /acme/dev/iac-registry/example-repo -> example-repo
                        slug = param["Name"].rstrip("/").split("/")[-1]
                        value["repo_slug"] = slug
                        value["_ssm_name"] = param["Name"]
                        entries.append(value)
                    except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as e:
                        print(f"WARNING: Malformed IaC registry entry {param['Name']}: {e}")
        except Exception as e:
            print(f"WARNING: Failed to load IaC registry from {prefix}: {e}")

    print(f"IaC registry: loaded {len(entries)} entries from {len(prefixes)} prefix(es): {prefixes}")
    for entry in entries:
        print(f"  - {entry.get('repo_slug')}: {entry.get('iac_type')} ({entry.get('repo_url', 'no url')})")

    return entries
