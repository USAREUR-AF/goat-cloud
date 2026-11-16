"""The protected tag key and the protected environments default to the generated contract."""

import importlib.util
import os

import compliance_contract
import pytest

_HK_LAMBDA = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "modules", "aws", "hunterkiller", "lambda")
)


def _load(name, monkeypatch, **env):
    for key in ("PROTECTED_ENVIRONMENTS", "PROTECTED_TAG_KEY"):
        monkeypatch.setenv(key, env.get(key, ""))
    spec = importlib.util.spec_from_file_location(
        f"hk_defaults_{name.replace('.', '_')}", os.path.join(_HK_LAMBDA, name)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", ["index.py", "executor_index.py"])
def test_unset_variables_fall_back_to_the_contract(name, monkeypatch):
    module = _load(name, monkeypatch)
    assert module.PROTECTED_TAG_KEY == compliance_contract.TAG_KEYS["protected"] == "Protected"
    assert module.PROTECTED_ENVIRONMENTS == compliance_contract.PROTECTED_ENVIRONMENTS == ["prod", "production"]


@pytest.mark.parametrize("name", ["index.py", "executor_index.py"])
def test_variables_override_the_contract(name, monkeypatch):
    module = _load(name, monkeypatch, PROTECTED_ENVIRONMENTS="live, stage", PROTECTED_TAG_KEY="Keep")
    assert module.PROTECTED_TAG_KEY == "Keep"
    assert module.PROTECTED_ENVIRONMENTS == ["live", "stage"]


def test_executor_protects_by_the_contract_defaults(monkeypatch):
    module = _load("executor_index.py", monkeypatch)
    assert module._is_protected({"resource_type": "ec2:instance", "tags": {"Environment": "Production"}}) is True
    assert module._is_protected({"resource_type": "ec2:instance", "tags": {"Protected": "true"}}) is True


def test_terraform_module_reads_the_generated_contract():
    module_dir = os.path.dirname(_HK_LAMBDA)
    with open(os.path.join(module_dir, "main.tf"), encoding="utf-8") as f:
        main = f.read()
    assert '"aws:ResourceTag/Protected"' not in main
    # One condition per tag-scoped delete statement the executor has a handler for.
    assert main.count('"aws:ResourceTag/${local.protected_tag_key}"') >= 11
    assert 'local.generated_contract.keys["protected"]' in main
    assert 'local.generated_contract.protected_values["environment"]' in main
    with open(os.path.join(module_dir, "contract.generated.tf"), encoding="utf-8") as f:
        generated = f.read()
    assert 'protected   = "Protected"' in generated
