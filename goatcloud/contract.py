"""The resource contract as one platform sees it.

Reads ``contract/contract.yaml`` and, when given, a deployment mapping, and
answers for one platform: the key of each fact, what carries it, which facts
are required, which values are accepted, and how an expiry value is decoded.

``scripts/contract-gen`` holds the same loading and mapping rules for
generating files. The two are to be consolidated when the package layout
lands; until then a change to the mapping rules is made in both.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONTRACT_PATH = Path(__file__).resolve().parent.parent / "contract" / "contract.yaml"

_COMPACT_UTC = re.compile(r"^([0-9]{4})([0-9]{2})([0-9]{2})t([0-9]{2})([0-9]{2})z$")


class ContractError(ValueError):
    """The contract or a mapping file is not valid."""


REGISTRY_ENTRY = "registry-entry"
# What may hold a fact. The list is open: a later minor version adds carriers,
# and a carrier not listed here is read as given.
KNOWN_CARRIERS = (
    "tag",
    "label",
    "annotation",
    "resource-manager-tag",
    "custom-field",
    REGISTRY_ENTRY,
    "inventory-record",
)
# Carriers that hold a fact somewhere other than on the resource, without a key.
KEYLESS_CARRIERS = (REGISTRY_ENTRY, "inventory-record")
# Facts whose values compare without regard to case.
CASE_INSENSITIVE_FACTS = ("managed_by",)


@dataclass(frozen=True)
class Fact:
    """One fact of the contract on one platform."""

    name: str
    key: str | None
    required: bool
    encoding: str | None = None
    allowed: tuple[str, ...] = ()
    pattern: str | None = None
    # What holds the fact on this platform: tag, label, registry-entry, ...
    carrier: str = "tag"
    description: str = ""

    @property
    def carried_by(self) -> str | None:
        """The carrier of a fact that has no key on the resource, else None."""
        return self.carrier if self.key is None else None

    def accepts(self, value: str) -> bool:
        """True when the value is one the contract accepts for this fact.

        A fact in CASE_INSENSITIVE_FACTS (managed_by) compares without regard to
        case; any other fact compares exactly.
        """
        if self.allowed:
            if self.name in CASE_INSENSITIVE_FACTS:
                if value.lower() not in {v.lower() for v in self.allowed}:
                    return False
            elif value not in self.allowed:
                return False
        return not (self.pattern and not re.match(self.pattern, value))


@dataclass(frozen=True)
class Rule:
    """Facts that become required when another fact has a given value."""

    id: str
    when_fact: str
    equals: str
    require: tuple[str, ...]
    description: str = ""


@dataclass(frozen=True)
class PlatformContract:
    """The contract resolved for one platform."""

    platform: str
    # The rendering's default carrier.
    carrier: str
    version: str
    facts: dict[str, Fact]
    rules: tuple[Rule, ...] = ()
    mapping_applied: bool = False
    # What the contract attaches to: resource, resource-group, project.
    unit: str = "resource"
    key_prefix: str = ""
    encodings: dict[str, str] = field(default_factory=dict)
    # Encoding name to the regular expression a written value matches in full.
    encoding_patterns: dict[str, str] = field(default_factory=dict)

    @property
    def cloud(self) -> str:
        """Deprecated: the earlier name of ``platform``."""
        return self.platform

    @property
    def kind(self) -> str:
        """Deprecated: the earlier name of ``carrier``."""
        return self.carrier

    @property
    def required(self) -> list[str]:
        """Required facts that have a key on this platform, in contract order."""
        return [f.name for f in self.facts.values() if f.required and f.key is not None]

    @property
    def keyed(self) -> list[str]:
        """Every fact that has a key on this platform, in contract order."""
        return [f.name for f in self.facts.values() if f.key is not None]

    @property
    def not_attached(self) -> list[Fact]:
        """Facts the contract defines that this platform does not write on a resource."""
        return [f for f in self.facts.values() if f.key is None]

    def key(self, fact: str) -> str | None:
        return self.facts[fact].key

    def decode_expiry(self, value: str) -> datetime:
        """Decode an expiry value written in this platform's encoding to a UTC datetime.

        Raises ``ValueError`` for a value the encoding's pattern refuses, also
        when a lenient parser could read it.
        """
        encoding = self.facts["expires_at"].encoding or "iso8601"
        pattern = self.encoding_patterns.get(encoding)
        if pattern and not re.fullmatch(pattern, value):
            raise ValueError(f"{value!r} does not match the {encoding} form {pattern}")
        return decode_timestamp(value, encoding)


# The earlier name of PlatformContract.
CloudContract = PlatformContract


def decode_timestamp(value: str, encoding: str) -> datetime:
    """Decode a timestamp in one of the contract's encodings. Raises ``ValueError`` when it does not parse."""
    if encoding == "compact-utc":
        match = _COMPACT_UTC.fullmatch(value)
        if not match:
            raise ValueError(f"expected yyyymmddthhmmz, found {value!r}")
        year, month, day, hour, minute = (int(part) for part in match.groups())
        return datetime(year, month, day, hour, minute, tzinfo=UTC)
    moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _read_yaml(path: str | Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ContractError(f"{path}: expected a mapping at the top level")
    return data


def _require(condition: Any, message: str) -> None:
    if not condition:
        raise ContractError(message)


def _fact_names(value: Any, where: str, facts: dict[str, Any]) -> list[str]:
    _require(isinstance(value, list) and all(isinstance(v, str) for v in value), f"{where}: expected a list of facts")
    unknown = [name for name in value if name not in facts]
    _require(not unknown, f"{where}: unknown fact {', '.join(unknown)}")
    return value


def _carrier_of(entry: dict[str, Any], rendering: dict[str, Any]) -> str:
    """The carrier of a rendered fact: its own, else the rendering's. ``kind`` is the earlier name."""
    return entry.get("carrier") or entry.get("kind") or rendering.get("carrier") or rendering.get("kind") or "tag"


def apply_mapping(contract: dict[str, Any], mapping: dict[str, Any]) -> dict[str, Any]:
    """Return the contract with a deployment mapping applied.

    A mapping renames keys, changes the carrier of a fact, sets a key prefix,
    replaces the required set, and sets the allowed and protected environment
    names. It cannot add a fact, a platform, a rule, or a value of any other
    fact.
    """
    unknown = set(mapping) - {"contract_schema_version", "required", "environment", "renderings"}
    _require(not unknown, f"mapping: unknown entry {', '.join(sorted(unknown))}")
    major = str(contract["schema_version"]).split(".")[0]
    _require(
        str(mapping.get("contract_schema_version")).split(".")[0] == major,
        f"mapping: contract_schema_version must be of major version {major}",
    )
    result = copy.deepcopy(contract)
    facts = result["facts"]
    encodings = result.get("encodings") or {}

    if "required" in mapping:
        names = _fact_names(mapping["required"], "mapping.required", facts)
        for name, fact in facts.items():
            fact["required"] = name in names
        # The set replaces the required set of every platform, also one the contract states per platform.
        for rendering in result["renderings"].values():
            rendering.pop("required", None)

    environment = mapping.get("environment") or {}
    _require(isinstance(environment, dict), "mapping.environment: expected a mapping")
    unknown = set(environment) - {"allowed", "protected_values"}
    _require(not unknown, f"mapping.environment: unknown entry {', '.join(sorted(unknown))}")
    for entry in ("allowed", "protected_values"):
        if entry in environment:
            values = environment[entry]
            _require(
                isinstance(values, list) and all(isinstance(v, str) and v for v in values),
                f"mapping.environment.{entry}: expected a list",
            )
            facts["environment"][entry] = list(values)
    allowed, protected = facts["environment"].get("allowed"), facts["environment"].get("protected_values", [])
    _require(
        not allowed or not set(protected) - set(allowed),
        "mapping.environment: protected_values holds a name that is not in allowed",
    )

    for platform, override in (mapping.get("renderings") or {}).items():
        where = f"mapping.renderings.{platform}"
        _require(platform in result["renderings"], f"{where}: unknown platform")
        _require(isinstance(override, dict), f"{where}: expected a mapping")
        unknown = set(override) - {"required", "key_prefix", "facts"}
        _require(not unknown, f"{where}: unknown entry {', '.join(sorted(unknown))}")
        rendering = result["renderings"][platform]
        if "key_prefix" in override:
            _require(isinstance(override["key_prefix"], str), f"{where}.key_prefix: expected text")
            rendering["key_prefix"] = override["key_prefix"]
        if "required" in override:
            rendering["required"] = _fact_names(override["required"], f"{where}.required", facts)
        for name, entry in (override.get("facts") or {}).items():
            at = f"{where}.facts.{name}"
            _require(name in facts, f"{where}.facts: unknown fact {name!r}")
            _require(
                isinstance(entry, dict) and entry and set(entry) <= {"key", "carrier", "kind", "encoding"},
                f"{at}: expected key, carrier or encoding",
            )
            entry = dict(entry)
            if "kind" in entry:
                # `kind` is the earlier name of `carrier`.
                entry.setdefault("carrier", entry.pop("kind"))
                entry.pop("kind", None)
            merged = {k: v for k, v in {**rendering["facts"][name], **entry}.items() if k != "kind"}
            carrier = _carrier_of(merged, rendering)
            _require(isinstance(carrier, str) and carrier, f"{at}.carrier: expected a carrier name")
            if carrier in KEYLESS_CARRIERS and merged.get("key") is None:
                merged = {"key": None, "carrier": carrier}
            elif carrier == REGISTRY_ENTRY:
                raise ContractError(
                    f"{at}: a fact carried by the {REGISTRY_ENTRY} has no key; "
                    "set carrier and encoding to write it on the resource"
                )
            else:
                _require(
                    isinstance(merged.get("key"), str) and merged["key"],
                    f"{at}: a fact carried by a {carrier} needs a key",
                )
                _require(
                    merged.get("encoding") in encodings,
                    f"{at}.encoding: unknown encoding {merged.get('encoding')!r}",
                )
            rendering["facts"][name] = merged
    return result


def resolve(contract: dict[str, Any], platform: str, *, mapping_applied: bool = False) -> PlatformContract:
    """Flatten a contract document for one platform."""
    renderings = contract.get("renderings") or {}
    _require(
        platform in renderings,
        f"unknown platform {platform!r}; the contract renders {', '.join(sorted(renderings))}",
    )
    rendering = renderings[platform]
    platform_required = rendering.get("required")
    prefix = rendering.get("key_prefix") or ""
    facts: dict[str, Fact] = {}
    for name, spec in contract["facts"].items():
        rendered = rendering["facts"].get(name) or {}
        required = name in platform_required if platform_required is not None else bool(spec.get("required"))
        key = rendered.get("key")
        facts[name] = Fact(
            name=name,
            key=None if key is None else prefix + key,
            required=required,
            encoding=rendered.get("encoding"),
            allowed=tuple(spec.get("allowed") or ()),
            pattern=spec.get("pattern"),
            carrier=_carrier_of(rendered, rendering),
            description=spec.get("description", ""),
        )
    rules = tuple(
        Rule(
            id=rule["id"],
            when_fact=rule["when"]["fact"],
            equals=rule["when"]["equals"],
            require=tuple(rule["require"]),
            description=rule.get("description", ""),
        )
        for rule in contract.get("rules") or []
    )
    return PlatformContract(
        platform=platform,
        carrier=rendering.get("carrier") or rendering.get("kind") or "tag",
        version=str(contract["schema_version"]),
        facts=facts,
        rules=rules,
        mapping_applied=mapping_applied,
        unit=rendering.get("unit", "resource"),
        key_prefix=prefix,
        encoding_patterns={
            name: entry["pattern"]
            for name, entry in (contract.get("encodings") or {}).items()
            if isinstance(entry, dict) and entry.get("pattern")
        },
        encodings={
            name: (entry.get("description", "") if isinstance(entry, dict) else str(entry))
            for name, entry in (contract.get("encodings") or {}).items()
        },
    )


def load(
    platform: str, contract_path: str | Path | None = None, mapping_path: str | Path | None = None
) -> PlatformContract:
    """Load the contract for one platform, with a deployment mapping applied when one is given."""
    document = _read_yaml(contract_path or DEFAULT_CONTRACT_PATH)
    for entry in ("schema_version", "facts", "renderings"):
        _require(entry in document, f"contract: missing {entry}")
    if mapping_path:
        document = apply_mapping(document, _read_yaml(mapping_path))
    return resolve(document, platform, mapping_applied=bool(mapping_path))
