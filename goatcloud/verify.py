"""Compare a report with a truth file and count true and false positives and negatives.

A truth file states, for units a test ground was built with, the result the
scan must give and the exact set of finding rule ids it must report:

    schema_version: "1.0"
    platform: azure
    grade: units            # optional: units (default) or resources
    units:
      - id: "*/resourcegroups/acme-ref-conforming"
        result: compliant
        rules: []
      - id: "*/resourcegroups/acme-ref-expired"
        result: non-compliant
        rules: [expired]

``id`` is a shell-style pattern matched without regard to case against the
unit id in the report; it must match exactly one unit.

Counting, with "positive" meaning the scan reports a problem:

    TP  an expected rule id is reported on the unit
    FN  an expected rule id is not reported; or the unit is expected
        non-compliant and reported otherwise; or the unit is missing from the
        report; or a unit expected to be evaluated is reported not-evaluated
    FP  a rule id is reported that the truth does not list; or the unit is
        reported non-compliant and expected otherwise
    TN  a unit expected compliant, pending or not-evaluated is reported so,
        with exactly the expected rule ids (counted once per unit)
"""

from __future__ import annotations

import fnmatch
from typing import Any

import yaml

from goatcloud import names

TRUTH_SCHEMA_VERSION = "1.0"


class TruthError(ValueError):
    """The truth file is not valid, or does not fit the report."""


def load_truth(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        truth = yaml.safe_load(handle)
    if not isinstance(truth, dict) or truth.get("schema_version") != TRUTH_SCHEMA_VERSION:
        raise TruthError(f"{path}: schema_version must be {TRUTH_SCHEMA_VERSION!r}")
    units = truth.get("units")
    if not isinstance(units, list) or not units:
        raise TruthError(f"{path}: units: expected a non-empty list")
    for index, unit in enumerate(units):
        where = f"{path}: units[{index}]"
        if not isinstance(unit, dict) or not isinstance(unit.get("id"), str) or not unit["id"]:
            raise TruthError(f"{where}: id: expected a pattern")
        if unit.get("result") not in names.RESULTS:
            raise TruthError(f"{where}: result: expected one of {', '.join(names.RESULTS)}")
        rules = unit.setdefault("rules", [])
        if not isinstance(rules, list) or not all(isinstance(rule, str) for rule in rules):
            raise TruthError(f"{where}: rules: expected a list of rule ids")
    return truth


def verify(truth: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    """Compare and return the counts, one line per mismatch, and the units the truth does not cover."""
    wanted, found = names.platform_of(truth), names.platform_of(report)
    if wanted and wanted != found:
        raise TruthError(f"truth is for platform {wanted!r}, the report is for {found!r}")
    expected_grade = truth.get("grade", "units")
    if report.get("graded") != expected_grade:
        raise TruthError(f"truth is for a scan that grades {expected_grade}; the report graded {report.get('graded')}")

    reported = {unit["id"]: unit for unit in report.get("units") or []}
    counts = {"TP": 0, "TN": 0, "FP": 0, "FN": 0}
    mismatches: list[str] = []
    covered: set[str] = set()

    def miss(kind: str, text: str) -> None:
        counts[kind] += 1
        mismatches.append(f"{kind} {text}")

    for expected in truth["units"]:
        pattern = expected["id"].lower()
        matched = [unit_id for unit_id in reported if fnmatch.fnmatchcase(unit_id.lower(), pattern)]
        if len(matched) != 1:
            if matched:
                miss("FN", f"{expected['id']}: the pattern matches {len(matched)} units in the report")
            else:
                miss(
                    "FN",
                    f"{expected['id']}: the pattern matches no unit in the report "
                    "(a cloud inventory can list a new resource minutes after it is created; scan again)",
                )
            covered.update(matched)
            continue
        unit = reported[matched[0]]
        covered.add(matched[0])
        want, got = expected["result"], unit["result"]
        want_rules, got_rules = set(expected["rules"]), set(unit.get("rules") or [])
        clean = True
        counts["TP"] += len(want_rules & got_rules)
        for rule in sorted(want_rules - got_rules):
            clean = False
            miss("FN", f"{expected['id']}: rule {rule} expected and not reported")
        for rule in sorted(got_rules - want_rules):
            clean = False
            miss("FP", f"{expected['id']}: rule {rule} reported and not expected")
        if want != got:
            clean = False
            kind = "FP" if got == names.NON_COMPLIANT else "FN"
            reason = f" ({unit['reason']})" if unit.get("reason") else ""
            miss(kind, f"{expected['id']}: result {got}{reason}, expected {want}")
        if clean and want != names.NON_COMPLIANT:
            counts["TN"] += 1

    uncovered = sorted(set(reported) - covered)
    return {
        "counts": counts,
        "mismatches": mismatches,
        "truth_units": len(truth["units"]),
        "uncovered_units": uncovered,
        "uncovered_non_compliant": sorted(u for u in uncovered if reported[u]["result"] == names.NON_COMPLIANT),
        "ok": counts["FP"] == 0 and counts["FN"] == 0,
    }
