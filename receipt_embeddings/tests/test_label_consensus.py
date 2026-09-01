"""Search-then-join contract for ``similar_labeled_words`` (spec §3.7)."""

from types import SimpleNamespace
from typing import Any, Optional

import pytest

from receipt_embeddings.label_consensus import (
    MIN_MATCHES,
    MIN_SIMILARITY,
    similar_labeled_words,
    word_vector_key,
)
from receipt_embeddings.service_limits import WORD_INDEX
from receipt_embeddings.testing import FakeVectorIndex
from receipt_embeddings.vector_client import VectorItem

IMAGE = "3f2a1b0c-4d5e-4f70-8192-a3b4c5d6e7f8"
OTHER = "9e8d7c6b-5a49-4382-a1b0-c9d8e7f6a5b4"

TARGET_IDS = {
    "image_id": IMAGE,
    "receipt_id": 1,
    "line_id": 2,
    "word_id": 3,
}


def _word_item(
    image_id: str,
    receipt_id: int,
    line_id: int,
    word_id: int,
    vector: list[float],
    **extra: Any,
) -> VectorItem:
    metadata = {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "line_id": line_id,
        "word_id": word_id,
        "label_status": "validated",
        "text": extra.pop("text", "12.99"),
        "merchant_name": extra.pop("merchant_name", "Sprouts"),
        **extra,
    }
    return VectorItem(
        key=word_vector_key(image_id, receipt_id, line_id, word_id),
        index=WORD_INDEX,
        vector=vector,
        metadata=metadata,
    )


def _row(
    image_id: str,
    receipt_id: int,
    line_id: int,
    word_id: int,
    label: str,
    status: str,
    reasoning: str = "because",
) -> SimpleNamespace:
    return SimpleNamespace(
        image_id=image_id,
        receipt_id=receipt_id,
        line_id=line_id,
        word_id=word_id,
        label=label,
        validation_status=status,
        reasoning=reasoning,
        label_proposed_by="tester",
        timestamp_added="2026-08-31T00:00:00+00:00",
    )


def _loader(rows: list[SimpleNamespace]):
    by_key = {
        (r.image_id, r.receipt_id, r.line_id, r.word_id, r.label): r
        for r in rows
    }

    def load(keys):
        return [by_key[key] for key in keys if key in by_key]

    return load


def _index_with_neighbors() -> FakeVectorIndex:
    # The target's own vector plus two identical-direction neighbors
    # (similarity 1.0) and one distant neighbor that must be cut.
    return FakeVectorIndex(
        [
            _word_item(IMAGE, 1, 2, 3, [1.0, 0.0]),
            _word_item(OTHER, 1, 1, 1, [1.0, 0.0], text="3.94"),
            _word_item(OTHER, 2, 1, 1, [0.99, 0.01], merchant_name="Costco"),
            # cosine distance 1.0 -> similarity 0.0, below the 0.80 cut
            _word_item(OTHER, 3, 1, 1, [0.0, 1.0], text="far away"),
        ]
    )


def _call(
    index: Any,
    rows: list[SimpleNamespace],
    label: str = "GRAND_TOTAL",
    **kwargs: Any,
) -> dict:
    return similar_labeled_words(
        index,
        _loader(rows),
        image_id=IMAGE,
        receipt_id=1,
        line_id=2,
        word_id=3,
        label=label,
        **kwargs,
    )


def test_evidence_for_and_against_carry_reasoning_and_provenance() -> None:
    rows = [
        _row(OTHER, 1, 1, 1, "GRAND_TOTAL", "VALID", "printed after TOTAL"),
        _row(OTHER, 2, 1, 1, "GRAND_TOTAL", "INVALID", "line item price"),
    ]
    result = _call(_index_with_neighbors(), rows)

    assert result["found_vector"] is True
    assert result["neighbors_after_cut"] == 2
    assert len(result["evidence_for"]) == 1
    assert len(result["evidence_against"]) == 1
    supporting = result["evidence_for"][0]
    assert supporting["reasoning"] == "printed after TOTAL"
    assert supporting["proposed_by"] == "tester"
    assert supporting["validation_status"] == "VALID"
    assert supporting["text"] == "3.94"
    assert result["evidence_against"][0]["reasoning"] == "line item price"


def test_distant_neighbor_is_cut_and_self_is_excluded() -> None:
    rows = [_row(OTHER, 3, 1, 1, "GRAND_TOTAL", "VALID")]
    result = _call(_index_with_neighbors(), rows)

    # The far-away neighbor's VALID row must not appear: it sits below
    # MIN_SIMILARITY. The target's own key never counts as evidence.
    assert result["evidence_for"] == []
    assert result["min_similarity"] == MIN_SIMILARITY
    keys = {
        (e["image_id"], e["receipt_id"])
        for e in result["evidence_for"] + result["evidence_against"]
    }
    assert (IMAGE, 1) not in keys


def test_alternative_labels_surface_what_neighbors_are() -> None:
    rows = [
        _row(OTHER, 1, 1, 1, "TAX", "VALID"),
        _row(OTHER, 2, 1, 1, "TAX", "VALID"),
    ]
    result = _call(_index_with_neighbors(), rows, label="GRAND_TOTAL")

    assert result["evidence_for"] == []
    assert result["recommended_status"] == "PENDING"
    assert result["alternative_labels"] == [
        {"label": "TAX", "neighbor_count": 2}
    ]


def test_missing_vector_degrades_gracefully() -> None:
    index = FakeVectorIndex([_word_item(OTHER, 1, 1, 1, [1.0, 0.0])])
    result = _call(index, [])

    assert result["found_vector"] is False
    assert result["recommended_status"] == "PENDING"
    assert "No stored vector" in result["reason"]
    assert result["evidence_for"] == []


class _ThrowingSearch:
    def get_vector(self, _key: str) -> list[float]:
        return [1.0, 0.0]

    def search(self, *_args: Any, **_kwargs: Any):
        raise RuntimeError("throttled")


def test_search_failure_degrades_gracefully() -> None:
    result = _call(_ThrowingSearch(), [])

    assert result["error_type"] == "vector_search_failed"
    assert result["recommended_status"] == "PENDING"
    assert "throttled" in result["reason"]


def test_label_join_failure_degrades_gracefully() -> None:
    def broken_loader(_keys):
        raise RuntimeError("batch get failed")

    result = similar_labeled_words(
        _index_with_neighbors(),
        broken_loader,
        **TARGET_IDS,
        label="GRAND_TOTAL",
    )

    assert result["error_type"] == "label_join_failed"
    assert result["recommended_status"] == "PENDING"
    assert result["evidence_for"] == []


def test_empty_index_answers_pending() -> None:
    index = FakeVectorIndex([_word_item(IMAGE, 1, 2, 3, [1.0, 0.0])])
    result = _call(index, [])

    assert result["neighbors_after_cut"] == 0
    assert result["recommended_status"] == "PENDING"
    assert result["confidence"] == 0.0


def test_consensus_reaches_valid_with_enough_agreement() -> None:
    index = FakeVectorIndex(
        [
            _word_item(IMAGE, 1, 2, 3, [1.0, 0.0]),
            _word_item(OTHER, 1, 1, 1, [1.0, 0.0]),
            _word_item(OTHER, 2, 1, 1, [0.999, 0.001]),
            _word_item(OTHER, 3, 1, 1, [0.998, 0.002]),
        ]
    )
    rows = [
        _row(OTHER, 1, 1, 1, "GRAND_TOTAL", "VALID"),
        _row(OTHER, 2, 1, 1, "GRAND_TOTAL", "VALID"),
        _row(OTHER, 3, 1, 1, "GRAND_TOTAL", "VALID"),
    ]
    result = _call(index, rows)

    assert len(result["evidence_for"]) >= MIN_MATCHES
    assert result["recommended_status"] == "VALID"
    assert result["confidence"] == 1.0


def test_same_merchant_boost_applies_only_with_target_merchant() -> None:
    # A neighbor whose cosine similarity is exactly 0.8 (vector
    # [0.8, 0.6] against [1, 0]: dot = 0.8, unit norms), so cosine
    # distance 0.2 and seam similarity 1 - 0.2 = 0.8 — right at the
    # cut, and low enough that the +0.10 boost is visible below the
    # 1.0 clamp.
    def index() -> FakeVectorIndex:
        return FakeVectorIndex(
            [
                _word_item(IMAGE, 1, 2, 3, [1.0, 0.0]),
                _word_item(OTHER, 1, 1, 1, [0.8, 0.6]),
            ]
        )

    rows = [_row(OTHER, 1, 1, 1, "GRAND_TOTAL", "VALID")]

    without = _call(index(), rows)
    assert without["evidence_for"][0]["same_merchant"] is None
    assert without["votes_for"] == pytest.approx(0.8)

    with_merchant = _call(index(), rows, target_merchant="Sprouts")
    assert with_merchant["evidence_for"][0]["same_merchant"] is True
    assert with_merchant["votes_for"] == pytest.approx(0.9)


class _RecordingIndex(FakeVectorIndex):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.top_ks: list[int] = []

    def search(self, vector, index, top_k, filters=None):
        self.top_ks.append(top_k)
        return super().search(vector, index, top_k, filters)


def test_top_k_is_clamped_to_search_vectors_cap() -> None:
    index = _RecordingIndex([_word_item(IMAGE, 1, 2, 3, [1.0, 0.0])])
    _call(index, [], top_k=500)

    assert index.top_ks == [100]


def test_validated_filter_reaches_the_backend() -> None:
    class _FilterRecordingIndex(FakeVectorIndex):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.filters: list[Optional[dict]] = []

        def search(self, vector, index, top_k, filters=None):
            self.filters.append(dict(filters) if filters else None)
            return super().search(vector, index, top_k, filters)

    index = _FilterRecordingIndex([_word_item(IMAGE, 1, 2, 3, [1.0, 0.0])])
    _call(index, [])

    assert index.filters == [{"label_status": "validated"}]


def _hydrated_row(label: str, status: str, reasoning: str) -> dict:
    return {
        "label": label,
        "validation_status": status,
        "reasoning": reasoning,
        "label_proposed_by": "human",
        "timestamp_added": "2026-08-31T00:00:00+00:00",
    }


def test_adapter_hydrated_label_rows_skip_the_loader() -> None:
    """Single-join reuse (E3 review P2-4): neighbors carrying the Dynamo
    adapter's ``label_rows`` hydration must not be re-fetched."""
    index = FakeVectorIndex(
        [
            _word_item(IMAGE, 1, 2, 3, [1.0, 0.0]),
            _word_item(
                OTHER,
                1,
                1,
                1,
                [1.0, 0.0],
                label_rows=[
                    _hydrated_row("GRAND_TOTAL", "VALID", "after TOTAL"),
                    _hydrated_row("TAX", "INVALID", "not a tax"),
                ],
            ),
        ]
    )

    def exploding_loader(_keys):
        raise AssertionError("hydrated neighbors must not hit the loader")

    result = similar_labeled_words(
        index,
        exploding_loader,
        **TARGET_IDS,
        label="GRAND_TOTAL",
    )

    assert len(result["evidence_for"]) == 1
    assert result["evidence_for"][0]["reasoning"] == "after TOTAL"
    assert result["evidence_for"][0]["proposed_by"] == "human"


def test_invalid_only_neighbor_surfaces_as_evidence_against() -> None:
    """Regression (E3 review P1-2): a word whose only verdict for the
    candidate label is INVALID is exactly the counterexample the tool
    exists to surface — via hydrated rows and via the loader alike."""
    hydrated_index = FakeVectorIndex(
        [
            _word_item(IMAGE, 1, 2, 3, [1.0, 0.0]),
            _word_item(
                OTHER,
                1,
                1,
                1,
                [1.0, 0.0],
                label_rows=[
                    _hydrated_row("GRAND_TOTAL", "INVALID", "line item price")
                ],
            ),
        ]
    )
    hydrated = similar_labeled_words(
        hydrated_index,
        lambda _keys: [],
        **TARGET_IDS,
        label="GRAND_TOTAL",
    )
    assert hydrated["evidence_for"] == []
    assert len(hydrated["evidence_against"]) == 1
    assert hydrated["evidence_against"][0]["reasoning"] == "line item price"

    loader_rows = [
        _row(OTHER, 1, 1, 1, "GRAND_TOTAL", "INVALID", "line item price")
    ]
    loaded = _call(_index_with_neighbors(), loader_rows)
    assert loaded["evidence_for"] == []
    assert any(
        entry["reasoning"] == "line item price"
        for entry in loaded["evidence_against"]
    )


def test_weak_similarity_neighbor_is_excluded_at_threshold() -> None:
    """Regression (E3 review P2-C): a true cosine similarity of 0.60
    (distance 0.40) must not pass min_similarity=0.80. The retired
    validator's ``1 - d/2`` halving inflated it to 0.80 and let it
    through."""
    index = FakeVectorIndex(
        [
            _word_item(IMAGE, 1, 2, 3, [1.0, 0.0]),
            # dot([1,0],[0.6,0.8]) = 0.6 on unit vectors: cosine
            # similarity 0.60, cosine distance 0.40.
            _word_item(OTHER, 1, 1, 1, [0.6, 0.8]),
        ]
    )
    rows = [_row(OTHER, 1, 1, 1, "GRAND_TOTAL", "VALID")]

    result = _call(index, rows)

    assert result["neighbors_after_cut"] == 0
    assert result["evidence_for"] == []
    assert result["recommended_status"] == "PENDING"


def test_chroma_ndarray_responses_yield_real_evidence() -> None:
    """Regression (E3 review P1-A): chromadb returns embeddings (and
    often ids/distances) as numpy arrays, whose truth value is
    ambiguous. The adapter must handle them so the default backend
    returns real evidence instead of degrading every call."""
    np = pytest.importorskip("numpy")
    from receipt_embeddings import ChromaVectorSearchClient

    neighbor_key = word_vector_key(OTHER, 1, 1, 1)

    class _NdArrayChroma:
        def get(self, **_kwargs):
            return {
                "ids": [word_vector_key(IMAGE, 1, 2, 3)],
                "embeddings": np.asarray([[1.0, 0.0]]),
            }

        def query(self, **_kwargs):
            return {
                "ids": np.asarray([[neighbor_key]]),
                "metadatas": [
                    [
                        {
                            "image_id": OTHER,
                            "receipt_id": 1,
                            "line_id": 1,
                            "word_id": 1,
                            "text": "3.94",
                            "merchant_name": "Sprouts",
                        }
                    ]
                ],
                "distances": np.asarray([[0.1]]),
            }

    client = ChromaVectorSearchClient(_NdArrayChroma())
    rows = [_row(OTHER, 1, 1, 1, "GRAND_TOTAL", "VALID", "after TOTAL")]

    result = similar_labeled_words(
        client,
        _loader(rows),
        **TARGET_IDS,
        label="GRAND_TOTAL",
    )

    assert result["found_vector"] is True
    assert "error_type" not in result
    assert len(result["evidence_for"]) == 1
    assert result["evidence_for"][0]["reasoning"] == "after TOTAL"


def test_chroma_ndarray_empty_embeddings_is_missing_vector() -> None:
    np = pytest.importorskip("numpy")
    from receipt_embeddings import ChromaVectorSearchClient

    class _EmptyChroma:
        def get(self, **_kwargs):
            return {"ids": [], "embeddings": np.asarray([])}

        def query(self, **_kwargs):  # pragma: no cover - never reached
            raise AssertionError("no search without a vector")

    result = similar_labeled_words(
        ChromaVectorSearchClient(_EmptyChroma()),
        _loader([]),
        **TARGET_IDS,
        label="GRAND_TOTAL",
    )

    assert result["found_vector"] is False
    assert "No stored vector" in result["reason"]
