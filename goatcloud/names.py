"""The schema 2.0 names this package reads and writes, in one place.

The names written are the ones ``adapters/schema.py`` declares. Inventory
documents are read under either name of a renamed field, so a saved inventory
stays readable.
"""

from __future__ import annotations

from typing import Any

from adapters import schema as _schema


def _declared(table: str, *candidates: str) -> str:
    """The first candidate the schema's field table declares; the first candidate when none is."""
    fields = getattr(_schema, table, {})
    return next((name for name in candidates if name in fields), candidates[0])


# Finding categories.
CONTRACT = getattr(_schema, "CATEGORY_CONTRACT", "contract")
UNCLAIMED = getattr(_schema, "CATEGORY_UNCLAIMED", "unclaimed")
MISSING = getattr(_schema, "CATEGORY_MISSING", "missing")
LIFECYCLE = getattr(_schema, "CATEGORY_LIFECYCLE", "lifecycle")
SCHEDULE = getattr(_schema, "CATEGORY_SCHEDULE", "schedule")
CATEGORIES = tuple(getattr(_schema, "FINDING_CATEGORIES", (CONTRACT, UNCLAIMED, MISSING, LIFECYCLE, SCHEDULE)))

# The cloud of a document or record.
_PLATFORM_NAMES = ("platform", "provider")
PLATFORM = _declared("REQUIRED_DOCUMENT_FIELDS", *_PLATFORM_NAMES)

# The account, subscription or project of a record, and the document's list of them.
_SCOPE_NAMES = ("scope", "sub_account_id")
_SCOPES_NAMES = ("scopes", "sub_accounts")
SCOPE = _declared("REQUIRED_RECORD_FIELDS", *_SCOPE_NAMES)
SCOPES = _declared("REQUIRED_DOCUMENT_FIELDS", *_SCOPES_NAMES)

# The container of a record inside its scope: the resource group on Azure.
_CONTAINER_NAMES = ("container", "resource_group")

# Results of a graded unit.
COMPLIANT = "compliant"
NON_COMPLIANT = "non-compliant"
NOT_EVALUATED = "not-evaluated"
PENDING = "pending"
RESULTS = (COMPLIANT, NON_COMPLIANT, NOT_EVALUATED, PENDING)

# Where a value in a finding's evidence was read.
FROM_RESOURCE = "resource"
FROM_UNIT = "unit"
FROM_REGISTRY = "registry"
ABSENT = "absent"


def platform_of(document: dict[str, Any]) -> str | None:
    """The cloud of an inventory, report or truth document."""
    return next((document[name] for name in _PLATFORM_NAMES if document.get(name)), None)


def scope_of(entry: dict[str, Any]) -> str:
    """The scope id of a record or of a scope entry."""
    return next((str(entry[name]) for name in _SCOPE_NAMES if entry.get(name)), "")


def scopes_of(document: dict[str, Any]) -> list[dict[str, Any]]:
    """The scope entries of an inventory or report document."""
    return next((document[name] for name in _SCOPES_NAMES if isinstance(document.get(name), list)), [])


def container_of(record: dict[str, Any]) -> str | None:
    """The record's container inside its scope, or None."""
    return next((str(record[name]) for name in _CONTAINER_NAMES if record.get(name)), None)
