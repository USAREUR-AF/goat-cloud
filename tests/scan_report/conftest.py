import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from goatcloud import contract, grade, report

HERE = Path(__file__).parent
FIXTURES = HERE / "fixtures"
NOW = datetime(2030, 6, 15, tzinfo=UTC)


def load_inventory(provider):
    return json.loads((FIXTURES / f"{provider}_inventory.json").read_text())


def build_report(provider, mapping=None, mode="units", inventory=None, **options):
    inventory = inventory or load_inventory(provider)
    resolved = contract.load(provider, mapping_path=mapping)
    graded = grade.GRADERS[mode](inventory, resolved, now=NOW, **options)
    return report.build(inventory, resolved, graded, generated_at=NOW)


@pytest.fixture
def azure_contract():
    return contract.load("azure")


@pytest.fixture
def gcp_contract():
    return contract.load("gcp")


@pytest.fixture
def gcp_mapping():
    return FIXTURES / "gcp_mapping.yaml"
