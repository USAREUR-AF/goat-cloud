"""Tests for scripts/parity-gen: the generator behind docs/cloud-parity.md."""

import copy
import importlib.machinery
import importlib.util
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(ROOT, "scripts", "parity-gen")
BASE = os.path.join(ROOT, "public-docs") if os.path.isdir(os.path.join(ROOT, "public-docs")) else ROOT
DATA = os.path.join(BASE, "parity.yaml")
PAGE = os.path.join(BASE, "docs", "cloud-parity.md")

_loader = importlib.machinery.SourceFileLoader("parity_gen", SCRIPT)
_spec = importlib.util.spec_from_loader("parity_gen", _loader)
parity_gen = importlib.util.module_from_spec(_spec)
_loader.exec_module(parity_gen)

SAMPLE = {
    "clouds": {"aws": "AWS", "azure": "Azure"},
    "capabilities": [
        {
            "id": "list",
            "name": "List every resource",
            "clouds": {
                "aws": {"mechanism": "Tagging API", "status": "built", "evidence": ["a.py"]},
                "azure": {
                    "mechanism": "Resource | Graph",
                    "status": "designed",
                    "compensating_step": "Run it from CI.",
                },
            },
        }
    ],
}
PAGE_TEXT = "".join(
    f"intro {n}\n<!-- BEGIN GENERATED: {n} -->\nold\n<!-- END GENERATED: {n} -->\n" for n in parity_gen.BLOCKS
)


def test_committed_page_matches_committed_data():
    assert parity_gen.main(["--data", DATA, "--page", PAGE, "--check"]) == 0


def test_committed_data_has_three_clouds_and_every_cell():
    data = parity_gen.load(DATA)
    assert list(data["clouds"]) == ["aws", "azure", "gcp"]
    for cap in data["capabilities"]:
        assert list(cap["clouds"]) == ["aws", "azure", "gcp"]


def test_render_has_one_row_per_capability_and_escapes_pipes():
    blocks = parity_gen.render(SAMPLE)
    assert blocks["summary"].splitlines()[2] == "| List every resource | Built | Designed |"
    assert "Resource \\| Graph" in blocks["mechanisms"]
    assert blocks["compensating"].splitlines()[2] == "| List every resource | Azure | Run it from CI. |"


def test_apply_replaces_only_generated_blocks():
    out = parity_gen.apply(PAGE_TEXT, parity_gen.render(SAMPLE))
    assert "old" not in out
    for name in parity_gen.BLOCKS:
        assert f"intro {name}\n" in out
    assert parity_gen.apply(out, parity_gen.render(SAMPLE)) == out


def test_apply_refuses_a_page_without_a_block():
    with pytest.raises(parity_gen.ParityError):
        parity_gen.apply("no blocks here", parity_gen.render(SAMPLE))


def test_check_fails_on_a_stale_page_and_write_fixes_it(tmp_path):
    import yaml

    data = tmp_path / "parity.yaml"
    page = tmp_path / "page.md"
    data.write_text(yaml.safe_dump(SAMPLE, sort_keys=False))
    page.write_text(PAGE_TEXT)
    args = ["--data", str(data), "--page", str(page)]
    assert parity_gen.main([*args, "--check"]) == 1
    assert parity_gen.main(args) == 0
    assert parity_gen.main([*args, "--check"]) == 0


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["capabilities"][0]["clouds"]["aws"].update(status="done"),
        lambda d: d["capabilities"][0]["clouds"]["aws"].pop("mechanism"),
        lambda d: d["capabilities"][0]["clouds"].pop("azure"),
        lambda d: d["capabilities"][0]["clouds"]["aws"].pop("evidence"),
        lambda d: d["capabilities"][0]["clouds"]["azure"].update(evidence=["b.py"]),
        lambda d: d["capabilities"].append(copy.deepcopy(d["capabilities"][0])),
    ],
)
def test_validate_refuses_bad_data(mutate):
    data = copy.deepcopy(SAMPLE)
    mutate(data)
    with pytest.raises(parity_gen.ParityError):
        parity_gen.validate(data)


def test_missing_evidence_lists_absent_paths(tmp_path):
    (tmp_path / "a.py").write_text("")
    assert parity_gen.missing_evidence(SAMPLE, str(tmp_path)) == []
    os.remove(tmp_path / "a.py")
    assert parity_gen.missing_evidence(SAMPLE, str(tmp_path)) == ["list/aws: a.py"]
