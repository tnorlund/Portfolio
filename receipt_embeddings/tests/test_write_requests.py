"""Canonical EmbeddingWriteRequest builder (polish-brief inventory item 3)."""

from types import SimpleNamespace

import pytest
from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.entities import EMBEDDING_DIMENSIONS
from receipt_embeddings.write_requests import build_embedding_write_requests

IMAGE_ID = "2f1e7204-84f1-4ab3-9b05-7dc6edebc1b7"


def _vector(seed: float) -> list[float]:
    return [seed] * EMBEDDING_DIMENSIONS


def _line(line_id: int, text: str) -> SimpleNamespace:
    return SimpleNamespace(line_id=line_id, text=text)


def _word(line_id: int, word_id: int, text: str) -> SimpleNamespace:
    return SimpleNamespace(line_id=line_id, word_id=word_id, text=text)


def _label(line_id: int, word_id: int, status: str) -> SimpleNamespace:
    return SimpleNamespace(
        line_id=line_id, word_id=word_id, validation_status=status
    )


def test_ingest_style_keeps_supplied_rows_and_vectors() -> None:
    requests = build_embedding_write_requests(
        image_id=IMAGE_ID,
        receipt_id=1,
        lines=[_line(1, "STORE"), _line(2, "#42")],
        words=[_word(1, 1, "STORE")],
        word_labels=[_label(1, 1, ValidationStatus.VALID.value)],
        merchant_name="Cafe",
        place_id="p1",
        row_line_ids_list=[[1, 2]],
        row_embeddings=[_vector(0.5)],
        word_embeddings=[_vector(0.3)],
        include_embedding_input=False,
        missing_row="raise",
    )
    assert len(requests) == 2
    line, word = requests
    assert line.kind == "line"
    assert line.line_id == 1
    assert line.row_line_ids == (1, 2)
    assert line.text == "STORE #42"
    assert line.embedding_input is None
    assert line.section_type == ""
    assert line.vector == _vector(0.5)
    assert word.label_status == "validated"
    assert word.vector == _vector(0.3)
    assert word.embedding_input is None


def test_missing_row_raise_vs_skip() -> None:
    kwargs = dict(
        image_id=IMAGE_ID,
        receipt_id=1,
        lines=[_line(1, "STORE")],
        words=[],
        word_labels=[],
        row_line_ids_list=[[99]],
        row_embeddings=[_vector(0.1)],
        word_embeddings=[],
        include_embedding_input=False,
    )
    with pytest.raises(ValueError, match="no matching receipt lines"):
        build_embedding_write_requests(missing_row="raise", **kwargs)
    assert build_embedding_write_requests(missing_row="skip", **kwargs) == []


def test_section_type_blanks_when_row_spans_sections() -> None:
    line = SimpleNamespace(
        image_id=IMAGE_ID,
        receipt_id=1,
        line_id=2,
        text="COFFEE 12.99",
        bounding_box={"x": 0.1, "y": 0.8, "width": 0.5, "height": 0.05},
    )
    word = SimpleNamespace(
        image_id=IMAGE_ID,
        receipt_id=1,
        line_id=2,
        word_id=3,
        text="COFFEE",
        bounding_box={"x": 0.1, "y": 0.8, "width": 0.2, "height": 0.05},
        calculate_centroid=lambda: (0.2, 0.825),
    )
    sections = [
        SimpleNamespace(line_ids=[2], section_type="ITEMS"),
    ]
    requests = build_embedding_write_requests(
        image_id=IMAGE_ID,
        receipt_id=1,
        lines=[line],
        words=[word],
        word_labels=[
            _label(2, 3, ValidationStatus.INVALID.value),
        ],
        merchant_name="Fixture Mart",
        place_id="p1",
        sections=sections,
        include_embedding_input=True,
    )
    assert requests[0].section_type == "ITEMS"
    assert requests[0].embedding_input == "<EDGE>\nCOFFEE 12.99\n<EDGE>"
    assert requests[1].label_status == "validated"
    assert requests[1].embedding_input is not None
