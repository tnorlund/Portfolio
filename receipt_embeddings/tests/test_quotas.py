"""Chroma Cloud quota constants and fake/real filter-contract tests.

These pin the real-client semantics that burned other Round A entrants:
Chroma Cloud caps query embeddings per call, and real chromadb rejects a
bare multi-key ``where`` dict — two or more equality filters must be
wrapped in ``$and`` by the adapter while the ``VectorSearchClient``
protocol keeps flat AND-of-equalities filters.
"""

from __future__ import annotations

from typing import Any

import pytest
from receipt_embeddings import (
    MAX_GET_LIMIT,
    MAX_QUERY_EMBEDDINGS_PER_CALL,
    VectorItem,
    build_chroma_where,
    ensure_get_ids_within_quota,
    ensure_query_embeddings_within_quota,
)
from receipt_embeddings.testing import FakeVectorIndex
from scripts.similarity_harness.capture_golden import _LiveCaptureSource
from scripts.similarity_harness.common import LINE_INDEX


@pytest.mark.unit
def test_quota_constants_are_the_verified_chroma_cloud_limits() -> None:
    assert MAX_QUERY_EMBEDDINGS_PER_CALL == 20
    assert MAX_GET_LIMIT == 250


@pytest.mark.unit
def test_quota_guards_pass_at_limit_and_reject_beyond() -> None:
    ensure_query_embeddings_within_quota(
        [[0.0]] * MAX_QUERY_EMBEDDINGS_PER_CALL
    )
    with pytest.raises(ValueError, match="at most 20 embeddings"):
        ensure_query_embeddings_within_quota(
            [[0.0]] * (MAX_QUERY_EMBEDDINGS_PER_CALL + 1)
        )

    ensure_get_ids_within_quota(["key"] * MAX_GET_LIMIT)
    with pytest.raises(ValueError, match="at most 250 ids"):
        ensure_get_ids_within_quota(["key"] * (MAX_GET_LIMIT + 1))


@pytest.mark.unit
def test_where_builder_wraps_two_or_more_filters_in_and() -> None:
    assert build_chroma_where(None) is None
    assert build_chroma_where({}) is None
    assert build_chroma_where({"merchant_name": "A"}) == {"merchant_name": "A"}
    assert build_chroma_where(
        {"section_type": "ITEMS", "merchant_name": "A"}
    ) == {"$and": [{"merchant_name": "A"}, {"section_type": "ITEMS"}]}
    assert build_chroma_where({"c": 3, "a": 1, "b": 2}) == {
        "$and": [{"a": 1}, {"b": 2}, {"c": 3}]
    }


@pytest.mark.unit
def test_where_builder_rejects_operator_keys() -> None:
    with pytest.raises(ValueError, match="flat equality predicates"):
        build_chroma_where({"$and": "anything"})  # type: ignore[dict-item]


def _corpus() -> list[VectorItem]:
    return [
        VectorItem(
            key="both",
            index=LINE_INDEX,
            vector=[1.0, 0.0],
            metadata={"merchant_name": "A", "section_type": "ITEMS"},
        ),
        VectorItem(
            key="merchant-only",
            index=LINE_INDEX,
            vector=[0.9, 0.1],
            metadata={"merchant_name": "A", "section_type": "TOTAL_LINE"},
        ),
        VectorItem(
            key="section-only",
            index=LINE_INDEX,
            vector=[0.8, 0.2],
            metadata={"merchant_name": "B", "section_type": "ITEMS"},
        ),
    ]


@pytest.mark.unit
def test_fake_treats_multi_key_filters_as_and_of_equalities() -> None:
    fake = FakeVectorIndex(_corpus())

    both = fake.search(
        [1.0, 0.0],
        LINE_INDEX,
        10,
        filters={"merchant_name": "A", "section_type": "ITEMS"},
    )
    assert [item.key for item in both] == ["both"]

    merchant = fake.search(
        [1.0, 0.0], LINE_INDEX, 10, filters={"merchant_name": "A"}
    )
    assert [item.key for item in merchant] == ["both", "merchant-only"]


@pytest.mark.unit
def test_fake_rejects_prebuilt_where_operator_shapes() -> None:
    fake = FakeVectorIndex(_corpus())
    with pytest.raises(ValueError, match="flat equality predicates"):
        fake.search(
            [1.0, 0.0],
            LINE_INDEX,
            10,
            filters={"$and": True},
        )


class _RecordingChroma:
    """Stub receipt_chroma client that records query call shapes."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def query(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {
            "ids": [["neighbor"]],
            "distances": [[0.25]],
            "metadatas": [[{}]],
            "embeddings": [[[1.0, 0.0]]],
        }


def _stub_source() -> tuple[Any, _RecordingChroma]:
    source = _LiveCaptureSource.__new__(_LiveCaptureSource)
    stub = _RecordingChroma()
    source._chroma = stub
    source._places = {}
    source._sections = {}
    return source, stub


@pytest.mark.unit
def test_live_search_sends_one_embedding_and_adapter_built_where() -> None:
    source, stub = _stub_source()

    items, vectors, _ = source.search(
        [1.0, 0.0],
        LINE_INDEX,
        5,
        filters={"merchant_name": "A", "section_type": "ITEMS"},
    )

    assert [item.key for item in items] == ["neighbor"]
    assert vectors == {"neighbor": [1.0, 0.0]}
    call = stub.calls[0]
    assert len(call["query_embeddings"]) == 1
    assert call["where"] == {
        "$and": [{"merchant_name": "A"}, {"section_type": "ITEMS"}]
    }

    source.search([1.0, 0.0], LINE_INDEX, 5, filters={"merchant_name": "A"})
    assert stub.calls[1]["where"] == {"merchant_name": "A"}

    source.search([1.0, 0.0], LINE_INDEX, 5)
    assert stub.calls[2]["where"] is None
