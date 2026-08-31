"""Old chroma import paths re-export the relocated implementation objects."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
import receipt_embeddings.formatting as new_formatting
import receipt_embeddings.formatting.line_format as new_line_format
import receipt_embeddings.openai as new_openai
import receipt_embeddings.openai.helpers as new_helpers

import receipt_chroma.embedding.formatting as old_formatting
import receipt_chroma.embedding.formatting.line_format as old_line_format
import receipt_chroma.embedding.openai as old_openai
import receipt_chroma.embedding.openai.helpers as old_helpers


@pytest.mark.unit
def test_formatting_package_exports_are_the_same_objects() -> None:
    assert list(old_formatting.__all__) == list(new_formatting.__all__)
    for name in new_formatting.__all__:
        assert getattr(old_formatting, name) is getattr(new_formatting, name)


@pytest.mark.unit
def test_openai_package_exports_are_the_same_objects() -> None:
    assert list(old_openai.__all__) == list(new_openai.__all__)
    for name in new_openai.__all__:
        assert getattr(old_openai, name) is getattr(new_openai, name)


_LINE_FORMAT_EXPORTS = (
    "LineLike",
    "format_line_context_embedding_input",
    "format_row_embedding_input",
    "format_visual_row",
    "get_primary_line_id",
    "get_row_embedding_inputs",
    "group_lines_into_visual_rows",
    "parse_prev_next_from_formatted",
)


@pytest.mark.unit
def test_formatting_submodule_callables_are_the_same_objects() -> None:
    for name in _LINE_FORMAT_EXPORTS:
        assert getattr(old_line_format, name) is getattr(new_line_format, name)


@pytest.mark.unit
def test_openai_submodule_callables_are_the_same_objects() -> None:
    assert (
        old_helpers.get_unique_receipt_and_image_ids
        is new_helpers.get_unique_receipt_and_image_ids
    )


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


@pytest.mark.unit
def test_old_and_new_paths_emit_byte_identical_formatting() -> None:
    row = [
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
    ]
    old_bytes = old_formatting.format_row_embedding_input(row, None, None)
    new_bytes = new_formatting.format_row_embedding_input(row, None, None)
    assert old_bytes == new_bytes
    assert old_bytes == "<EDGE>\nORGANIC COFFEE 12.99\n<EDGE>"
