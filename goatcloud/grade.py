"""Grade an inventory against the contract.

The unit that carries the contract is graded first: the resource group on
Azure, the project on Google Cloud. A unit gets one of four results:

    compliant       evaluated, no failing finding
    non-compliant   evaluated, at least one HIGH or MEDIUM finding
    not-evaluated   could not be evaluated, with the reason; never counted
                    as compliant or as non-compliant
    pending         a required fact is missing and the unit was created
                    within the lag; a later scan decides

Resources are listed under their unit with whether they carry their own tags
or labels, as information. ``grade_resources`` grades each resource instead.

The rules and severities are those of the AWS scanner
(``modules/aws/compliance-scanner/lambda/scanners/tag_compliance.py`` and
``lifecycle_compliance.py``), written against contract facts. The two copies
are to be consolidated when the scanner reads schema 2.0 records.
Differences from the AWS scanner: a value is checked whenever the fact is
present; a temporary resource without an expiry is a finding at any age; an
expiry that cannot be read is a finding.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from goatcloud import names
from goatcloud.contract import Fact, PlatformContract
from goatcloud.profiles import CAN_CARRY, CANNOT_CARRY, UNCLASSIFIED, Profile, capability, profile_for

SEVERITIES = ("HIGH", "MEDIUM", "LOW", "INFO")
# A unit with a finding of one of these severities is non-compliant.
FAILING_SEVERITIES = frozenset({"HIGH", "MEDIUM"})
# Missing either of these facts is HIGH, as is missing every required fact.
CRITICAL_FACTS = frozenset({"managed_by", "project"})
EXPIRING_SOON_DAYS = 14
DEFAULT_LAG_MINUTES = 15

# Rule ids.
MISSING_REQUIRED = "missing-required"
INVALID_VALUE = "invalid-value"
EXPIRED = "expired"
EXPIRING_SOON = "expiring-soon"
EXPIRY_UNREADABLE = "expiry-unreadable"
INHERITED_ONLY = "inherited-only"
PROTECTED = "protected"
# Rules whose finding says a required fact is absent; only these can be pending.
_MISSING_RULES = {MISSING_REQUIRED}

DIRECT = "direct"
INHERITED = "inherited"
MISSING = "missing"


def _lookup(pairs: dict[str, Any], key: str, case_insensitive: bool) -> str | None:
    """The value under ``key``, or None when absent or empty."""
    if key in pairs:
        value = pairs[key]
    elif case_insensitive:
        folded = key.lower()
        value = next((v for k, v in pairs.items() if k.lower() == folded), None)
    else:
        value = None
    return str(value) if value not in (None, "") else None


class _Subject:
    """What is being graded: where each fact was found, and with what value."""

    def __init__(
        self,
        record: dict[str, Any],
        contract: PlatformContract,
        profile: Profile,
        *,
        own_source: str,
        inherited: dict[str, Any] | None = None,
    ):
        self.record = record
        self.profile = profile
        self.own_source = own_source
        self.own = record.get("tags") or {}
        self.inherited = inherited or {}
        self.source: dict[str, str] = {}
        self.value: dict[str, str] = {}
        for name in contract.keyed:
            key = contract.key(name)
            value = _lookup(self.own, key, profile.keys_case_insensitive)
            if value is not None:
                self.source[name], self.value[name] = DIRECT, value
                continue
            value = _lookup(self.inherited, key, profile.keys_case_insensitive)
            if value is not None:
                self.source[name], self.value[name] = INHERITED, value
            else:
                self.source[name] = MISSING

    def evidence(self, contract: PlatformContract, facts: Iterable[str]) -> list[dict[str, Any]]:
        """For each fact: the key it was looked for under, the value seen and where it was read."""
        read_from = {DIRECT: self.own_source, INHERITED: names.FROM_UNIT, MISSING: names.ABSENT}
        return [
            {
                "fact": name,
                "key": contract.key(name),
                "value": self.value.get(name),
                "read_from": read_from[self.source[name]],
            }
            for name in facts
        ]


def _finding(subject: _Subject, category: str, severity: str, rule: str, issue: str, recommendation: str, **extra: Any):
    record = subject.record
    return {
        "category": category,
        "severity": severity,
        "rule": rule,
        "resource_id": record.get("resource_id", ""),
        "resource_type": record.get("resource_type", ""),
        "resource_name": record.get("name"),
        names.SCOPE: names.scope_of(record),
        "location": record.get("location"),
        "issue": issue,
        "recommendation": recommendation,
        **extra,
    }


def _missing(subject: _Subject, contract: PlatformContract) -> list[dict[str, Any]]:
    required = contract.required
    missing = [name for name in required if subject.source[name] == MISSING]
    if not missing:
        return []
    high = len(missing) == len(required) or CRITICAL_FACTS.intersection(missing)
    keys = [contract.key(name) for name in missing]
    kind = subject.profile.kind
    return [
        _finding(
            subject,
            names.CONTRACT,
            "HIGH" if high else "MEDIUM",
            MISSING_REQUIRED,
            f"Missing required {kind}s: {', '.join(keys)}",
            f"Add the missing {kind}s: {', '.join(keys)}",
            missing_facts=missing,
            missing_keys=keys,
            evidence=subject.evidence(contract, missing),
        )
    ]


def _values(subject: _Subject, contract: PlatformContract) -> list[dict[str, Any]]:
    findings = []
    kind = subject.profile.kind
    for name, value in subject.value.items():
        fact: Fact = contract.facts[name]
        if fact.accepts(value):
            continue
        if fact.allowed and not fact.accepts(value):
            accepted = ", ".join(sorted(fact.allowed))
            expected, recommendation = f"expected one of: {accepted}", f"Set {fact.key} to one of: {accepted}."
        else:
            expected = f"expected to match {fact.pattern}"
            recommendation = f"Set {fact.key} to a value that matches {fact.pattern}."
        findings.append(
            _finding(
                subject,
                names.CONTRACT,
                "MEDIUM",
                INVALID_VALUE,
                f"{fact.key} {kind} is '{value}' ({expected})",
                recommendation,
                evidence=subject.evidence(contract, [name]),
            )
        )
    return findings


def _inherited(subject: _Subject, contract: PlatformContract) -> list[dict[str, Any]]:
    facts = [name for name in contract.required if subject.source[name] == INHERITED]
    if not facts:
        return []
    keys = ", ".join(contract.key(name) for name in facts)
    unit, kind = subject.profile.unit_kind, subject.profile.kind
    return [
        _finding(
            subject,
            names.CONTRACT,
            "LOW",
            INHERITED_ONLY,
            f"Inherited, not on the resource: {keys} (from the {unit})",
            f"Add the {kind}s to the resource, or keep them on the {unit} and copy them by policy.",
            evidence=subject.evidence(contract, facts),
        )
    ]


def _protected(subject: _Subject, contract: PlatformContract) -> list[dict[str, Any]]:
    profile = subject.profile
    declared = contract.facts.get("protected")
    key = declared.key if declared and declared.key else profile.protected_key
    for pairs, source in ((subject.own, subject.own_source), (subject.inherited, names.FROM_UNIT)):
        value = _lookup(pairs, key, profile.keys_case_insensitive)
        if value is not None and value.lower() == profile.protected_value:
            return [
                _finding(
                    subject,
                    names.LIFECYCLE,
                    "INFO",
                    PROTECTED,
                    f"Marked protected ({key}: {value})",
                    "No action. A protected unit is reported and never acted on.",
                    evidence=[{"fact": None, "key": key, "value": value, "read_from": source}],
                )
            ]
    return []


def _lifecycle(subject: _Subject, contract: PlatformContract, now: datetime) -> list[dict[str, Any]]:
    findings = []
    kind = subject.profile.kind
    text = subject.value.get("expires_at")
    key = contract.key("expires_at") if "expires_at" in contract.facts else None
    evidence = subject.evidence(contract, ["expires_at"]) if key else []

    if text is not None:
        try:
            expiry = contract.decode_expiry(text)
        except (ValueError, TypeError):
            encoding = contract.facts["expires_at"].encoding
            findings.append(
                _finding(
                    subject,
                    names.LIFECYCLE,
                    "MEDIUM",
                    EXPIRY_UNREADABLE,
                    f"{key} {kind} is '{text}', which is not a timestamp in {encoding} form",
                    f"Write {key} as: {contract.encodings.get(encoding or '', encoding)}",
                    evidence=evidence,
                )
            )
        else:
            if expiry < now:
                days = (now - expiry).days
                findings.append(
                    _finding(
                        subject,
                        names.LIFECYCLE,
                        "HIGH",
                        EXPIRED,
                        f"Expired {days} days ago ({key}: {text})",
                        f"Delete it or update {key} to extend its lifecycle.",
                        expires_at=expiry.isoformat(),
                        evidence=evidence,
                    )
                )
            elif (expiry - now).days <= EXPIRING_SOON_DAYS:
                days = (expiry - now).days
                findings.append(
                    _finding(
                        subject,
                        names.LIFECYCLE,
                        "INFO",
                        EXPIRING_SOON,
                        f"Expires in {days} day{'s' if days != 1 else ''} ({key}: {text})",
                        f"Extend {key} if still needed, or plan for teardown.",
                        expires_at=expiry.isoformat(),
                        evidence=evidence,
                    )
                )

    for rule in contract.rules:
        if subject.value.get(rule.when_fact) != rule.equals:
            continue
        absent = [n for n in rule.require if contract.facts[n].key is not None and subject.source[n] == MISSING]
        if not absent:
            continue
        keys = ", ".join(contract.key(n) for n in absent)
        when_key = contract.key(rule.when_fact)
        findings.append(
            _finding(
                subject,
                names.LIFECYCLE,
                "MEDIUM",
                rule.id,
                f"{when_key} is '{rule.equals}' without {keys}",
                f"Add {keys}, or change {when_key} if it is needed long-term.",
                missing_facts=absent,
                evidence=subject.evidence(contract, [rule.when_fact, *absent]),
            )
        )
    return findings


def _created_within(record: dict[str, Any], now: datetime, lag: timedelta) -> bool:
    """True only when the creation time is known and inside the lag."""
    text = record.get("created_at")
    if not text:
        return False
    try:
        created = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return False
    created = created if created.tzinfo else created.replace(tzinfo=UTC)
    return timedelta(0) <= now - created <= lag


def _evaluate(
    subject: _Subject, contract: PlatformContract, now: datetime, lag: timedelta, report_inherited: bool
) -> tuple[str, list[dict[str, Any]]]:
    findings = _missing(subject, contract) + _values(subject, contract) + _lifecycle(subject, contract, now)
    findings += _protected(subject, contract)
    if report_inherited and subject.inherited:
        findings += _inherited(subject, contract)
    failing = [f for f in findings if f["severity"] in FAILING_SEVERITIES]
    if not failing:
        return names.COMPLIANT, findings
    if any(f["rule"] in _MISSING_RULES for f in failing) and _created_within(subject.record, now, lag):
        return names.PENDING, findings
    return names.NON_COMPLIANT, findings


# ---------------------------------------------------------------------------
# Completeness of the input
# ---------------------------------------------------------------------------


def incomplete_input(
    inventory: dict[str, Any], profile: Profile, expected_scopes: Iterable[str] = ()
) -> list[dict[str, Any]]:
    """What the inventory is known to lack: ``[{scope, reason}]``.

    ``scope`` None means the whole inventory. A unit in an affected
    scope is not evaluated and no compliance figure is given for it.
    """
    issues: list[dict[str, Any]] = []

    def add(sub_account: str | None, reason: str) -> None:
        entry = {names.SCOPE: sub_account, "reason": reason}
        if entry not in issues:
            issues.append(entry)

    for entry in inventory.get("incomplete") or []:
        add(names.scope_of(entry) or None, str(entry.get("reason") or "the adapter reported a partial result"))

    listed = {names.scope_of(entry) for entry in names.scopes_of(inventory)}
    listed.discard("")
    if not listed:
        add(None, "the scan returned no scope: no access to it, or it is empty")
    folded = {s.lower() for s in listed}
    for expected in expected_scopes:
        if expected.lower() not in folded:
            add(expected, "asked for and not returned: no access to it, or it does not exist")
    unresolved = re.compile(profile.unresolved_scope) if profile.unresolved_scope else None
    for record in inventory.get("resources") or []:
        sub_account = names.scope_of(record)
        if unresolved and unresolved.match(sub_account):
            add(sub_account, profile.unresolved_reason)
        elif sub_account not in listed:
            add(sub_account or None, "resources were returned for a scope the scan did not list")
    return issues


def _blocked(sub_account: str, incomplete: list[dict[str, Any]]) -> str | None:
    for entry in incomplete:
        if entry[names.SCOPE] in (None, sub_account):
            return f"incomplete scan: {entry['reason']}"
    return None


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------


def _unit_id(record: dict[str, Any], profile: Profile) -> str | None:
    container = names.container_of(record)
    if profile.unit_in_container and not container:
        return None
    return profile.unit_id_template.format(scope=names.scope_of(record), container=container).lower()


def _unit_entry(record: dict[str, Any], unit_id: str, kind: str) -> dict[str, Any]:
    return {
        "id": unit_id,
        "kind": kind,
        "name": record.get("name"),
        "resource_type": record.get("resource_type", ""),
        names.SCOPE: names.scope_of(record),
        "location": record.get("location"),
        "created_at": record.get("created_at"),
    }


def _member_summary(
    members: list[dict[str, Any]], contract: PlatformContract, profile: Profile, unclassified: frozenset[str]
) -> dict[str, Any]:
    """What the resources of a unit carry themselves. Information; never a finding."""
    classes: Counter[str] = Counter()
    with_own, own_facts = 0, Counter()
    for record in members:
        cls, _ = capability(record, profile, unclassified)
        classes[cls] += 1
        if cls != CAN_CARRY:
            continue
        subject = _Subject(record, contract, profile, own_source=names.FROM_RESOURCE)
        present = [name for name in contract.keyed if subject.source[name] == DIRECT]
        with_own += bool(present)
        own_facts.update(present)
    return {
        "total": len(members),
        "can_carry": classes[CAN_CARRY],
        "cannot_carry": classes[CANNOT_CARRY],
        "unclassified": classes[UNCLASSIFIED],
        "with_own": with_own,
        "without_own": classes[CAN_CARRY] - with_own,
        "own_facts": {name: own_facts[name] for name in contract.keyed if own_facts[name]},
    }


def grade_units(
    inventory: dict[str, Any],
    contract: PlatformContract,
    *,
    profile: Profile | None = None,
    now: datetime | None = None,
    lag_minutes: float = DEFAULT_LAG_MINUTES,
    expected_scopes: Iterable[str] = (),
) -> dict[str, Any]:
    """Grade the unit that carries the contract; list its resources as information."""
    profile = profile or profile_for(contract.platform)
    now = now or datetime.now(UTC)
    lag = timedelta(minutes=lag_minutes)
    records = inventory.get("resources") or []
    unclassified = frozenset(inventory.get("unclassified_types") or {})
    incomplete = incomplete_input(inventory, profile, expected_scopes)

    unit_records: dict[str, dict[str, Any]] = {}
    members: dict[str, list[dict[str, Any]]] = {}
    no_unit: Counter[str] = Counter()
    for record in records:
        unit_id = _unit_id(record, profile)
        if record.get("resource_type", "").lower() == profile.unit_record_type.lower() and unit_id:
            unit_records[unit_id] = record
        elif unit_id:
            members.setdefault(unit_id, []).append(record)
        else:
            no_unit[names.scope_of(record)] += 1

    # Where the unit is the scope itself, its entry in the document
    # carries the same tags or labels as its record; use it when the record
    # was not returned (for example under a type filter).
    if not profile.unit_in_container:
        for entry in names.scopes_of(inventory):
            unit_id = names.scope_of(entry).lower()
            if unit_id and unit_id not in unit_records:
                unit_records[unit_id] = {
                    "resource_id": unit_id,
                    "resource_type": profile.unit_record_type,
                    "name": entry.get("name") or unit_id,
                    names.SCOPE: names.scope_of(entry),
                    "tags": entry.get("tags") or {},
                    "created_at": None,
                }

    # A unit that resources point at and the inventory does not hold cannot
    # be graded, and nothing may be concluded about its scope.
    for unit_id in sorted(set(members) - set(unit_records)):
        sub_account = names.scope_of(members[unit_id][0])
        entry = {
            names.SCOPE: sub_account,
            "reason": f"the {profile.unit_kind} of {len(members[unit_id])} resources is not in the inventory",
        }
        if entry not in incomplete:
            incomplete.append(entry)

    units, findings = [], []
    for unit_id in sorted(set(unit_records) | set(members)):
        record = unit_records.get(unit_id)
        if record is None:
            first = members[unit_id][0]
            unit = {
                "id": unit_id,
                "kind": profile.unit_kind,
                "name": None,
                "resource_type": profile.unit_record_type,
                names.SCOPE: names.scope_of(first),
                "location": None,
                "created_at": None,
                "result": names.NOT_EVALUATED,
                "reason": f"the {profile.unit_kind} is not in the inventory",
                "rules": [],
            }
        else:
            unit = _unit_entry(record, unit_id, profile.unit_kind)
            blocked = _blocked(unit[names.SCOPE], incomplete)
            if blocked:
                unit.update(result=names.NOT_EVALUATED, reason=blocked, rules=[])
            else:
                subject = _Subject(record, contract, profile, own_source=names.FROM_UNIT)
                result, own = _evaluate(subject, contract, now, lag, report_inherited=False)
                for finding in own:
                    finding["unit_id"] = unit_id
                findings.extend(own)
                unit.update(
                    result=result,
                    reason=f"created within {lag_minutes:g} minutes" if result == names.PENDING else None,
                    rules=sorted({f["rule"] for f in own}),
                    facts=dict(subject.source),
                )
        unit["resources"] = _member_summary(members.get(unit_id, []), contract, profile, unclassified)
        units.append(unit)

    return {
        "graded": "units",
        "units": units,
        "findings": _ordered(findings),
        "incomplete": incomplete,
        "resources_without_unit": dict(sorted(no_unit.items())),
    }


def grade_resources(
    inventory: dict[str, Any],
    contract: PlatformContract,
    *,
    profile: Profile | None = None,
    now: datetime | None = None,
    lag_minutes: float = DEFAULT_LAG_MINUTES,
    expected_scopes: Iterable[str] = (),
    report_inherited: bool = False,
) -> dict[str, Any]:
    """Grade every resource; each resource is its own unit in the result."""
    profile = profile or profile_for(contract.platform)
    now = now or datetime.now(UTC)
    lag = timedelta(minutes=lag_minutes)
    unclassified = frozenset(inventory.get("unclassified_types") or {})
    incomplete = incomplete_input(inventory, profile, expected_scopes)

    units, findings = [], []
    for record in inventory.get("resources") or []:
        unit = _unit_entry(record, record.get("resource_id", ""), "resource")
        blocked = _blocked(unit[names.SCOPE], incomplete)
        cls, reason = capability(record, profile, unclassified)
        if blocked or cls != CAN_CARRY:
            unit.update(result=names.NOT_EVALUATED, reason=blocked or reason, rules=[])
        else:
            inherited = record.get(profile.inherited_field) if profile.inherited_field else None
            subject = _Subject(record, contract, profile, own_source=names.FROM_RESOURCE, inherited=inherited)
            result, own = _evaluate(subject, contract, now, lag, report_inherited)
            for finding in own:
                finding["unit_id"] = unit["id"]
            findings.extend(own)
            unit.update(
                result=result,
                reason=f"created within {lag_minutes:g} minutes" if result == names.PENDING else None,
                rules=sorted({f["rule"] for f in own}),
                facts=dict(subject.source),
            )
        units.append(unit)

    return {
        "graded": "resources",
        "units": units,
        "findings": _ordered(findings),
        "incomplete": incomplete,
        "resources_without_unit": {},
    }


def _ordered(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order = {severity: index for index, severity in enumerate(SEVERITIES)}
    return sorted(findings, key=lambda f: (order[f["severity"]], f["category"], f["resource_type"], f["resource_id"]))


GRADERS = {"units": grade_units, "resources": grade_resources}
