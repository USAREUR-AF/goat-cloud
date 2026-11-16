"""Deployment-specific dashboard content: empty by default, read from a site directory."""

import json

import site_config


def test_defaults_name_no_deployment():
    config = site_config.load(site_dir="/nonexistent")
    assert config == site_config.DEFAULTS
    assert site_config.overview_html(site_dir="/nonexistent") is None


def test_site_json_overrides_and_placeholders(tmp_path, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "dev")
    (tmp_path / "site.json").write_text(
        json.dumps(
            {
                "module_enrichment": {"module.wiki": {"name": "Wiki", "console": "https://{region}.example/{env}"}},
                "identity_nodes": ["idp"],
                "not_a_key": 1,
            }
        )
    )
    config = site_config.load(site_dir=str(tmp_path))
    assert config["module_enrichment"]["module.wiki"]["console"] == f"https://{site_config.REGION}.example/dev"
    assert config["identity_nodes"] == ["idp"]
    assert config["connections"] is None
    assert "not_a_key" not in config


def test_invalid_site_json_keeps_defaults(tmp_path):
    (tmp_path / "site.json").write_text("{not json")
    assert site_config.load(site_dir=str(tmp_path)) == site_config.DEFAULTS


def test_overview_html_is_filled_and_escaped(tmp_path, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "a<b")
    (tmp_path / "overview.html").write_text("<h1>{{project}}</h1><p>{{env}} {{domain}} {{region}}</p>")
    body = site_config.overview_html(site_dir=str(tmp_path))
    assert body == f"<h1>{site_config.PROJECT}</h1><p>a&lt;b {site_config.DOMAIN_NAME} {site_config.REGION}</p>"


def test_builtin_overview_names_only_this_tooling():
    from views_overview import render_overview

    page = render_overview("admin@example.com")
    assert "GOVERNANCE &amp; COMPLIANCE" in page
    assert site_config.PROJECT in page


def test_custom_overview_replaces_the_builtin(tmp_path, monkeypatch):
    import views_overview

    (tmp_path / "overview.html").write_text("<h1>Our estate</h1>")
    monkeypatch.setattr(site_config, "SITE_DIR", str(tmp_path))
    page = views_overview.render_overview("admin@example.com")
    assert "<h1>Our estate</h1>" in page
    assert "GOVERNANCE &amp; COMPLIANCE" not in page


def test_builtin_architecture_tables_hold_only_this_tooling():
    import arch_shared

    ends = {c["from"] for c in arch_shared.CONNECTIONS} | {c["to"] for c in arch_shared.CONNECTIONS}
    assert ends == {"compliance_scanner", "compliance_dashboard", "hunterkiller"}


def test_default_brand_and_auth_rules():
    import data_security

    assert site_config.DEFAULTS["brand"] == "goat-cloud"
    assert site_config.DEFAULTS["auth_rules"] is None
    assert data_security.classify_auth("OIDC") == ("OIDC provider", "ALB OIDC", "Controlled (identity provider)", "ok")
    assert data_security.classify_auth("AWS_IAM")[1] == "Function URL (IAM)"
    assert data_security.classify_auth("") == ("None", "None", "Open", "critical")
    assert data_security.classify_auth("something else")[3] == "critical"


def test_site_auth_rules_replace_the_builtin(monkeypatch):
    import data_security

    rules = [
        {
            "match": ["Proxy"],
            "identity_provider": "Acme IdP",
            "auth_layer": "Proxy",
            "registration": "Invite",
            "gap_level": "ok",
        },
        {"match": ["Acme OIDC"], "identity_provider": "Acme IdP", "auth_layer": "ALB OIDC", "registration": "Invite"},
    ]
    monkeypatch.setitem(site_config.load(), "auth_rules", rules)
    assert data_security.classify_auth("Acme OIDC + Proxy") == ("Acme IdP", "Proxy", "Invite", "ok")
    assert data_security.classify_auth("Acme OIDC") == ("Acme IdP", "ALB OIDC", "Invite", "ok")
    assert data_security.classify_auth("IAM")[3] == "critical"


def test_oidc_label_and_default_rule_agree(monkeypatch):
    import data_security

    monkeypatch.setitem(site_config.load(), "oidc_provider_name", "")
    label = f"{site_config.get('oidc_provider_name')} OIDC".strip()
    assert data_security.classify_auth(label)[3] == "ok"
