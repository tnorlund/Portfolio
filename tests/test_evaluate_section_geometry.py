"""Tests for the offline section geometry/embedding experiment."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import numpy as np

# isort: off
# receipt_upload lands in a different import group depending on whether
# the venv has it installed (the receipt_agent CI job does not), so the
# grouping here is not reproducible across environments. Pin it.
from receipt_dynamo.entities import ReceiptRow
from receipt_upload.section_assignment import RowFeatures
from scripts.evaluate_section_geometry import (
    EvidenceWeights,
    _metric_delta,
    _structure_matrix,
    repair_metadata_only_vector_lag,
)

# isort: on


def _feature(row_id: int, *, amount: str | None = None) -> RowFeatures:
    row = ReceiptRow(
        image_id="00000000-0000-4000-8000-000000000001",
        receipt_id=1,
        row_id=row_id,
        line_ids=[row_id],
        grouping_version="visual-rows-v1",
        y_min=0.9 - row_id * 0.1,
        y_max=0.95 - row_id * 0.1,
        x_min=0.1,
        x_max=0.9,
        created_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
        price_column_x=0.9 if amount else None,
        label_text="total" if amount else None,
        amount_text=amount,
        amount_line_id=row_id if amount else None,
        amount_word_id=1 if amount else None,
    )
    return RowFeatures(
        row=row,
        position=(row_id - 1) / 2,
        x_span=0.8,
        alpha_ratio=0.5,
        has_amount=float(amount is not None),
        amount_density=0.5,
        has_quantity=0.0,
        tokens=(),
    )


def test_structure_matrix_contains_arithmetic_reconciliation() -> None:
    matrix = _structure_matrix(
        [
            _feature(1, amount="2.00"),
            _feature(2, amount="3.00"),
            _feature(3, amount="5.00"),
        ]
    )
    assert matrix.shape == (3, 16)
    assert np.isfinite(matrix).all()
    assert matrix[2, 14] == 1.0


def _lag_database(path: str, unsafe: bool = False) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE segments(id TEXT PRIMARY KEY, scope TEXT);
            CREATE TABLE max_seq_id(segment_id TEXT PRIMARY KEY, seq_id INTEGER);
            CREATE TABLE embeddings_queue(
                seq_id INTEGER PRIMARY KEY,
                operation INTEGER,
                vector BLOB
            );
            INSERT INTO segments VALUES ('vector', 'VECTOR'), ('metadata', 'METADATA');
            INSERT INTO max_seq_id VALUES ('vector', 10), ('metadata', 12);
            INSERT INTO embeddings_queue VALUES (11, 1, NULL);
            """)
        connection.execute(
            "INSERT INTO embeddings_queue VALUES (12, ?, ?)",
            (2 if unsafe else 1, b"vector" if unsafe else None),
        )


def test_metadata_only_vector_lag_repair_is_guarded(tmp_path) -> None:
    path = tmp_path / "safe.sqlite3"
    _lag_database(str(path))
    assert repair_metadata_only_vector_lag(path) == {
        "from_seq": 10,
        "to_seq": 12,
        "updates": 2,
    }
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute(
                "SELECT seq_id FROM max_seq_id WHERE segment_id='vector'"
            ).fetchone()[0]
            == 12
        )


def test_vector_lag_repair_rejects_vector_writes(tmp_path) -> None:
    path = tmp_path / "unsafe.sqlite3"
    _lag_database(str(path), unsafe=True)
    try:
        repair_metadata_only_vector_lag(path)
    except RuntimeError as error:
        assert "Unsafe Chroma repair" in str(error)
    else:  # pragma: no cover - assertion aid
        raise AssertionError("unsafe vector checkpoint advance was accepted")


def test_metric_delta_reports_per_section_recall() -> None:
    baseline = {
        "agreement": 0.5,
        "macro_recall": 0.4,
        "per_type": {"ITEMS": {"recall": 0.25}},
    }
    candidate = {
        "agreement": 0.75,
        "macro_recall": 0.6,
        "per_type": {"ITEMS": {"recall": 0.75}},
    }
    assert _metric_delta(baseline, candidate) == {
        "agreement": 0.25,
        "macro_recall": 0.19999999999999996,
        "recall_ITEMS": 0.5,
    }
    assert EvidenceWeights(embedding_knn=1.0).as_dict()["embedding_knn"] == 1.0
