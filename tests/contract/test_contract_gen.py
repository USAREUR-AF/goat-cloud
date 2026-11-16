"""Tests for scripts/contract-gen: the contract, its generated copies and the drift check."""

import copy
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys

import pytest
import yaml

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
_SCRIPT = os.path.join(_ROOT, "scripts", "contract-gen")


def _load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


gen = _load(_SCRIPT, "contract_gen")

ALL_TARGET_PATHS = [target["path"] for target in gen.load_targets()]
_TARGETS = {}
for _target in gen.load_targets():
    if _target["platform"] == "aws":
        _TARGETS.setdefault(_target["kind"], _target["path"])
HCL_TAGS_PATH = _TARGETS["hcl-locals"]
HCL_REGISTRY_PATH = _TARGETS["hcl-iac-type-variable"]
PYTHON_PATH = _TARGETS["python-constants"]
JSON_AWS_PATH = _TARGETS["published-json"]
LINT_PATH = _TARGETS["python-block"]
MANAGED_BY = [
    "terraform",
    "opentofu",
    "pulumi",
    "serverless",
    "cdk",
    "cloudformation",
    "sam",
    "copilot",
    "bicep",
    "arm",
    "infrastructure-manager",
    "ansible",
    "helm",
    "manual",
]
FACTS = [
    "project",
    "environment",
    "managed_by",
    "lifecycle",
    "expires_at",
    "repository",
    "owner",
    "contact",
    "cost_center",
    "protected",
    "schedule",
]


@pytest.fixture
def contract():
    return gen.load_contract()


@pytest.fixture
def tree(tmp_path):
    """A copy of the contract and every generated file, free to modify."""
    for rel in (gen.CONTRACT_PATH, gen.TARGETS_PATH, *ALL_TARGET_PATHS):
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(os.path.join(_ROOT, rel), dest)
    return tmp_path


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------


class TestContract:
    def test_loads_and_validates(self, contract):
        assert contract["schema_version"] == "1.0"

    def test_facts(self, contract):
        assert list(contract["facts"]) == FACTS
        required = [name for name, fact in contract["facts"].items() if fact["required"]]
        assert required == ["project", "environment", "managed_by", "lifecycle", "repository"]

    def test_allowed_values(self, contract):
        assert contract["facts"]["managed_by"]["allowed"] == MANAGED_BY
        assert contract["facts"]["lifecycle"]["allowed"] == ["persistent", "temporary", "experiment", "deprecated"]

    @pytest.mark.parametrize("lifecycle", ["temporary", "experiment"])
    def test_lifecycle_requires_expires_at(self, contract, lifecycle):
        rule = next(r for r in contract["rules"] if r["id"] == f"{lifecycle}-requires-expires-at")
        assert rule["when"] == {"fact": "lifecycle", "equals": lifecycle}
        assert rule["require"] == ["expires_at"]

    def test_three_platforms(self, contract):
        assert set(contract["renderings"]) == {"aws", "azure", "gcp"}

    def test_units(self, contract):
        units = {platform: rendering["unit"] for platform, rendering in contract["renderings"].items()}
        assert units == {"aws": "resource", "azure": "resource-group", "gcp": "project"}
        for platform in units:
            assert gen.resolve(contract, platform)["unit"] == units[platform]

    def test_no_fact_states_a_kind(self, contract):
        """The frozen file names what holds a fact `carrier`; `kind` appears nowhere in it."""
        for rendering in contract["renderings"].values():
            assert "kind" not in rendering
            assert all("kind" not in entry for entry in rendering["facts"].values())

    def test_owner_and_contact_are_never_a_person(self, contract):
        assert "never a person" in contract["facts"]["owner"]["description"]
        assert "never a person" in contract["facts"]["contact"]["description"]

    def test_azure_keys_equal_aws_keys(self, contract):
        assert gen.resolve(contract, "azure")["keys"] == gen.resolve(contract, "aws")["keys"]

    def test_gcp_rendering(self, contract):
        gcp = gen.resolve(contract, "gcp")
        assert contract["renderings"]["gcp"]["carrier"] == "label"
        assert {k: gcp["keys"][k] for k in ("project", "environment", "managed_by", "lifecycle", "expires_at")} == {
            # `project` on Google Cloud names the Google Cloud project, so the fact is written under workload
            "project": "workload",
            "environment": "environment",
            "managed_by": "managed-by",
            "lifecycle": "lifecycle",
            "expires_at": "expires-at",
        }
        assert "repository" not in gcp["keys"]
        assert gcp["required_keys"] == ["workload", "environment", "managed-by", "lifecycle", "owner"]
        assert contract["renderings"]["gcp"]["facts"]["expires_at"]["encoding"] == "compact-utc"
        assert contract["renderings"]["gcp"]["facts"]["owner"]["encoding"] == "gcp-label"
        for fact in ("repository", "contact"):
            assert contract["renderings"]["gcp"]["facts"][fact] == {"key": None, "carrier": "registry-entry"}
            assert gcp["carriers"][fact] == "registry-entry"
            assert fact not in gcp["keys"]
        assert gcp["required_facts"] == ["project", "environment", "managed_by", "lifecycle", "owner"]

    def test_gcp_expiry_example_fits_a_label_value(self, contract):
        pattern = contract["renderings"]["gcp"]["value_pattern"]
        assert re.match(pattern, "20261201t0000z")
        assert not re.match(pattern, "2026-12-01T00:00:00Z")


class TestValidation:
    def _broken(self, contract, mutate):
        broken = copy.deepcopy(contract)
        mutate(broken)
        with pytest.raises(gen.ContractError) as err:
            gen.validate_contract(broken)
        return str(err.value)

    def test_unsupported_schema_version(self, contract):
        assert "schema_version" in self._broken(contract, lambda c: c.update(schema_version="9.9"))

    def test_fact_without_rendering(self, contract):
        message = self._broken(contract, lambda c: c["renderings"]["aws"]["facts"].pop("owner"))
        assert "renderings.aws.facts" in message and "owner" in message

    def test_rendering_of_unknown_fact(self, contract):
        def mutate(c):
            c["renderings"]["aws"]["facts"]["color"] = {"key": "Color", "encoding": "text"}

        assert "unknown fact" in self._broken(contract, mutate)

    def test_duplicate_key(self, contract):
        def mutate(c):
            c["renderings"]["aws"]["facts"]["owner"]["key"] = "Project"

        assert "more than one fact" in self._broken(contract, mutate)

    def test_label_key_outside_platform_limits(self, contract):
        def mutate(c):
            c["renderings"]["gcp"]["facts"]["owner"]["key"] = "Owner"

        assert "key_pattern" in self._broken(contract, mutate)

    def test_allowed_value_outside_platform_limits(self, contract):
        assert "value_pattern" in self._broken(contract, lambda c: c["facts"]["lifecycle"]["allowed"].append("Kept"))

    def test_unknown_encoding(self, contract):
        def mutate(c):
            c["renderings"]["aws"]["facts"]["owner"]["encoding"] = "rot13"

        assert "unknown encoding" in self._broken(contract, mutate)

    def test_keyless_fact_needs_carrier(self, contract):
        def mutate(c):
            c["renderings"]["gcp"]["facts"]["repository"] = {"key": None}

        assert "registry-entry" in self._broken(contract, mutate)

    def test_registry_entry_fact_has_no_key(self, contract):
        def mutate(c):
            c["renderings"]["gcp"]["facts"]["repository"] = {"key": "repository", "carrier": "registry-entry"}

        assert "key: null" in self._broken(contract, mutate)

    def test_unit_is_required(self, contract):
        assert "renderings.aws.unit" in self._broken(contract, lambda c: c["renderings"]["aws"].pop("unit"))

    def test_rendering_required_names_known_facts(self, contract):
        message = self._broken(contract, lambda c: c["renderings"]["gcp"].update(required=["colour"]))
        assert "renderings.gcp.required" in message


class TestOpenLists:
    """A carrier or an entry a later minor version adds is a note, never an error."""

    def test_unknown_carrier_is_a_note(self, contract):
        changed = copy.deepcopy(contract)
        changed["renderings"]["gcp"]["facts"]["owner"]["carrier"] = "example-carrier"
        notes = []
        gen.validate_contract(changed, notes)
        assert any("unknown carrier 'example-carrier'" in note for note in notes)
        assert gen.resolve(changed, "gcp")["carriers"]["owner"] == "example-carrier"

    def test_known_carriers_are_not_noted(self, contract):
        changed = copy.deepcopy(contract)
        changed["renderings"]["gcp"]["facts"]["owner"]["carrier"] = "annotation"
        notes = []
        gen.validate_contract(changed, notes)
        assert notes == []

    def test_unknown_entry_is_a_note(self, contract):
        changed = copy.deepcopy(contract)
        changed["renderings"]["aws"]["colour"] = "blue"
        notes = []
        gen.validate_contract(changed, notes)
        assert notes == ["renderings.aws: unknown entry colour is ignored"]

    def test_key_prefix(self, contract):
        changed = copy.deepcopy(contract)
        changed["renderings"]["azure"]["key_prefix"] = "acme:"
        resolved = gen.resolve(changed, "azure")
        assert resolved["keys"]["project"] == "acme:Project"
        assert gen.published_contract(resolved)["key_prefix"] == "acme:"

    def test_a_rendering_overrides_required(self, contract):
        assert gen.resolve(contract, "aws")["required_facts"] == [
            "project",
            "environment",
            "managed_by",
            "lifecycle",
            "repository",
        ]
        assert "owner" in gen.resolve(contract, "gcp")["required_facts"]


class TestValidationOfCarriersAndValues:
    def _broken(self, contract, mutate):
        broken = copy.deepcopy(contract)
        mutate(broken)
        with pytest.raises(gen.ContractError) as err:
            gen.validate_contract(broken)
        return str(err.value)

    def test_keyless_carrier_other_than_registry_entry(self, contract):
        changed = copy.deepcopy(contract)
        changed["renderings"]["aws"]["facts"]["schedule"] = {"key": None, "carrier": "inventory-record"}
        gen.validate_contract(changed)
        assert "schedule" not in gen.resolve(changed, "aws")["keys"]

    def test_encoding_needs_a_description(self, contract):
        assert "encodings.text" in self._broken(contract, lambda c: c["encodings"].update(text="as written"))

    def test_protected_environment_outside_the_allowed_list(self, contract):
        assert "protected_values" in self._broken(contract, lambda c: c["facts"]["environment"].update(allowed=["dev"]))

    def test_rule_on_unknown_value(self, contract):
        def mutate(c):
            c["rules"][0]["when"]["equals"] = "eternal"

        assert "rules[0]" in self._broken(contract, mutate)

    def test_bad_pattern(self, contract):
        assert "pattern" in self._broken(contract, lambda c: c["facts"]["repository"].update(pattern="("))


# ---------------------------------------------------------------------------
# Generated copies
# ---------------------------------------------------------------------------


class TestGeneratedCopies:
    def test_repository_copies_are_current(self, contract):
        out = io.StringIO()
        assert gen.check(contract, out=out) == [], out.getvalue()

    def test_published_json(self, contract):
        published = json.loads(gen.render_json(gen.resolve(contract, "aws")))
        assert published["version"] == "1.0"
        assert published["required_tags"] == ["Project", "Environment", "ManagedBy", "Lifecycle", "Repository"]
        assert published["valid_managed_by"] == contract["facts"]["managed_by"]["allowed"]
        assert published["valid_lifecycle"] == contract["facts"]["lifecycle"]["allowed"]
        assert published["tag_validation"]["ManagedBy"] == {"enum": published["valid_managed_by"]}
        assert published["tag_validation"]["Lifecycle"] == {"enum": published["valid_lifecycle"]}
        assert published["tag_validation"]["Repository"] == {"pattern": contract["facts"]["repository"]["pattern"]}
        assert published["tag_validation"]["ExpiresAt"] == {"pattern": published["patterns"]["expires_at"]}
        assert published["keys"]["expires_at"] == "ExpiresAt"

    def test_python_constants(self):
        import compliance_contract
        import compliance_schema

        with open(os.path.join(_ROOT, JSON_AWS_PATH), encoding="utf-8") as f:
            published = json.load(f)
        assert published["required_tags"] == compliance_contract.REQUIRED_TAGS
        assert published["valid_managed_by"] == compliance_contract.IAC_TYPES
        assert frozenset(published["valid_managed_by"]) == compliance_contract.VALID_MANAGED_BY
        assert frozenset(published["valid_lifecycle"]) == compliance_contract.VALID_LIFECYCLE
        assert published["tag_validation"]["Repository"]["pattern"] == compliance_contract.REPOSITORY_PATTERN
        assert published["keys"] == compliance_contract.TAG_KEYS
        for name in ("REQUIRED_TAGS", "IAC_TYPES", "VALID_MANAGED_BY", "VALID_LIFECYCLE", "REPOSITORY_PATTERN"):
            assert getattr(compliance_schema, name) is getattr(compliance_contract, name)

    def test_registry_accepts_every_iac_tool_and_not_manual(self, contract):
        hcl = gen.render_hcl_registry(gen.resolve(contract, "aws"))
        listed = re.search(r"contains\(\[(.*?)\]", hcl).group(1)
        assert json.loads(f"[{listed}]") == MANAGED_BY[:-1]

    def test_lint_fallback_is_the_published_contract(self, contract):
        lint = _load(os.path.join(_ROOT, LINT_PATH), "compliance_lint")
        assert gen.published_contract(gen.resolve(contract, "aws")) == lint._DEFAULT_CONTRACT

    def test_generated_files_carry_the_header(self, contract):
        files = gen.generated_files(contract)
        for rel in (HCL_TAGS_PATH, HCL_REGISTRY_PATH, PYTHON_PATH):
            assert "GENERATED by scripts/contract-gen" in files[rel]
            assert "Do not edit" in files[rel]
        assert gen.LINT_BEGIN in files[LINT_PATH]
        assert "Do not edit" in gen.LINT_BEGIN

    def test_generated_python_is_ruff_formatted(self, contract, tmp_path):
        if not shutil.which("ruff"):
            pytest.skip("ruff not installed")
        path = tmp_path / "compliance_contract.py"
        path.write_text(gen.render_python(gen.resolve(contract, "aws")), encoding="utf-8")
        config = os.path.join(_ROOT, "ruff.toml")
        for command in (["ruff", "format", "--check"], ["ruff", "check"]):
            result = subprocess.run([*command, "--config", config, str(path)], capture_output=True, text=True)
            assert result.returncode == 0, result.stdout + result.stderr

    def test_generated_hcl_is_terraform_formatted(self, contract, tmp_path):
        if not shutil.which("terraform"):
            pytest.skip("terraform not installed")
        aws = gen.resolve(contract, "aws")
        (tmp_path / "tags.tf").write_text(gen.render_hcl_tags(aws), encoding="utf-8")
        (tmp_path / "registry.tf").write_text(gen.render_hcl_registry(aws), encoding="utf-8")
        result = subprocess.run(["terraform", "fmt", "-check", "-diff", str(tmp_path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr


class TestTargets:
    """contract/targets.yaml decides which files are generated."""

    def _add(self, tree, **target):
        path = tree / gen.TARGETS_PATH
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data["targets"].append(target)
        path.write_text(yaml.safe_dump(data), encoding="utf-8")

    def test_every_listed_file_is_generated(self, contract):
        assert list(gen.generated_files(contract)) == [t["path"] for t in gen.load_targets()]

    @pytest.mark.parametrize("platform, key", [("azure", "ManagedBy"), ("gcp", "managed-by")])
    def test_a_platform_module_is_added_by_listing_it(self, tree, platform, key):
        rel = f"modules/{platform}/another-module/contract.generated.tf"
        self._add(tree, path=rel, kind="hcl-locals", platform=platform)
        contract = gen.load_contract(str(tree / gen.CONTRACT_PATH))

        assert gen.check(contract, root=str(tree), out=io.StringIO()) == [rel]
        assert gen.write(contract, root=str(tree), out=io.StringIO()) == [rel]
        text = (tree / rel).read_text(encoding="utf-8")
        assert f'managed_by  = "{key}"' in text
        assert '"infrastructure-manager", "ansible", "helm", "manual"]' in text
        assert gen.check(contract, root=str(tree), out=io.StringIO()) == []

    @pytest.mark.parametrize(
        "target, fragment",
        [
            ({"path": "x.tf", "kind": "hcl", "platform": "aws"}, "kind"),
            ({"path": "../x.tf", "kind": "hcl-locals", "platform": "aws"}, "inside the repository"),
            ({"path": "/tmp/x.tf", "kind": "hcl-locals", "platform": "aws"}, "inside the repository"),
            ({"path": "scripts/compliance-lint", "kind": "hcl-locals", "platform": "aws"}, "listed twice"),
            ({"path": "x.tf", "kind": "hcl-locals"}, "expected path, kind, platform"),
            ({"path": "x.tf", "kind": "hcl-locals", "platform": "moon"}, "unknown platform"),
            (
                {"path": "x.tf", "kind": "hcl-locals", "platform": "aws", "default": "terraform"},
                "unknown entry default",
            ),
            (
                {"path": "x.tf", "kind": "hcl-iac-type-variable", "platform": "aws", "default": "manual"},
                "not an IaC tool",
            ),
        ],
    )
    def test_invalid_target_is_refused(self, tree, target, fragment):
        self._add(tree, **target)
        contract = gen.load_contract(str(tree / gen.CONTRACT_PATH))
        with pytest.raises(gen.ContractError) as err:
            gen.generated_files(contract, root=str(tree))
        assert fragment in str(err.value)

    def test_cloud_is_read_as_the_earlier_name_of_platform(self, tree):
        rel = "modules/azure/another-module/contract.generated.tf"
        self._add(tree, path=rel, kind="hcl-locals", cloud="azure")
        contract = gen.load_contract(str(tree / gen.CONTRACT_PATH))
        assert rel in gen.generated_files(contract, root=str(tree))


class TestEveryPlatform:
    """The contract reaches the tag or label module and the registry module of each platform."""

    MODULES = {  # noqa: RUF012
        "aws": ("modules/aws/compliance-tags", "modules/aws/iac-registry"),
        "azure": ("modules/azure/compliance-tags", "modules/azure/iac-registry"),
        "gcp": ("modules/gcp/compliance-labels", "modules/gcp/iac-registry"),
    }

    @pytest.mark.parametrize("platform", ["aws", "azure", "gcp"])
    def test_targets(self, platform):
        tags, registry = self.MODULES[platform]
        targets = {(t["path"], t["kind"]) for t in gen.load_targets() if t["platform"] == platform}
        assert (f"{tags}/contract.generated.tf", "hcl-locals") in targets
        assert (f"{registry}/iac_type.generated.tf", "hcl-iac-type-variable") in targets
        assert (f"contract/dist/compliance-contract.{platform}.json", "published-json") in targets

    @pytest.mark.parametrize("platform", ["aws", "azure", "gcp"])
    def test_no_hand_written_value_list_in_the_modules(self, platform):
        for module in self.MODULES[platform]:
            for name in sorted(os.listdir(os.path.join(_ROOT, module))):
                if not name.endswith(".tf") or name.endswith(".generated.tf"):
                    continue
                with open(os.path.join(_ROOT, module, name), encoding="utf-8") as f:
                    text = f.read()
                assert '"pulumi"' not in text, f"{module}/{name} lists managed-by values by hand"
                assert '"deprecated"' not in text, f"{module}/{name} lists lifecycle values by hand"
                assert 'variable "iac_type"' not in text.replace('# variable "iac_type"', "")

    @pytest.mark.parametrize("platform", ["aws", "azure", "gcp"])
    def test_one_managed_by_list_and_registry_list(self, contract, platform):
        resolved = gen.resolve(contract, platform)
        assert resolved["managed_by"] == MANAGED_BY
        assert resolved["iac_tools"] == MANAGED_BY[:-1]
        path = os.path.join(_ROOT, "contract", "dist", f"compliance-contract.{platform}.json")
        with open(path, encoding="utf-8") as f:
            assert json.load(f)["allowed"]["managed_by"] == MANAGED_BY

    def test_gcp_published_contract(self, contract):
        published = gen.published_contract(gen.resolve(contract, "gcp"))
        assert published["required_facts"] == ["project", "environment", "managed_by", "lifecycle", "owner"]
        assert (published["platform"], published["unit"]) == ("gcp", "project")
        assert "repository" not in published["keys"] and "contact" not in published["keys"]
        assert published["carriers"]["repository"] == published["carriers"]["contact"] == "registry-entry"
        assert published["patterns"]["expires_at"] == r"^\d{8}t\d{4}z$"


class TestCompactUtcEncoding:
    """adapters/gcp encodes and decodes the expiry exactly as the contract's encoding text states."""

    def test_adapter_agrees_with_the_contract_text(self, contract):
        from datetime import UTC, datetime

        from adapters.gcp import inventory

        assert contract["renderings"]["gcp"]["facts"]["expires_at"]["encoding"] == "compact-utc"
        text = contract["encodings"]["compact-utc"]["description"]
        form, example = re.fullmatch(r"A UTC timestamp as lowercase (\S+), for example (\S+)\.", text).groups()
        assert form == "yyyymmddthhmmz"
        iso_example = re.search(r"for example (\S+)\.$", contract["encodings"]["iso8601"]["description"]).group(1)
        moment = datetime.fromisoformat(iso_example.replace("Z", "+00:00"))

        assert inventory.decode_expires_at(example) == moment == datetime(2026, 12, 1, tzinfo=UTC)
        assert inventory.encode_expires_at(moment) == example
        assert len(example) == len(form) and example == example.lower()
        assert re.match(contract["renderings"]["gcp"]["value_pattern"], example)
        assert re.match(contract["encodings"]["compact-utc"]["pattern"], example)
        assert re.match(contract["encodings"]["iso8601"]["pattern"], iso_example)
        assert not re.match(contract["encodings"]["iso8601"]["pattern"], example)


class TestLambdaBuildsCarryTheContract:
    """Whatever copies compliance_schema.py into a Lambda directory also copies compliance_contract.py."""

    @pytest.mark.parametrize("module", ["compliance-scanner", "compliance-dashboard", "hunterkiller"])
    def test_terraform_build_step(self, module):
        with open(os.path.join(_ROOT, "modules", "aws", module, "main.tf"), encoding="utf-8") as f:
            text = f.read()
        commands = [line for line in text.splitlines() if "compliance_schema.py' '${path.module}/lambda/" in line]
        assert commands, "build step not found"
        for line in commands:
            assert "/../shared/compliance_contract.py' '${path.module}/lambda/compliance_contract.py'" in line
        assert "filesha256(local.shared_contract_path)" in text

    @pytest.mark.parametrize("script", ["local-dashboard", "test-dashboard"])
    def test_local_scripts(self, script):
        path = os.path.join(_ROOT, "scripts", script)
        if not os.path.isfile(path):
            pytest.skip(f"scripts/{script} is not in this tree")
        with open(path, encoding="utf-8") as f:
            assert "compliance_contract.py" in f.read()

    def test_copies_are_ignored_by_git(self):
        with open(os.path.join(_ROOT, ".gitignore"), encoding="utf-8") as f:
            ignored = f.read().splitlines()
        for module in ("compliance-scanner", "compliance-dashboard", "hunterkiller"):
            assert f"modules/aws/{module}/lambda/compliance_contract.py" in ignored


# ---------------------------------------------------------------------------
# --check and write
# ---------------------------------------------------------------------------


class TestCheck:
    def _contract(self, tree):
        return gen.load_contract(str(tree / gen.CONTRACT_PATH))

    def test_clean_tree(self, tree):
        assert gen.check(self._contract(tree), root=str(tree), out=io.StringIO()) == []

    @pytest.mark.parametrize("rel", ALL_TARGET_PATHS)
    def test_hand_edit_of_a_generated_file_is_reported(self, tree, rel):
        path = tree / rel
        path.write_text(path.read_text(encoding="utf-8").replace("cloudformation", "cloudformatoin"), encoding="utf-8")
        out = io.StringIO()
        assert gen.check(self._contract(tree), root=str(tree), out=out) == [rel]
        assert f"DIFFERS  {rel}" in out.getvalue()

    def test_missing_generated_file_is_reported(self, tree):
        (tree / PYTHON_PATH).unlink()
        out = io.StringIO()
        assert gen.check(self._contract(tree), root=str(tree), out=out) == [PYTHON_PATH]
        assert "MISSING" in out.getvalue()

    def test_contract_change_marks_every_copy_stale_until_written(self, tree):
        path = tree / gen.CONTRACT_PATH
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data["facts"]["managed_by"]["allowed"].append("crossplane")
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        contract = self._contract(tree)

        assert gen.check(contract, root=str(tree), out=io.StringIO()) == ALL_TARGET_PATHS
        assert gen.write(contract, root=str(tree), out=io.StringIO()) == ALL_TARGET_PATHS
        assert gen.check(contract, root=str(tree), out=io.StringIO()) == []
        for rel in ALL_TARGET_PATHS:
            assert "crossplane" in (tree / rel).read_text(encoding="utf-8")

    def test_write_replaces_only_the_marked_block_of_the_lint_script(self, tree):
        path = tree / LINT_PATH
        before = path.read_text(encoding="utf-8")
        path.write_text(before.replace('"deprecated"', '"retired"'), encoding="utf-8")
        gen.write(self._contract(tree), root=str(tree), out=io.StringIO())
        assert path.read_text(encoding="utf-8") == before

    def test_lint_script_without_markers_is_an_error(self, tree):
        path = tree / LINT_PATH
        path.write_text(path.read_text(encoding="utf-8").replace(gen.LINT_END, ""), encoding="utf-8")
        with pytest.raises(gen.ContractError):
            gen.check(self._contract(tree), root=str(tree), out=io.StringIO())


class TestCommandLine:
    def _run(self, *args):
        return subprocess.run([sys.executable, _SCRIPT, *args], capture_output=True, text=True)

    def test_check_passes_on_the_repository(self):
        result = self._run("--check")
        assert result.returncode == 0, result.stdout + result.stderr

    def test_check_exits_1_on_drift(self, tree):
        script = tree / "scripts" / "contract-gen"
        shutil.copy2(_SCRIPT, script)
        target = tree / JSON_AWS_PATH
        target.write_text(target.read_text(encoding="utf-8").replace('"sam",', ""), encoding="utf-8")
        result = subprocess.run([sys.executable, str(script), "--check"], capture_output=True, text=True)
        assert result.returncode == 1
        assert JSON_AWS_PATH in result.stderr
        assert "Run scripts/contract-gen" in result.stderr

    def test_invalid_contract_exits_2(self, tmp_path):
        bad = tmp_path / "contract.yaml"
        bad.write_text("schema_version: '0.1'\n", encoding="utf-8")
        result = self._run("--check", "--contract", str(bad))
        assert result.returncode == 2
        assert "schema_version" in result.stderr

    def test_mapping_needs_out_dir(self):
        result = self._run("--mapping", os.path.join(_ROOT, "contract", "mapping.example.yaml"))
        assert result.returncode == 2

    def test_out_dir_writes_each_platform(self, tmp_path):
        result = self._run("--out-dir", str(tmp_path))
        assert result.returncode == 0, result.stderr
        assert sorted(os.listdir(tmp_path)) == [
            "compliance-contract.aws.json",
            "compliance-contract.azure.json",
            "compliance-contract.gcp.json",
        ]
        with open(tmp_path / "compliance-contract.aws.json", encoding="utf-8") as f:
            unmapped = f.read()
        with open(os.path.join(_ROOT, JSON_AWS_PATH), encoding="utf-8") as f:
            assert unmapped == f.read()


# ---------------------------------------------------------------------------
# Deployment mapping
# ---------------------------------------------------------------------------


class TestMapping:
    def _mapping(self, **entries):
        return {"contract_schema_version": "1.0", **entries}

    def test_example_mapping(self, contract):
        with open(os.path.join(_ROOT, "contract", "mapping.example.yaml"), encoding="utf-8") as f:
            mapped = gen.apply_mapping(contract, yaml.safe_load(f))
        aws = gen.resolve(mapped, "aws")
        gcp = gen.resolve(mapped, "gcp")
        assert aws["keys"]["cost_center"] == "BillingCode"
        assert aws["required_keys"] == ["Project", "Environment", "ManagedBy", "Lifecycle", "Repository", "Owner"]
        assert gcp["keys"]["project"] == "app"
        assert gcp["keys"]["environment"] == "stage"
        assert gcp["required_keys"] == ["app", "stage", "managed-by", "owner", "billing_code"]
        # the top-level required set replaces the required set of every platform
        assert gen.resolve(mapped, "azure")["required_facts"] == gen.resolve(mapped, "aws")["required_facts"]
        assert gcp["allowed"]["environment"] == ["dev", "staging", "live"]
        assert gcp["protected_values"] == {"environment": ["live"]}

    def test_mapping_renders_a_mix_of_carriers(self, contract):
        """One platform holds facts as labels, a resource manager tag and registry entry fields."""
        with open(os.path.join(_ROOT, "contract", "mapping.example.yaml"), encoding="utf-8") as f:
            mapped = gen.apply_mapping(contract, yaml.safe_load(f))
        published = gen.published_contract(gen.resolve(mapped, "gcp"))
        assert published["carriers"] == {
            "project": "label",
            "environment": "resource-manager-tag",
            "managed_by": "label",
            "lifecycle": "label",
            "expires_at": "label",
            "repository": "registry-entry",
            "owner": "label",
            "contact": "label",
            "cost_center": "label",
            "protected": "label",
            "schedule": "label",
        }
        assert published["keys"]["environment"] == "stage"
        assert published["keys"]["contact"] == "team-channel"
        assert "repository" not in published["keys"]
        hcl = gen.render_hcl_tags(gen.resolve(mapped, "gcp"))
        assert 'environment = "resource-manager-tag"' in hcl
        assert 'repository  = "registry-entry"' in hcl

    def test_resource_manager_tag_key_is_not_held_to_the_label_limits(self, contract):
        mapping = self._mapping(
            renderings={"gcp": {"facts": {"environment": {"key": "Environment", "carrier": "resource-manager-tag"}}}}
        )
        assert gen.resolve(gen.apply_mapping(contract, mapping), "gcp")["keys"]["environment"] == "Environment"

    def test_mapping_moves_a_fact_to_the_registry_entry(self, contract):
        mapping = self._mapping(renderings={"aws": {"facts": {"contact": {"carrier": "registry-entry"}}}})
        aws = gen.resolve(gen.apply_mapping(contract, mapping), "aws")
        assert "contact" not in aws["keys"] and aws["carriers"]["contact"] == "registry-entry"

    def test_mapping_reads_kind_as_the_earlier_name_of_carrier(self, contract):
        mapping = self._mapping(renderings={"aws": {"facts": {"contact": {"kind": "registry-entry"}}}})
        assert gen.resolve(gen.apply_mapping(contract, mapping), "aws")["carriers"]["contact"] == "registry-entry"

    def test_mapping_sets_a_key_prefix(self, contract):
        mapping = self._mapping(renderings={"azure": {"key_prefix": "acme:"}})
        assert gen.resolve(gen.apply_mapping(contract, mapping), "azure")["keys"]["owner"] == "acme:Owner"

    def test_mapping_sets_environment_names(self, contract):
        mapping = self._mapping(environment={"allowed": ["dev", "live"], "protected_values": ["live"]})
        for platform in ("aws", "azure", "gcp"):
            published = gen.published_contract(gen.resolve(gen.apply_mapping(contract, mapping), platform))
            assert published["allowed"]["environment"] == ["dev", "live"]
            assert published["protected_values"]["environment"] == ["live"]

    def test_mapping_leaves_the_contract_untouched(self, contract):
        before = copy.deepcopy(contract)
        gen.apply_mapping(contract, self._mapping(required=["project"]))
        assert contract == before

    def test_renamed_key_carries_its_validation(self, contract):
        mapping = self._mapping(renderings={"aws": {"facts": {"managed_by": {"key": "Tool"}}}})
        published = gen.published_contract(gen.resolve(gen.apply_mapping(contract, mapping), "aws"))
        assert "Tool" in published["required_tags"]
        assert published["tag_validation"]["Tool"] == {"enum": published["valid_managed_by"]}
        assert "ManagedBy" not in published["tag_validation"]

    def test_values_cannot_be_changed(self, contract):
        mapped = gen.apply_mapping(contract, self._mapping(renderings={"gcp": {"facts": {"owner": {"key": "team"}}}}))
        assert mapped["facts"] == contract["facts"]
        assert mapped["rules"] == contract["rules"]

    @pytest.mark.parametrize(
        "mapping, fragment",
        [
            ({"contract_schema_version": "2.0"}, "contract_schema_version"),
            ({"contract_schema_version": "1.0", "facts": {}}, "unknown entry"),
            ({"contract_schema_version": "1.0", "required": ["color"]}, "unknown fact"),
            ({"contract_schema_version": "1.0", "renderings": {"moon": {}}}, "unknown platform"),
            (
                {"contract_schema_version": "1.0", "renderings": {"aws": {"facts": {"owner": {"carried_by": "x"}}}}},
                "expected key, carrier or encoding",
            ),
            (
                {"contract_schema_version": "1.0", "renderings": {"aws": {"facts": {"owner": {"key": "Project"}}}}},
                "more than one fact",
            ),
            (
                {"contract_schema_version": "1.0", "renderings": {"gcp": {"facts": {"owner": {"key": "Owner"}}}}},
                "key_pattern",
            ),
            (
                {"contract_schema_version": "1.0", "renderings": {"gcp": {"facts": {"repository": {"key": "repo"}}}}},
                "has no key",
            ),
            (
                {"contract_schema_version": "1.0", "environment": {"allowed": ["dev"]}},
                "protected_values",
            ),
            (
                {"contract_schema_version": "1.0", "environment": {"names": ["dev"]}},
                "mapping.environment: unknown entry",
            ),
        ],
    )
    def test_invalid_mapping_is_refused(self, contract, mapping, fragment):
        with pytest.raises(gen.ContractError) as err:
            gen.apply_mapping(contract, mapping)
        assert fragment in str(err.value)

    def test_command_line_applies_the_mapping(self, tmp_path):
        result = subprocess.run(
            [
                sys.executable,
                _SCRIPT,
                "--mapping",
                os.path.join(_ROOT, "contract", "mapping.example.yaml"),
                "--out-dir",
                str(tmp_path),
                "--platform",
                "gcp",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert os.listdir(tmp_path) == ["compliance-contract.gcp.json"]
        with open(tmp_path / "compliance-contract.gcp.json", encoding="utf-8") as f:
            assert json.load(f)["keys"]["environment"] == "stage"


def test_cloud_flag_is_a_deprecated_alias_of_platform(tmp_path):
    result = subprocess.run(
        [sys.executable, _SCRIPT, "--out-dir", str(tmp_path), "--cloud", "azure"], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert os.listdir(tmp_path) == ["compliance-contract.azure.json"]
