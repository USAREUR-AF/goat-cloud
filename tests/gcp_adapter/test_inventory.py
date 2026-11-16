"""Tests for the Google Cloud inventory adapter.

Every fixture is written by hand in the REST shape of a searchAllResources
response. No identifier belongs to a real project, and nothing here calls a
cloud API.
"""

from __future__ import annotations

import io
import json
import re
import urllib.parse
from datetime import UTC, datetime, timedelta, timezone

import pytest

from adapters import schema
from adapters.gcp import inventory

PROJECT_NUMBER = "123456789012"
OTHER_NUMBER = "123456789013"

# The API returns these on every result that sits in a project under a folder.
ANCESTRY = {
    "folders": ["folders/222222222222"],
    "organization": "organizations/111111111111",
    "parentFullResourceName": "//cloudresourcemanager.googleapis.com/projects/example-project",
    "parentAssetType": "cloudresourcemanager.googleapis.com/Project",
}

INSTANCE = {
    **ANCESTRY,
    "name": "//compute.googleapis.com/projects/example-project/zones/us-central1-a/instances/web-1",
    "assetType": "compute.googleapis.com/Instance",
    "project": f"projects/{PROJECT_NUMBER}",
    "displayName": "web-1",
    "location": "us-central1-a",
    "labels": {
        "project": "example-app",
        "environment": "dev",
        "managed-by": "terraform",
        "lifecycle": "temporary",
        "expires-at": "20261201t0000z",
    },
    "state": "RUNNING",
    "createTime": "2026-01-15T08:30:00Z",
}

# A bucket's resource name carries no project at all.
BUCKET = {
    **ANCESTRY,
    "name": "//storage.googleapis.com/example-project-example-service-tfstate",
    "assetType": "storage.googleapis.com/Bucket",
    "project": f"projects/{PROJECT_NUMBER}",
    "displayName": "example-project-example-service-tfstate",
    "location": "us-central1",
    "labels": {"role": "iac-registry"},
    "createTime": "2026-01-10T00:00:00Z",
}

# A secret's resource name carries the project NUMBER.
SECRET = {
    **ANCESTRY,
    "name": f"//secretmanager.googleapis.com/projects/{PROJECT_NUMBER}/secrets/example-secret",
    "assetType": "secretmanager.googleapis.com/Secret",
    "project": f"projects/{PROJECT_NUMBER}",
    "displayName": "example-secret",
    "location": "global",
    "createTime": "2026-02-01T12:00:00Z",
}

# No labels field, no display name, no state, no location.
FIREWALL = {
    **ANCESTRY,
    "name": "//compute.googleapis.com/projects/example-project/global/firewalls/allow-internal",
    "assetType": "compute.googleapis.com/Firewall",
    "project": f"projects/{PROJECT_NUMBER}",
}

# An enabled API is returned as an asset. Its name carries the project NUMBER
# and it has a state but no creation time and no labels.
ENABLED_API = {
    **ANCESTRY,
    "name": f"//serviceusage.googleapis.com/projects/{PROJECT_NUMBER}/services/example.googleapis.com",
    "assetType": "serviceusage.googleapis.com/Service",
    "project": f"projects/{PROJECT_NUMBER}",
    "displayName": "example.googleapis.com",
    "location": "global",
    "state": "ENABLED",
}

# An App Engine name carries the project id under apps/, not under projects/,
# and the result has no location.
APP_VERSION = {
    **ANCESTRY,
    "name": "//appengine.googleapis.com/apps/example-project/services/default/versions/v1",
    "assetType": "appengine.googleapis.com/Version",
    "project": f"projects/{PROJECT_NUMBER}",
    "displayName": "v1",
    "state": "SERVING",
    "createTime": "2026-03-01T09:00:00Z",
}

# The project's own asset names the project by its ID.
OWN_PROJECT_ASSET = {
    "folders": ["folders/222222222222"],
    "organization": "organizations/111111111111",
    "parentFullResourceName": "//cloudresourcemanager.googleapis.com/folders/222222222222",
    "parentAssetType": "cloudresourcemanager.googleapis.com/Folder",
    "name": "//cloudresourcemanager.googleapis.com/projects/example-project",
    "assetType": "cloudresourcemanager.googleapis.com/Project",
    "project": f"projects/{PROJECT_NUMBER}",
    "displayName": "Example Project",
    "location": "global",
    "labels": {"env": "dev", "owner": ""},
    "state": "ACTIVE",
    "createTime": "2026-01-01T00:00:00Z",
    "additionalAttributes": {"projectId": "example-project"},
}

# In a second project whose id appears on no resource name.
ORPHAN_BUCKET = {
    "name": "//storage.googleapis.com/another-bucket",
    "assetType": "storage.googleapis.com/Bucket",
    "project": f"projects/{OTHER_NUMBER}",
    "location": "europe-west1",
}

PROJECT_ASSET = {
    "name": f"//cloudresourcemanager.googleapis.com/projects/{OTHER_NUMBER}",
    "assetType": "cloudresourcemanager.googleapis.com/Project",
    "project": f"projects/{OTHER_NUMBER}",
    "displayName": "Second Example",
    "state": "ACTIVE",
    "additionalAttributes": {"projectId": "second-example"},
}

FOLDER = {
    "name": "//cloudresourcemanager.googleapis.com/folders/222222222222",
    "assetType": "cloudresourcemanager.googleapis.com/Folder",
    "organization": "organizations/111111111111",
    "displayName": "Example Folder",
    "state": "ACTIVE",
}


class FakeApi:
    """Serves fixed pages keyed by page token and records every request."""

    def __init__(self, pages: dict[str, dict]):
        self.pages = pages
        self.requests: list[dict] = []

    def __call__(self, request: dict) -> dict:
        self.requests.append(request)
        return self.pages[request["page_token"]]


def single_page(*results: dict) -> FakeApi:
    return FakeApi({"": {"results": list(results)}})


# ---------------------------------------------------------------------------
# Paging
# ---------------------------------------------------------------------------


class TestPaging:
    def test_follows_next_page_token_to_the_end(self):
        api = FakeApi(
            {
                "": {"results": [INSTANCE], "nextPageToken": "page-2"},
                "page-2": {"results": [BUCKET], "nextPageToken": "page-3"},
                "page-3": {"results": [SECRET]},
            }
        )
        records = inventory.list_resources("projects/example-project", api)
        assert [r["resource_id"] for r in records] == [INSTANCE["name"], BUCKET["name"], SECRET["name"]]
        assert [r["page_token"] for r in api.requests] == ["", "page-2", "page-3"]

    def test_every_request_repeats_the_same_parameters(self):
        api = FakeApi({"": {"results": [], "nextPageToken": "t"}, "t": {"results": [INSTANCE]}})
        inventory.list_resources(
            "folders/222222222222",
            api,
            query="labels.role:iac-registry",
            asset_types=["storage.googleapis.com/Bucket"],
            page_size=50,
        )
        for request in api.requests:
            assert request["scope"] == "folders/222222222222"
            assert request["query"] == "labels.role:iac-registry"
            assert request["asset_types"] == ["storage.googleapis.com/Bucket"]
            assert request["page_size"] == 50

    def test_an_empty_page_in_the_middle_does_not_end_the_scan(self):
        api = FakeApi({"": {"nextPageToken": "t"}, "t": {"results": [INSTANCE]}})
        assert len(inventory.list_resources("projects/example-project", api)) == 1

    def test_empty_string_token_ends_the_scan(self):
        api = FakeApi({"": {"results": [INSTANCE], "nextPageToken": ""}})
        assert len(inventory.list_resources("projects/example-project", api)) == 1
        assert len(api.requests) == 1

    def test_no_results_at_all(self):
        assert inventory.list_resources("projects/example-project", single_page()) == []

    def test_a_repeated_token_is_an_error_not_an_endless_loop(self):
        api = FakeApi({"": {"results": [INSTANCE], "nextPageToken": "t"}, "t": {"results": [], "nextPageToken": "t"}})
        with pytest.raises(RuntimeError, match="same nextPageToken"):
            inventory.list_resources("projects/example-project", api)

    def test_default_page_size_is_the_api_maximum(self):
        api = single_page()
        inventory.list_resources("projects/example-project", api)
        assert api.requests[0]["page_size"] == 500

    @pytest.mark.parametrize("page_size", [0, -1, 501])
    def test_page_size_outside_the_api_range_is_refused(self, page_size):
        with pytest.raises(ValueError, match="page_size"):
            inventory.list_resources("projects/example-project", single_page(), page_size=page_size)

    @pytest.mark.parametrize(
        "scope",
        ["organizations/111111111111", "folders/222222222222", "projects/example-project", "projects/123456789012"],
    )
    def test_accepted_scopes(self, scope):
        assert inventory.list_resources(scope, single_page()) == []

    @pytest.mark.parametrize(
        "scope",
        ["", "example-project", "organizations/example", "folders/", "projects/a/b", "subscriptions/x"],
    )
    def test_refused_scopes(self, scope):
        api = single_page()
        with pytest.raises(ValueError, match="scope"):
            inventory.list_resources(scope, api)
        assert api.requests == []


# ---------------------------------------------------------------------------
# Record shape
# ---------------------------------------------------------------------------


class TestRecord:
    def test_full_record(self):
        assert inventory.to_record(INSTANCE) == {
            "resource_id": "//compute.googleapis.com/projects/example-project/zones/us-central1-a/instances/web-1",
            "platform": "gcp",
            "scope": "example-project",
            "location": "us-central1-a",
            "resource_type": "compute.googleapis.com/Instance",
            "name": "web-1",
            "tags": INSTANCE["labels"],
            "state": "RUNNING",
            "created_at": "2026-01-15T08:30:00Z",
            "estimated_monthly_cost": None,
            "grade": "labels+ownership",
        }

    def test_every_record_has_the_same_fields(self):
        api = single_page(INSTANCE, BUCKET, SECRET, FIREWALL, FOLDER, PROJECT_ASSET)
        for record in inventory.list_resources("organizations/111111111111", api):
            assert set(record) == {
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
                "grade",
            }

    def test_every_record_is_a_valid_schema_record(self):
        api = single_page(INSTANCE, BUCKET, SECRET, FIREWALL, ORPHAN_BUCKET, FOLDER, PROJECT_ASSET)
        for record in inventory.list_resources("organizations/111111111111", api):
            assert schema.validate_record(record) == []
            assert record["name"]

    def test_document_is_a_valid_schema_document(self):
        api = single_page(INSTANCE, BUCKET, SECRET, FIREWALL, ORPHAN_BUCKET, FOLDER, PROJECT_ASSET)
        document = inventory.discover("organizations/111111111111", api)
        assert schema.validate_document(document) == []
        assert document["schema_version"] == schema.SCHEMA_VERSION

    def test_missing_labels_is_an_empty_dict(self):
        assert inventory.to_record(FIREWALL)["tags"] == {}

    def test_empty_labels_object_is_an_empty_dict(self):
        assert inventory.to_record({**FIREWALL, "labels": {}})["tags"] == {}

    def test_null_labels_is_an_empty_dict(self):
        assert inventory.to_record({**FIREWALL, "labels": None})["tags"] == {}

    def test_labels_are_kept_as_found(self):
        labels = {"App_Name": "kept", "empty": ""}
        assert inventory.to_record({**FIREWALL, "labels": labels})["tags"] == labels

    def test_tags_is_a_copy(self):
        record = inventory.to_record(INSTANCE)
        record["tags"]["added"] = "x"
        assert "added" not in INSTANCE["labels"]

    def test_absent_optional_fields_are_none(self):
        record = inventory.to_record(FIREWALL)
        assert record["location"] is None
        assert record["state"] is None
        assert record["created_at"] is None

    def test_empty_strings_from_the_client_library_are_none(self):
        record = inventory.to_record({**FIREWALL, "location": "", "state": "", "createTime": "", "displayName": ""})
        assert (record["location"], record["state"], record["created_at"]) == (None, None, None)
        assert record["name"] == "allow-internal"

    def test_enabled_api_record(self):
        record = inventory.to_record(ENABLED_API, {PROJECT_NUMBER: "example-project"})
        assert record["resource_type"] == "serviceusage.googleapis.com/Service"
        assert (record["state"], record["created_at"], record["tags"]) == ("ENABLED", None, {})

    def test_multi_region_location_is_kept(self):
        assert inventory.to_record({**BUCKET, "location": "eu"})["location"] == "eu"

    def test_own_project_asset_fills_the_scopes_entry(self):
        document = inventory.discover("folders/222222222222", single_page(ENABLED_API, OWN_PROJECT_ASSET))
        assert document["scopes"] == [
            {
                "scope": "example-project",
                "scope_type": "project",
                "name": "Example Project",
                "state": "ACTIVE",
                "tags": {"env": "dev", "owner": ""},
            }
        ]

    def test_name_falls_back_to_the_last_segment(self):
        assert inventory.to_record(FIREWALL)["name"] == "allow-internal"


# ---------------------------------------------------------------------------
# scope: project id versus project number
# ---------------------------------------------------------------------------


class TestScope:
    def test_project_id_is_read_from_the_resource_name(self):
        assert inventory.to_record(INSTANCE)["scope"] == "example-project"

    def test_number_resolves_through_a_sibling_resource(self):
        # The bucket and the secret show only the number; the instance shows both.
        records = inventory.list_resources("organizations/111111111111", single_page(BUCKET, SECRET, INSTANCE))
        assert [r["scope"] for r in records] == ["example-project"] * 3

    def test_number_resolves_when_the_sibling_is_on_a_later_page(self):
        api = FakeApi({"": {"results": [BUCKET], "nextPageToken": "t"}, "t": {"results": [INSTANCE]}})
        assert inventory.list_resources("folders/222222222222", api)[0]["scope"] == "example-project"

    def test_number_resolves_through_the_project_asset(self):
        records = inventory.list_resources("organizations/111111111111", single_page(ORPHAN_BUCKET, PROJECT_ASSET))
        assert [r["scope"] for r in records] == ["second-example", "second-example"]

    def test_number_resolves_through_a_project_id_scope(self):
        records = inventory.list_resources("projects/example-project", single_page(BUCKET, SECRET))
        assert [r["scope"] for r in records] == ["example-project", "example-project"]

    def test_a_project_number_scope_does_not_invent_an_id(self):
        records = inventory.list_resources(f"projects/{PROJECT_NUMBER}", single_page(BUCKET))
        assert records[0]["scope"] == PROJECT_NUMBER

    def test_unresolvable_number_is_kept_as_the_number(self):
        records = inventory.list_resources("organizations/111111111111", single_page(ORPHAN_BUCKET, INSTANCE))
        assert records[0]["scope"] == OTHER_NUMBER
        assert records[1]["scope"] == "example-project"

    def test_two_projects_do_not_cross(self):
        api = single_page(BUCKET, ORPHAN_BUCKET, INSTANCE, PROJECT_ASSET)
        records = inventory.list_resources("organizations/111111111111", api)
        assert [r["scope"] for r in records] == [
            "example-project",
            "second-example",
            "example-project",
            "second-example",
        ]

    def test_resource_outside_any_project_has_its_organization(self):
        assert inventory.to_record(FOLDER)["scope"] == "organizations/111111111111"

    def test_resource_with_no_project_and_no_organization(self):
        orphan = {"name": "//example.googleapis.com/things/x", "assetType": "example.googleapis.com/Thing"}
        assert inventory.to_record(orphan)["scope"] == ""

    def test_number_only_results_resolve_through_the_project_asset_named_by_id(self):
        api = single_page(ENABLED_API, APP_VERSION, BUCKET, OWN_PROJECT_ASSET)
        records = inventory.list_resources("organizations/111111111111", api)
        assert [r["scope"] for r in records] == ["example-project"] * 4

    def test_number_only_results_alone_keep_the_number(self):
        api = single_page(ENABLED_API, APP_VERSION, BUCKET, SECRET)
        records = inventory.list_resources("organizations/111111111111", api)
        assert [r["scope"] for r in records] == [PROJECT_NUMBER] * 4

    def test_a_project_under_an_organization_is_not_scoped_to_the_organization(self):
        # Every result in a project also carries its organization and folders.
        assert inventory.to_record(BUCKET, {})["scope"] == PROJECT_NUMBER

    def test_mapping_is_number_to_id(self):
        mapping = inventory.project_ids_by_number([INSTANCE, BUCKET, PROJECT_ASSET, ORPHAN_BUCKET])
        assert mapping == {PROJECT_NUMBER: "example-project", OTHER_NUMBER: "second-example"}


# ---------------------------------------------------------------------------
# Asset type classes and grades
# ---------------------------------------------------------------------------


class TestGrades:
    @pytest.mark.parametrize(
        ("result", "grade"),
        [
            (INSTANCE, "labels+ownership"),
            (BUCKET, "labels+ownership"),
            (OWN_PROJECT_ASSET, "labels+ownership"),
            (FIREWALL, "ownership"),
            (ENABLED_API, "none"),
            (APP_VERSION, "none"),
            (FOLDER, "none"),
        ],
    )
    def test_grade_follows_the_class_of_the_asset_type(self, result, grade):
        assert inventory.to_record(result)["grade"] == grade

    def test_unknown_type_is_graded_as_a_resource(self):
        unknown = {**FIREWALL, "assetType": "example.googleapis.com/Widget"}
        assert inventory.to_record(unknown)["grade"] == "labels+ownership"

    def test_unknown_types_are_counted_in_the_document(self):
        widget = {**FIREWALL, "assetType": "example.googleapis.com/Widget"}
        gadget = {**FIREWALL, "assetType": "example.googleapis.com/Gadget"}
        document = inventory.discover("projects/example-project", single_page(widget, INSTANCE, widget, gadget))
        assert document["unclassified_types"] == {
            "example.googleapis.com/Gadget": 1,
            "example.googleapis.com/Widget": 2,
        }

    def test_no_unknown_types_is_an_empty_map(self):
        document = inventory.discover("projects/example-project", single_page(INSTANCE, ENABLED_API))
        assert document["unclassified_types"] == {}

    def test_a_custom_class_table_is_used(self):
        classes = inventory.TypeClasses(
            {
                "default_class": "record",
                "grades": {"resource": "labels+ownership", "record": "none"},
                "types": {"compute.googleapis.com/Firewall": "resource"},
            }
        )
        document = inventory.discover("projects/example-project", single_page(FIREWALL, INSTANCE), type_classes=classes)
        assert [r["grade"] for r in document["resources"]] == ["labels+ownership", "none"]
        assert document["unclassified_types"] == {"compute.googleapis.com/Instance": 1}

    def test_a_class_without_a_grade_is_refused(self):
        with pytest.raises(ValueError, match="no grade"):
            inventory.TypeClasses(
                {"default_class": "resource", "grades": {"resource": "none"}, "types": {"a/B": "typo"}}
            )


class TestResourceTypesFile:
    classes = inventory.load_type_classes()

    def test_three_classes_and_their_grades(self):
        assert self.classes.grades == {
            "resource": "labels+ownership",
            "resource-unlabelable": "ownership",
            "record": "none",
        }
        assert self.classes.default_class == "resource"

    def test_every_type_has_a_known_class_and_a_well_formed_name(self):
        assert len(self.classes.types) > 80
        for asset_type, cls in self.classes.types.items():
            assert cls in self.classes.grades, asset_type
            assert re.fullmatch(r"[a-z0-9.]+\.googleapis\.com/[A-Za-z0-9]+", asset_type), asset_type

    def test_no_type_is_listed_twice(self):
        listed = re.findall(r"^  ([a-z0-9.]+/[A-Za-z0-9]+):", inventory.RESOURCE_TYPES_FILE.read_text(), flags=re.M)
        assert len(listed) == len(set(listed)) == len(self.classes.types)

    @pytest.mark.parametrize(
        "asset_type",
        [
            "serviceusage.googleapis.com/Service",
            "sqladmin.googleapis.com/Backup",
            "sqladmin.googleapis.com/BackupRun",
            "iam.googleapis.com/ServiceAccountKey",
            "artifactregistry.googleapis.com/DockerImage",
            "appengine.googleapis.com/Version",
            "compute.googleapis.com/InstanceSettings",
            "compute.googleapis.com/Project",
            "cloudbilling.googleapis.com/ProjectBillingInfo",
        ],
    )
    def test_records_are_never_graded(self, asset_type):
        assert self.classes.grade(asset_type) == "none"

    @pytest.mark.parametrize(
        "asset_type",
        ["compute.googleapis.com/Firewall", "compute.googleapis.com/Network", "iam.googleapis.com/ServiceAccount"],
    )
    def test_unlabelable_resources_are_graded_on_ownership_only(self, asset_type):
        assert self.classes.grade(asset_type) == "ownership"


# ---------------------------------------------------------------------------
# Scope resolution under a filter
# ---------------------------------------------------------------------------


class FilteredApi:
    """Answers by the request's asset types; records every request."""

    def __init__(self, filtered: list[dict], projects: list[dict], project_pages: int = 1):
        self.filtered, self.projects, self.project_pages = filtered, projects, project_pages
        self.requests: list[dict] = []

    def __call__(self, request: dict) -> dict:
        self.requests.append(request)
        if request["asset_types"] == [inventory.PROJECT_ASSET_TYPE] and not request["query"]:
            if self.project_pages == 2 and not request["page_token"]:
                return {"results": self.projects[:1], "nextPageToken": "more"}
            return {"results": self.projects[1:] if self.project_pages == 2 else self.projects}
        return {"results": self.filtered}


class TestScopeUnderAFilter:
    def test_asset_type_filter_makes_one_project_search(self):
        api = FilteredApi([BUCKET, ORPHAN_BUCKET], [OWN_PROJECT_ASSET, PROJECT_ASSET])
        document = inventory.discover("organizations/111111111111", api, asset_types=["storage.googleapis.com/Bucket"])
        assert [r["scope"] for r in document["resources"]] == ["example-project", "second-example"]
        assert len(api.requests) == 2
        assert api.requests[1]["scope"] == "organizations/111111111111"
        assert api.requests[1]["asset_types"] == ["cloudresourcemanager.googleapis.com/Project"]
        assert api.requests[1]["query"] == ""

    def test_project_assets_from_the_lookup_are_not_records(self):
        api = FilteredApi([BUCKET], [OWN_PROJECT_ASSET])
        document = inventory.discover("folders/222222222222", api, asset_types=["storage.googleapis.com/Bucket"])
        assert [r["resource_type"] for r in document["resources"]] == ["storage.googleapis.com/Bucket"]
        assert [s["scope"] for s in document["scopes"]] == ["example-project"]

    def test_query_filter_makes_the_project_search(self):
        api = FilteredApi([ENABLED_API], [OWN_PROJECT_ASSET])
        document = inventory.discover("folders/222222222222", api, query="state:ENABLED")
        assert document["resources"][0]["scope"] == "example-project"
        assert len(api.requests) == 2

    def test_the_project_search_is_paged(self):
        api = FilteredApi([ORPHAN_BUCKET], [OWN_PROJECT_ASSET, PROJECT_ASSET], project_pages=2)
        document = inventory.discover("organizations/111111111111", api, asset_types=["storage.googleapis.com/Bucket"])
        assert document["resources"][0]["scope"] == "second-example"
        assert len(api.requests) == 3

    def test_no_project_search_when_every_record_resolves(self):
        api = FilteredApi([INSTANCE, BUCKET], [OWN_PROJECT_ASSET])
        inventory.discover("organizations/111111111111", api, asset_types=["compute.googleapis.com/Instance"])
        assert len(api.requests) == 1

    def test_no_project_search_under_a_project_id_scope(self):
        api = FilteredApi([BUCKET], [OWN_PROJECT_ASSET])
        document = inventory.discover("projects/example-project", api, asset_types=["storage.googleapis.com/Bucket"])
        assert document["resources"][0]["scope"] == "example-project"
        assert len(api.requests) == 1

    def test_no_project_search_without_a_filter(self):
        api = FilteredApi([ORPHAN_BUCKET], [PROJECT_ASSET])
        document = inventory.discover("organizations/111111111111", api)
        assert document["resources"][0]["scope"] == OTHER_NUMBER
        assert len(api.requests) == 1

    def test_no_project_search_when_the_filter_matches_nothing(self):
        api = FilteredApi([], [OWN_PROJECT_ASSET])
        inventory.discover("organizations/111111111111", api, query="labels.role:iac-registry")
        assert len(api.requests) == 1

    def test_a_project_the_lookup_cannot_see_keeps_its_number(self):
        api = FilteredApi([BUCKET, ORPHAN_BUCKET], [OWN_PROJECT_ASSET])
        document = inventory.discover("organizations/111111111111", api, asset_types=["storage.googleapis.com/Bucket"])
        assert [r["scope"] for r in document["resources"]] == ["example-project", OTHER_NUMBER]


# ---------------------------------------------------------------------------
# The document's list of projects
# ---------------------------------------------------------------------------


class TestScopes:
    def test_one_entry_per_project_sorted(self):
        api = single_page(ORPHAN_BUCKET, PROJECT_ASSET, BUCKET, INSTANCE, FOLDER)
        document = inventory.discover("organizations/111111111111", api)
        assert document["scopes"] == [
            {"scope": "example-project", "scope_type": "project", "name": None, "state": None, "tags": {}},
            {
                "scope": "second-example",
                "scope_type": "project",
                "name": "Second Example",
                "state": "ACTIVE",
                "tags": {},
            },
        ]

    def test_project_labels_are_carried(self):
        labelled = {**PROJECT_ASSET, "labels": {"environment": "dev"}}
        document = inventory.discover("organizations/111111111111", single_page(labelled))
        assert document["scopes"][0]["tags"] == {"environment": "dev"}

    def test_unresolved_project_is_listed_by_number(self):
        document = inventory.discover("organizations/111111111111", single_page(ORPHAN_BUCKET))
        assert document["scopes"] == [
            {"scope": OTHER_NUMBER, "scope_type": "project", "name": None, "state": None, "tags": {}}
        ]

    def test_resources_outside_projects_add_no_entry(self):
        assert inventory.discover("organizations/111111111111", single_page(FOLDER))["scopes"] == []

    def test_generated_at_is_utc_to_the_second(self):
        stamp = inventory.discover("projects/example-project", single_page())["generated_at"]
        assert datetime.fromisoformat(stamp).utcoffset() == timedelta(0)
        assert datetime.fromisoformat(stamp).microsecond == 0


# ---------------------------------------------------------------------------
# expires-at
# ---------------------------------------------------------------------------


class TestExpiresAt:
    def test_decodes_the_documented_example(self):
        assert inventory.decode_expires_at("20261201t0000z") == datetime(2026, 12, 1, 0, 0, tzinfo=UTC)

    def test_decodes_hours_and_minutes(self):
        assert inventory.decode_expires_at("20270131t2359z") == datetime(2027, 1, 31, 23, 59, tzinfo=UTC)

    def test_decoded_value_is_utc(self):
        assert inventory.decode_expires_at("20261201t0000z").utcoffset() == timedelta(0)

    def test_decodes_the_label_on_a_record(self):
        record = inventory.to_record(INSTANCE)
        assert inventory.decode_expires_at(record["tags"]["expires-at"]).isoformat() == "2026-12-01T00:00:00+00:00"

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "2026-12-01T00:00:00Z",  # ISO 8601 is not the label form
            "20261201T0000Z",  # label values are lowercase
            "20261201t0000",  # no zone marker
            "20261201t000000z",  # seconds are not encoded
            "2026121t0000z",
            "20261201",
            " 20261201t0000z",
            "20261201t0000z\n",
            "never",
        ],
    )
    def test_other_forms_are_refused(self, value):
        with pytest.raises(ValueError):
            inventory.decode_expires_at(value)

    @pytest.mark.parametrize("value", ["20261301t0000z", "20260230t0000z", "20261201t2400z", "20261201t0060z"])
    def test_impossible_dates_are_refused(self, value):
        with pytest.raises(ValueError):
            inventory.decode_expires_at(value)

    def test_encode_matches_the_terraform_module(self):
        assert inventory.encode_expires_at(datetime(2026, 12, 1, tzinfo=UTC)) == "20261201t0000z"

    def test_encode_converts_to_utc_and_drops_seconds(self):
        moment = datetime(2026, 12, 1, 2, 30, 45, tzinfo=timezone(timedelta(hours=2)))
        assert inventory.encode_expires_at(moment) == "20261201t0030z"

    def test_encode_refuses_a_naive_datetime(self):
        with pytest.raises(ValueError):
            inventory.encode_expires_at(datetime(2026, 12, 1))

    def test_round_trip(self):
        moment = datetime(2028, 2, 29, 13, 5, tzinfo=UTC)
        assert inventory.decode_expires_at(inventory.encode_expires_at(moment)) == moment


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


class Refused(Exception):
    """Stands for urllib.error.HTTPError and GoogleAPICallError: both carry the HTTP status as ``code``."""

    def __init__(self, code):
        super().__init__(f"HTTP {code}")
        self.code = code


class TestIncomplete:
    def test_complete_inventory_reports_nothing(self):
        document = inventory.discover("projects/example-project", single_page(INSTANCE, BUCKET))
        assert document["incomplete"] == []
        assert schema.validate_document(document) == []

    def test_project_number_that_is_not_resolved_is_reported(self):
        document = inventory.discover("folders/1", single_page(INSTANCE, ORPHAN_BUCKET))
        assert document["incomplete"] == [
            {"scope": OTHER_NUMBER, "reason": "the project number could not be resolved to a project id"}
        ]
        assert schema.validate_document(document) == []

    @pytest.mark.parametrize(
        ("code", "reason"), [(403, "the caller cannot search this scope"), (429, "the search was throttled")]
    )
    def test_search_refused_part_way_is_reported_and_the_results_read_stand(self, code, reason):
        def fetch_page(request):
            if request["page_token"]:
                raise Refused(code)
            return {"results": [INSTANCE], "nextPageToken": "page-2"}

        document = inventory.discover("organizations/1", fetch_page)
        assert document["incomplete"] == [{"scope": None, "reason": f"organizations/1: {reason} after 1 results"}]
        assert len(document["resources"]) == 1

    def test_search_refused_outright_is_reported(self):
        def fetch_page(request):
            raise Refused(403)

        document = inventory.discover("projects/example-project", fetch_page)
        assert document["resources"] == [] and document["scopes"] == []
        assert document["incomplete"][0]["reason"].endswith("the caller cannot search this scope after 0 results")

    def test_any_other_error_is_raised(self):
        def fetch_page(request):
            raise Refused(500)

        with pytest.raises(Refused):
            inventory.discover("projects/example-project", fetch_page)


class TestCli:
    def test_writes_the_inventory_to_a_file(self, tmp_path, capsys):
        out = tmp_path / "inventory.json"
        code = inventory.main(["--scope", "projects/example-project", "--out", str(out)], single_page(INSTANCE, BUCKET))
        assert code == 0
        document = json.loads(out.read_text())
        assert set(document) == {
            "schema_version",
            "platform",
            "generated_at",
            "scopes",
            "unclassified_types",
            "incomplete",
            "resources",
        }
        assert document["schema_version"] == "2.0"
        assert document["platform"] == "gcp"
        assert [r["scope"] for r in document["resources"]] == ["example-project", "example-project"]
        assert [s["scope"] for s in document["scopes"]] == ["example-project"]
        assert capsys.readouterr().out == ""

    def test_writes_to_standard_output_by_default(self, capsys):
        inventory.main(["--scope", "projects/example-project"], single_page(FIREWALL))
        assert json.loads(capsys.readouterr().out)["resources"][0]["tags"] == {}

    def test_passes_filters_to_the_api(self, capsys):
        api = single_page()
        inventory.main(
            [
                "--scope",
                "organizations/111111111111",
                "--asset-type",
                "storage.googleapis.com/Bucket",
                "--asset-type",
                "compute.googleapis.com/Instance",
                "--query",
                "labels.role:iac-registry",
                "--page-size",
                "100",
            ],
            api,
        )
        request = api.requests[0]
        assert request["asset_types"] == ["storage.googleapis.com/Bucket", "compute.googleapis.com/Instance"]
        assert request["query"] == "labels.role:iac-registry"
        assert request["page_size"] == 100

    def test_scope_is_required(self):
        with pytest.raises(SystemExit):
            inventory.main([], single_page())


# ---------------------------------------------------------------------------
# The REST transport, against a stand-in opener
# ---------------------------------------------------------------------------


class TestRestTransport:
    def test_url_for_a_first_request(self):
        url = inventory.rest_request_url(
            {"scope": "projects/example-project", "query": "", "asset_types": [], "page_size": 500, "page_token": ""}
        )
        assert url == "https://cloudasset.googleapis.com/v1/projects/example-project:searchAllResources?pageSize=500"

    def test_url_carries_filters_and_token(self):
        url = inventory.rest_request_url(
            {
                "scope": "folders/222222222222",
                "query": "labels.role:iac-registry",
                "asset_types": ["storage.googleapis.com/Bucket", "compute.googleapis.com/Disk"],
                "page_size": 50,
                "page_token": "page-2",
            }
        )
        assert url == (
            "https://cloudasset.googleapis.com/v1/folders/222222222222:searchAllResources"
            "?pageSize=50&query=labels.role%3Aiac-registry"
            "&assetTypes=storage.googleapis.com%2FBucket&assetTypes=compute.googleapis.com%2FDisk&pageToken=page-2"
        )

    def test_pages_are_read_with_the_token_and_quota_project(self):
        bodies = {
            None: {"results": [ENABLED_API], "nextPageToken": "page-2"},
            "page-2": {"results": [OWN_PROJECT_ASSET]},  # a last page has no nextPageToken key
        }
        seen = []

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def opener(request, timeout):
            seen.append(request)
            token = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query).get("pageToken", [None])[0]
            return Response(json.dumps(bodies[token]).encode())

        fetch = inventory.rest_page_fetcher("synthetic-token", "example-project", opener=opener)
        records = inventory.list_resources("projects/example-project", fetch, page_size=1)

        assert [r["scope"] for r in records] == ["example-project", "example-project"]
        assert len(seen) == 2
        assert seen[0].get_method() == "GET"
        assert seen[0].get_header("Authorization") == "Bearer synthetic-token"
        assert seen[0].get_header("X-goog-user-project") == "example-project"

    def test_quota_project_header_is_optional(self):
        seen = []

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def opener(request, timeout):
            seen.append(request)
            return Response(b"{}")

        assert (
            inventory.list_resources("projects/example-project", inventory.rest_page_fetcher("t", opener=opener)) == []
        )
        assert seen[0].get_header("X-goog-user-project") is None


# ---------------------------------------------------------------------------
# The client-library transport, against a stand-in client
# ---------------------------------------------------------------------------


class TestClientTransport:
    """Runs only where google-cloud-asset is installed; it builds real response types offline."""

    def test_library_pages_become_rest_shaped_dicts(self):
        asset_v1 = pytest.importorskip("google.cloud.asset_v1")

        responses = {
            "": asset_v1.SearchAllResourcesResponse(
                results=[
                    asset_v1.ResourceSearchResult(
                        name=INSTANCE["name"],
                        asset_type=INSTANCE["assetType"],
                        project=INSTANCE["project"],
                        display_name="web-1",
                        location="us-central1-a",
                        labels={"environment": "dev"},
                        state="RUNNING",
                    )
                ],
                next_page_token="page-2",
            ),
            "page-2": asset_v1.SearchAllResourcesResponse(
                results=[
                    asset_v1.ResourceSearchResult(
                        name=BUCKET["name"], asset_type=BUCKET["assetType"], project=BUCKET["project"]
                    )
                ]
            ),
        }

        class Pager:
            def __init__(self, response):
                self.pages = iter([response])

        class Client:
            def __init__(self):
                self.requests = []

            def search_all_resources(self, request):
                self.requests.append(request)
                return Pager(responses[request.page_token])

        client = Client()
        records = inventory.list_resources("projects/example-project", inventory.client_page_fetcher(client))

        assert [r.page_token for r in client.requests] == ["", "page-2"]
        assert client.requests[0].scope == "projects/example-project"
        assert client.requests[0].page_size == 500
        assert records[0]["tags"] == {"environment": "dev"}
        assert records[0]["state"] == "RUNNING"
        assert records[1]["scope"] == "example-project"
        assert records[1]["tags"] == {}
        assert records[1]["location"] is None
        assert records[1]["created_at"] is None
