"""Command line: ``python -m goatcloud scan``, ``grade`` and ``verify``."""

from __future__ import annotations

import argparse
import json
import os
import sys
import unicodedata
from typing import Any

from goatcloud import contract as contract_module
from goatcloud import grade as grade_module
from goatcloud import names
from goatcloud import report as report_module
from goatcloud import verify as verify_module
from goatcloud.profiles import PROFILES

# An OAuth access token for Google Cloud. When set, the scan calls the REST
# endpoint with it and needs no client library.
GCP_TOKEN_VARIABLE = "GOOGLE_OAUTH_ACCESS_TOKEN"


def _azure_inventory(args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    from adapters.azure import inventory

    if bool(args.subscription) == bool(args.management_group):
        raise SystemExit("azure: give --subscription or --management-group, one of the two")
    credential = None
    if args.az_cli:
        from azure.identity import AzureCliCredential

        credential = AzureCliCredential(subscription=args.subscription[0] if args.subscription else None)
    document = inventory.discover(
        inventory.sdk_page_fn(credential),
        subscriptions=args.subscription,
        management_groups=args.management_group,
        arm_get=inventory.sdk_arm_get_fn(credential),
    )
    return document, list(args.subscription)


def _gcp_inventory(args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    from adapters.gcp import inventory

    if not args.scope:
        raise SystemExit("gcp: give --scope organizations/<number>, folders/<number> or projects/<id>")
    token = os.environ.get(GCP_TOKEN_VARIABLE)
    fetch_page = inventory.rest_page_fetcher(token, args.quota_project) if token else None
    kind, _, identifier = args.scope.partition("/")
    return inventory.discover(args.scope, fetch_page), ([identifier] if kind == "projects" else [])


INVENTORIES = {"azure": _azure_inventory, "gcp": _gcp_inventory}


def terminal_safe(value: object) -> str:
    """The text of ``value`` with every control or format character escaped.

    Inventory, report and truth files come from outside: a reason or a unit id
    may hold an escape sequence that would move the cursor, rewrite earlier
    output or set the window title. Printed text never carries one raw.
    """
    text = str(value)
    return "".join(ch if unicodedata.category(ch) not in ("Cc", "Cf", "Zl", "Zp") else ascii(ch)[1:-1] for ch in text)


def _say(*parts: object, file=None) -> None:
    print(" ".join(terminal_safe(part) for part in parts), file=file)


def _write(path: str, text: str) -> None:
    if path == "-":
        sys.stdout.write(text)
    else:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)


def run(inventory: dict[str, Any], args: argparse.Namespace, expected: list[str]) -> dict[str, Any]:
    """Grade an inventory document and write the reports named in ``args``."""
    platform = names.platform_of(inventory)
    if platform not in PROFILES:
        raise SystemExit(f"inventory platform is {platform!r}; this command grades: {', '.join(sorted(PROFILES))}")
    contract = contract_module.load(platform, args.contract, args.mapping)
    options: dict[str, Any] = {"lag_minutes": args.lag_minutes, "expected_scopes": expected}
    if args.grade == "resources":
        options["report_inherited"] = args.report_inherited
    graded = grade_module.GRADERS[args.grade](inventory, contract, **options)
    report = report_module.build(inventory, contract, graded)
    if args.out:
        _write(args.out, json.dumps(report, indent=2, sort_keys=True) + "\n")
    if args.html:
        _write(args.html, report_module.render_html(report))
    summary = report["summary"]
    results = ", ".join(f"{count} {name}" for name, count in summary["results"].items())
    percentage = summary["compliance_percentage"]
    unit = report["contract"]["unit"]
    _say(
        f"{platform}: {summary['units']} {unit}s: {results}; "
        f"compliance {'n/a' if percentage is None else f'{percentage:g}%'}; "
        f"{summary['total_findings']} findings; {summary['total_resources']} resources listed",
        file=sys.stderr,
    )
    for entry in report["incomplete"]:
        _say(f"INCOMPLETE {entry[names.SCOPE] or 'whole scan'}: {entry['reason']}", file=sys.stderr)
    return report


def run_verify(args: argparse.Namespace) -> int:
    with open(args.report, encoding="utf-8") as handle:
        report = json.load(handle)
    outcome = verify_module.verify(verify_module.load_truth(args.truth), report)
    counts = outcome["counts"]
    for line in outcome["mismatches"]:
        _say(line)
    _say(
        f"TP {counts['TP']}  TN {counts['TN']}  FP {counts['FP']}  FN {counts['FN']}  "
        f"({outcome['truth_units']} truth units; {len(outcome['uncovered_units'])} units in the report "
        f"not covered by the truth, {len(outcome['uncovered_non_compliant'])} of them non-compliant)"
    )
    if args.strict and outcome["uncovered_non_compliant"]:
        for unit_id in outcome["uncovered_non_compliant"]:
            _say(f"UNCOVERED non-compliant {unit_id}")
        return 1
    return 0 if outcome["ok"] else 1


def _report_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--contract", help="contract file (default: contract/contract.yaml of this checkout)")
    parser.add_argument("--mapping", help="deployment mapping file: key names and required set of one deployment")
    parser.add_argument("--out", help="write the JSON report here; - for standard output")
    parser.add_argument("--html", help="write the HTML report here")
    parser.add_argument(
        "--grade",
        choices=sorted(grade_module.GRADERS),
        default="units",
        help="units (default): grade the resource group or project that carries the contract; "
        "resources: grade every resource",
    )
    parser.add_argument(
        "--lag-minutes",
        type=float,
        default=grade_module.DEFAULT_LAG_MINUTES,
        help="a unit created within this many minutes that misses a required fact is pending (default 15)",
    )
    parser.add_argument(
        "--report-inherited",
        action="store_true",
        help="with --grade resources: add a LOW finding for a required fact found only on the unit",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m goatcloud",
        description="Read-only: list a cloud scope, grade it against the resource contract, write a report.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    scan = commands.add_parser("scan", help="list a scope, grade it and write the report")
    scan.add_argument("--platform", choices=sorted(INVENTORIES), help="the platform to list (required)")
    scan.add_argument("--provider", dest="platform", choices=sorted(INVENTORIES), help="deprecated: use --platform")
    scan.add_argument("--subscription", action="append", default=[], help="azure: subscription id; repeatable")
    scan.add_argument("--management-group", action="append", default=[], help="azure: management group; repeatable")
    scan.add_argument("--az-cli", action="store_true", help="azure: sign in as the Azure CLI account")
    scan.add_argument("--scope", help="gcp: organizations/<number>, folders/<number> or projects/<id>")
    scan.add_argument("--quota-project", help=f"gcp: project billed for the API calls, with {GCP_TOKEN_VARIABLE}")
    scan.add_argument("--inventory-out", help="also write the inventory here, for `grade --inventory`")
    _report_arguments(scan)

    grade = commands.add_parser("grade", help="grade a saved inventory and write the report")
    grade.add_argument("--inventory", required=True, help="inventory file written by scan or by an adapter")
    grade.add_argument(
        "--expect-scope",
        action="append",
        default=[],
        help="a subscription or project the inventory must hold; the scan is incomplete without it; repeatable",
    )
    _report_arguments(grade)

    verify = commands.add_parser("verify", help="compare a report with a truth file; exit 1 on a false result")
    verify.add_argument("--truth", required=True, help="truth file: unit id pattern, expected result and rule ids")
    verify.add_argument("--report", required=True, help="JSON report written by scan or grade")
    verify.add_argument(
        "--strict", action="store_true", help="also fail when a non-compliant unit is not covered by the truth"
    )

    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            return run_verify(args)
        if not (args.out or args.html):
            parser.error("give --out, --html or both")
        if args.command == "scan":
            if not args.platform:
                parser.error("scan needs --platform")
            inventory, expected = INVENTORIES[args.platform](args)
            if args.inventory_out:
                _write(args.inventory_out, json.dumps(inventory, indent=2, sort_keys=True) + "\n")
        else:
            with open(args.inventory, encoding="utf-8") as handle:
                inventory = json.load(handle)
            expected = args.expect_scope
        run(inventory, args, expected)
    except contract_module.ContractError as error:
        raise SystemExit(terminal_safe(f"contract: {error}")) from error
    except verify_module.TruthError as error:
        raise SystemExit(terminal_safe(f"truth: {error}")) from error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
