"""Deployment-specific content for the dashboard.

The dashboard code describes no particular deployment. A deployment that wants
its own services named on the architecture, security and overview pages
supplies a directory through the Terraform input ``site_config_dir``; the build
copies it into the package as ``site/``. Two files are read from it:

  site.json      — tables listed in DEFAULTS below; a key that is absent keeps
                   its default. In every string, ``{region}``, ``{env}`` and
                   ``{domain}`` are replaced with the deployment's values.
  overview.html  — body of the overview page, replacing the built-in one.
                   ``{{env}}``, ``{{domain}}``, ``{{project}}`` and
                   ``{{region}}`` are replaced, HTML-escaped.

Without the directory every table is empty and the built-in overview is shown.
"""

import html
import json
import os

from data_core import DOMAIN_NAME, PROJECT, REGION

SITE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site")

DEFAULTS = {
    # module address -> display overrides (name, desc, auth, tier, icon, console),
    # merged over the built-in entries in arch_shared
    "module_enrichment": {},
    # service-to-service edges for the architecture views; when present it
    # replaces the built-in list in arch_shared
    "connections": None,
    # product name in page titles and the header
    "brand": "goat-cloud",
    # name of the OIDC provider, used to label services whose listener rule
    # authenticates with OIDC (for example "Keycloak"); unnamed by default
    "oidc_provider_name": "",
    # How the security page classifies a service from its auth text (the auth
    # label that arch_shared detects, or a module_enrichment "auth" value).
    # The first rule with a keyword found in the text wins; a service that
    # matches no rule is reported as having no authentication. Each rule:
    # {"match": [keywords], "identity_provider", "auth_layer", "registration",
    #  "gap_level": ok | warning | critical | info}. When present it replaces
    # the built-in rules (AUTH_RULES in data_security).
    "auth_rules": None,
    # the internet edge in front of the load balancer, drawn on the architecture
    # views: {"id", "name", "icon", "type", "desc", "host", "graph_desc"}; none by default
    "edge_node": None,
    # fixed monthly costs that Cost Explorer does not report: rows of [name, amount, note]
    "fixed_costs": [],
    # short module names whose auth and data edges are followed one step further
    "identity_nodes": [],
    # modules that are the auth infrastructure itself, not consumers of it
    "auth_infra_modules": [],
    # modules that are public on purpose
    "public_by_design_modules": [],
    # node id -> {"x": .., "y": ..} fixed positions on the interaction graph
    "node_anchors": {},
    # extra routes for the self-check page
    "self_check_routes": [],
    # sentence shown with a generated invite link
    "invite_sign_in_note": "",
}

_cache = None


def _values():
    return {"region": REGION, "env": os.environ.get("ENVIRONMENT", ""), "domain": DOMAIN_NAME, "project": PROJECT}


def _fill(value, values):
    if isinstance(value, str):
        for key in ("region", "env", "domain"):
            value = value.replace("{" + key + "}", values[key])
        return value
    if isinstance(value, list):
        return [_fill(v, values) for v in value]
    if isinstance(value, dict):
        return {k: _fill(v, values) for k, v in value.items()}
    return value


def load(site_dir=None):
    """Return the site tables, defaults filled in. Cached for the default directory."""
    global _cache
    if site_dir is None and _cache is not None:
        return _cache
    config = dict(DEFAULTS)
    path = os.path.join(site_dir or SITE_DIR, "site.json")
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                loaded = json.load(fh)
        except (OSError, ValueError) as e:
            print(f"WARNING: could not read {path}: {e}")
            loaded = {}
        unknown = sorted(set(loaded) - set(DEFAULTS)) if isinstance(loaded, dict) else []
        if unknown:
            print(f"WARNING: unknown keys in site.json ignored: {unknown}")
        if isinstance(loaded, dict):
            config.update({k: _fill(v, _values()) for k, v in loaded.items() if k in DEFAULTS})
    if site_dir is None:
        _cache = config
    return config


def get(key):
    return load()[key]


def overview_html(site_dir=None):
    """Return the deployment's overview page body, or None when it supplies none."""
    path = os.path.join(site_dir or SITE_DIR, "overview.html")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        body = fh.read()
    for key, value in _values().items():
        body = body.replace("{{" + key + "}}", html.escape(value))
    return body
