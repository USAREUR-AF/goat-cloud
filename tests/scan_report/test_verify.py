"""The truth-file check: the synthetic truth sets give no false result, and every kind of false result is caught."""

import copy
import json

import pytest
import yaml

from goatcloud import __main__ as cli
from goatcloud import verify

from .conftest import FIXTURES, build_report, load_inventory


def truth(name):
    return verify.load_truth(str(FIXTURES / name))


@pytest.mark.parametrize(
    ("provider", "mode", "name", "tp", "tn"),
    [
        ("azure", "units", "azure_truth.yaml", 8, 4),
        ("azure", "resources", "azure_resources_truth.yaml", 2, 3),
        ("gcp", "units", "gcp_truth.yaml", 7, 4),
    ],
)
def test_truth_sets_have_no_false_result(provider, mode, name, tp, tn):
    outcome = verify.verify(truth(name), build_report(provider, mode=mode))
    assert outcome["counts"] == {"TP": tp, "TN": tn, "FP": 0, "FN": 0}
    assert outcome["mismatches"] == []
    assert outcome["ok"] is True


@pytest.mark.parametrize(("provider", "name"), [("azure", "azure_truth.yaml"), ("gcp", "gcp_truth.yaml")])
def test_truth_sets_cover_every_unit_and_each_single_rule(provider, name):
    outcome = verify.verify(truth(name), build_report(provider))
    assert outcome["uncovered_units"] == []
    rules = [tuple(unit["rules"]) for unit in truth(name)["units"]]
    for rule in ("missing-required", "expired", "expiry-unreadable", "invalid-value", "protected"):
        assert (rule,) in rules, rule
    assert () in rules


class TestFalseResultsAreCaught:
    @pytest.fixture
    def report(self):
        return build_report("azure")

    @pytest.fixture
    def expected(self):
        return truth("azure_truth.yaml")

    def unit(self, report, name):
        return next(u for u in report["units"] if u["id"].endswith(name))

    def test_rule_reported_and_not_expected_is_a_false_positive(self, report, expected):
        self.unit(report, "acme-conforming")["rules"] = ["expired"]
        outcome = verify.verify(expected, report)
        assert (outcome["counts"]["FP"], outcome["counts"]["FN"], outcome["ok"]) == (1, 0, False)
        assert outcome["mismatches"] == ["FP */resourcegroups/acme-conforming: rule expired reported and not expected"]

    def test_non_compliant_reported_for_a_compliant_unit_is_a_false_positive(self, report, expected):
        self.unit(report, "acme-conforming")["result"] = "non-compliant"
        assert verify.verify(expected, report)["counts"]["FP"] == 1

    def test_rule_expected_and_not_reported_is_a_false_negative(self, report, expected):
        self.unit(report, "acme-expired").update(result="compliant", rules=[])
        outcome = verify.verify(expected, report)
        assert (outcome["counts"]["FP"], outcome["counts"]["FN"]) == (0, 2)
        assert outcome["counts"]["TP"] == 7

    def test_wrong_rule_is_one_of_each(self, report, expected):
        self.unit(report, "acme-expired")["rules"] = ["expiry-unreadable"]
        outcome = verify.verify(expected, report)
        assert (outcome["counts"]["FP"], outcome["counts"]["FN"]) == (1, 1)

    def test_not_evaluated_where_a_result_is_expected_is_a_false_negative(self, report, expected):
        self.unit(report, "acme-conforming").update(result="not-evaluated", reason="incomplete scan: throttled")
        outcome = verify.verify(expected, report)
        assert outcome["counts"]["FN"] == 1
        assert "not-evaluated (incomplete scan: throttled), expected compliant" in outcome["mismatches"][0]

    def test_unit_missing_from_the_report_is_a_false_negative(self, report, expected):
        report["units"] = [u for u in report["units"] if not u["id"].endswith("acme-expired")]
        outcome = verify.verify(expected, report)
        assert outcome["counts"]["FN"] == 1
        assert "matches no unit" in outcome["mismatches"][0]

    def test_pattern_matching_several_units_is_refused_as_a_false_negative(self, report, expected):
        expected = copy.deepcopy(expected)
        expected["units"] = [{"id": "*/resourcegroups/acme-*", "result": "compliant", "rules": []}]
        outcome = verify.verify(expected, report)
        assert outcome["counts"] == {"TP": 0, "TN": 0, "FP": 0, "FN": 1}
        assert "matches 10 units" in outcome["mismatches"][0]

    def test_pending_reported_as_non_compliant_is_a_false_positive(self):
        report = build_report("gcp")
        next(u for u in report["units"] if u["id"] == "example-new")["result"] = "non-compliant"
        assert verify.verify(truth("gcp_truth.yaml"), report)["counts"]["FP"] == 1

    def test_units_the_truth_does_not_cover_are_listed(self, report, expected):
        expected = copy.deepcopy(expected)
        expected["units"] = expected["units"][:1]
        outcome = verify.verify(expected, report)
        assert outcome["ok"] is True
        assert len(outcome["uncovered_units"]) == 9
        assert len(outcome["uncovered_non_compliant"]) == 6

    def test_patterns_match_without_regard_to_case(self, report, expected):
        expected = copy.deepcopy(expected)
        expected["units"] = [{"id": "*/ResourceGroups/ACME-Conforming", "result": "compliant", "rules": []}]
        assert verify.verify(expected, report)["counts"]["TN"] == 1


class TestTruthFile:
    def test_wrong_provider_or_grade_is_refused(self):
        with pytest.raises(verify.TruthError, match="platform"):
            verify.verify(truth("gcp_truth.yaml"), build_report("azure"))
        with pytest.raises(verify.TruthError, match="grades resources"):
            verify.verify(truth("azure_resources_truth.yaml"), build_report("azure"))

    @pytest.mark.parametrize(
        ("document", "message"),
        [
            ({"schema_version": "9"}, "schema_version"),
            ({"schema_version": "1.0", "units": []}, "non-empty list"),
            ({"schema_version": "1.0", "units": [{"result": "compliant"}]}, "id"),
            ({"schema_version": "1.0", "units": [{"id": "x", "result": "fine"}]}, "result"),
            ({"schema_version": "1.0", "units": [{"id": "x", "result": "compliant", "rules": "expired"}]}, "rules"),
        ],
    )
    def test_invalid_truth_file(self, tmp_path, document, message):
        path = tmp_path / "truth.yaml"
        path.write_text(yaml.safe_dump(document))
        with pytest.raises(verify.TruthError, match=message):
            verify.load_truth(str(path))


class TestCommand:
    def write(self, tmp_path, report):
        path = tmp_path / "report.json"
        path.write_text(json.dumps(report))
        return str(path)

    def test_exit_0_and_counts_when_there_is_no_false_result(self, tmp_path, capsys):
        report = self.write(tmp_path, build_report("gcp"))
        assert cli.main(["verify", "--truth", str(FIXTURES / "gcp_truth.yaml"), "--report", report]) == 0
        assert "TP 7  TN 4  FP 0  FN 0" in capsys.readouterr().out

    def test_exit_1_on_a_false_result(self, tmp_path, capsys):
        document = build_report("gcp")
        document["units"][0]["rules"] = ["expired"]
        report = self.write(tmp_path, document)
        assert cli.main(["verify", "--truth", str(FIXTURES / "gcp_truth.yaml"), "--report", report]) == 1
        out = capsys.readouterr().out
        assert "FP " in out and "FP 1" in out

    def test_strict_fails_on_an_uncovered_non_compliant_unit(self, tmp_path, capsys):
        truth_path = tmp_path / "truth.yaml"
        truth_path.write_text(
            yaml.safe_dump(
                {
                    "schema_version": "1.0",
                    "platform": "gcp",
                    "units": [{"id": "example-conforming", "result": "compliant", "rules": []}],
                }
            )
        )
        report = self.write(tmp_path, build_report("gcp"))
        assert cli.main(["verify", "--truth", str(truth_path), "--report", report]) == 0
        assert cli.main(["verify", "--truth", str(truth_path), "--report", report, "--strict"]) == 1
        assert "UNCOVERED non-compliant example-expired" in capsys.readouterr().out

    def test_unreadable_truth_is_a_readable_error(self, tmp_path):
        truth_path = tmp_path / "truth.yaml"
        truth_path.write_text("schema_version: '9'\n")
        with pytest.raises(SystemExit, match="truth: "):
            cli.main(["verify", "--truth", str(truth_path), "--report", self.write(tmp_path, build_report("gcp"))])


class TestControlCharacters:
    """Text from an inventory, report or truth file never reaches the terminal with a raw control character."""

    ESC = "\x1b]0;title\x07\x1b[2J"

    def test_terminal_safe_escapes_controls_and_keeps_text(self):
        assert cli.terminal_safe(f"a{self.ESC}b‮") == "a\\x1b]0;title\\x07\\x1b[2Jb\\u202e"
        assert cli.terminal_safe("plain text: ok") == "plain text: ok"
        assert cli.terminal_safe("line\nbreak\r") == "line\\nbreak\\r"

    def test_incomplete_reason_is_escaped(self, tmp_path, capsys):
        inventory = load_inventory("azure")
        inventory["incomplete"] = [{"scope": None, "reason": f"cut short{self.ESC}"}]
        path = tmp_path / "inventory.json"
        path.write_text(json.dumps(inventory))
        cli.main(["grade", "--inventory", str(path), "--out", str(tmp_path / "report.json")])
        err = capsys.readouterr().err
        assert "\x1b" not in err and "\x07" not in err
        assert "INCOMPLETE whole scan: cut short\\x1b]0;title\\x07\\x1b[2J" in err

    def test_verify_output_is_escaped(self, tmp_path, capsys):
        document = build_report("gcp")
        unit = next(u for u in document["units"] if u["id"] == "example-missing-environment")
        unit["id"] = f"example-missing-environment{self.ESC}"
        report = tmp_path / "report.json"
        report.write_text(json.dumps(document))
        truth = tmp_path / "truth.yaml"
        truth.write_text(
            yaml.safe_dump(
                {
                    "schema_version": "1.0",
                    "platform": "gcp",
                    "units": [{"id": "example-expired", "result": "compliant", "rules": []}],
                }
            )
        )
        cli.main(["verify", "--truth", str(truth), "--report", str(report), "--strict"])
        out = capsys.readouterr().out
        assert "\x1b" not in out and "\x07" not in out
        assert "\\x1b" in out
