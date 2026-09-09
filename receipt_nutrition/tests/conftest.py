"""Isolated synthetic evidence shared by nutrition contract tests."""

import json
from pathlib import Path
from typing import Any

import pytest

from receipt_nutrition.models import Product


@pytest.fixture
def fixture_path() -> Path:
    return Path(__file__).parent / "fixtures/contract_cases.json"


@pytest.fixture
def document(fixture_path: Path) -> dict[str, Any]:
    return json.loads(fixture_path.read_text())


@pytest.fixture
def products(document: dict[str, Any]) -> dict[str, Product]:
    return {
        item["product_id"]: Product.model_validate(item)
        for item in document["products"]
    }
