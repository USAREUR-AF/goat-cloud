"""Every grading rule on each platform, on the unit and on the resource."""

import pytest

from goatcloud import contract, grade, names
from goatcloud.profiles import CAN_CARRY, CANNOT_CARRY, PROFILES, UNCLASSIFIED, capability, profile_for

from .conftest import NOW, load_inventory

SUB = "00000000-0000-0000-0000-000000000000"
REPO = "https://git.example/acme/example-repo"
AZURE_FULL = {
    "Project": "acme",
    "Environment": "dev",
    "ManagedBy": "terraform",
    "Lifecycle": "persistent",
    "Repository": REPO,
}
GCP_FULL = {
    "workload": "acme",
    "environment": "dev",
    "managed-by": "terraform",
    "lifecycle": "persistent",
    "owner": "platform",
}
FULL = {"azure": AZURE_FULL, "gcp": GCP_FULL}
CLOUDS = ["azure", "gcp"]
MODES = ["units", "resources"]
RG_TYPE = "microsoft.resources/subscriptions/resourcegroups"
PROJECT_TYPE = "cloudresourcemanager.googleapis.com/Project"
UNIT_ID = {"azure": f"/subscriptions/{SUB}/resourcegroups/acme-rg", "gcp": "example-project"}


def unit_record(cloud, tags, **extra):
    if cloud == "azure":
        base = {"resource_id": UNIT_ID[cloud], "resource_type": RG_TYPE, "container": "acme-rg", "scope": SUB}
    else:
        base = {
            "resource_id": "//cloudresourcemanager.googleapis.com/projects/example-project",
            "resource_type": PROJECT_TYPE,
            "scope": "example-project",
            "grade": "labels+ownership",
        }
    return {**base, "platform": cloud, "location": "example-location", "name": "example", "tags": tags, **extra}


def member(cloud, tags, **extra):
    if cloud == "azure":
        base = {
            "resource_id": f"{UNIT_ID[cloud]}/providers/microsoft.network/virtualnetworks/acme-vnet",
            "resource_type": "microsoft.network/virtualnetworks",
            "container": "acme-rg",
            "scope": SUB,
        }
    else:
        base = {
            "resource_id": "//compute.googleapis.com/projects/example-project/zones/z/instances/acme-vm",
            "resource_type": "compute.googleapis.com/Instance",
            "scope": "example-project",
            "grade": "labels+ownership",
        }
    return {**base, "platform": cloud, "location": "example-location", "name": "member", "tags": tags, **extra}


def inventory(cloud, records, **extra):
    sub = SUB if cloud == "azure" else "example-project"
    return {
        "platform": cloud,
        "scopes": [
            {
                "scope": sub,
                "scope_type": "subscription" if cloud == "azure" else "project",
                "name": "example",
                "state": None,
                "tags": {},
            }
        ],
        "resources": records,
        **extra,
    }


def run(cloud, mode, tags, **extra):
    """Grade one subject carrying ``tags``: the unit, or a resource. Returns (unit entry, findings, contract)."""
    options = {k: extra.pop(k) for k in ("report_inherited", "lag_minutes") if k in extra}
    resolved = contract.load(cloud)
    record = unit_record(cloud, tags, **extra) if mode == "units" else member(cloud, tags, **extra)
    result = grade.GRADERS[mode](inventory(cloud, [record]), resolved, now=NOW, **options)
    (unit,) = result["units"]
    return unit, result["findings"], resolved


def without(cloud, *facts):
    resolved = contract.load(cloud)
    drop = {resolved.key(fact) for fact in facts}
    return {k: v for k, v in FULL[cloud].items() if k not in drop}


def with_fact(cloud, fact, value, base=None):
    return {**(base if base is not None else FULL[cloud]), contract.load(cloud).key(fact): value}


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("cloud", CLOUDS)
class TestContractRules:
    def test_all_present_is_compliant(self, cloud, mode):
        unit, findings, _ = run(cloud, mode, FULL[cloud])
        assert (unit["result"], unit["rules"], findings) == (names.COMPLIANT, [], [])

    def test_all_missing_is_high(self, cloud, mode):
        unit, (finding,), resolved = run(cloud, mode, {})
        assert unit["result"] == names.NON_COMPLIANT
        assert (finding["category"], finding["severity"], finding["rule"]) == ("contract", "HIGH", "missing-required")
        assert finding["missing_facts"] == resolved.required

    @pytest.mark.parametrize("fact", ["managed_by", "project"])
    def test_missing_a_critical_fact_is_high(self, cloud, mode, fact):
        _, (finding,), _ = run(cloud, mode, without(cloud, fact))
        assert (finding["severity"], finding["missing_facts"]) == ("HIGH", [fact])

    @pytest.mark.parametrize("fact", ["environment", "lifecycle"])
    def test_missing_another_fact_is_medium(self, cloud, mode, fact):
        unit, (finding,), _ = run(cloud, mode, without(cloud, fact))
        assert (finding["severity"], finding["missing_facts"]) == ("MEDIUM", [fact])
        assert unit["result"] == names.NON_COMPLIANT

    def test_empty_value_counts_as_missing(self, cloud, mode):
        _, (finding,), _ = run(cloud, mode, with_fact(cloud, "environment", ""))
        assert finding["missing_facts"] == ["environment"]

    @pytest.mark.parametrize(("fact", "value"), [("managed_by", "chef"), ("lifecycle", "forever")])
    def test_value_outside_the_contract_is_medium(self, cloud, mode, fact, value):
        unit, (finding,), _ = run(cloud, mode, with_fact(cloud, fact, value))
        assert (finding["severity"], finding["rule"]) == ("MEDIUM", "invalid-value")
        assert unit["result"] == names.NON_COMPLIANT

    def test_value_is_checked_when_other_facts_are_missing(self, cloud, mode):
        unit, _, _ = run(cloud, mode, with_fact(cloud, "managed_by", "chef", base={}))
        assert unit["rules"] == ["invalid-value", "missing-required"]

    def test_protected_is_reported_and_does_not_change_the_result(self, cloud, mode):
        key = PROFILES[cloud].protected_key
        unit, (finding,), _ = run(cloud, mode, {**FULL[cloud], key: "true"})
        assert (unit["result"], unit["rules"]) == (names.COMPLIANT, ["protected"])
        assert finding["severity"] == "INFO"
        unit, _, _ = run(cloud, mode, {**without(cloud, "environment"), key: "true"})
        assert (unit["result"], unit["rules"]) == (names.NON_COMPLIANT, ["missing-required", "protected"])
        # the contract accepts only "true": any other value is not protected and is outside the contract
        assert run(cloud, mode, {**FULL[cloud], key: "false"})[0]["rules"] == ["invalid-value"]


def test_repository_value_is_graded_on_azure():
    _, (finding,), _ = run("azure", "units", {**AZURE_FULL, "Repository": "example-repo"})
    assert finding["evidence"] == [
        {"fact": "repository", "key": "Repository", "value": "example-repo", "read_from": "unit"}
    ]


def test_repository_is_not_graded_on_gcp():
    unit, findings, resolved = run("gcp", "units", {**GCP_FULL, "repository": "example-repo"})
    assert findings == [] and "repository" not in unit["facts"] and "repository" not in resolved.required


EXPIRY = {
    "azure": {"past": "2030-06-01T00:00:00Z", "soon": "2030-06-20T00:00:00Z", "far": "2031-01-01T00:00:00Z"},
    "gcp": {"past": "20300601t0000z", "soon": "20300620t0000z", "far": "20310101t0000z"},
}


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("cloud", CLOUDS)
class TestLifecycleRules:
    def tags(self, cloud, expiry=None, lifecycle="temporary"):
        tags = with_fact(cloud, "lifecycle", lifecycle)
        return tags if expiry is None else with_fact(cloud, "expires_at", expiry, base=tags)

    def test_expired_is_high(self, cloud, mode):
        unit, (finding,), _ = run(cloud, mode, self.tags(cloud, EXPIRY[cloud]["past"]))
        assert (finding["category"], finding["severity"], finding["rule"]) == ("lifecycle", "HIGH", "expired")
        assert "Expired 14 days ago" in finding["issue"]
        assert unit["result"] == names.NON_COMPLIANT

    def test_expiring_within_14_days_is_info_and_compliant(self, cloud, mode):
        unit, (finding,), _ = run(cloud, mode, self.tags(cloud, EXPIRY[cloud]["soon"]))
        assert (finding["severity"], finding["rule"]) == ("INFO", "expiring-soon")
        assert unit["result"] == names.COMPLIANT

    def test_far_expiry_has_no_finding(self, cloud, mode):
        assert run(cloud, mode, self.tags(cloud, EXPIRY[cloud]["far"]))[1] == []

    def test_expiry_is_read_on_a_persistent_unit(self, cloud, mode):
        assert run(cloud, mode, self.tags(cloud, EXPIRY[cloud]["past"], "persistent"))[0]["rules"] == ["expired"]

    def test_temporary_without_expiry_is_medium(self, cloud, mode):
        unit, (finding,), _ = run(cloud, mode, self.tags(cloud))
        assert (finding["category"], finding["severity"]) == ("lifecycle", "MEDIUM")
        assert finding["rule"] == "temporary-requires-expires-at"
        assert unit["result"] == names.NON_COMPLIANT

    def test_unreadable_expiry_is_a_finding(self, cloud, mode):
        unit, (finding,), _ = run(cloud, mode, self.tags(cloud, "next-week"))
        assert (finding["severity"], finding["rule"]) == ("MEDIUM", "expiry-unreadable")
        assert finding["evidence"][0]["value"] == "next-week"
        assert unit["result"] == names.NON_COMPLIANT


def test_gcp_does_not_read_an_iso_expiry():
    assert run("gcp", "units", with_fact("gcp", "expires_at", "2030-06-01"))[0]["rules"] == ["expiry-unreadable"]


class TestEvidence:
    @pytest.mark.parametrize("cloud", CLOUDS)
    def test_missing_fact_names_the_key_looked_for(self, cloud):
        _, (finding,), resolved = run(cloud, "units", without(cloud, "environment"))
        assert finding["evidence"] == [
            {"fact": "environment", "key": resolved.key("environment"), "value": None, "read_from": "absent"}
        ]
        assert finding["unit_id"] == UNIT_ID[cloud]
        assert finding["resource_id"] and finding["location"] == "example-location"

    def test_value_read_from_the_unit_or_the_resource(self):
        _, (finding,), _ = run("azure", "units", {**AZURE_FULL, "ManagedBy": "chef"})
        assert finding["evidence"][0]["read_from"] == "unit"
        _, (finding,), _ = run("azure", "resources", {**AZURE_FULL, "ManagedBy": "chef"})
        assert finding["evidence"][0]["read_from"] == "resource"
        _, (finding,), _ = run("azure", "resources", {}, inherited_tags={**AZURE_FULL, "ManagedBy": "chef"})
        assert finding["evidence"] == [{"fact": "managed_by", "key": "ManagedBy", "value": "chef", "read_from": "unit"}]

    @pytest.mark.parametrize("cloud", CLOUDS)
    def test_every_finding_of_the_truth_sets_has_evidence(self, cloud):
        resolved = contract.load(cloud)
        for mode in MODES:
            for finding in grade.GRADERS[mode](load_inventory(cloud), resolved, now=NOW)["findings"]:
                assert finding["rule"] and finding["evidence"], finding
                for entry in finding["evidence"]:
                    assert set(entry) == {"fact", "key", "value", "read_from"}
                    assert entry["read_from"] in {"resource", "unit", "registry", "absent"}
                    assert (entry["value"] is None) == (entry["read_from"] == "absent")


class TestFreshness:
    MISSING = staticmethod(lambda cloud: without(cloud, "environment"))

    @pytest.mark.parametrize("mode", MODES)
    @pytest.mark.parametrize("cloud", CLOUDS)
    def test_recent_unit_with_a_missing_fact_is_pending(self, cloud, mode):
        unit, (finding,), _ = run(cloud, mode, self.MISSING(cloud), created_at="2030-06-14T23:50:00Z")
        assert (unit["result"], unit["rules"]) == (names.PENDING, ["missing-required"])
        assert unit["reason"] == "created within 15 minutes"
        assert finding["rule"] == "missing-required"

    @pytest.mark.parametrize("created_at", [None, "", "not-a-time", "2030-06-14T23:44:00Z", "2030-06-15T00:05:00Z"])
    def test_unknown_or_older_creation_time_does_not_excuse(self, created_at):
        unit, _, _ = run("gcp", "units", self.MISSING("gcp"), created_at=created_at)
        assert unit["result"] == names.NON_COMPLIANT

    def test_lag_is_configurable(self):
        unit, _, _ = run("gcp", "units", self.MISSING("gcp"), created_at="2030-06-14T23:00:00Z", lag_minutes=90)
        assert unit["result"] == names.PENDING
        unit, _, _ = run("gcp", "units", self.MISSING("gcp"), created_at="2030-06-14T23:50:00Z", lag_minutes=0)
        assert unit["result"] == names.NON_COMPLIANT

    def test_recent_unit_is_not_excused_for_other_rules(self):
        tags = with_fact("gcp", "managed_by", "chef")
        assert run("gcp", "units", tags, created_at="2030-06-14T23:55:00Z")[0]["result"] == names.NON_COMPLIANT

    def test_recent_compliant_unit_is_compliant(self):
        assert run("gcp", "units", GCP_FULL, created_at="2030-06-14T23:55:00Z")[0]["result"] == names.COMPLIANT


class TestUnits:
    def test_azure_unit_is_the_resource_group_and_resources_are_information(self):
        records = [
            unit_record("azure", AZURE_FULL),
            member("azure", {}),
            member("azure", {"Project": "acme"}, resource_id="other"),
        ]
        result = grade.grade_units(inventory("azure", records), contract.load("azure"), now=NOW)
        (unit,) = result["units"]
        assert (unit["id"], unit["kind"], unit["result"]) == (UNIT_ID["azure"], "resource group", names.COMPLIANT)
        assert result["findings"] == []
        assert unit["resources"] == {
            "total": 2,
            "can_carry": 2,
            "cannot_carry": 0,
            "unclassified": 0,
            "with_own": 1,
            "without_own": 1,
            "own_facts": {"project": 1},
        }

    def test_a_resource_without_tags_in_a_non_compliant_group_is_not_a_finding(self):
        records = [unit_record("azure", {}), member("azure", {})]
        result = grade.grade_units(inventory("azure", records), contract.load("azure"), now=NOW)
        assert [f["resource_type"] for f in result["findings"]] == [RG_TYPE]

    def test_azure_tag_names_match_without_regard_to_case(self):
        unit, findings, _ = run("azure", "units", {k.lower(): v for k, v in AZURE_FULL.items()})
        assert findings == [] and unit["result"] == names.COMPLIANT

    def test_gcp_label_keys_are_case_sensitive(self):
        labels = {k: v for k, v in GCP_FULL.items() if k != "workload"} | {"Workload": "acme"}
        assert run("gcp", "units", labels)[1][0]["missing_facts"] == ["project"]

    def test_gcp_unit_is_the_project(self):
        records = [unit_record("gcp", GCP_FULL), member("gcp", {})]
        (unit,) = grade.grade_units(inventory("gcp", records), contract.load("gcp"), now=NOW)["units"]
        assert (unit["id"], unit["kind"], unit["result"]) == ("example-project", "project", names.COMPLIANT)
        assert unit["resources"]["total"] == 1

    def test_gcp_project_labels_come_from_the_sub_account_entry_when_its_record_is_absent(self):
        document = inventory("gcp", [member("gcp", {})])
        document["scopes"][0]["tags"] = without("gcp", "environment")
        result = grade.grade_units(document, contract.load("gcp"), now=NOW)
        (unit,) = result["units"]
        assert (unit["result"], unit["rules"]) == (names.NON_COMPLIANT, ["missing-required"])
        assert result["incomplete"] == []

    def test_resource_group_missing_from_the_inventory_is_not_evaluated(self):
        result = grade.grade_units(inventory("azure", [member("azure", AZURE_FULL)]), contract.load("azure"), now=NOW)
        (unit,) = result["units"]
        assert unit["result"] == names.NOT_EVALUATED
        assert unit["reason"] == "the resource group is not in the inventory"
        assert result["incomplete"][0]["reason"] == "the resource group of 1 resources is not in the inventory"

    def test_resource_outside_any_unit_is_counted(self):
        orphan = member("azure", {}, container=None)
        result = grade.grade_units(
            inventory("azure", [unit_record("azure", AZURE_FULL), orphan]), contract.load("azure")
        )
        assert result["resources_without_unit"] == {SUB: 1}

    @pytest.mark.parametrize("cloud", CLOUDS)
    def test_other_names_of_renamed_fields_read_the_same(self, cloud):
        def renamed(entry):
            entry = dict(entry)
            entry["sub_account_id"] = entry.pop("scope")
            if "container" in entry:
                entry["resource_group"] = entry.pop("container")
            return entry

        document = load_inventory(cloud)
        other = {
            "provider": document["platform"],
            "sub_accounts": [renamed(s) for s in document["scopes"]],
            "resources": [renamed(r) for r in document["resources"]],
            "unclassified_types": document.get("unclassified_types", {}),
        }
        resolved = contract.load(cloud)
        before = grade.grade_units(document, resolved, now=NOW)
        after = grade.grade_units(other, resolved, now=NOW)
        assert [(u["id"], u["result"], u["rules"]) for u in after["units"]] == [
            (u["id"], u["result"], u["rules"]) for u in before["units"]
        ]
        assert after["incomplete"] == []


class TestTypesThatCannotCarry:
    @pytest.mark.parametrize(
        ("resource_type", "expected"),
        [
            ("microsoft.compute/virtualmachines", CAN_CARRY),
            ("Microsoft.Compute/virtualMachines", CAN_CARRY),
            ("microsoft.storage/storageaccounts", CAN_CARRY),
            (RG_TYPE, CAN_CARRY),
            ("microsoft.authorization/roleassignments", CANNOT_CARRY),
            ("microsoft.storage/storageaccounts/blobservices", CANNOT_CARRY),
            ("example.provider/widgets", UNCLASSIFIED),
        ],
    )
    def test_azure_classes_from_the_tag_support_data(self, resource_type, expected):
        assert capability({"resource_type": resource_type}, PROFILES["azure"])[0] == expected

    @pytest.mark.parametrize(
        ("value", "expected"),
        [("labels+ownership", CAN_CARRY), ("ownership", CANNOT_CARRY), ("none", CANNOT_CARRY), ("x", CANNOT_CARRY)],
    )
    def test_gcp_classes_from_the_adapter_grade(self, value, expected):
        record = {"resource_type": "compute.googleapis.com/Instance", "grade": value}
        assert capability(record, PROFILES["gcp"])[0] == expected

    def test_gcp_type_the_adapter_lists_as_unclassified(self):
        record = {"resource_type": "example.googleapis.com/Widget", "grade": "labels+ownership"}
        assert capability(record, PROFILES["gcp"], frozenset({"example.googleapis.com/Widget"}))[0] == UNCLASSIFIED

    def test_gcp_record_without_a_grade_is_unclassified(self):
        assert capability({"resource_type": "compute.googleapis.com/Instance"}, PROFILES["gcp"])[0] == UNCLASSIFIED

    @pytest.mark.parametrize(
        ("cloud", "extra", "reason"),
        [
            ("azure", {"resource_type": "microsoft.authorization/roleassignments"}, "the type cannot carry tags"),
            (
                "azure",
                {"resource_type": "example.provider/widgets"},
                "unclassified type: nothing states whether it can carry tags",
            ),
            ("gcp", {"grade": "ownership"}, "the type has no labels field"),
            ("gcp", {"grade": "none"}, "a record, not a deployed resource"),
        ],
    )
    def test_such_a_resource_is_never_graded(self, cloud, extra, reason):
        unit, findings, _ = run(cloud, "resources", {}, **extra)
        assert (unit["result"], unit["reason"], unit["rules"], findings) == (names.NOT_EVALUATED, reason, [], [])

    def test_it_is_not_counted_as_lacking_tags_under_its_unit(self):
        records = [
            unit_record("azure", AZURE_FULL),
            member("azure", {}, resource_type="microsoft.authorization/roleassignments"),
            member("azure", {}, resource_type="example.provider/widgets", resource_id="w"),
        ]
        (unit,) = grade.grade_units(inventory("azure", records), contract.load("azure"), now=NOW)["units"]
        assert unit["result"] == names.COMPLIANT
        assert (unit["resources"]["cannot_carry"], unit["resources"]["unclassified"]) == (1, 1)
        assert unit["resources"]["without_own"] == 0


class TestIncompleteInput:
    def graded(self, cloud, document, **options):
        return grade.grade_units(document, contract.load(cloud), now=NOW, **options)

    def test_complete_input(self):
        assert self.graded("azure", inventory("azure", [unit_record("azure", {})]))["incomplete"] == []

    def test_asked_for_sub_account_that_was_not_returned(self):
        other = "11111111-1111-1111-1111-111111111111"
        result = self.graded(
            "azure", inventory("azure", [unit_record("azure", {})]), expected_scopes=[SUB.upper(), other]
        )
        assert result["incomplete"] == [
            {
                "scope": other,
                "reason": "asked for and not returned: no access to it, or it does not exist",
            }
        ]
        # the sub-account that was returned is still evaluated
        assert result["units"][0]["result"] == names.NON_COMPLIANT

    def test_nothing_returned(self):
        result = self.graded("azure", {"platform": "azure", "scopes": [], "resources": []})
        assert result["incomplete"][0]["scope"] is None

    def test_gcp_project_number_that_was_not_resolved(self):
        record = unit_record("gcp", GCP_FULL, scope="123456789012")
        document = inventory("gcp", [record])
        document["scopes"][0]["scope"] = "123456789012"
        result = self.graded("gcp", document)
        assert result["incomplete"] == [
            {"scope": "123456789012", "reason": "the project number could not be resolved to a project id"}
        ]
        (unit,) = result["units"]
        assert unit["result"] == names.NOT_EVALUATED
        assert unit["reason"].startswith("incomplete scan: the project number")
        assert result["findings"] == []

    def test_resources_of_a_sub_account_the_scan_did_not_list(self):
        record = unit_record("azure", {}, scope="11111111-1111-1111-1111-111111111111")
        result = self.graded("azure", inventory("azure", [record]))
        assert result["incomplete"][0]["reason"] == "resources were returned for a scope the scan did not list"
        assert result["units"][0]["result"] == names.NOT_EVALUATED

    def test_partial_result_reported_by_the_adapter(self):
        document = inventory("azure", [unit_record("azure", AZURE_FULL)])
        document["incomplete"] = [{"scope": SUB, "reason": "throttled after 3 pages"}]
        result = self.graded("azure", document)
        assert result["units"][0]["reason"] == "incomplete scan: throttled after 3 pages"
        document["incomplete"] = [{"reason": "permission denied on part of the scope"}]
        assert self.graded("azure", document)["incomplete"][0]["scope"] is None

    def test_per_resource_grading_is_blocked_the_same_way(self):
        document = inventory("azure", [member("azure", AZURE_FULL)])
        document["incomplete"] = [{"scope": SUB, "reason": "throttled"}]
        result = grade.grade_resources(document, contract.load("azure"), now=NOW)
        assert result["units"][0]["result"] == names.NOT_EVALUATED and result["findings"] == []


class TestPerResourceInheritance:
    def test_fact_on_the_resource_group_satisfies_the_resource(self):
        unit, findings, _ = run("azure", "resources", {"Project": "acme"}, inherited_tags=AZURE_FULL)
        assert findings == [] and unit["result"] == names.COMPLIANT
        assert (unit["facts"]["project"], unit["facts"]["environment"]) == ("direct", "inherited")

    def test_inherited_only_is_a_low_finding_when_asked(self):
        unit, (finding,), _ = run(
            "azure", "resources", {"Project": "acme"}, inherited_tags=AZURE_FULL, report_inherited=True
        )
        assert (finding["severity"], finding["rule"]) == ("LOW", "inherited-only")
        assert {e["read_from"] for e in finding["evidence"]} == {"unit"}
        assert unit["result"] == names.COMPLIANT

    def test_the_resource_value_wins_over_the_group_value(self):
        assert run("azure", "resources", {"ManagedBy": "chef"}, inherited_tags=AZURE_FULL)[0]["rules"] == [
            "invalid-value"
        ]

    def test_gcp_has_no_inheritance(self):
        assert run("gcp", "resources", {}, inherited_tags=GCP_FULL)[0]["rules"] == ["missing-required"]


def test_mapping_changes_what_is_graded(gcp_mapping):
    labels = {"app_name": "acme", "env": "dev", "owner": "platform", "cost_center": "cc-0000"}
    document = inventory("gcp", [unit_record("gcp", labels)])
    default = grade.grade_units(document, contract.load("gcp"), now=NOW)
    mapped = grade.grade_units(document, contract.load("gcp", mapping_path=gcp_mapping), now=NOW)
    assert default["units"][0]["result"] == names.NON_COMPLIANT
    assert (mapped["units"][0]["result"], mapped["findings"]) == (names.COMPLIANT, [])


def test_findings_are_ordered_by_severity():
    result = grade.grade_units(load_inventory("azure"), contract.load("azure"), now=NOW)
    order = [grade.SEVERITIES.index(f["severity"]) for f in result["findings"]]
    assert order == sorted(order)


def test_unknown_provider_names_the_known_ones():
    with pytest.raises(KeyError, match="azure, gcp"):
        profile_for("example-cloud")
