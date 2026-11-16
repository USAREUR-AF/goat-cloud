"""The reusable compliance workflow runs the pinned script isolated from the caller's checkout."""

from pathlib import Path

import yaml

_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "compliance-check.yml"


def _runs():
    workflow = yaml.safe_load(_WORKFLOW.read_text())
    return [step.get("run", "") for job in workflow["jobs"].values() for step in job.get("steps", [])]


def test_script_is_downloaded_outside_the_checkout():
    fetch = next(run for run in _runs() if "aws s3 cp" in run)
    assert '"$RUNNER_TEMP/compliance_lint.py"' in fetch
    assert "./compliance_lint.py" not in fetch
    assert "sha256sum -c" in fetch and "$RUNNER_TEMP/compliance_lint.py" in fetch.split("sha256sum")[0]


def test_script_runs_in_isolated_mode_from_runner_temp():
    run = next(run for run in _runs() if "compliance_lint.py" in run and "--iac-type" in run)
    assert 'python -I "$RUNNER_TEMP/compliance_lint.py"' in run
