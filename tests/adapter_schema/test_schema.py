"""Tests for adapters/schema.py: the schema 2.0 inventory record and document."""

import copy

import pytest

from adapters import schema

RECORD = {
    "resource_id": "example://scope-1/things/thing-1",
    "platform": "aws",
    "scope": "scope-1",
    "location": None,
    "resource_type": "example:thing",
    "name": "thing-1",
    "tags": {"Project": "example"},
    "state": None,
    "created_at": None,
    "estimated_monthly_cost": None,
}

DOCUMENT = {
    "schema_version": "2.0",
    "platform": "aws",
    "generated_at": "2026-01-01T00:00:00+00:00",
    "scopes": [{"scope": "scope-1", "scope_type": "account", "name": None, "state": None, "tags": {}}],
    "resources": [RECORD],
}


def test_field_lists():
    assert list(schema.REQUIRED_RECORD_FIELDS) == [
        "resource_id",
        "platform",
        "scope",
        "location",
        "resource_type",
        "name",
        "tags",
        "state",
        "created_at",
        "estimated_monthly_cost",
    ]
    assert list(schema.OPTIONAL_RECORD_FIELDS) == [
        "inherited_tags",
        "kind",
        "container",
        "provisioning_state",
        "grade",
        "cost_basis",
    ]
    assert list(schema.REQUIRED_DOCUMENT_FIELDS) == [
        "schema_version",
        "platform",
        "generated_at",
        "scopes",
        "resources",
    ]
    assert list(schema.OPTIONAL_DOCUMENT_FIELDS) == ["currency", "unclassified_types", "incomplete"]
    assert list(schema.SCOPE_FIELDS) == ["scope", "scope_type", "name", "state", "tags"]
    assert list(schema.INCOMPLETE_FIELDS) == ["scope", "reason"]
    assert schema.SCHEMA_VERSION == "2.0"


def test_names_that_are_not_used():
    for table in (schema.REQUIRED_RECORD_FIELDS, schema.OPTIONAL_RECORD_FIELDS, schema.REQUIRED_DOCUMENT_FIELDS):
        for name in ("provider", "sub_account_id", "sub_accounts", "resource_group"):
            assert name not in table


def test_known_values():
    assert schema.KNOWN_PLATFORMS == ("aws", "azure", "gcp", "kubernetes", "baremetal", "cloudflare")
    assert schema.KNOWN_SCOPE_TYPES == ("account", "subscription", "project")
    assert schema.KNOWN_COST_BASES == ("billed", "allocated", "amortized", "list-price", "estimated")
    assert schema.SCOPE_TYPE_OF_PLATFORM == {"aws": "account", "azure": "subscription", "gcp": "project"}


def test_record_key_is_platform_scope_resource_id():
    assert schema.record_key(RECORD) == ("aws", "scope-1", "example://scope-1/things/thing-1")
    other_scope = {**RECORD, "scope": "scope-2"}
    other_platform = {**RECORD, "platform": "azure"}
    assert len({schema.record_key(r) for r in (RECORD, other_scope, other_platform)}) == 3


def test_currency():
    assert schema.currency_of(DOCUMENT) == "USD"
    assert schema.currency_of({**DOCUMENT, "currency": "EUR"}) == "EUR"
    assert schema.validate_document({**DOCUMENT, "currency": "EUR"}) == []
    assert schema.validate_document({**DOCUMENT, "currency": 1}) == ["document.currency: expected str, got int"]


def test_minimal_record_is_valid():
    assert schema.validate_record(RECORD) == []


def test_optional_fields_are_accepted():
    record = {
        **RECORD,
        "inherited_tags": {"Owner": "team"},
        "kind": None,
        "container": "rg",
        "provisioning_state": "ok",
        "grade": "labels+ownership",
        "cost_basis": "list-price",
    }
    notes = []
    assert schema.validate_record(record, notes=notes) == []
    assert notes == []


def test_cost_may_be_a_number():
    assert schema.validate_record({**RECORD, "estimated_monthly_cost": 12.5}) == []
    assert schema.validate_record({**RECORD, "estimated_monthly_cost": 0}) == []


@pytest.mark.parametrize("field", list(schema.REQUIRED_RECORD_FIELDS))
def test_missing_required_field(field):
    record = {k: v for k, v in RECORD.items() if k != field}
    assert schema.validate_record(record) == [f"record: missing field {field}"]


@pytest.mark.parametrize(
    "change, fragment",
    [
        ({"name": None}, "record.name: expected str"),
        ({"name": ""}, "record.name: must not be empty"),
        ({"resource_id": ""}, "record.resource_id: must not be empty"),
        ({"tags": None}, "record.tags: expected dict"),
        ({"tags": {"a": 1}}, "record.tags: every tag name and value must be a string"),
        ({"inherited_tags": []}, "record.inherited_tags: expected dict"),
        ({"estimated_monthly_cost": "free"}, "record.estimated_monthly_cost"),
        ({"estimated_monthly_cost": True}, "record.estimated_monthly_cost"),
        ({"platform": 1}, "record.platform: expected str"),
        ({"cost_basis": None}, "record.cost_basis: expected str"),
    ],
)
def test_invalid_record(change, fragment):
    issues = schema.validate_record({**RECORD, **change})
    assert len(issues) == 1 and fragment in issues[0], issues


def test_record_that_is_not_an_object():
    assert schema.validate_record(["x"]) == ["record: expected an object, got list"]


class TestLaterVersionsAreAccepted:
    """A field or value this version does not know is a note, never a problem."""

    @pytest.mark.parametrize(
        "change, note",
        [
            ({"observed_at": "2030-01-01T00:00:00Z"}, "record: unknown field observed_at"),
            ({"platform": "example-platform"}, "record.platform: unknown value 'example-platform'"),
            ({"cost_basis": "negotiated"}, "record.cost_basis: unknown value 'negotiated'"),
        ],
    )
    def test_record(self, change, note):
        notes = []
        assert schema.validate_record({**RECORD, **change}, notes=notes) == []
        assert len(notes) == 1 and notes[0].startswith(note), notes
        # without a notes list the same record is valid and nothing is raised
        assert schema.validate_record({**RECORD, **change}) == []

    @pytest.mark.parametrize("platform", ["kubernetes", "baremetal", "cloudflare"])
    def test_known_platforms_beyond_the_three_clouds(self, platform):
        notes = []
        assert schema.validate_record({**RECORD, "platform": platform}, notes=notes) == []
        assert notes == []

    def test_document(self):
        document = copy.deepcopy(DOCUMENT)
        document["schema_version"] = "2.3"
        document["hosted_on"] = "example"
        document["scopes"][0]["scope_type"] = "cluster"
        document["scopes"][0]["parent"] = "example"
        document["resources"][0]["identifiers"] = ["a"]
        notes = []
        assert schema.validate_document(document, notes=notes) == []
        assert sorted(note.split(":")[0] for note in notes) == [
            "document",
            "document.schema_version",
            "resources[0]",
            "scopes[0]",
            "scopes[0].scope_type",
        ]

    def test_another_major_version_is_a_problem(self):
        issues = schema.validate_document({**DOCUMENT, "schema_version": "3.0"})
        assert issues == ["document.schema_version: expected major version 2, got '3.0'"]


def test_document_is_valid():
    notes = []
    assert schema.validate_document(DOCUMENT, notes=notes) == []
    assert notes == []


@pytest.mark.parametrize(
    "mutate, fragment",
    [
        (lambda d: d.update(schema_version="1.0"), "document.schema_version"),
        (lambda d: d.pop("scopes"), "document: missing field scopes"),
        (lambda d: d["scopes"][0].pop("tags"), "scopes[0]: missing field tags"),
        (lambda d: d["scopes"][0].pop("scope_type"), "scopes[0]: missing field scope_type"),
        (lambda d: d["resources"][0].update(name=None), "resources[0].name"),
        (lambda d: d["resources"][0].update(platform="gcp"), "resources[0].platform: differs"),
    ],
)
def test_invalid_document(mutate, fragment):
    document = copy.deepcopy(DOCUMENT)
    mutate(document)
    issues = schema.validate_document(document)
    assert len(issues) == 1 and fragment in issues[0], issues


@pytest.mark.parametrize(
    "resource_id, name",
    [
        ("//example.invalid/projects/p/things/thing-1", "thing-1"),
        ("/subscriptions/s/resourceGroups/Example-RG/", "Example-RG"),
        ("plain", "plain"),
    ],
)
def test_name_from_id(resource_id, name):
    assert schema.name_from_id(resource_id) == name


def test_finding_categories():
    assert schema.FINDING_CATEGORIES == ("contract", "unclaimed", "missing", "lifecycle", "schedule")
    assert schema.CATEGORIES_FROM_1_0 == {
        "tag_compliance": "contract",
        "drift_detection": "unclaimed",
        "lifecycle_compliance": "lifecycle",
        "schedule_compliance": "schedule",
    }
    assert set(schema.CATEGORIES_FROM_1_0.values()) == set(schema.FINDING_CATEGORIES) - {"missing"}


class TestIncomplete:
    def document(self, incomplete):
        return {**copy.deepcopy(DOCUMENT), "resources": [], "incomplete": incomplete}

    @pytest.mark.parametrize(
        "incomplete",
        [[], [{"scope": None, "reason": "cut short"}], [{"scope": "example-scope", "reason": "no access"}]],
    )
    def test_valid(self, incomplete):
        assert schema.validate_document(self.document(incomplete)) == []

    @pytest.mark.parametrize(
        ("incomplete", "issue"),
        [
            ({}, "document.incomplete: expected list, got dict"),
            ([{"scope": None}], "incomplete[0]: missing field reason"),
            ([{"scope": 1, "reason": "x"}], "incomplete[0].scope: expected str or None, got int"),
        ],
    )
    def test_invalid(self, incomplete, issue):
        assert schema.validate_document(self.document(incomplete)) == [issue]
