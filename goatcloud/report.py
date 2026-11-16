"""Build the report (schema 2.0) from a graded inventory, and render it as one static HTML file."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from html import escape
from typing import Any

from goatcloud import REPORT_SCHEMA_VERSION, names
from goatcloud.contract import PlatformContract
from goatcloud.grade import DIRECT, INHERITED, SEVERITIES
from goatcloud.profiles import CANNOT_CARRY, UNCLASSIFIED, Profile, capability, profile_for

# Rows written per table in the HTML; the JSON report holds all.
HTML_ROWS = 500
EVALUATED = (names.COMPLIANT, names.NON_COMPLIANT)


def _percentage(part: int, whole: int) -> float | None:
    return round(100 * part / whole, 1) if whole else None


def _results(units: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(unit["result"] for unit in units)
    return {result: counts.get(result, 0) for result in names.RESULTS}


def _compliance(results: dict[str, int], incomplete: bool) -> float | None:
    """Compliant over compliant plus non-compliant. None when nothing was evaluated or the input is incomplete."""
    if incomplete:
        return None
    return _percentage(results[names.COMPLIANT], results[names.COMPLIANT] + results[names.NON_COMPLIANT])


def build(
    inventory: dict[str, Any],
    contract: PlatformContract,
    graded: dict[str, Any],
    *,
    profile: Profile | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Assemble the report document."""
    profile = profile or profile_for(contract.platform)
    resources = inventory.get("resources") or []
    units, findings, incomplete = graded["units"], graded["findings"], graded["incomplete"]
    with_facts = [unit for unit in units if "facts" in unit]
    results = _results(units)

    coverage = {}
    for name in contract.keyed:
        sources = Counter(unit["facts"][name] for unit in with_facts)
        coverage[name] = {
            "key": contract.key(name),
            "required": contract.facts[name].required,
            "direct": sources[DIRECT],
            "inherited": sources[INHERITED],
            "missing": len(with_facts) - sources[DIRECT] - sources[INHERITED],
            "percentage": _percentage(sources[DIRECT] + sources[INHERITED], len(with_facts)),
        }

    unclassified = frozenset(inventory.get("unclassified_types") or {})
    by_type: dict[str, dict[str, Any]] = {}
    cannot_carry: Counter[tuple[str, str]] = Counter()
    unclassified_counts: Counter[str] = Counter()
    for record in resources:
        resource_type = record.get("resource_type", "")
        cls, reason = capability(record, profile, unclassified)
        entry = by_type.setdefault(resource_type, {"total": 0, "class": cls})
        entry["total"] += 1
        if cls == CANNOT_CARRY:
            cannot_carry[(resource_type, reason)] += 1
        elif cls == UNCLASSIFIED:
            unclassified_counts[resource_type] += 1

    blocked = {entry[names.SCOPE] for entry in incomplete}
    by_scope = []
    listed = [names.scope_of(entry) for entry in names.scopes_of(inventory)]
    for sub_account in sorted({*listed, *(unit[names.SCOPE] for unit in units)} - {""}):
        own = _results([unit for unit in units if unit[names.SCOPE] == sub_account])
        affected = None in blocked or sub_account in blocked
        by_scope.append(
            {
                names.SCOPE: sub_account,
                "units": sum(own.values()),
                "results": own,
                "compliance_percentage": _compliance(own, affected),
                "incomplete": affected,
            }
        )

    severities = Counter(f["severity"] for f in findings)
    categories = Counter(f["category"] for f in findings)
    reasons = Counter(unit["reason"] for unit in units if unit["result"] == names.NOT_EVALUATED)

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        names.PLATFORM: contract.platform,
        "generated_at": (generated_at or datetime.now(UTC)).isoformat(timespec="seconds"),
        "inventory_generated_at": inventory.get("generated_at"),
        "graded": graded["graded"],
        names.SCOPES: names.scopes_of(inventory),
        "incomplete": incomplete,
        "contract": {
            "version": contract.version,
            "carrier": profile.kind,
            "unit": profile.unit_kind if graded["graded"] == "units" else "resource",
            "mapping_applied": contract.mapping_applied,
            "keys": {name: contract.key(name) for name in contract.keyed},
            "required": contract.required,
        },
        "summary": {
            "total_resources": len(resources),
            "units": len(units),
            "results": results,
            "evaluated_units": results[names.COMPLIANT] + results[names.NON_COMPLIANT],
            "compliance_percentage": _compliance(results, bool(incomplete)),
            "total_findings": len(findings),
            "findings_by_severity": {s: severities.get(s, 0) for s in SEVERITIES},
            "findings_by_category": {c: categories.get(c, 0) for c in names.CATEGORIES},
            "by_scope": by_scope,
            "resources_by_type": dict(sorted(by_type.items())),
            "fact_coverage": coverage,
        },
        "categories": {
            category: (
                {"status": "not_available", "reason": profile.not_available[category]}
                if category in profile.not_available
                else {"status": "available"}
            )
            for category in names.CATEGORIES
        },
        "not_evaluated": {
            "units_by_reason": [{"reason": reason, "count": count} for reason, count in sorted(reasons.items())],
            "cannot_carry_types": [
                {"resource_type": resource_type, "count": count, "reason": reason}
                for (resource_type, reason), count in sorted(cannot_carry.items())
            ],
            "unclassified_types": [
                {"resource_type": resource_type, "count": count}
                for resource_type, count in sorted(unclassified_counts.items())
            ],
            "resources_without_unit": graded.get("resources_without_unit") or {},
            "facts": [
                {
                    "fact": fact.name,
                    "reason": f"not a {profile.kind} on this platform"
                    + (f"; carried by the {fact.carried_by.replace('-', ' ')}" if fact.carried_by else ""),
                }
                for fact in contract.not_attached
            ],
        },
        "units": units,
        "findings": findings,
        "resources": resources,
    }


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

_CSS = """
:root{--bg:#fbfbfa;--panel:#fff;--ink:#1c1e21;--muted:#5d646c;--line:#d9dde1;--bar:#2f6f9f;--bar2:#9cc3dd;
--track:#e9ecef;--high:#b3261e;--medium:#9a5b00;--low:#3d5a80;--info:#5d646c;--ok:#1f6f43}
@media (prefers-color-scheme:dark){:root{--bg:#15171a;--panel:#1d2024;--ink:#e6e8ea;--muted:#a3abb3;--line:#353a40;
--bar:#6fb1dc;--bar2:#3c6480;--track:#2a2e33;--high:#ff8a80;--medium:#f2b866;--low:#9db8d9;--info:#a3abb3;--ok:#7fd1a3}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:1.5rem;margin:0 0 4px}h2{font-size:1.1rem;margin:32px 0 10px}h3{font-size:1rem;margin:20px 0 6px}
p{margin:6px 0}.muted{color:var(--muted)}
code{font:0.86em ui-monospace,SFMono-Regular,Menlo,monospace;overflow-wrap:anywhere}
code.t{overflow-wrap:normal}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:16px 0}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:12px 14px}
.tile b{display:block;font-size:1.7rem;line-height:1.2;font-variant-numeric:tabular-nums}
.tile>span{color:var(--muted);font-size:0.86rem}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;background:var(--panel);border:1px solid var(--line)}
th,td{text-align:left;vertical-align:top;padding:7px 10px;border-bottom:1px solid var(--line)}
th{font-size:0.82rem;color:var(--muted);font-weight:600}
td.nw{white-space:nowrap}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.track{display:flex;height:12px;min-width:140px;background:var(--track);border-radius:3px;overflow:hidden}
.track i{display:block;background:var(--bar)}.track i.inh{background:var(--bar2)}
.sev{font-weight:700;font-size:0.82rem;letter-spacing:.02em}
.HIGH{color:var(--high)}.MEDIUM{color:var(--medium)}.LOW{color:var(--low)}.INFO{color:var(--info)}.ok{color:var(--ok)}
.key{display:inline-block;width:10px;height:10px;border-radius:2px;background:var(--bar);margin:0 4px 0 12px}
.key.inh{background:var(--bar2)}
.banner{border:2px solid var(--high);border-radius:6px;padding:10px 14px;margin:16px 0;background:var(--panel)}
.banner b{color:var(--high)}
.res{font-weight:700;white-space:nowrap}.res.compliant{color:var(--ok)}.res.non-compliant{color:var(--high)}
.res.not-evaluated,.res.pending{color:var(--medium)}
ul.ev{margin:4px 0 0;padding-left:18px;color:var(--muted);font-size:0.9em}
@media print{body{background:#fff;color:#000;font-size:11px}main{max-width:none;padding:0}
.tile,table{border-color:#999}tr{break-inside:avoid}h2{break-after:avoid}
.track,.track i,.key{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
""".strip()

_PLATFORM_NAMES = {"aws": "AWS", "azure": "Azure", "gcp": "Google Cloud"}
_RESULT_ORDER = {names.NON_COMPLIANT: 0, names.PENDING: 1, names.NOT_EVALUATED: 2, names.COMPLIANT: 3}


def _n(key: str, value: Any) -> str:
    """A number carrying its name, so a reader and a test find the same figure."""
    return f'<span data-n="{escape(key)}">{escape(str(value))}</span>'


def _type(resource_type: str) -> str:
    """A resource type that may wrap after a slash, never inside a word."""
    return f'<code class="t">{escape(resource_type).replace("/", "/<wbr>")}</code>'


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:g}%"


def _tile(key: str, value: Any, label: str, css: str = "") -> str:
    return f'<div class="tile"><b class="{css}">{_n(key, value)}</b><span>{escape(label)}</span></div>'


def _more(shown: int, total: int, what: str) -> str:
    return f"<p class=muted>{total - shown} more {what} are in the JSON report.</p>" if total > shown else ""


def _banner(report: dict[str, Any]) -> str:
    if not report["incomplete"]:
        return ""
    items = "".join(
        f"<li><code>{escape(str(entry[names.SCOPE] or 'the whole scan'))}</code>: {escape(entry['reason'])}</li>"
        for entry in report["incomplete"]
    )
    return (
        '<div class="banner"><b>Incomplete scan.</b> No compliance figure is given for what is affected, '
        f"and its units are not evaluated.<ul>{items}</ul></div>"
    )


def _coverage_rows(report: dict[str, Any]) -> str:
    rows = []
    for fact, entry in report["summary"]["fact_coverage"].items():
        whole = entry["direct"] + entry["inherited"] + entry["missing"]
        direct = 100 * entry["direct"] / whole if whole else 0
        inherited = 100 * entry["inherited"] / whole if whole else 0
        rows.append(
            "<tr>"
            f"<td><code>{escape(entry['key'])}</code><br><span class=muted>{escape(fact)}"
            f"{'' if entry['required'] else ', optional'}</span></td>"
            f'<td><div class="track" role="img" aria-label="{escape(_pct(entry["percentage"]))}">'
            f'<i style="width:{direct:.2f}%"></i><i class="inh" style="width:{inherited:.2f}%"></i></div></td>'
            f"<td class=n>{_n(f'coverage.{fact}.percentage', _pct(entry['percentage']))}</td>"
            f"<td class=n>{_n(f'coverage.{fact}.direct', entry['direct'])}</td>"
            f"<td class=n>{_n(f'coverage.{fact}.inherited', entry['inherited'])}</td>"
            f"<td class=n>{_n(f'coverage.{fact}.missing', entry['missing'])}</td>"
            "</tr>"
        )
    return "".join(rows)


def _unit_rows(report: dict[str, Any]) -> str:
    units = sorted(report["units"], key=lambda unit: (_RESULT_ORDER[unit["result"]], unit["id"]))
    rows = []
    for unit in units[:HTML_ROWS]:
        members = unit.get("resources")
        if members:
            carried = f"{members['with_own']} of {members['can_carry']}"
            others = members["cannot_carry"] + members["unclassified"]
            inside = f"<td class=n>{members['total']}</td><td class=n>{carried}</td><td class=n>{others}</td>"
        else:
            inside = "<td class=n></td><td class=n></td><td class=n></td>"
        detail = ", ".join(unit.get("rules") or []) or (unit.get("reason") or "")
        rows.append(
            "<tr>"
            f"<td>{escape(str(unit.get('name') or ''))}<br><code class=muted>{escape(unit['id'])}</code></td>"
            f'<td><span class="res {unit["result"]}">{escape(unit["result"])}</span></td>'
            f"<td>{escape(detail)}</td>{inside}"
            "</tr>"
        )
    return "".join(rows)


def _evidence(finding: dict[str, Any]) -> str:
    items = []
    for entry in finding.get("evidence") or []:
        value = "absent" if entry["value"] is None else f"'{entry['value']}'"
        where = "" if entry["read_from"] == names.ABSENT else f", read from the {entry['read_from']}"
        items.append(f"<li><code>{escape(str(entry['key']))}</code>: {escape(value)}{escape(where)}</li>")
    return f'<ul class="ev">{"".join(items)}</ul>' if items else ""


def _findings_sections(report: dict[str, Any]) -> str:
    parts = []
    for severity in SEVERITIES:
        rows = [f for f in report["findings"] if f["severity"] == severity]
        if not rows:
            continue
        parts.append(f'<h3><span class="sev {severity}">{severity}</span> &middot; {len(rows)}</h3>')
        body = "".join(
            "<tr>"
            f"<td>{escape(str(f.get('resource_name') or ''))}<br><code class=muted>{escape(f['resource_id'])}</code></td>"
            f"<td><code>{escape(f['rule'])}</code></td>"
            f"<td>{escape(f['issue'])}<br><span class=muted>{escape(f['recommendation'])}</span>{_evidence(f)}</td>"
            "</tr>"
            for f in rows[:HTML_ROWS]
        )
        parts.append(
            "<div class=scroll><table><thead><tr><th>Where</th><th>Rule</th>"
            f"<th>Issue, what to do, evidence</th></tr></thead><tbody>{body}</tbody></table></div>"
        )
        parts.append(_more(HTML_ROWS, len(rows), f"{severity} findings"))
    return "".join(parts) or '<p class="ok">No findings.</p>'


def render_html(report: dict[str, Any]) -> str:
    """Render the report as one self-contained HTML document: no script, no external asset."""
    summary = report["summary"]
    results = summary["results"]
    platform = names.platform_of(report)
    platform_name = _PLATFORM_NAMES.get(platform, platform)
    kind = report["contract"].get("carrier") or report["contract"].get("kind") or "tag"
    unit = report["contract"]["unit"]
    sub_accounts = names.scopes_of(report)
    scope = ", ".join(str(s.get("name") or names.scope_of(s)) for s in sub_accounts) or "none listed"
    severities = summary["findings_by_severity"]

    tiles = "".join(
        [
            _tile(
                "compliance_percentage",
                _pct(summary["compliance_percentage"]),
                f"of evaluated {unit}s compliant",
            ),
            _tile("units", summary["units"], f"{unit}s"),
            _tile("results.compliant", results[names.COMPLIANT], "compliant", "ok"),
            _tile("results.non-compliant", results[names.NON_COMPLIANT], "non-compliant", "HIGH"),
            _tile("results.not-evaluated", results[names.NOT_EVALUATED], "not evaluated", "MEDIUM"),
            _tile("results.pending", results[names.PENDING], "pending", "MEDIUM"),
            _tile("total_resources", summary["total_resources"], "resources listed"),
        ]
    )
    severity_tiles = "".join(
        f'<div class="tile"><b class="{s}">{_n(f"findings.{s}", severities[s])}</b><span>{s}</span></div>'
        for s in SEVERITIES
    )
    type_rows = "".join(
        f"<tr><td>{_type(t)}</td><td class=n>{e['total']}</td><td>{escape(e['class'])}</td></tr>"
        for t, e in sorted(summary["resources_by_type"].items(), key=lambda item: (-item[1]["total"], item[0]))
    )
    left_out = report["not_evaluated"]
    not_evaluated_rows = (
        "".join(
            f"<tr><td>{escape(unit.capitalize())}s</td><td class=n>{e['count']}</td><td>{escape(e['reason'])}</td></tr>"
            for e in left_out["units_by_reason"]
        )
        + "".join(
            f"<tr><td>{_type(e['resource_type'])}</td><td class=n>{e['count']}</td>"
            f"<td>cannot carry {escape(kind)}s: {escape(e['reason'])}</td></tr>"
            for e in left_out["cannot_carry_types"]
        )
        + "".join(
            f"<tr><td>{_type(e['resource_type'])}</td><td class=n>{e['count']}</td>"
            f"<td>unclassified type: nothing states whether it can carry {escape(kind)}s</td></tr>"
            for e in left_out["unclassified_types"]
        )
        + "".join(
            f"<tr><td>Resources in <code>{escape(sub or 'no scope')}</code></td><td class=n>{count}</td>"
            f"<td>belong to no {escape(report['contract']['unit'])}</td></tr>"
            for sub, count in left_out["resources_without_unit"].items()
        )
        + "".join(
            f"<tr><td>Fact <code>{escape(e['fact'])}</code></td><td class=n></td><td>{escape(e['reason'])}</td></tr>"
            for e in left_out["facts"]
        )
        + "".join(
            f"<tr><td>Category <code>{escape(name)}</code></td><td class=n></td><td>{escape(entry['reason'])}</td></tr>"
            for name, entry in report["categories"].items()
            if entry["status"] == "not_available"
        )
    )
    mapping = "with a deployment mapping" if report["contract"]["mapping_applied"] else "no deployment mapping"
    required = ", ".join(
        f"<code>{escape(report['contract']['keys'][f])}</code>" for f in report["contract"]["required"]
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{escape(platform_name)} resource contract report</title>
<style>{_CSS}</style>
</head>
<body>
<main>
<h1>{escape(platform_name)} resource contract report</h1>
<p class="muted">Scope: {escape(scope)} &middot; generated {escape(report["generated_at"])} &middot;
contract {escape(report["contract"]["version"])}, {mapping}</p>
<p>The {escape(unit)} carries the contract. Required {escape(kind)}s: {required}</p>
{_banner(report)}
<div class="tiles">{tiles}</div>
<div class="tiles">{severity_tiles}</div>
<p class="muted">Compliance is compliant over compliant plus non-compliant. A {escape(unit)} that is not evaluated
or pending is in neither count.</p>

<h2>{escape(unit.capitalize())}s</h2>
<div class="scroll"><table>
<thead><tr><th>{escape(unit.capitalize())}</th><th>Result</th><th>Rules reported, or why not evaluated</th>
<th class=n>Resources</th><th class=n>With own {escape(kind)}s</th><th class=n>Cannot carry or unclassified</th></tr></thead>
<tbody>{_unit_rows(report)}</tbody></table></div>
{_more(HTML_ROWS, len(report["units"]), unit + "s")}

<h2>Coverage by {escape(kind)}</h2>
<p class="muted">Share of evaluated {escape(unit)}s that carry each {escape(kind)}.
<span class="key"></span>on the {escape(unit)} <span class="key inh"></span>inherited from its container</p>
<div class="scroll"><table>
<thead><tr><th>{escape(kind.capitalize())}</th><th>Coverage</th><th class=n>Share</th><th class=n>Direct</th>
<th class=n>Inherited</th><th class=n>Missing</th></tr></thead>
<tbody>{_coverage_rows(report)}</tbody></table></div>

<h2>Findings</h2>
{_findings_sections(report)}

<h2>Resources by type</h2>
<div class="scroll"><table>
<thead><tr><th>Type</th><th class=n>Listed</th><th>Can it carry {escape(kind)}s</th></tr></thead>
<tbody>{type_rows}</tbody></table></div>

<h2>Not evaluated</h2>
<div class="scroll"><table>
<thead><tr><th>What</th><th class=n>Count</th><th>Why</th></tr></thead>
<tbody>{not_evaluated_rows}</tbody></table></div>
</main>
</body>
</html>
"""
