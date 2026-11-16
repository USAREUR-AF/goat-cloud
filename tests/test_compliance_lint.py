"""compliance-lint recognizes the tag module by its source, under any module name."""

import importlib.machinery
import importlib.util
import os

import pytest

_SCRIPT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "scripts", "compliance-lint"))
_loader = importlib.machinery.SourceFileLoader("compliance_lint", _SCRIPT)
_spec = importlib.util.spec_from_loader("compliance_lint", _loader)
lint = importlib.util.module_from_spec(_spec)
_loader.exec_module(lint)

CONTRACT = {"required_tags": ["Project", "Environment", "ManagedBy", "Lifecycle", "Repository"]}

PROVIDER = """
provider "aws" {{
  region = "eu-west-1"
  default_tags {{
    tags = {tags}
  }}
}}
"""


def _module(name, source):
    return f'''
module "{name}" {{
  source      = "{source}"
  project     = "acme"
  environment = "dev"
}}
'''


def _write(tmp_path, **files):
    for name, text in files.items():
        (tmp_path / f"{name}.tf").write_text(text)
    return str(tmp_path)


@pytest.mark.parametrize(
    "name, source",
    [
        ("compliance", "../../modules/aws/compliance-tags"),
        ("tags", "git::https://github.com/acme/example-repo.git//modules/aws/compliance-tags?ref=v0.1.0"),
        ("governance_tags", "git::ssh://git@example.com/acme/example-repo.git//modules/aws/compliance-tags/"),
        ("labels", "../modules/gcp/compliance-labels"),
    ],
)
def test_tag_module_is_recognized_under_any_name(tmp_path, name, source):
    path = _write(
        tmp_path,
        main=_module(name, source),
        providers=PROVIDER.format(tags=f"module.{name}.default_tags"),
    )
    assert lint.lint_terraform(path, CONTRACT) == []


def test_module_named_compliance_with_another_source_is_not_the_tag_module(tmp_path):
    path = _write(
        tmp_path,
        main=_module("compliance", "../modules/aws/compliance-scanner"),
        providers=PROVIDER.format(tags="module.compliance.default_tags"),
    )
    findings = lint.lint_terraform(path, CONTRACT)
    assert len(findings) == 1
    assert "Missing tags" in findings[0]


def test_declared_but_unused_tag_module_does_not_count(tmp_path):
    path = _write(
        tmp_path,
        main=_module("tags", "../modules/aws/compliance-tags"),
        providers=PROVIDER.format(tags='{ Project = "acme" }'),
    )
    findings = lint.lint_terraform(path, CONTRACT)
    assert findings == ["Missing tags in default_tags: Environment, Lifecycle, ManagedBy, Repository"]


def test_literal_tags_are_still_checked(tmp_path):
    tags = '{ Project = "a", Environment = "b", ManagedBy = "terraform", Lifecycle = "persistent", Repository = "https://example.com/r" }'
    assert lint.lint_terraform(_write(tmp_path, providers=PROVIDER.format(tags=tags)), CONTRACT) == []


def test_no_default_tags_is_a_finding(tmp_path):
    findings = lint.lint_terraform(_write(tmp_path, main=_module("tags", "../compliance-tags")), CONTRACT)
    assert any("No provider default_tags" in f for f in findings)


def test_tag_module_names():
    text = _module("a", "x/compliance-tags") + _module("b", "x/compliance-tags-extra") + _module("c", "x/vpc")
    assert lint.tag_module_names(text) == {"a"}
