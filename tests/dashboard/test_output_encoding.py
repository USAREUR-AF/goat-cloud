"""Output-encoding (XSS) tests for the dashboard views: hostile values must never reach the page unescaped."""

import html as html_mod
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import arch_custom
import arch_drawio
import arch_repo
import auth
import data
import pytest
import views
import views_architecture
import views_base
import views_cure
import views_detail
import views_interactions
import views_network
import views_protected
import views_repo_detail
import views_repos
from data_terraform import _repo_file_url
from factories import make_finding, make_hk_record, make_resource
from views_access import render_access
from views_base import _e, _js_json, _safe_path, _safe_url
from views_changes import _render_findings_table, _render_severity_changes
from views_protected import _action_badge, _resource_table, render_protected
from views_security import _render_exposure_section, _render_images_section, _render_vuln_section
from views_self import _render_configuration, _render_performance
from views_services import _render_hosted_rows, _render_platform_rows
from views_tree import _render_module_node

_C_ATTR = '"><script>alert(1)</script>'
_C_JS = "'-alert(1)-'"
_C_SCRIPT = "</script><script>alert(1)</script>"
_C_URL = "javascript:alert(1)"
_C_PAYLOADS = [_C_ATTR, _C_JS, _C_SCRIPT, _C_URL]


def _c_assert_inert(page):
    """No payload survives as markup, as an attribute breakout, as a JS string breakout or as a link target."""
    assert "<script>alert(1)" not in page
    assert '"><script' not in page
    assert _C_JS not in page
    assert not re.search(r"""(?:href|src)\s*=\s*["']?\s*javascript:""", page, flags=re.I)


def _c_confirm_arguments(page):
    """Return the decoded argument of every inline confirm(...) handler that carries data."""
    handlers = re.findall(r'onclick="return confirm\((.*?)\)"', page, flags=re.S)
    return [html_mod.unescape(h) for h in handlers]


class TestJsJson:
    @pytest.mark.parametrize("payload", [*_C_PAYLOADS, "<!-- x -->", "a & b", "line\u2028sep\u2029end"])
    def test_round_trips_and_has_no_markup_characters(self, payload):
        out = _js_json({"k": payload, payload: [payload]})
        assert json.loads(out) == {"k": payload, payload: [payload]}
        for ch in ("<", ">", "&", "\u2028", "\u2029"):
            assert ch not in out

    def test_cannot_close_a_script_block(self):
        out = _js_json(_C_SCRIPT)
        assert "</script" not in out.lower()
        assert "\\u003c/script\\u003e" in out

    def test_benign_values_match_json_dumps(self):
        obj = {"name": "web-01", "count": 3, "tags": ["a", "b"], "ok": True, "none": None}
        assert _js_json(obj) == json.dumps(obj)
        assert _js_json(obj, indent=2) == json.dumps(obj, indent=2)

    def test_html_escaped_form_is_a_safe_attribute_value(self):
        attr = _e(_js_json(f"Delete {_C_JS} {_C_ATTR}?"))
        assert '"' not in attr and "'" not in attr and "<" not in attr
        assert json.loads(html_mod.unescape(attr)) == f"Delete {_C_JS} {_C_ATTR}?"


class TestSafeUrl:
    @pytest.mark.parametrize(
        "url",
        ["https://github.com/acme/platform-infra", "http://example.com/a?b=c&d=e#f", "HTTPS://EXAMPLE.COM/x"],
    )
    def test_http_and_https_pass_unchanged(self, url):
        assert _safe_url(url) == url

    @pytest.mark.parametrize(
        "url",
        [
            _C_URL,
            "JaVaScRiPt:alert(1)",
            " javascript:alert(1)",
            "java\tscript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "vbscript:msgbox(1)",
            "//example.com/x",
            "/relative/path",
            "github.com/acme/repo",
            "https://",
            "https://example.com/a b",
            "https://example.com/\njavascript:alert(1)",
            "",
            None,
            42,
        ],
    )
    def test_everything_else_is_dropped(self, url):
        assert _safe_url(url) == ""

    def test_surrounding_whitespace_is_trimmed(self):
        assert _safe_url("  https://example.com/x \n") == "https://example.com/x"


class TestSafePath:
    @pytest.mark.parametrize("path", ["/", "/pending", "/findings?severity=HIGH&page=2"])
    def test_local_paths_pass(self, path):
        assert _safe_path(path) == path

    @pytest.mark.parametrize(
        "path", [_C_URL, "//example.com", "https://example.com", "/\\example.com", "/a\nb", "", None]
    )
    def test_other_values_fall_back(self, path):
        assert _safe_path(path) == "/"
        assert _safe_path(path, default="/self") == "/self"


class TestBaseLayout:
    def test_success_page_back_link_takes_no_scheme(self):
        page = views_base.render_success("Done", _C_ATTR, back_url=_C_URL)
        _c_assert_inert(page)
        assert '<a href="/" class="btn btn-secondary">' in page

    def test_success_page_keeps_a_local_back_link(self):
        assert '<a href="/pending" class="btn btn-secondary">' in views_base.render_success("Done", "ok", "/pending")

    @pytest.mark.parametrize("payload", _C_PAYLOADS)
    def test_error_page_and_title(self, payload):
        _c_assert_inert(views_base.render_error(payload, f"Page not found: /{payload}"))

    def test_arch_nav_breadcrumb(self):
        nav = views_base.render_nav_arch(_C_ATTR, sub_breadcrumb=_C_ATTR, meta=_C_ATTR)
        _c_assert_inert(nav)

    def test_badge_title(self):
        assert '"><script' not in views_base._badge("X", title=_C_ATTR)


class TestFindingRow:
    @pytest.mark.parametrize("payload", _C_PAYLOADS)
    def test_every_field_hostile(self, payload):
        f = make_finding(
            severity=payload,
            resource_arn=f"arn:aws:ec2:eu-central-1:123456789012:instance/{payload}",
            resource_type=payload,
            resource_name=payload,
            region=payload,
            state=payload,
            issue=payload,
            category=payload,
            tags={"Repository": payload, "Contact": payload, "ManagedBy": payload, payload: payload},
        )
        _c_assert_inert(views._finding_row(f))

    def test_console_link_cannot_leave_its_attribute(self):
        f = make_finding(resource_arn='arn:aws:ec2:eu-central-1:123456789012:instance/i-1" onmouseover="alert(1)')
        row = views._finding_row(f)
        assert 'onmouseover="alert(1)' not in row
        assert "onmouseover=&quot;alert(1)" in row

    def test_https_repository_tag_still_links(self):
        f = make_finding(tags={"Repository": "https://github.com/acme/platform-infra"})
        assert 'href="https://github.com/acme/platform-infra"' in views._finding_row(f)

    def test_benign_console_link_unchanged(self):
        row = views._finding_row(make_finding())
        assert (
            'href="https://eu-central-1.console.aws.amazon.com/ec2/home?region=eu-central-1'
            '#InstanceDetails:instanceId=i-0abc123def456"'
        ) in row


class TestFindingsPage:
    @pytest.mark.parametrize("payload", _C_PAYLOADS)
    def test_query_parameters_are_not_reflected_raw(self, payload):
        findings = [make_finding(tags={"Contact": payload}) for _ in range(101)]
        page = views.render_findings(findings, severity_filter=payload, group_by=payload, contact_filter=payload)
        _c_assert_inert(page)

    def test_benign_filter_links_unchanged(self):
        page = views.render_findings([make_finding()], severity_filter="HIGH", group_by="contact")
        assert 'href="/findings?severity=MEDIUM&group=contact"' in page
        assert 'href="/findings?group=pivot&severity=HIGH"' in page

    @pytest.mark.parametrize("payload", _C_PAYLOADS)
    def test_grouped_and_pivot_views(self, payload):
        f = make_finding(resource_type=payload, region=payload, category=payload, tags={"Contact": payload})
        _c_assert_inert(views.render_findings([f], group_by="contact"))
        _c_assert_inert(views.render_findings([f], group_by="pivot"))

    @pytest.mark.parametrize("payload", _C_PAYLOADS)
    def test_expiring_soon_section(self, payload):
        info = make_finding(
            severity="INFO",
            resource_name=payload,
            resource_type=payload,
            region=payload,
            issue=payload,
            tags={"Repository": payload},
        )
        _c_assert_inert(views.render_findings([make_finding()], info_findings=[info]))


class TestCompliantPage:
    @pytest.mark.parametrize("payload", _C_PAYLOADS)
    def test_expiring_resource_fields(self, payload):
        r = make_resource(
            name=payload,
            resource_type=payload,
            region=payload,
            expires_at=payload,
            days_remaining=2,
            tags={"Repository": payload, "Service": payload},
        )
        summary = {
            "by_type": {payload: {"total": 1, "compliant": 1}},
            "by_region": {payload: {"total": 1, "compliant": 1}},
            "total_compliant": 1,
            "total_resources": 1,
        }
        _c_assert_inert(views.render_compliant(summary, expiring_soon=[r]))
        _c_assert_inert(views.render_all_resources(summary))

    def test_javascript_repository_tag_is_not_a_link(self):
        r = make_resource(expires_at="2030-01-01T00:00:00", days_remaining=2, tags={"Repository": _C_URL})
        page = views.render_compliant({}, expiring_soon=[r])
        assert "javascript:" not in page

    def test_https_repository_tag_still_links(self):
        r = make_resource(expires_at="2030-01-01T00:00:00", days_remaining=2)
        page = views.render_compliant({}, expiring_soon=[r])
        assert '<a href="https://github.com/acme/platform-infra" style="color:#3b82f6; font-size:11px;">' in page


class TestPendingPage:
    @pytest.mark.parametrize("approval", ["pending", "approved"])
    @pytest.mark.parametrize("payload", [*_C_PAYLOADS, "');alert(1);//", '\\");alert(1);//', "a\\'b"])
    def test_resource_name_stays_inside_the_confirm_string(self, payload, approval):
        item = make_hk_record(
            resource_name=payload,
            resource_arn=f"arn:aws:ec2:eu-central-1:123456789012:instance/{payload}",
            resource_type=payload,
            contact_email=payload,
            contact_source=f"tag:{payload}",
            approval_status=approval,
            approved_by=payload,
            warned_at=payload,
        )
        page = views.render_pending([item], is_admin=True, executor_armed=True)
        _c_assert_inert(page)
        args = _c_confirm_arguments(page)
        with_name = [a for a in args if a.startswith('"')]
        assert len(with_name) == 3
        for arg in with_name:
            # The whole argument is exactly one JSON string literal: nothing can follow it.
            assert payload in json.loads(arg)
            assert "<" not in arg and ">" not in arg

    def test_benign_name_reads_the_same(self):
        item = make_hk_record(resource_name="web-01", approval_status="approved")
        page = views.render_pending([item], is_admin=True, executor_armed=True)
        messages = [json.loads(a) for a in _c_confirm_arguments(page) if a.startswith('"')]
        assert messages == [
            "ABORT destruction of web-01?",
            "Revoke teardown approval for web-01?",
            "DESTROY web-01 NOW? This cannot be undone!",
        ]


class TestOverridesPage:
    @pytest.mark.parametrize("payload", _C_PAYLOADS)
    def test_stored_record_fields(self, payload):
        o = {
            "resource_arn": payload,
            "override_type": payload,
            "reason": payload,
            "created_by": payload,
            "created_at": payload,
            "expires_at": payload,
            "resource_type": payload,
        }
        _c_assert_inert(views.render_overrides([o], is_admin=True))


class TestHomePage:
    @pytest.mark.parametrize("payload", _C_PAYLOADS)
    def test_flash_report_date_and_job_status(self, payload):
        jobs = {"compliance-scanner": {"last_run": payload, "status": payload, "summary": payload}}
        page = views.render_home({}, 0, 0, payload, flash=payload, job_statuses=jobs)
        _c_assert_inert(page)


class TestCurePage:
    @pytest.mark.parametrize("payload", _C_PAYLOADS)
    def test_finding_and_terraform_fields(self, payload):
        arn = f"arn:aws:ec2:eu-central-1:123456789012:volume/{payload}"
        f = make_finding(
            severity=payload,
            resource_arn=arn,
            resource_type=payload,
            resource_name=payload,
            region=payload,
            issue=payload,
            category=payload,
            recommendation=payload,
            tags={
                "Repository": payload,
                "Service": payload,
                "Purpose": payload,
                "ManagedBy": "terraform",
                payload: payload,
            },
        )
        tf_info = {
            "found": True,
            "tf_address": payload,
            "file_path": payload,
            "repo_url": payload,
            "top_module": payload,
        }
        live = {
            "attached": True,
            "volume_state": payload,
            "volume_size_gb": payload,
            "volume_type": payload,
            "device_name": payload,
            "instance": {
                "instance_id": payload,
                "name": payload,
                "state": payload,
                "instance_type": payload,
                "tags": {"Service": payload},
            },
        }
        parent = {"tf_address": payload, "file_path": payload, "instance_id": payload, "device_name": payload}
        _c_assert_inert(views_cure.render_cure([f], tf_info, arn, volume_parent=parent, volume_live=live))
        _c_assert_inert(views_cure.render_cure([f], {"found": False, "reason": payload}, arn, volume_parent=parent))
        _c_assert_inert(views_cure._render_volume_context({"attached": False, "volume_state": payload}, None, arn))

    def test_click_ops_commands(self):
        arn = f"arn:aws:ec2:eu-central-1:123456789012:instance/{_C_ATTR}"
        f = make_finding(resource_arn=arn, region=_C_ATTR)
        page = views_cure.render_cure([f], {"found": False}, arn, import_hint={"command": _C_ATTR})
        _c_assert_inert(page)

    def test_javascript_repo_url_is_not_a_link(self):
        f = make_finding(tags={"ManagedBy": "terraform"})
        tf_info = {"found": True, "tf_address": "module.a.aws_x.y", "file_path": "main.tf", "repo_url": _C_URL}
        page = views_cure.render_cure([f], tf_info, f["resource_arn"])
        assert "javascript:" not in page

    def test_https_repo_url_still_links(self):
        f = make_finding(tags={"ManagedBy": "terraform"})
        url = "https://github.com/acme/platform-infra/blob/main/main.tf"
        tf_info = {"found": True, "tf_address": "module.a.aws_x.y", "file_path": "main.tf", "repo_url": url}
        assert f'<a href="{url}" target="_blank"' in views_cure.render_cure([f], tf_info, f["resource_arn"])

    @pytest.mark.parametrize(
        "arn",
        [
            'arn:aws:ec2:eu-central-1:123456789012:instance/"><script>alert(1)</script>',
            "arn:aws:<img src=x onerror=alert(1)>:eu-central-1:123456789012:thing/x",
            "arn:aws:ec2:<script>alert(1)</script>:123456789012:instance/i-1",
            'arn:aws:s3:::bucket"><script>alert(1)</script>',
            _C_ATTR,
        ],
    )
    def test_stale_link_reflects_nothing_from_the_arn_parameter(self, arn):
        page = views_cure.render_cure([], {}, arn)
        _c_assert_inert(page)
        assert "<img" not in page.lower()
        assert "<Script" not in page

    def test_stale_link_type_label_for_a_normal_arn(self):
        page = views_cure.render_cure([], {}, "arn:aws:ssm:eu-central-1:123456789012:session/abc")
        assert "Ssm &rsaquo; Session" in page


class TestChangesTables:
    @pytest.mark.parametrize("payload", _C_PAYLOADS)
    def test_findings_and_severity_change_rows(self, payload):
        f = make_finding(
            severity=payload,
            resource_arn=payload,
            resource_name=payload,
            resource_type=payload,
            region=payload,
            issue=payload,
            previous_severity=payload,
        )
        _c_assert_inert(_render_findings_table("NEW", [f]))
        _c_assert_inert(_render_findings_table("RESOLVED", [f], resolved=True))
        _c_assert_inert(_render_severity_changes([f]))

    def test_benign_severity_unchanged(self):
        assert 'font-weight:700;">HIGH</span>' in _render_findings_table("NEW", [make_finding()])


class TestModuleDetailPage:
    @pytest.mark.parametrize("payload", _C_PAYLOADS)
    def test_state_content_and_path_segment(self, payload, monkeypatch):
        arn = f"arn:aws:ec2:eu-central-1:123456789012:instance/{payload}"
        tree = [
            {
                "module": payload,
                "file_path": payload,
                "stats": {"total": 1, "compliant": 0, "non_compliant": 1},
                "resources": [{"arn": arn, "id": payload, "type": payload, "name": payload, "compliant": False}],
                "children": {payload: [{"compliant": True}]},
            }
        ]
        meta = {payload: {"name": payload, "desc": payload, "host": payload, "auth": payload, "zone": payload}}
        finding = make_finding(
            resource_arn=arn, resource_name=payload, issue=payload, category=payload, severity=payload
        )
        monkeypatch.setattr(views_detail, "get_compliance_report", lambda: {})
        monkeypatch.setattr(views_detail, "get_tf_module_tree", lambda report: tree)
        monkeypatch.setattr(views_detail, "get_module_meta", lambda: meta)
        monkeypatch.setattr(views_detail, "get_all_findings", lambda report: [finding])
        _c_assert_inert(views_detail.render_detail(payload))
        _c_assert_inert(views_detail.render_detail("module.absent-" + payload))


class TestReposPages:
    @pytest.mark.parametrize("payload", _C_PAYLOADS)
    def test_registry_entries(self, payload, monkeypatch):
        report = {
            "iac_registry": {
                "entries": [
                    {
                        "repo_slug": payload,
                        "repo_url": payload,
                        "iac_type": payload,
                        "resource_count": 2,
                        "last_deploy": payload,
                    }
                ],
                "total_external_managed": 0,
            },
            "unregistered_cfn_stacks": [
                {"stack_name": payload, "iac_type": payload, "resource_count": payload, "region": payload}
            ],
            "discovered_resources": [
                make_resource(tags={"Repository": payload, "Lifecycle": "temporary", "ExpiresAt": payload})
            ],
        }
        config = {
            "trusted_github_orgs": [payload],
            "deploy_allowed_regions": [payload],
            "state_bucket": payload,
            "deploy_role_arn": payload,
            "lock_table": payload,
            "onboarding_guide_url": payload,
            "guide_url": payload,
        }
        monkeypatch.setattr(views_repos, "get_compliance_report", lambda: report)
        monkeypatch.setattr(views_repos, "_platform_config_cache", config)
        _c_assert_inert(views_repos.render_repos())

    def test_onboarding_guide_link_scheme(self):
        assert "javascript:" not in views_repos._render_onboarding("bucket", guide_url=_C_URL)
        ok = views_repos._render_onboarding("bucket", guide_url="https://example.com/guide")
        assert '<a href="https://example.com/guide" target="_blank">' in ok

    def test_registry_repo_url_scheme(self, monkeypatch):
        entry = {"repo_slug": "acme-app", "repo_url": _C_URL, "iac_type": "terraform", "resource_count": 1}
        monkeypatch.setattr(views_repos, "get_compliance_report", lambda: {"iac_registry": {"entries": [entry]}})
        monkeypatch.setattr(views_repos, "_platform_config_cache", {})
        page = views_repos.render_repos()
        assert "javascript:" not in page
        assert 'href="/repos/acme-app"' in page

    @pytest.mark.parametrize("payload", _C_PAYLOADS)
    def test_repo_detail(self, payload, monkeypatch):
        info = {"repo_url": payload, "iac_type": payload, "last_deploy": payload}
        resources = [
            {
                "arn": payload,
                "console_url": payload,
                "compliant": False,
                "resource_type": payload,
                "name": payload,
                "region": payload,
                "tags": {"Lifecycle": payload, "ExpiresAt": payload, "Service": payload},
            }
        ]
        monkeypatch.setattr(views_repo_detail, "get_repo_info", lambda slug: info)
        monkeypatch.setattr(views_repo_detail, "get_repo_resources", lambda slug: resources)
        _c_assert_inert(views_repo_detail.render_repo_detail(payload))
        monkeypatch.setattr(views_repo_detail, "get_repo_info", lambda slug: None)
        _c_assert_inert(views_repo_detail.render_repo_detail(payload))

    def test_repo_detail_keeps_https_links(self, monkeypatch):
        info = {"repo_url": "https://github.com/acme/app", "iac_type": "terraform", "last_deploy": "2026-01-01"}
        console = "https://eu-central-1.console.aws.amazon.com/ec2/home?region=eu-central-1#Instances:"
        resources = [
            {
                "arn": "arn:aws:ec2:eu-central-1:123456789012:instance/i-1",
                "console_url": console,
                "compliant": True,
                "resource_type": "ec2:instance",
                "name": "web",
                "region": "eu-central-1",
                "tags": {},
            }
        ]
        monkeypatch.setattr(views_repo_detail, "get_repo_info", lambda slug: info)
        monkeypatch.setattr(views_repo_detail, "get_repo_resources", lambda slug: resources)
        page = views_repo_detail.render_repo_detail("acme-app")
        assert "href='https://github.com/acme/app'" in page
        assert f'href="{console}"' in page


_A_ATTR = '"><script>alert(1)</script>'
_A_JS = "'-alert(1)-'"
_A_SCRIPT = "</script><script>alert(1)</script>"
_A_URL = "javascript:alert(1)"
_A_LAMBDA_DIR = Path(views_interactions.__file__).parent


def _a_graph(name):
    return {
        "nodes": [
            {"id": "root", "type": "root", "name": "root"},
            {
                "id": "r1",
                "type": "resource",
                "name": name,
                "resource_type": name,
                "unique_tags": {name: name},
                "console_url": _A_URL,
                "arn": name,
            },
        ],
        "links": [],
        "stats": {"total": 1, "managed": 0, "unmanaged": 1},
    }


def _a_script_bodies(page):
    return re.findall(r"<script>(.*?)</script>", page, flags=re.S)


def _a_no_raw_payload(page):
    assert "<script>alert(1)" not in page
    assert "</script><script>" not in page


def _a_embedded_data(page, var="DATA"):
    """Parse the JSON literal assigned to ``var`` in an inline script."""
    m = re.search(rf"(?:const|var) {var} = (.*);\n", page)
    assert m, f"{var} not embedded"
    return json.loads(m.group(1))


class TestNetworkScriptEmbedding:
    def test_graph_json_cannot_close_the_script_block(self):
        page = views_network.render_network(_a_graph(_A_SCRIPT))
        _a_no_raw_payload(page)
        assert _a_embedded_data(page)["nodes"][1]["name"] == _A_SCRIPT

    def test_html_comment_opener_is_escaped(self):
        page = views_network.render_network(_a_graph("<!--<script>"))
        assert "<!--<script>" not in page

    def test_attribute_payload_does_not_appear_raw(self):
        page = views_network.render_network(_a_graph(_A_ATTR))
        _a_no_raw_payload(page)

    def test_links_open_only_through_the_scheme_guard(self):
        page = views_network.render_network(_a_graph("x"))
        assert "window.open(d.console_url" not in page
        assert "window.open(item.url" not in page
        assert "function safeOpen(u)" in page

    def test_tooltip_escape_covers_double_quote(self):
        page = views_network.render_network(_a_graph("x"))
        assert '.replace(/"/g,"&quot;")' in page

    def test_repo_network(self, monkeypatch):
        import data_repo

        monkeypatch.setattr(data_repo, "get_repo_graph", lambda slug: _a_graph(_A_SCRIPT))
        monkeypatch.setattr(data_repo, "get_repo_info", lambda slug: {"repo_slug": _A_ATTR})
        page = views_network.render_repo_network(_A_ATTR)
        _a_no_raw_payload(page)
        assert "window.open(d.console_url" not in page
        assert _a_embedded_data(page)["nodes"][1]["name"] == _A_SCRIPT


class TestInteractionsPage:
    def _a_data(self, name):
        node = {
            "id": name,
            "name": name,
            "zone": "apps",
            "color": name,
            "type": name,
            "host": name,
            "auth": name,
            "desc": name,
            "icon": "ecs",
            "link": _A_URL,
            "focal": False,
            "components": [{"label": name, "count": 1, "color": name, "desc": name, "submodule": name}],
        }
        return {"nodes": [node], "edges": [], "focal": name, "global": False}

    def test_data_json_cannot_close_the_script_block(self):
        page = views_interactions._render_page("t", self._a_data(_A_SCRIPT), "")
        _a_no_raw_payload(page)
        assert _a_embedded_data(page)["nodes"][0]["name"] == _A_SCRIPT

    def test_title_focal_and_module_address_are_escaped(self):
        for payload in (_A_ATTR, _A_SCRIPT):
            page = views_interactions._render_page(
                payload,
                {"nodes": [], "edges": [], "focal": "", "global": False},
                "",
                focal_display=payload,
                module_address=payload,
            )
            _a_no_raw_payload(page)
            assert f'custom/{payload}"' not in page

    def test_placeholder_inside_data_is_not_substituted_again(self):
        data = self._a_data("__NODE_ANCHORS_JSON__")
        page = views_interactions._render_page("t", data, "", node_anchors={"+alert(1)+": {"x": 0, "y": 0}})
        assert _a_embedded_data(page)["nodes"][0]["name"] == "__NODE_ANCHORS_JSON__"

    def test_reflected_module_address(self, monkeypatch):
        monkeypatch.setattr(views_interactions, "get_module_meta", lambda: {})
        monkeypatch.setattr(views_interactions, "get_service_subgraph", lambda short: (set(), []))
        page = views_interactions.render_interactions("module." + _A_ATTR)
        _a_no_raw_payload(page)
        page = views_interactions.render_interactions(_A_SCRIPT)
        _a_no_raw_payload(page)

    def test_benign_module_link_is_unchanged(self, monkeypatch):
        monkeypatch.setattr(views_interactions, "get_module_meta", lambda: {})
        monkeypatch.setattr(views_interactions, "get_service_subgraph", lambda short: (set(), []))
        page = views_interactions.render_interactions("module.my_service")
        assert '<a href="/architecture/custom/module.my_service">' in page
        assert '<strong style="color:#e2e8f0;">My Service</strong>' in page


class TestInteractionsJs:
    def test_tooltip_escape_covers_quotes(self):
        js = (_A_LAMBDA_DIR / "interactions_d3.js").read_text()
        assert '.replace(/"/g,"&quot;")' in js
        assert "&#39;" in js

    def test_no_unescaped_data_in_tooltip_markup(self):
        js = (_A_LAMBDA_DIR / "interactions_d3.js").read_text()
        assert "' + cd.color + '" not in js
        assert "window.open(d.link" not in js
        assert "safeOpen(d.link)" in js


class TestArchitectureLanding:
    def test_module_name_and_address_are_escaped(self, monkeypatch):
        meta = {f"module.{_A_ATTR}": {"name": _A_SCRIPT, "tier": "hosted"}}
        monkeypatch.setattr(views_architecture, "get_module_meta", lambda: meta)
        page = views_architecture.render_architecture()
        _a_no_raw_payload(page)

    def test_benign_module_link_is_unchanged(self, monkeypatch):
        meta = {"module.my_service": {"name": "My Service", "tier": "hosted"}}
        monkeypatch.setattr(views_architecture, "get_module_meta", lambda: meta)
        page = views_architecture.render_architecture()
        assert '<a href="/architecture/custom/module.my_service" ' in page
        assert ">My Service</a>" in page


class TestServiceCard:
    def test_link_is_not_interpolated_into_the_click_handler(self):
        card = arch_custom._render_service_card(
            "module.x", {"name": _A_ATTR, "host": _A_ATTR, "desc": _A_SCRIPT}, {}, "#fff", link=f"/x/{_A_JS}/{_A_ATTR}"
        )
        _a_no_raw_payload(card)
        assert _A_JS not in card
        assert "window.location.href=this.dataset.href;" in card

    def test_benign_link_is_carried_in_a_data_attribute(self):
        card = arch_custom._render_service_card("module.my_service", {"name": "n"}, {}, "#fff")
        assert 'data-href="/architecture/custom/module.my_service"' in card


class TestRepoArchitectureService:
    def _a_render(self, monkeypatch, console_url, name="n"):
        resource = {
            "arn": "arn:aws:lambda:eu-central-1:123456789012:function:" + name,
            "resource_type": "lambda:function",
            "region": name,
            "name": name,
            "compliant": False,
            "tags": {},
            "console_url": console_url,
            "estimated_monthly_cost": 1.0,
        }
        monkeypatch.setattr(arch_repo, "get_repo_info", lambda slug: {"iac_type": name})
        monkeypatch.setattr(arch_repo, "get_repo_resources", lambda slug: [resource])
        return arch_repo.render_repo_architecture_service(_A_ATTR, arch_repo._service_from_type("lambda:function"), "")

    def test_javascript_console_url_is_not_a_link(self, monkeypatch):
        page = self._a_render(monkeypatch, _A_URL)
        assert "javascript:" not in page

    def test_https_console_url_is_kept(self, monkeypatch):
        page = self._a_render(monkeypatch, "https://console.example/x?a=1&b=2")
        assert 'href="https://console.example/x?a=1&amp;b=2"' in page

    def test_names_and_slug_are_escaped(self, monkeypatch):
        page = self._a_render(monkeypatch, "", name=_A_SCRIPT)
        _a_no_raw_payload(page)


class TestDrawioXml:
    def _a_generate(self, monkeypatch, name):
        tree = [{"module": "module.svc", "stats": {"total": 1}, "arch": {}}]
        meta = {"module.svc": {"name": name, "zone": "apps", "icon": "ecs", "type": name, "host": name, "auth": name}}
        monkeypatch.setattr(arch_drawio, "get_compliance_report", lambda: {})
        monkeypatch.setattr(arch_drawio, "get_tf_module_tree", lambda report: tree)
        monkeypatch.setattr(arch_drawio, "get_module_meta", lambda: meta)
        monkeypatch.setattr(arch_drawio, "get_active_connections", lambda: [])
        return arch_drawio.generate_drawio_xml()

    def test_quote_cannot_break_out_of_the_value_attribute(self, monkeypatch):
        xml = self._a_generate(monkeypatch, '" style="x')
        root = ET.fromstring(xml)
        values = [c.get("value") for c in root.iter("mxCell") if c.get("value")]
        assert any('" style="x' in v for v in values)
        assert all(c.get("style") != "x" for c in root.iter("mxCell"))

    def test_html_label_markup_is_inert_after_xml_decoding(self, monkeypatch):
        xml = self._a_generate(monkeypatch, "<img src=x onerror=alert(1)>")
        root = ET.fromstring(xml)
        labels = [c.get("value") for c in root.iter("mxCell") if "html=1" in (c.get("style") or "")]
        assert any("&lt;img" in label for label in labels)
        assert all("<img" not in label for label in labels)

    def test_benign_label_is_unchanged(self, monkeypatch):
        xml = self._a_generate(monkeypatch, "svc")
        root = ET.fromstring(xml)
        labels = [c.get("value") for c in root.iter("mxCell") if "html=1" in (c.get("style") or "")]
        assert "svc<br>(svc)<br>svc<br>[svc]" in labels

    def test_error_message_is_attribute_safe(self):
        xml = arch_drawio._error_xml('x" onload="y <b>')
        root = ET.fromstring(xml)
        assert root.find(".//mxCell[@id='2']").get("value") == 'x" onload="y <b>'


_B_HTML = '"><script>alert(1)</script>'
_B_JS = "'-alert(1)-'"
_B_SCRIPT = "</script><script>alert(1)</script>"
_B_URL = "javascript:alert(1)"
_B_PAYLOADS = [_B_HTML, _B_JS, _B_SCRIPT, _B_URL]


def _b_assert_inert(html):
    """No payload survives as markup, as a quote-breaking attribute value, or as a link target."""
    assert "<script>alert(1)" not in html
    assert '"><script' not in html
    assert "'-alert(1)-'" not in html
    assert 'href="javascript:' not in html
    assert "href='javascript:" not in html


@pytest.fixture()
def _b_no_admin(monkeypatch):
    monkeypatch.setattr(auth, "is_admin", lambda email: False)


def _b_service(**overrides):
    svc = {
        "module": "module.app",
        "name": "App",
        "zone": "apps",
        "icon": "ecs",
        "host": "app.example.com",
        "auth": "OIDC",
        "notes": "",
        "desc": "An app",
        "console": "https://console.example.com/app",
        "resources": 3,
        "compliant": 3,
        "non_compliant": 0,
        "pct": 100,
    }
    svc.update(overrides)
    return svc


@pytest.mark.usefixtures("_b_no_admin")
class TestAccessPageEncoding:
    @pytest.mark.parametrize("payload", _B_PAYLOADS)
    @pytest.mark.parametrize(
        ("role", "source"),
        [("viewer", "invite-link:admin@example.com"), ("admin", "promoted:admin@example.com")],
    )
    def test_email_in_confirm_handler_is_js_and_attribute_escaped(self, payload, role, source):
        email = f"{payload}@example.com"
        html = render_access([{"email": email, "role": role, "source": source}])
        _b_assert_inert(html)
        # After the browser decodes the attribute, the handler is one call with one JS string literal
        handlers = re.findall(r'onclick="return confirm\(([^"]*)\)">', html)
        assert handlers
        for handler in handlers:
            assert email in json.loads(html_mod.unescape(handler))

    def test_benign_email_confirm_text_is_unchanged(self):
        html = render_access([{"email": "user@example.com", "role": "viewer", "source": "invite-link"}])
        assert 'onclick="return confirm(&quot;Revoke access for user@example.com?&quot;)"' in html
        assert 'onclick="return confirm(&quot;Promote user@example.com to admin?&quot;)"' in html

    @pytest.mark.parametrize("payload", _B_PAYLOADS)
    def test_source_attribution_is_escaped(self, payload):
        html = render_access([{"email": "user@example.com", "role": "viewer", "source": f"invite-link:{payload}"}])
        _b_assert_inert(html)
        html = render_access([{"email": "user@example.com", "role": "viewer", "source": payload}])
        _b_assert_inert(html)


class TestProtectedPageEncoding:
    @pytest.mark.parametrize("payload", _B_PAYLOADS)
    def test_resource_table_fields_are_escaped(self, payload):
        html = _resource_table(
            [
                {
                    "name": payload,
                    "resolved_arn": f"arn:aws:s3:::{payload}",
                    "is_compliant": False,
                    "finding_count": payload,
                    "action": payload,
                    "reason": payload,
                    "state": payload,
                    "resource_type": payload,
                }
            ]
        )
        _b_assert_inert(html)
        assert "<SCRIPT>" not in html

    def test_action_badge_escapes_unknown_action_and_keeps_known_labels(self):
        assert "<SCRIPT>" not in _action_badge("<script>alert(1)</script>")
        assert "&lt;SCRIPT&gt;" in _action_badge("<script>alert(1)</script>")
        assert ">HK EXEMPT<" in _action_badge("never_destroy")
        assert ">CUSTOM_ACTION<" in _action_badge("custom_action")

    @pytest.mark.usefixtures("_b_no_admin")
    @pytest.mark.parametrize("payload", _B_PAYLOADS)
    def test_unknown_category_heading_and_notes_are_escaped(self, monkeypatch, payload):
        monkeypatch.setattr(
            views_protected,
            "get_protected_resources",
            lambda: {
                "summary": {"total": payload, "compliant": 0, "noncompliant": 0, "total_cost": 0},
                "resources": [{"name": "x", "category": payload, "arn_pattern": "arn:aws:s3:::x"}],
                "config": {"notes": [{"name": payload, "reason": payload, "action": payload}]},
            },
        )
        html = render_protected()
        _b_assert_inert(html)
        assert "<Script>" not in html
        assert "<SCRIPT>" not in html


class TestServicesPageEncoding:
    @pytest.mark.parametrize("payload", _B_PAYLOADS)
    def test_hosted_rows_escape_state_derived_fields(self, payload):
        html = _render_hosted_rows(
            [
                _b_service(
                    name=payload, zone=payload, icon=payload, host=payload, auth=payload, notes=payload, desc=payload
                )
            ]
        )
        _b_assert_inert(html)

    @pytest.mark.parametrize("payload", _B_PAYLOADS)
    def test_platform_rows_escape_state_derived_fields(self, payload):
        html = _render_platform_rows([_b_service(name=payload, zone=payload, icon=payload, desc=payload)])
        _b_assert_inert(html)

    @pytest.mark.parametrize("url", [_B_URL, "JaVaScRiPt:alert(1)", " javascript:alert(1)", "data:text/html,x", "//x"])
    def test_console_link_requires_http_scheme(self, url):
        html = _render_platform_rows([_b_service(console=url)])
        assert "Open in Console" not in html
        assert "alert(1)" not in html

    def test_benign_console_link_is_unchanged(self):
        html = _render_platform_rows([_b_service()])
        assert '<a href="https://console.example.com/app" target="_blank"' in html
        assert 'data-zone="apps" data-tier="platform"' in html


class TestSecurityPageEncoding:
    @pytest.mark.parametrize("payload", _B_PAYLOADS)
    def test_exposure_rows(self, payload):
        html = _render_exposure_section(
            [
                {
                    "hostname": payload,
                    "service": payload,
                    "auth_layer": payload,
                    "protocol": payload,
                    "has_auth": False,
                    "cf_proxied": None,
                    "waf": None,
                }
            ]
        )
        _b_assert_inert(html)

    @pytest.mark.parametrize("payload", _B_PAYLOADS)
    def test_image_names_tags_and_scan_text(self, monkeypatch, payload):
        monkeypatch.setenv("ACCOUNT_ID", "123456789012")
        findings = {
            payload: {
                "repo": payload,
                "tags": [payload],
                "scan_date": payload,
                "latest_digest": payload,
                "deployed_digest": payload,
                "critical": 1,
                "high": 0,
                "drift": {"has_update": True, "staleness": payload, "deployed_age_days": payload},
                "deployed_findings": {"scan_date": payload},
            },
            "broken": {"error": payload},
        }
        inventory = {payload: {"status": payload, "base": payload, "ecr_digest": payload, "notes": payload}}
        html = _render_vuln_section(findings, admin=True) + _render_images_section(findings, inventory, admin=True)
        _b_assert_inert(html)


class TestTreePageEncoding:
    @pytest.mark.parametrize("payload", _B_PAYLOADS)
    def test_module_node_escapes_state_content(self, payload):
        resource = {"compliant": False, "type": payload, "name": payload, "arn": payload, "id": payload}
        mod = {
            "module": payload,
            "file_path": payload,
            "stats": {"total": 1, "compliant": 0, "non_compliant": 1},
            "children": {payload: [resource]},
        }
        html = _render_module_node(mod, 0)
        _b_assert_inert(html)


class TestSelfPageEncoding:
    @pytest.mark.parametrize("payload", _B_PAYLOADS)
    def test_perf_results_are_escaped(self, monkeypatch, payload):
        route = {"route": payload, "status": payload, "ttfb_ms": 1, "total_ms": payload, "size_kb": payload}
        route["cf_cache"] = payload
        one_pass = {
            "routes": [route],
            "duration_ms": payload,
            "summary": {"p95_ttfb_ms": payload, "avg_ttfb_ms": 1, "slow_routes": [payload], "failed_routes": []},
        }
        monkeypatch.setattr(
            data, "get_perf_results", lambda: {"timestamp": payload, "passes": {"cold": one_pass, "warm": one_pass}}
        )
        trend = {"date": payload, "p95_cold": 1, "p95_warm": 1, "failed_count": payload}
        monkeypatch.setattr(data, "get_perf_trend", lambda days=14: [trend, trend])
        html = _render_performance()
        _b_assert_inert(html)

    @pytest.mark.parametrize("payload", _B_PAYLOADS)
    def test_configuration_values_are_escaped(self, monkeypatch, payload):
        monkeypatch.setenv("TF_STATE_KEY", payload)
        _b_assert_inert(_render_configuration())


class TestRepoFileUrl:
    def test_http_repository_tag_keeps_its_link(self):
        assert (
            _repo_file_url("https://example.com/org/repo", "main.tf")
            == "https://example.com/org/repo/blob/main/main.tf"
        )

    @pytest.mark.parametrize("base", [_B_URL, "JAVASCRIPT:alert(1)//", "data:text/html,x", "org/repo", _B_HTML])
    def test_non_http_repository_tag_gives_no_link(self, base):
        assert _repo_file_url(base, "main.tf") == ""

    def test_no_file_gives_no_link(self):
        assert _repo_file_url("https://example.com/org/repo", "") == ""


def test_third_party_script_is_pinned_with_integrity():
    """Every external script the dashboard loads names an exact version and carries an integrity hash."""
    import re
    from pathlib import Path

    lambda_dir = Path(__file__).resolve().parents[2] / "modules" / "aws" / "compliance-dashboard" / "lambda"
    tags = []
    for path in lambda_dir.glob("*.py"):
        tags += re.findall(r"<script[^>]*\bsrc=\"https?://[^>]*>", path.read_text())
    assert tags, "no external script found; update this test"
    for tag in tags:
        assert "d3js.org" not in tag
        assert re.search(r'integrity="sha(384|512)-[A-Za-z0-9+/=]+"', tag), tag
        assert 'crossorigin="anonymous"' in tag, tag
        assert re.search(r"/\d+\.\d+\.\d+/", tag), tag


class TestConsoleLinkHost:
    @pytest.mark.parametrize("region", ["evil.example/#", "eu-central-1.evil.example", "x/../", "", None, 7])
    def test_region_that_is_not_a_region_name_cannot_choose_the_host(self, region):
        import urllib.parse

        from views_base import REGION, _console_url

        url = _console_url("arn:aws:ec2:eu-central-1:123456789012:instance/i-0123456789abcdef0", region)
        assert urllib.parse.urlsplit(url).netloc == f"{REGION}.console.aws.amazon.com"

    def test_region_name_is_used(self):
        from views_base import _console_url

        url = _console_url("arn:aws:ec2:us-gov-west-1:123456789012:instance/i-0123456789abcdef0", "us-gov-west-1")
        assert url.startswith("https://us-gov-west-1.console.aws.amazon.com/")


def test_run_report_shortens_names_before_escaping(monkeypatch):
    import re

    import data_compliance

    arn = "arn:aws:ec2:eu-central-1:123456789012:instance/" + "&" * 60
    report = {
        "batch_id": "b1",
        "generated_at": "2026-01-01T00:00:00",
        "summary": {"total": 1, "by_status": {"warned": 1}, "savings_monthly": 0},
        "resources": [{"resource_arn": arn, "status": "warned", "estimated_monthly_cost": 1}],
    }
    monkeypatch.setattr(data_compliance, "get_latest_hk_run_report", lambda: report)
    page = data_compliance.get_latest_hk_run_html()
    cell = re.search(r"<td class='mono'>(.*?)</td>", page).group(1)
    assert cell == "&amp;" * 40
