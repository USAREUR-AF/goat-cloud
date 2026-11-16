"""Every published Terraform module states the Terraform and provider versions it needs, and ships no lock file."""

import re
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]
_PATHS = _ROOT / "oss" / "public-paths.yaml"
# The path list exists in the private repository; in the public tree every
# module under modules/aws/ is a published one.
_SPEC = yaml.safe_load(_PATHS.read_text()) if _PATHS.exists() else None
if _SPEC is not None:
    PUBLIC_MODULES = sorted(
        entry.rstrip("/")
        for entry in _SPEC["include"]
        if re.fullmatch(r"modules/aws/[a-z-]+/", entry) and entry != "modules/aws/shared/"
    )
else:
    PUBLIC_MODULES = sorted(
        f"modules/aws/{p.name}" for p in (_ROOT / "modules" / "aws").iterdir() if p.is_dir() and p.name != "shared"
    )
# Owned on another branch; it gains its constraints there.
PENDING = {"modules/aws/iac-registry"}


def _terraform_blocks(module):
    return "\n".join((module / name).read_text() for name in sorted(p.name for p in module.glob("*.tf")))


@pytest.mark.parametrize("path", [p for p in PUBLIC_MODULES if p not in PENDING])
def test_module_states_its_versions(path):
    source = _terraform_blocks(_ROOT / path)
    assert re.search(r'required_version\s*=\s*">=\s*\d+\.\d+', source), path
    block = re.search(r"required_providers\s*\{(.*?)\n  \}", source, re.DOTALL)
    assert block, path
    declared = set(re.findall(r"^\s*(\w+)\s*=\s*\{", block.group(1), re.MULTILINE))
    used = set(re.findall(r'^(?:resource|data)\s+"([a-z0-9]+)_', source, re.MULTILINE))
    assert used <= declared, f"{path}: providers used but not declared: {used - declared}"
    for name in declared:
        assert re.search(rf'{name}\s*=\s*\{{\s*source\s*=\s*"hashicorp/{name}"\s*version\s*=\s*"[^"]+"', block.group(1))


@pytest.mark.parametrize("path", PUBLIC_MODULES)
def test_module_ships_no_lock_file(path):
    assert not (_ROOT / path / ".terraform.lock.hcl").exists()


@pytest.mark.skipif(_SPEC is None, reason="the path list is in the private repository only")
def test_export_excludes_lock_files():
    assert "*.terraform.lock.hcl" in _SPEC["exclude"]
