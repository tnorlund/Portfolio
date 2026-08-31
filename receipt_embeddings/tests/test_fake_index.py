"""Exact-NN ranking, filters, and protocol surface."""

from __future__ import annotations

import numpy as np
import pytest

from receipt_embeddings.testing.fake_index import (
    FakeVectorIndex,
    cosine_distance,
)
from receipt_embeddings.vector_client import (
    LINE_EMBEDDINGS_INDEX,
    WORD_EMBEDDINGS_INDEX,
    VectorSearchClient,
    line_item_key,
    word_item_key,
)

pytestmark = pytest.mark.unit


def test_fake_index_is_vector_search_client() -> None:
    index = FakeVectorIndex()
    assert isinstance(index, VectorSearchClient)


def test_exact_nn_ranks_by_cosine_distance() -> None:
    index = FakeVectorIndex()
    index.add("near", [1.0, 0.0, 0.0], LINE_EMBEDDINGS_INDEX)
    index.add("mid", [0.7, 0.7, 0.0], LINE_EMBEDDINGS_INDEX)
    index.add("far", [0.0, 1.0, 0.0], LINE_EMBEDDINGS_INDEX)
    hits = index.search([1.0, 0.0, 0.0], LINE_EMBEDDINGS_INDEX, top_k=3)
    assert [item.key for item in hits] == ["near", "mid", "far"]
    assert hits[0].distance < hits[1].distance < hits[2].distance


def test_tie_break_is_key_ascending() -> None:
    index = FakeVectorIndex()
    vec = [1.0, 0.0]
    index.add("b", vec, LINE_EMBEDDINGS_INDEX)
    index.add("a", vec, LINE_EMBEDDINGS_INDEX)
    hits = index.search(vec, LINE_EMBEDDINGS_INDEX, top_k=2)
    assert [item.key for item in hits] == ["a", "b"]
    assert hits[0].distance == pytest.approx(hits[1].distance)


def test_filters_are_equality_only() -> None:
    index = FakeVectorIndex()
    index.add(
        "validated",
        [1.0, 0.0],
        WORD_EMBEDDINGS_INDEX,
        {"label_status": "validated"},
    )
    index.add(
        "pending",
        [0.99, 0.01],
        WORD_EMBEDDINGS_INDEX,
        {"label_status": "pending"},
    )
    hits = index.search(
        [1.0, 0.0],
        WORD_EMBEDDINGS_INDEX,
        top_k=10,
        filters={"label_status": "validated"},
    )
    assert [item.key for item in hits] == ["validated"]


def test_indexes_are_isolated() -> None:
    index = FakeVectorIndex()
    index.add("line", [1.0, 0.0], LINE_EMBEDDINGS_INDEX)
    index.add("word", [1.0, 0.0], WORD_EMBEDDINGS_INDEX)
    hits = index.search([1.0, 0.0], LINE_EMBEDDINGS_INDEX, top_k=10)
    assert [item.key for item in hits] == ["line"]


def test_get_vector_round_trip() -> None:
    index = FakeVectorIndex()
    key = line_item_key("img", 1, 2)
    index.add(key, [0.0, 1.0, 0.0], LINE_EMBEDDINGS_INDEX)
    assert list(index.get_vector(key)) == [0.0, 1.0, 0.0]


def test_get_vector_missing_raises() -> None:
    index = FakeVectorIndex()
    with pytest.raises(KeyError, match="no vector"):
        index.get_vector("missing")


def test_unknown_index_raises() -> None:
    index = FakeVectorIndex()
    with pytest.raises(ValueError, match="Unknown index"):
        index.add("x", [1.0], "not-an-index")
    with pytest.raises(ValueError, match="Unknown index"):
        index.search([1.0], "not-an-index", top_k=1)


def test_top_k_zero_is_empty() -> None:
    index = FakeVectorIndex()
    index.add("a", [1.0], LINE_EMBEDDINGS_INDEX)
    assert index.search([1.0], LINE_EMBEDDINGS_INDEX, top_k=0) == []


def test_word_key_uses_word_index() -> None:
    key = word_item_key("img", 1, 2, 3)
    assert "#WORD#" in key


def test_zero_norm_is_maximally_far() -> None:
    dist = cosine_distance(np.zeros(3), np.array([[1.0, 0.0, 0.0]]))
    assert float(dist[0]) == pytest.approx(2.0)
