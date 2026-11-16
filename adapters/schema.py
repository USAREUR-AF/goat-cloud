"""Report schema 2.0: the inventory record and document every adapter returns.

Standard library only. An adapter builds its records and document as plain
dicts; ``validate_record`` and ``validate_document`` return the list of
problems found (an empty list means valid) and never raise.

Identity
--------
A record is identified by the triple (platform, scope, resource_id).
``record_key`` returns it. Stores and deltas key on it.

Record fields
-------------
Required on every record:

    resource_id             str, non-empty: the platform's own identifier
    platform                str: aws, azure, gcp, kubernetes, baremetal,
                            cloudflare (KNOWN_PLATFORMS)
    scope                   str: the AWS account id, Azure subscription id or
                            Google Cloud project id (FOCUS: Sub Account)
    location                str or None
    resource_type           str
    name                    str, non-empty; when the platform gives no name,
                            the last segment of resource_id
    tags                    dict of str to str: tags or labels as found
    state                   str or None
    created_at              str or None
    estimated_monthly_cost  number or None (None: no cost source configured),
                            in the document's currency

Optional, present only on platforms that have the concept:

    inherited_tags          dict of str to str: tags of the enclosing container
    kind                    str or None
    container               str or None: the grouping inside the scope (Azure:
                            the resource group)
    provisioning_state      str or None
    grade                   str: how the adapter says the record is graded
                            (Google Cloud: labels+ownership, ownership, none)
    cost_basis              str: what estimated_monthly_cost is (KNOWN_COST_BASES:
                            billed, allocated, amortized, list-price, estimated)

Document fields
---------------
    schema_version          SCHEMA_VERSION
    platform                str, as on a record
    generated_at            str: UTC timestamp
    scopes                  list of {scope, scope_type, name, state, tags};
                            scope_type is account, subscription or project
                            (KNOWN_SCOPE_TYPES)
    resources               list of records, each with the document's platform

Optional:

    currency                str: the currency of every cost in the document;
                            DEFAULT_CURRENCY when absent
    unclassified_types      dict of resource type to count: types the adapter
                            has no class for
    incomplete              list of {scope, reason}: what the inventory is
                            known to lack. scope is the affected scope, or None
                            when the whole inventory is affected. An empty
                            list, or no field, means nothing is known to lack.

Compatibility
-------------
Within major version 2 a later minor version adds optional fields and new
values of platform, scope_type and cost_basis. A reader of this version
accepts them: a field or value it does not know is a note, not a problem.
Pass a list as ``notes`` to receive the notes.

Finding categories
------------------
FINDING_CATEGORIES names the categories of a schema 2.0 report: contract,
unclaimed, missing, lifecycle, schedule. CATEGORIES_FROM_1_0 maps the four
category names of the schema 1.0 report to theirs.
"""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = "2.0"
KNOWN_PLATFORMS = ("aws", "azure", "gcp", "kubernetes", "baremetal", "cloudflare")
KNOWN_SCOPE_TYPES = ("account", "subscription", "project")
KNOWN_COST_BASES = ("billed", "allocated", "amortized", "list-price", "estimated")
DEFAULT_CURRENCY = "USD"

# The scope type of each platform that has one fixed type.
SCOPE_TYPE_OF_PLATFORM = {"aws": "account", "azure": "subscription", "gcp": "project"}

# Finding categories of schema 2.0.
CATEGORY_CONTRACT = "contract"  # a fact is missing, or its value is outside the contract
CATEGORY_UNCLAIMED = "unclaimed"  # the resource exists and nothing declared claims it
CATEGORY_MISSING = "missing"  # the resource is declared and was not observed
CATEGORY_LIFECYCLE = "lifecycle"  # expired, expiring, or lacking a required expiry
CATEGORY_SCHEDULE = "schedule"  # runs without a schedule
FINDING_CATEGORIES = (
    CATEGORY_CONTRACT,
    CATEGORY_UNCLAIMED,
    CATEGORY_MISSING,
    CATEGORY_LIFECYCLE,
    CATEGORY_SCHEDULE,
)

# The category names of the schema 1.0 report and their 2.0 names. The 1.0
# report has no category for a missing resource.
CATEGORIES_FROM_1_0 = {
    "tag_compliance": CATEGORY_CONTRACT,
    "drift_detection": CATEGORY_UNCLAIMED,
    "lifecycle_compliance": CATEGORY_LIFECYCLE,
    "schedule_compliance": CATEGORY_SCHEDULE,
}

_TEXT = (str,)
_OPTIONAL_TEXT = (str, type(None))
_TAGS = (dict,)
_COST = (int, float, type(None))

REQUIRED_RECORD_FIELDS: dict[str, tuple[type, ...]] = {
    "resource_id": _TEXT,
    "platform": _TEXT,
    "scope": _TEXT,
    "location": _OPTIONAL_TEXT,
    "resource_type": _TEXT,
    "name": _TEXT,
    "tags": _TAGS,
    "state": _OPTIONAL_TEXT,
    "created_at": _OPTIONAL_TEXT,
    "estimated_monthly_cost": _COST,
}

OPTIONAL_RECORD_FIELDS: dict[str, tuple[type, ...]] = {
    "inherited_tags": _TAGS,
    "kind": _OPTIONAL_TEXT,
    "container": _OPTIONAL_TEXT,
    "provisioning_state": _OPTIONAL_TEXT,
    "grade": _TEXT,
    "cost_basis": _TEXT,
}

REQUIRED_DOCUMENT_FIELDS: dict[str, tuple[type, ...]] = {
    "schema_version": _TEXT,
    "platform": _TEXT,
    "generated_at": _TEXT,
    "scopes": (list,),
    "resources": (list,),
}

OPTIONAL_DOCUMENT_FIELDS: dict[str, tuple[type, ...]] = {
    "currency": _TEXT,
    "unclassified_types": (dict,),
    "incomplete": (list,),
}

SCOPE_FIELDS: dict[str, tuple[type, ...]] = {
    "scope": _TEXT,
    "scope_type": _TEXT,
    "name": _OPTIONAL_TEXT,
    "state": _OPTIONAL_TEXT,
    "tags": _TAGS,
}

INCOMPLETE_FIELDS: dict[str, tuple[type, ...]] = {
    "scope": _OPTIONAL_TEXT,
    "reason": _TEXT,
}

# Fields whose values are open lists: a value outside the known ones is a note.
_KNOWN_VALUES = {
    "platform": KNOWN_PLATFORMS,
    "scope_type": KNOWN_SCOPE_TYPES,
    "cost_basis": KNOWN_COST_BASES,
}

_NON_EMPTY = ("resource_id", "name")


def name_from_id(resource_id: str) -> str:
    """The last segment of a resource identifier: the name of a resource that has none."""
    return resource_id.rstrip("/").rsplit("/", 1)[-1]


def record_key(record: dict[str, Any]) -> tuple[str, str, str]:
    """The identity of a record: (platform, scope, resource_id).

    Stores and deltas key on it: two records with the same key are the same
    resource seen at different times.
    """
    return (record["platform"], record["scope"], record["resource_id"])


def currency_of(document: dict[str, Any]) -> str:
    """The currency of every cost in a document."""
    return document.get("currency") or DEFAULT_CURRENCY


def _type_names(types: tuple[type, ...]) -> str:
    return " or ".join("None" if t is type(None) else t.__name__ for t in types)


def _note(notes: list[str] | None, text: str) -> None:
    if notes is not None:
        notes.append(text)


def _check_fields(
    value: Any,
    required: dict[str, tuple[type, ...]],
    optional: dict[str, tuple[type, ...]],
    where: str,
    notes: list[str] | None = None,
) -> list[str]:
    if not isinstance(value, dict):
        return [f"{where}: expected an object, got {type(value).__name__}"]
    issues = [f"{where}: missing field {field}" for field in required if field not in value]
    for field, item in value.items():
        types = required.get(field) or optional.get(field)
        if types is None:
            _note(notes, f"{where}: unknown field {field}")
        elif isinstance(item, bool) or not isinstance(item, types):
            issues.append(f"{where}.{field}: expected {_type_names(types)}, got {type(item).__name__}")
        elif types is _TAGS and not all(isinstance(k, str) and isinstance(v, str) for k, v in item.items()):
            issues.append(f"{where}.{field}: every tag name and value must be a string")
        elif field in _KNOWN_VALUES and item not in _KNOWN_VALUES[field]:
            _note(notes, f"{where}.{field}: unknown value {item!r} (known: {', '.join(_KNOWN_VALUES[field])})")
    return issues


def validate_record(record: Any, where: str = "record", notes: list[str] | None = None) -> list[str]:
    """Return the problems with one inventory record; an empty list means valid.

    A field or value this version does not know is appended to ``notes`` when
    a list is given, and is never a problem.
    """
    issues = _check_fields(record, REQUIRED_RECORD_FIELDS, OPTIONAL_RECORD_FIELDS, where, notes)
    if not isinstance(record, dict):
        return issues
    for field in _NON_EMPTY:
        if record.get(field) == "":
            issues.append(f"{where}.{field}: must not be empty")
    return issues


def validate_document(document: Any, notes: list[str] | None = None) -> list[str]:
    """Return the problems with an inventory document; an empty list means valid.

    A field or value this version does not know is appended to ``notes`` when
    a list is given, and is never a problem.
    """
    issues = _check_fields(document, REQUIRED_DOCUMENT_FIELDS, OPTIONAL_DOCUMENT_FIELDS, "document", notes)
    if not isinstance(document, dict):
        return issues
    version = document.get("schema_version")
    if isinstance(version, str) and version.split(".")[0] != SCHEMA_VERSION.split(".")[0]:
        issues.append(
            f"document.schema_version: expected major version {SCHEMA_VERSION.split('.')[0]}, got {version!r}"
        )
    elif isinstance(version, str) and version != SCHEMA_VERSION:
        _note(notes, f"document.schema_version: {version!r} is not {SCHEMA_VERSION!r}; unknown fields are ignored")
    platform = document.get("platform")
    if isinstance(document.get("scopes"), list):
        for i, scope in enumerate(document["scopes"]):
            issues.extend(_check_fields(scope, SCOPE_FIELDS, {}, f"scopes[{i}]", notes))
    if isinstance(document.get("incomplete"), list):
        for i, entry in enumerate(document["incomplete"]):
            issues.extend(_check_fields(entry, INCOMPLETE_FIELDS, {}, f"incomplete[{i}]", notes))
    if isinstance(document.get("resources"), list):
        for i, record in enumerate(document["resources"]):
            where = f"resources[{i}]"
            issues.extend(validate_record(record, where, notes))
            if isinstance(record, dict) and record.get("platform") != platform:
                issues.append(f"{where}.platform: differs from the document's platform")
    return issues
