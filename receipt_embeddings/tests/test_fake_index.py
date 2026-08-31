"""Unit tests for FakeVectorIndex (exact cosine NN, deterministic)."""

from __future__ import annotations

import math
import random

import numpy as np
import pytest

from receipt_embeddings import (
    LINES_INDEX,
    WORDS_INDEX,
    ScoredItem,
    VectorSearchClient,
)
from receipt_embeddings.testing import FakeVectorIndex


def _unit(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec]


@pytest.fixture(name="index")
def _index() -> FakeVectorIndex:
    idx = FakeVectorIndex()
    idx.add(LINES_INDEX, "a", [1.0, 0.0, 0.0], {"merchant_name": "Vons"})
    idx.add(LINES_INDEX, "b", [0.9, 0.1, 0.0], {"merchant_name": "Vons"})
    idx.add(LINES_INDEX, "c", [0.0, 1.0, 0.0], {"merchant_name": "Costco"})
    idx.add(LINES_INDEX, "d", [0.0, 0.0, 1.0], {"merchant_name": "CVS"})
    return idx


def test_satisfies_protocol(index: FakeVectorIndex) -> None:
    assert isinstance(index, VectorSearchClient)


def test_exact_nearest_neighbor_ordering(index: FakeVectorIndex) -> None:
    hits = index.search([1.0, 0.0, 0.0], LINES_INDEX, top_k=4)
    assert [h.key for h in hits] == ["a", "b", "c", "d"]
    assert hits[0].distance == pytest.approx(0.0, abs=1e-12)
    # cosine distance of orthogonal vectors is 1.0
    assert hits[2].distance == pytest.approx(1.0, abs=1e-12)
    # distances ascend
    assert all(
        hits[i].distance <= hits[i + 1].distance for i in range(len(hits) - 1)
    )


def test_matches_brute_force_on_random_corpus() -> None:
    rng = random.Random(42)
    dim, n = 8, 50
    idx = FakeVectorIndex()
    corpus: dict[str, list[float]] = {}
    for i in range(n):
        vec = [rng.gauss(0, 1) for _ in range(dim)]
        key = f"k{i:03d}"
        corpus[key] = vec
        idx.add(WORDS_INDEX, key, vec, {})
    query = [rng.gauss(0, 1) for _ in range(dim)]

    q = np.asarray(query)
    expected = sorted(
        (
            (
                round(
                    1.0
                    - float(np.dot(v, q))
                    / (np.linalg.norm(v) * np.linalg.norm(q)),
                    12,
                ),
                k,
            )
            for k, v in ((k, np.asarray(v)) for k, v in corpus.items())
        ),
    )[:10]
    hits = idx.search(query, WORDS_INDEX, top_k=10)
    assert [(h.distance, h.key) for h in hits] == [
        (pytest.approx(d, abs=1e-9), k) for d, k in expected
    ]


def test_deterministic_across_runs_and_insertion_order() -> None:
    vecs = {f"k{i}": [math.sin(i), math.cos(i), 0.5 * i] for i in range(20)}
    forward, backward = FakeVectorIndex(), FakeVectorIndex()
    for key, vec in vecs.items():
        forward.add(LINES_INDEX, key, vec, {"i": key})
    for key in reversed(list(vecs)):
        backward.add(LINES_INDEX, key, vecs[key], {"i": key})
    query = [0.3, -0.2, 0.9]
    assert forward.search(query, LINES_INDEX, 20) == backward.search(
        query, LINES_INDEX, 20
    )


def test_tie_break_is_by_key() -> None:
    idx = FakeVectorIndex()
    # Same direction, different magnitude: identical cosine distance.
    idx.add(LINES_INDEX, "zeta", [2.0, 0.0], {})
    idx.add(LINES_INDEX, "alpha", [1.0, 0.0], {})
    hits = idx.search([1.0, 0.0], LINES_INDEX, 2)
    assert [h.key for h in hits] == ["alpha", "zeta"]
    assert hits[0].distance == hits[1].distance


def test_equality_filters(index: FakeVectorIndex) -> None:
    hits = index.search(
        [1.0, 0.0, 0.0],
        LINES_INDEX,
        top_k=4,
        filters={"merchant_name": "Vons"},
    )
    assert [h.key for h in hits] == ["a", "b"]
    assert (
        index.search(
            [1.0, 0.0, 0.0],
            LINES_INDEX,
            4,
            filters={"merchant_name": "absent"},
        )
        == []
    )


def test_indexes_are_isolated(index: FakeVectorIndex) -> None:
    assert index.search([1.0, 0.0, 0.0], WORDS_INDEX, 5) == []
    index.add(WORDS_INDEX, "w", [1.0, 0.0, 0.0], {})
    assert index.count(WORDS_INDEX) == 1
    assert index.count(LINES_INDEX) == 4


def test_get_vector_roundtrip(index: FakeVectorIndex) -> None:
    assert index.get_vector("a", LINES_INDEX) == [1.0, 0.0, 0.0]
    assert index.get_vector("missing", LINES_INDEX) is None


def test_zero_norm_vectors_rank_last() -> None:
    idx = FakeVectorIndex()
    idx.add(LINES_INDEX, "zero", [0.0, 0.0], {})
    idx.add(LINES_INDEX, "near", _unit([1.0, 0.1]), {})
    hits = idx.search([1.0, 0.0], LINES_INDEX, 2)
    assert hits[0].key == "near"
    assert hits[1].distance == pytest.approx(1.0)


def test_top_k_bounds(index: FakeVectorIndex) -> None:
    assert index.search([1.0, 0.0, 0.0], LINES_INDEX, 0) == []
    assert len(index.search([1.0, 0.0, 0.0], LINES_INDEX, 100)) == 4


def test_upsert_replaces(index: FakeVectorIndex) -> None:
    index.add(LINES_INDEX, "a", [0.0, 1.0, 0.0], {"merchant_name": "Ralphs"})
    hits = index.search([0.0, 1.0, 0.0], LINES_INDEX, 1)
    assert hits[0] == ScoredItem(
        key="a", distance=0.0, metadata={"merchant_name": "Ralphs"}
    )
    assert index.count(LINES_INDEX) == 4


def test_rejects_malformed_vectors() -> None:
    idx = FakeVectorIndex()
    with pytest.raises(ValueError):
        idx.add(LINES_INDEX, "bad", [], {})
