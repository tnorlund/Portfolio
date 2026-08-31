"""Unit tests for FakeVectorIndex exact cosine search."""

from __future__ import annotations

import numpy as np
import pytest

from receipt_embeddings import (
    INDEX_LINES,
    INDEX_WORDS,
    ScoredItem,
    VectorSearchClient,
    cosine_distance,
)
from receipt_embeddings.testing import FakeVectorIndex


def _index() -> FakeVectorIndex:
    fake = FakeVectorIndex()
    fake.upsert(
        key="a",
        vector=[1.0, 0.0, 0.0],
        index=INDEX_LINES,
        metadata={"merchant_name": "A", "label_status": "validated"},
    )
    fake.upsert(
        key="b",
        vector=[0.0, 1.0, 0.0],
        index=INDEX_LINES,
        metadata={"merchant_name": "B", "label_status": "pending"},
    )
    fake.upsert(
        key="c",
        vector=[1.0, 0.1, 0.0],
        index=INDEX_LINES,
        metadata={"merchant_name": "A", "label_status": "validated"},
    )
    fake.upsert(
        key="w",
        vector=[1.0, 0.0, 0.0],
        index=INDEX_WORDS,
        metadata={"text": "total"},
    )
    return fake


def test_fake_satisfies_protocol() -> None:
    fake = _index()
    assert isinstance(fake, VectorSearchClient)
    assert callable(fake.search)
    assert callable(fake.get_vector)


def test_exact_cosine_ranking() -> None:
    fake = _index()
    results = fake.search([1.0, 0.0, 0.0], INDEX_LINES, top_k=3)
    assert [item.key for item in results] == ["a", "c", "b"]
    assert results[0].score == pytest.approx(0.0, abs=1e-12)
    assert results[0].score < results[1].score < results[2].score
    # Cosine distance of orthogonal unit vectors is 1.0.
    assert results[2].score == pytest.approx(1.0, abs=1e-9)


def test_tie_break_is_by_key() -> None:
    fake = FakeVectorIndex()
    fake.upsert(key="z", vector=[1.0, 0.0], index=INDEX_LINES)
    fake.upsert(key="m", vector=[1.0, 0.0], index=INDEX_LINES)
    fake.upsert(key="a", vector=[1.0, 0.0], index=INDEX_LINES)
    results = fake.search([1.0, 0.0], INDEX_LINES, top_k=3)
    assert [item.key for item in results] == ["a", "m", "z"]
    assert all(item.score == pytest.approx(0.0) for item in results)


def test_search_is_deterministic() -> None:
    fake = _index()
    first = fake.search([0.9, 0.1, 0.0], INDEX_LINES, top_k=2)
    second = fake.search([0.9, 0.1, 0.0], INDEX_LINES, top_k=2)
    assert [(i.key, i.score) for i in first] == [
        (i.key, i.score) for i in second
    ]


def test_filters_are_equality_and() -> None:
    fake = _index()
    validated = fake.search(
        [1.0, 0.0, 0.0],
        INDEX_LINES,
        top_k=10,
        filters={"label_status": "validated"},
    )
    assert {item.key for item in validated} == {"a", "c"}
    both = fake.search(
        [1.0, 0.0, 0.0],
        INDEX_LINES,
        top_k=10,
        filters={"label_status": "validated", "merchant_name": "B"},
    )
    assert both == []


def test_index_alias_and_isolation() -> None:
    fake = _index()
    lines = fake.search([1.0, 0.0, 0.0], "line-embeddings", top_k=10)
    words = fake.search([1.0, 0.0, 0.0], "word-embeddings", top_k=10)
    assert "w" not in {item.key for item in lines}
    assert [item.key for item in words] == ["w"]


def test_top_k_and_empty() -> None:
    fake = _index()
    assert len(fake.search([1.0, 0.0, 0.0], INDEX_LINES, top_k=1)) == 1
    empty = FakeVectorIndex()
    assert empty.search([1.0], INDEX_LINES, top_k=5) == []
    with pytest.raises(ValueError, match="top_k"):
        fake.search([1.0, 0.0, 0.0], INDEX_LINES, top_k=0)


def test_get_vector_round_trip() -> None:
    fake = _index()
    vector = fake.get_vector("a")
    assert vector is not None
    np.testing.assert_allclose(vector, [1.0, 0.0, 0.0])
    assert fake.get_vector("missing") is None


def test_upsert_rejects_cross_index_key() -> None:
    fake = _index()
    with pytest.raises(ValueError, match="already stored"):
        fake.upsert(
            key="a",
            vector=[0.0, 1.0, 0.0],
            index=INDEX_WORDS,
        )


def test_scored_item_score_is_cosine_distance() -> None:
    distance = cosine_distance([1.0, 0.0], [0.0, 1.0])
    assert distance == pytest.approx(1.0)
    assert cosine_distance([1.0, 0.0], [1.0, 0.0]) == pytest.approx(0.0)
    assert cosine_distance([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(2.0)
    item = ScoredItem(key="x", score=distance, metadata={})
    assert item.score == pytest.approx(1.0)
