"""What differs between platforms when grading, as data.

``grade`` reads a profile and has no branch on the platform's name. A platform is
added by adding an entry here and a rendering in the contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import yaml

from goatcloud import names

DATA = Path(__file__).resolve().parent / "data"

CAN_CARRY = "can-carry"
CANNOT_CARRY = "cannot-carry"
UNCLASSIFIED = "unclassified"


@dataclass(frozen=True)
class Profile:
    """Grading facts about one cloud."""

    platform: str
    # How the cloud names its key-value pairs.
    kind: str = "tag"
    # Key names are compared without regard to case.
    keys_case_insensitive: bool = False

    # The unit that carries the contract.
    unit_kind: str = "unit"
    # Resource type of the record that is the unit.
    unit_record_type: str = ""
    # The unit's id, built from fields of the unit record or of a member
    # record. {scope} is the record's scope id.
    unit_id_template: str = "{scope}"
    # The unit is the record's container; the template takes {container}.
    unit_in_container: bool = False

    # Per-resource grading: record field that holds the unit's pairs.
    inherited_field: str | None = None

    # Whether a type can carry tags or labels. Either the adapter states it on
    # the record (grade_field) or a data file lists the types (type_data).
    grade_field: str | None = None
    carry_values: frozenset[str] = frozenset()
    cannot_carry_reasons: dict[str, str] = field(default_factory=dict)
    type_data: str | None = None

    # A scope id of this form means the adapter could not resolve it.
    unresolved_scope: str | None = None
    unresolved_reason: str = ""

    # The pair that marks a unit as protected, and the value that sets it.
    protected_key: str = "Protected"
    protected_value: str = "true"

    # Report categories with no source on this cloud, each with the reason.
    not_available: dict[str, str] = field(default_factory=dict)


_NOT_BUILT = {
    names.UNCLAIMED: "not built: ownership from IaC state needs the registry and state adapters",
    names.MISSING: "not built: resources in IaC state and absent from the cloud need the state adapters",
    names.SCHEDULE: "not built",
}

PROFILES: dict[str, Profile] = {
    "azure": Profile(
        platform="azure",
        kind="tag",
        keys_case_insensitive=True,
        unit_kind="resource group",
        unit_record_type="microsoft.resources/subscriptions/resourcegroups",
        unit_id_template="/subscriptions/{scope}/resourcegroups/{container}",
        unit_in_container=True,
        inherited_field="inherited_tags",
        type_data="azure_tag_support.yaml",
        protected_key="Protected",
        not_available=dict(_NOT_BUILT),
    ),
    "gcp": Profile(
        platform="gcp",
        kind="label",
        unit_kind="project",
        unit_record_type="cloudresourcemanager.googleapis.com/Project",
        unit_id_template="{scope}",
        grade_field="grade",
        carry_values=frozenset({"labels+ownership"}),
        cannot_carry_reasons={
            "ownership": "the type has no labels field",
            "none": "a record, not a deployed resource",
        },
        unresolved_scope=r"^[0-9]+$",
        unresolved_reason="the project number could not be resolved to a project id",
        protected_key="protected",
        not_available=dict(_NOT_BUILT),
    ),
}


def profile_for(platform: str) -> Profile:
    """The profile of a platform. Raises ``KeyError`` naming the known platforms."""
    try:
        return PROFILES[platform]
    except KeyError:
        raise KeyError(f"no grading profile for platform {platform!r}; known: {', '.join(sorted(PROFILES))}") from None


@cache
def _type_data(name: str) -> dict[str, str]:
    document = yaml.safe_load((DATA / name).read_text(encoding="utf-8"))
    classes = {t: CAN_CARRY for t in document.get("can_carry_tags") or []}
    classes.update({t: CANNOT_CARRY for t in document.get("cannot_carry_tags") or []})
    for resource_type, value in (document.get("unverified") or {}).items():
        classes[resource_type] = CAN_CARRY if value == "can_carry_tags" else CANNOT_CARRY
    return classes


def capability(record: dict, profile: Profile, unclassified_types: frozenset[str] = frozenset()) -> tuple[str, str]:
    """Whether the record's type can carry tags or labels: ``(class, reason)``.

    The class is ``can-carry``, ``cannot-carry`` or ``unclassified``. A type
    nothing classifies is ``unclassified`` and is never graded.
    """
    resource_type = record.get("resource_type", "")
    unclassified = (UNCLASSIFIED, f"unclassified type: nothing states whether it can carry {profile.kind}s")
    if resource_type in unclassified_types:
        return unclassified
    if profile.grade_field and record.get(profile.grade_field) is not None:
        value = record[profile.grade_field]
        if value in profile.carry_values:
            return CAN_CARRY, ""
        return CANNOT_CARRY, profile.cannot_carry_reasons.get(value, f"{profile.grade_field} is {value!r}")
    if profile.type_data:
        found = _type_data(profile.type_data).get(resource_type.lower())
        if found == CAN_CARRY:
            return CAN_CARRY, ""
        if found == CANNOT_CARRY:
            return CANNOT_CARRY, f"the type cannot carry {profile.kind}s"
    return unclassified
