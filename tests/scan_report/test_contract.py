"""Contract loading for one platform, with and without a deployment mapping."""

from datetime import UTC, datetime

import pytest
import yaml

from goatcloud import contract


def test_azure_keys_and_required(azure_contract):
    assert (azure_contract.platform, azure_contract.carrier, azure_contract.unit) == ("azure", "tag", "resource-group")
    # the earlier names still read
    assert (azure_contract.cloud, azure_contract.kind) == ("azure", "tag")
    assert azure_contract.required == ["project", "environment", "managed_by", "lifecycle", "repository"]
    assert azure_contract.key("project") == "Project"
    assert azure_contract.key("expires_at") == "ExpiresAt"
    assert azure_contract.not_attached == []
    assert azure_contract.mapping_applied is False


def test_gcp_keys_and_required(gcp_contract):
    assert (gcp_contract.carrier, gcp_contract.unit) == ("label", "project")
    assert gcp_contract.key("managed_by") == "managed-by"
    # the fact project is written under workload: project names the Google Cloud project itself
    assert gcp_contract.key("project") == "workload"
    assert gcp_contract.required == ["project", "environment", "managed_by", "lifecycle", "owner"]
    # repository and contact are carried by the registry entry, not by a label
    assert [f.name for f in gcp_contract.not_attached] == ["repository", "contact"]
    assert gcp_contract.facts["repository"].carried_by == "registry-entry"
    assert gcp_contract.facts["contact"].carrier == "registry-entry"
    assert gcp_contract.facts["owner"].carrier == "label"
    assert "repository" not in gcp_contract.keyed


def test_allowed_values_and_pattern(azure_contract):
    assert azure_contract.facts["managed_by"].accepts("terraform")
    # accepted values compare without regard to case
    assert azure_contract.facts["managed_by"].accepts("Terraform")
    assert azure_contract.facts["managed_by"].accepts("ansible")
    assert azure_contract.facts["managed_by"].accepts("helm")
    assert not azure_contract.facts["managed_by"].accepts("chef")
    assert azure_contract.facts["lifecycle"].accepts("temporary")
    assert azure_contract.facts["repository"].accepts("git@git.example:acme/example-repo.git")
    assert not azure_contract.facts["repository"].accepts("example-repo")
    assert azure_contract.facts["project"].accepts("anything")


def test_rules(azure_contract):
    assert [(rule.id, rule.when_fact, rule.equals, rule.require) for rule in azure_contract.rules] == [
        ("temporary-requires-expires-at", "lifecycle", "temporary", ("expires_at",)),
        ("experiment-requires-expires-at", "lifecycle", "experiment", ("expires_at",)),
    ]


def test_expiry_encodings(azure_contract, gcp_contract):
    moment = datetime(2030, 12, 1, tzinfo=UTC)
    assert azure_contract.decode_expiry("2030-12-01T00:00:00Z") == moment
    # an expiry outside the encoding's pattern is refused, also when a lenient parser could read it
    with pytest.raises(ValueError, match="does not match"):
        azure_contract.decode_expiry("2030-12-01")
    assert gcp_contract.decode_expiry("20301201t0000z") == moment
    for bad in ("2030-12-01T00:00:00Z", "20301301t0000z", "20301201T0000Z", ""):
        with pytest.raises(ValueError):
            gcp_contract.decode_expiry(bad)
    with pytest.raises(ValueError):
        azure_contract.decode_expiry("soon")


def test_unknown_platform():
    with pytest.raises(contract.ContractError, match="unknown platform"):
        contract.load("example-cloud")


class TestMapping:
    def test_renames_keys_and_replaces_required_for_one_platform(self, gcp_mapping):
        mapped = contract.load("gcp", mapping_path=gcp_mapping)
        assert mapped.mapping_applied is True
        assert mapped.key("project") == "app_name"
        assert mapped.key("environment") == "env"
        assert mapped.key("cost_center") == "cost_center"
        assert mapped.key("owner") == "owner"
        assert mapped.required == ["project", "environment", "owner", "cost_center"]
        # another platform is untouched
        assert contract.load("azure", mapping_path=gcp_mapping).required == contract.load("azure").required

    def test_top_level_required_applies_to_every_platform(self, tmp_path):
        path = tmp_path / "mapping.yaml"
        path.write_text(yaml.safe_dump({"contract_schema_version": "1.0", "required": ["project", "owner"]}))
        assert contract.load("azure", mapping_path=path).required == ["project", "owner"]
        assert contract.load("gcp", mapping_path=path).required == ["project", "owner"]

    def test_example_mapping_of_the_repository_loads(self):
        example = contract.DEFAULT_CONTRACT_PATH.parent / "mapping.example.yaml"
        mapped = contract.load("gcp", mapping_path=example)
        assert mapped.key("project") == "app"
        # the example holds the environment as a resource manager tag and the contact as a label
        assert (mapped.key("environment"), mapped.facts["environment"].carrier) == ("stage", "resource-manager-tag")
        assert (mapped.key("contact"), mapped.facts["contact"].carrier) == ("team-channel", "label")
        assert [f.name for f in mapped.not_attached] == ["repository"]
        assert mapped.facts["environment"].allowed == ("dev", "staging", "live")

    def test_a_required_fact_carried_by_the_registry_entry_is_not_a_required_key(self, tmp_path):
        path = tmp_path / "mapping.yaml"
        mapping = {"contract_schema_version": "1.0", "renderings": {"gcp": {"required": ["project", "repository"]}}}
        path.write_text(yaml.safe_dump(mapping))
        assert contract.load("gcp", mapping_path=path).required == ["project"]

    def test_a_fact_moves_to_the_registry_entry(self, tmp_path):
        path = tmp_path / "mapping.yaml"
        mapping = {
            "contract_schema_version": "1.0",
            "renderings": {"azure": {"facts": {"contact": {"key": None, "carrier": "registry-entry"}}}},
        }
        path.write_text(yaml.safe_dump(mapping))
        mapped = contract.load("azure", mapping_path=path)
        assert mapped.key("contact") is None
        assert mapped.facts["contact"].carried_by == "registry-entry"

    def test_kind_is_read_as_the_earlier_name_of_carrier(self, tmp_path):
        path = tmp_path / "mapping.yaml"
        mapping = {
            "contract_schema_version": "1.0",
            "renderings": {"azure": {"facts": {"contact": {"key": None, "kind": "registry-entry"}}}},
        }
        path.write_text(yaml.safe_dump(mapping))
        assert contract.load("azure", mapping_path=path).facts["contact"].carrier == "registry-entry"

    def test_an_unknown_carrier_is_read_as_given(self, tmp_path):
        path = tmp_path / "mapping.yaml"
        mapping = {
            "contract_schema_version": "1.0",
            "renderings": {"gcp": {"facts": {"owner": {"carrier": "example-carrier"}}}},
        }
        path.write_text(yaml.safe_dump(mapping))
        assert contract.load("gcp", mapping_path=path).facts["owner"].carrier == "example-carrier"

    def test_key_prefix(self, tmp_path):
        path = tmp_path / "mapping.yaml"
        mapping = {"contract_schema_version": "1.0", "renderings": {"azure": {"key_prefix": "acme:"}}}
        path.write_text(yaml.safe_dump(mapping))
        mapped = contract.load("azure", mapping_path=path)
        assert (mapped.key_prefix, mapped.key("project")) == ("acme:", "acme:Project")

    @pytest.mark.parametrize(
        ("mapping", "message"),
        [
            ({"contract_schema_version": "9.9"}, "contract_schema_version"),
            ({"contract_schema_version": "1.0", "facts": {}}, "unknown entry"),
            ({"contract_schema_version": "1.0", "required": ["colour"]}, "unknown fact"),
            ({"contract_schema_version": "1.0", "renderings": {"example-cloud": {}}}, "unknown platform"),
            (
                {"contract_schema_version": "1.0", "renderings": {"gcp": {"facts": {"repository": {"key": "repo"}}}}},
                "has no key",
            ),
            (
                {"contract_schema_version": "1.0", "renderings": {"gcp": {"facts": {"project": {"encoding": "x"}}}}},
                "unknown encoding",
            ),
            (
                {"contract_schema_version": "1.0", "renderings": {"gcp": {"facts": {"project": {"carried_by": "x"}}}}},
                "expected key, carrier or encoding",
            ),
            ({"contract_schema_version": "1.0", "environment": {"names": ["dev"]}}, "mapping.environment"),
            ({"contract_schema_version": "1.0", "environment": {"allowed": ["dev"]}}, "protected_values"),
        ],
    )
    def test_invalid_mapping_is_refused(self, tmp_path, mapping, message):
        path = tmp_path / "mapping.yaml"
        path.write_text(yaml.safe_dump(mapping))
        with pytest.raises(contract.ContractError, match=message):
            contract.load("gcp", mapping_path=path)


def test_encoding_pattern_refuses_what_a_lenient_parser_would_read(tmp_path):
    document = yaml.safe_load(contract.DEFAULT_CONTRACT_PATH.read_text())
    document["encodings"]["iso8601"] = {
        "description": "A UTC timestamp ending in Z.",
        "pattern": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?Z$",
    }
    path = tmp_path / "contract.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False))
    strict = contract.load("azure", path)
    assert strict.decode_expiry("2030-12-01T00:00:00Z") == datetime(2030, 12, 1, tzinfo=UTC)
    assert strict.encodings["iso8601"] == "A UTC timestamp ending in Z."
    for refused in ("2030-12-01", "2030-12-01T00:00:00+00:00", "2030-12-01 00:00:00Z"):
        with pytest.raises(ValueError, match="does not match"):
            strict.decode_expiry(refused)


def test_keyless_fact_written_with_kind_is_not_attached(tmp_path):
    """A contract file that still says `kind` (the earlier name of `carrier`) reads the same."""
    document = yaml.safe_load(contract.DEFAULT_CONTRACT_PATH.read_text())
    document["renderings"]["gcp"]["facts"]["contact"] = {"key": None, "kind": "registry-entry"}
    path = tmp_path / "contract.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False))
    resolved = contract.load("gcp", path)
    assert resolved.facts["contact"].carried_by == "registry-entry"
    assert "contact" not in resolved.keyed
