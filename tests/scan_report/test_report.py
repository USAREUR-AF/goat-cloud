"""Report document shape, the HTML file, and the command line."""

import json
import re
from pathlib import Path

import pytest

from goatcloud import __main__ as cli
from goatcloud import contract, grade, report

from .conftest import FIXTURES, build_report, load_inventory

GOLDEN = Path(__file__).parent / "golden"
NUMBER = re.compile(r'<span data-n="([^"]+)">([^<]*)</span>')
SUB = "00000000-0000-0000-0000-000000000000"


def html_numbers(html):
    return dict(NUMBER.findall(html))


class TestDocument:
    def test_top_level_shape(self):
        document = build_report("azure")
        assert sorted(document) == [
            "categories",
            "contract",
            "findings",
            "generated_at",
            "graded",
            "incomplete",
            "inventory_generated_at",
            "not_evaluated",
            "platform",
            "resources",
            "schema_version",
            "scopes",
            "summary",
            "units",
        ]
        assert (document["schema_version"], document["platform"], document["graded"]) == ("2.0", "azure", "units")
        assert document["generated_at"] == "2030-06-15T00:00:00+00:00"
        assert document["contract"]["unit"] == "resource group"
        assert len(document["resources"]) == 15
        json.dumps(document)

    def test_azure_summary(self):
        summary = build_report("azure")["summary"]
        assert summary["total_resources"] == 15
        assert summary["units"] == 10
        assert summary["results"] == {"compliant": 4, "non-compliant": 6, "not-evaluated": 0, "pending": 0}
        assert summary["evaluated_units"] == 10
        assert summary["compliance_percentage"] == 40.0
        assert summary["findings_by_severity"] == {"HIGH": 2, "MEDIUM": 4, "LOW": 0, "INFO": 2}
        assert summary["findings_by_category"] == {
            "contract": 3,
            "unclaimed": 0,
            "missing": 0,
            "lifecycle": 5,
            "schedule": 0,
        }
        assert summary["by_scope"] == [
            {
                "scope": SUB,
                "units": 10,
                "results": summary["results"],
                "compliance_percentage": 40.0,
                "incomplete": False,
            }
        ]
        assert summary["resources_by_type"]["microsoft.authorization/roleassignments"] == {
            "total": 1,
            "class": "cannot-carry",
        }

    def test_gcp_summary_keeps_pending_out_of_the_percentage(self):
        summary = build_report("gcp")["summary"]
        assert summary["results"] == {"compliant": 3, "non-compliant": 5, "not-evaluated": 0, "pending": 1}
        assert summary["evaluated_units"] == 8
        assert summary["compliance_percentage"] == 37.5

    def test_fact_coverage_over_evaluated_units(self):
        coverage = build_report("azure")["summary"]["fact_coverage"]
        assert coverage["environment"] == {
            "key": "Environment",
            "required": True,
            "direct": 8,
            "inherited": 0,
            "missing": 2,
            "percentage": 80.0,
        }
        assert coverage["expires_at"]["required"] is False

    def test_units_carry_result_rules_and_their_resources(self):
        units = {u["name"]: u for u in build_report("azure")["units"]}
        assert (units["acme-expired"]["result"], units["acme-expired"]["rules"]) == ("non-compliant", ["expired"])
        assert units["acme-mixed"]["resources"]["total"] == 4
        assert units["acme-mixed"]["resources"]["with_own"] == 1

    def test_not_evaluated_lists_types_with_counts_and_reasons(self):
        left_out = build_report("gcp")["not_evaluated"]
        assert left_out["cannot_carry_types"] == [
            {
                "resource_type": "iam.googleapis.com/ServiceAccount",
                "count": 1,
                "reason": "the type has no labels field",
            },
            {
                "resource_type": "serviceusage.googleapis.com/Service",
                "count": 1,
                "reason": "a record, not a deployed resource",
            },
        ]
        assert left_out["unclassified_types"] == [{"resource_type": "example.googleapis.com/Widget", "count": 1}]
        assert left_out["facts"] == [
            {"fact": "repository", "reason": "not a label on this platform; carried by the registry entry"},
            {"fact": "contact", "reason": "not a label on this platform; carried by the registry entry"},
        ]
        azure = build_report("azure")["not_evaluated"]
        assert azure["unclassified_types"] == [{"resource_type": "example.provider/widgets", "count": 1}]
        assert azure["cannot_carry_types"][0]["resource_type"] == "microsoft.authorization/roleassignments"

    def test_per_resource_mode(self):
        document = build_report("azure", mode="resources")
        assert (document["graded"], document["contract"]["unit"]) == ("resources", "resource")
        assert document["summary"]["units"] == 15
        assert document["summary"]["results"]["not-evaluated"] == 2
        assert document["not_evaluated"]["units_by_reason"] == [
            {"reason": "the type cannot carry tags", "count": 1},
            {"reason": "unclassified type: nothing states whether it can carry tags", "count": 1},
        ]
        assert (
            build_report("azure", mode="resources", report_inherited=True)["summary"]["findings_by_severity"]["LOW"]
            == 2
        )

    def test_mapping_changes_the_result(self, gcp_mapping):
        default = {u["id"]: u["result"] for u in build_report("gcp")["units"]}
        mapped_report = build_report("gcp", mapping=gcp_mapping)
        mapped = {u["id"]: u["result"] for u in mapped_report["units"]}
        assert mapped_report["contract"]["mapping_applied"] is True
        assert mapped_report["contract"]["keys"]["project"] == "app_name"
        assert (default["example-other-keys"], mapped["example-other-keys"]) == ("non-compliant", "compliant")
        assert (default["example-conforming"], mapped["example-conforming"]) == ("compliant", "non-compliant")

    @pytest.mark.parametrize("provider", ["azure", "gcp"])
    def test_categories(self, provider):
        categories = build_report(provider)["categories"]
        assert list(categories) == ["contract", "unclaimed", "missing", "lifecycle", "schedule"]
        assert categories["contract"] == categories["lifecycle"] == {"status": "available"}
        for name in ("unclaimed", "missing", "schedule"):
            assert categories[name]["status"] == "not_available"
            assert categories[name]["reason"].startswith("not built")


class TestIncomplete:
    def document(self, **options):
        inventory = load_inventory("azure")
        inventory["incomplete"] = [{"scope": SUB, "reason": "throttled after 3 pages"}]
        return build_report("azure", inventory=inventory, **options)

    def test_no_compliance_figure_and_nothing_evaluated(self):
        document = self.document()
        assert document["incomplete"] == [{"scope": SUB, "reason": "throttled after 3 pages"}]
        assert document["summary"]["compliance_percentage"] is None
        assert document["summary"]["results"] == {"compliant": 0, "non-compliant": 0, "not-evaluated": 10, "pending": 0}
        assert document["summary"]["by_scope"][0]["compliance_percentage"] is None
        assert document["summary"]["by_scope"][0]["incomplete"] is True
        assert document["findings"] == []

    def test_stated_at_the_top_of_the_html(self):
        html = report.render_html(self.document())
        assert html.index("Incomplete scan.") < html.index('<div class="tiles">')
        assert "throttled after 3 pages" in html
        assert html_numbers(html)["compliance_percentage"] == "n/a"
        assert "Incomplete scan." not in report.render_html(build_report("azure"))

    def test_one_affected_sub_account_leaves_the_other_with_its_figure(self):
        other = "11111111-1111-1111-1111-111111111111"
        document = build_report("azure", expected_scopes=[SUB, other])
        by_sub = {entry["scope"]: entry for entry in document["summary"]["by_scope"]}
        assert by_sub[SUB]["compliance_percentage"] == 40.0
        assert document["summary"]["compliance_percentage"] is None
        assert document["incomplete"][0]["scope"] == other

    def test_empty_inventory_is_incomplete(self):
        resolved = contract.load("azure")
        inventory = {"provider": "azure", "resources": [], "scopes": []}
        document = report.build(inventory, resolved, grade.grade_units(inventory, resolved))
        assert document["summary"]["compliance_percentage"] is None
        assert document["incomplete"]


class TestHtml:
    @pytest.mark.parametrize("provider", ["azure", "gcp"])
    def test_key_numbers_match_the_golden_file(self, provider):
        html = report.render_html(build_report(provider))
        assert html_numbers(html) == json.loads((GOLDEN / f"{provider}_numbers.json").read_text())

    @pytest.mark.parametrize("provider", ["azure", "gcp"])
    def test_numbers_in_the_html_are_the_numbers_in_the_document(self, provider):
        document = build_report(provider)
        numbers = html_numbers(report.render_html(document))
        summary = document["summary"]
        assert numbers["units"] == str(summary["units"])
        assert numbers["total_resources"] == str(summary["total_resources"])
        assert numbers["compliance_percentage"] == f"{summary['compliance_percentage']:g}%"
        for result, count in summary["results"].items():
            assert numbers[f"results.{result}"] == str(count)
        for severity, count in summary["findings_by_severity"].items():
            assert numbers[f"findings.{severity}"] == str(count)
        for fact, entry in summary["fact_coverage"].items():
            assert numbers[f"coverage.{fact}.direct"] == str(entry["direct"])

    def test_self_contained(self):
        html = report.render_html(build_report("azure"))
        assert html.startswith("<!doctype html>")
        for external in ("<script", "<link", "<img", "@import", "url(", "http://", 'src="', 'href="'):
            assert external not in html
        assert "prefers-color-scheme:dark" in html and "@media print" in html

    def test_sections(self):
        html = report.render_html(build_report("gcp"))
        for heading in ("Projects", "Coverage by label", "Findings", "Resources by type", "Not evaluated"):
            assert f"<h2>{heading}</h2>" in html
        assert "The project carries the contract." in html
        assert "serviceusage.googleapis.com/<wbr>Service" in html
        assert "unclassified type" in html and "example.googleapis.com/<wbr>Widget" in html
        assert "<code>unclaimed</code>" in html and "not built" in html
        assert "carried by the registry entry" in html
        assert '<span class="res pending">pending</span>' in html
        assert "<h2>Resource groups</h2>" in report.render_html(build_report("azure"))

    def test_findings_show_their_evidence(self):
        html = report.render_html(build_report("azure"))
        assert "<code>Environment</code>: absent</li>" in html
        assert "<code>ManagedBy</code>: &#x27;chef&#x27;, read from the unit</li>" in html
        assert "<code>invalid-value</code>" in html

    def test_findings_are_grouped_by_severity_in_order(self):
        html = report.render_html(build_report("azure"))
        positions = [html.index(f'<span class="sev {s}">') for s in ("HIGH", "MEDIUM", "INFO")]
        assert positions == sorted(positions)
        assert '<span class="sev LOW">' not in html

    def test_non_compliant_units_are_listed_first(self):
        html = report.render_html(build_report("azure"))
        assert html.index('<span class="res non-compliant">') < html.index('<span class="res compliant">')

    def test_values_from_the_cloud_are_escaped(self):
        document = build_report("azure")
        document["findings"][0]["resource_name"] = "<script>alert(1)</script>"
        document["units"][0]["name"] = "<b>bold</b>"
        html = report.render_html(document)
        assert "<script>" not in html and "<b>bold</b>" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html

    def test_long_tables_are_capped_with_a_note(self, monkeypatch):
        monkeypatch.setattr(report, "HTML_ROWS", 2)
        html = report.render_html(build_report("azure"))
        assert "2 more MEDIUM findings are in the JSON report." in html
        assert "8 more resource groups are in the JSON report." in html


class TestCommandLine:
    def test_grade_writes_both_reports(self, tmp_path, capsys):
        out, html = tmp_path / "report.json", tmp_path / "report.html"
        code = cli.main(
            ["grade", "--inventory", str(FIXTURES / "azure_inventory.json"), "--out", str(out), "--html", str(html)]
        )
        assert code == 0
        assert json.loads(out.read_text())["summary"]["units"] == 10
        assert html.read_text().startswith("<!doctype html>")
        assert "azure: 10 resource groups: " in capsys.readouterr().err

    def test_grade_resources_with_inherited_findings(self, tmp_path):
        out = tmp_path / "report.json"
        inventory = str(FIXTURES / "azure_inventory.json")
        cli.main(["grade", "--inventory", inventory, "--grade", "resources", "--report-inherited", "--out", str(out)])
        document = json.loads(out.read_text())
        assert document["graded"] == "resources"
        assert document["summary"]["findings_by_severity"]["LOW"] == 2

    def test_grade_with_mapping_and_lag(self, tmp_path, gcp_mapping):
        out = tmp_path / "report.json"
        inventory = str(FIXTURES / "gcp_inventory.json")
        cli.main(["grade", "--inventory", inventory, "--mapping", str(gcp_mapping), "--out", str(out)])
        assert json.loads(out.read_text())["contract"]["keys"]["environment"] == "env"

    def test_expected_sub_account_that_is_absent_is_reported(self, tmp_path, capsys):
        out = tmp_path / "report.json"
        inventory = str(FIXTURES / "gcp_inventory.json")
        cli.main(["grade", "--inventory", inventory, "--expect-scope", "example-absent", "--out", str(out)])
        assert json.loads(out.read_text())["summary"]["compliance_percentage"] is None
        assert "INCOMPLETE example-absent: asked for and not returned" in capsys.readouterr().err

    def test_scan_azure_uses_the_adapter_and_expects_the_subscription(self, tmp_path, monkeypatch):
        from adapters.azure import inventory

        document = load_inventory("azure")
        seen = {}
        monkeypatch.setattr(inventory, "sdk_page_fn", lambda credential=None: "page-fn")
        monkeypatch.setattr(inventory, "sdk_arm_get_fn", lambda credential=None: "arm-get")
        monkeypatch.setattr(inventory, "discover", lambda page, **scope: seen.update(scope, page=page) or document)
        out, saved = tmp_path / "report.json", tmp_path / "inventory.json"
        other = "11111111-1111-1111-1111-111111111111"
        cli.main(
            [
                "scan",
                "--platform",
                "azure",
                "--subscription",
                SUB,
                "--subscription",
                other,
                "--out",
                str(out),
                "--inventory-out",
                str(saved),
            ]
        )
        assert seen == {
            "subscriptions": [SUB, other],
            "management_groups": [],
            "arm_get": "arm-get",
            "page": "page-fn",
        }
        assert json.loads(saved.read_text()) == document
        assert json.loads(out.read_text())["incomplete"][0]["scope"] == other

    def test_scan_gcp_over_rest_when_a_token_is_set(self, tmp_path, monkeypatch):
        from adapters.gcp import inventory

        document = load_inventory("gcp")
        seen = {}
        monkeypatch.setenv(cli.GCP_TOKEN_VARIABLE, "synthetic-token")
        monkeypatch.setattr(inventory, "rest_page_fetcher", lambda token, quota: seen.update(token=token, quota=quota))
        monkeypatch.setattr(inventory, "discover", lambda scope, fetch: seen.update(scope=scope) or document)
        out = tmp_path / "report.json"
        cli.main(
            [
                "scan",
                "--platform",
                "gcp",
                "--scope",
                "projects/example-mixed",
                "--quota-project",
                "example-mixed",
                "--out",
                str(out),
            ]
        )
        assert seen == {"token": "synthetic-token", "quota": "example-mixed", "scope": "projects/example-mixed"}
        assert json.loads(out.read_text())["incomplete"] == []

    @pytest.mark.parametrize(
        "argv",
        [
            ["grade", "--inventory", "x.json"],
            ["scan", "--platform", "example-cloud", "--out", "-"],
            ["scan", "--platform", "azure", "--out", "-"],
            ["scan", "--platform", "gcp", "--out", "-"],
            ["grade", "--inventory", "x.json", "--grade", "everything", "--out", "-"],
        ],
    )
    def test_usage_errors(self, argv, monkeypatch):
        monkeypatch.delenv(cli.GCP_TOKEN_VARIABLE, raising=False)
        with pytest.raises(SystemExit) as error:
            cli.main(argv)
        assert error.value.code not in (0, None)

    def test_inventory_of_an_unknown_provider_is_refused(self, tmp_path):
        path = tmp_path / "inventory.json"
        path.write_text(json.dumps({"provider": "example-cloud", "resources": []}))
        with pytest.raises(SystemExit, match="azure, gcp"):
            cli.main(["grade", "--inventory", str(path), "--out", "-"])

    def test_invalid_mapping_is_a_readable_error(self, tmp_path):
        mapping = tmp_path / "mapping.yaml"
        mapping.write_text("contract_schema_version: '9.9'\n")
        with pytest.raises(SystemExit, match="contract: mapping"):
            cli.main(
                ["grade", "--inventory", str(FIXTURES / "gcp_inventory.json"), "--mapping", str(mapping), "--out", "-"]
            )
