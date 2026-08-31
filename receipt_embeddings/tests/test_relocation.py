"""Relocated formatting/openai stay free of chromadb and keep stable output."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

from receipt_embeddings.formatting.line_format import (
    format_row_embedding_input,
    format_visual_row,
)
from receipt_embeddings.openai.helpers import get_unique_receipt_and_image_ids

_PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "receipt_embeddings"


def _chromadb_import_names(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", maxsplit=1)[0]
                if root == "chromadb":
                    names.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".", maxsplit=1)[0]
            if root == "chromadb":
                names.append(node.module)
    return names


@pytest.mark.unit
def test_package_has_zero_chromadb_imports() -> None:
    violations: list[str] = []
    for path in sorted(_PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for name in _chromadb_import_names(tree):
            relative = path.relative_to(_PACKAGE_ROOT)
            violations.append(f"{relative}: {name}")
    assert violations == []


@dataclass
class _Line:
    image_id: str
    receipt_id: int
    line_id: int
    text: str
    bounding_box: dict[str, float]

    def calculate_centroid(self) -> tuple[float, float]:
        box = self.bounding_box
        return (
            box["x"] + box["width"] / 2,
            box["y"] + box["height"] / 2,
        )


_FIXED_ROW = (
    _Line(
        "img",
        1,
        1,
        "ORGANIC COFFEE",
        {"x": 0.1, "y": 0.5, "width": 0.4, "height": 0.05},
    ),
    _Line(
        "img",
        1,
        2,
        "12.99",
        {"x": 0.7, "y": 0.5, "width": 0.15, "height": 0.05},
    ),
)
_FIXED_ROW_BYTES = "ORGANIC COFFEE 12.99"
_FIXED_EMBEDDING_BYTES = "<EDGE>\nORGANIC COFFEE 12.99\n<EDGE>"


@pytest.mark.unit
def test_relocated_formatting_is_byte_identical_on_fixed_inputs() -> None:
    assert format_visual_row(_FIXED_ROW) == _FIXED_ROW_BYTES
    assert (
        format_row_embedding_input(_FIXED_ROW, None, None)
        == _FIXED_EMBEDDING_BYTES
    )
    assert format_visual_row(_FIXED_ROW) == _FIXED_ROW_BYTES
    assert (
        format_row_embedding_input(_FIXED_ROW, None, None)
        == _FIXED_EMBEDDING_BYTES
    )


@pytest.mark.unit
def test_relocated_openai_helpers_are_byte_identical_on_fixed_inputs() -> None:
    results = [
        {"custom_id": "WORD#img1#line1#123"},
        {"custom_id": "WORD#img1#line2#123"},
        {"custom_id": "LINE#img2#line1#456"},
    ]
    expected = [(123, "img1"), (456, "img2")]
    first = sorted(get_unique_receipt_and_image_ids(results))
    second = sorted(get_unique_receipt_and_image_ids(results))
    assert first == expected
    assert first == second
