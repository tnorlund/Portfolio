"""Contract tests pinning the fake index to the SearchVectors quotas.

Round A standing amendment: every test double carries contract tests
pinning it to the real dependency's validation semantics. The real
dependency here is the SearchVectors API surface, whose limits live in
``receipt_embeddings.dynamo_quotas``.
"""

import pytest

from receipt_embeddings import VectorItem
from receipt_embeddings.dynamo_quotas import (
    DYNAMO_INDEX_BY_PROTOCOL_INDEX,
    EMBEDDING_DIMENSIONS,
    LINE_EMBEDDINGS_INDEX,
    MAX_VECTOR_INDEXES_PER_TABLE,
    PROTOCOL_LINE_INDEX,
    PROTOCOL_WORD_INDEX,
    SEARCH_VECTORS_MAX_TOP_K,
    SEARCH_VECTORS_MIN_TOP_K,
    WORD_EMBEDDINGS_INDEX,
    ensure_equality_only_filters,
    ensure_top_k_within_search_quota,
    resolve_dynamo_index_name,
)
from receipt_embeddings.testing import FakeVectorIndex


@pytest.fixture
def populated_fake():
    items = [
        VectorItem(
            key=f"IMAGE#img#RECEIPT#00001#LINE#{line_id:05d}",
            index=PROTOCOL_LINE_INDEX,
            vector=[1.0, float(line_id)],
            metadata={"section_type": "HEADER"},
        )
        for line_id in range(1, 4)
    ]
    return FakeVectorIndex(items)


@pytest.mark.unit
def test_quota_values_match_service_limits():
    assert SEARCH_VECTORS_MAX_TOP_K == 100
    assert SEARCH_VECTORS_MIN_TOP_K == 1
    assert MAX_VECTOR_INDEXES_PER_TABLE == 5
    assert EMBEDDING_DIMENSIONS == 1536


@pytest.mark.unit
def test_index_name_mapping_is_total_and_stable():
    assert DYNAMO_INDEX_BY_PROTOCOL_INDEX == {
        PROTOCOL_LINE_INDEX: LINE_EMBEDDINGS_INDEX,
        PROTOCOL_WORD_INDEX: WORD_EMBEDDINGS_INDEX,
    }
    assert resolve_dynamo_index_name(PROTOCOL_LINE_INDEX) == "line-embeddings"
    assert resolve_dynamo_index_name(PROTOCOL_WORD_INDEX) == "word-embeddings"
    # Physical names resolve to themselves.
    assert resolve_dynamo_index_name("line-embeddings") == "line-embeddings"
    with pytest.raises(ValueError, match="unknown vector index"):
        resolve_dynamo_index_name("letters-vectors")


@pytest.mark.unit
def test_top_k_guard_matches_fake_boundaries(populated_fake):
    """The fake accepts exactly the range the quota guard accepts."""

    query = [1.0, 0.0]
    for top_k in (SEARCH_VECTORS_MIN_TOP_K, SEARCH_VECTORS_MAX_TOP_K):
        assert ensure_top_k_within_search_quota(top_k) == top_k
        populated_fake.search(query, PROTOCOL_LINE_INDEX, top_k)

    for bad_top_k in (0, SEARCH_VECTORS_MAX_TOP_K + 1):
        with pytest.raises(ValueError):
            ensure_top_k_within_search_quota(bad_top_k)
        with pytest.raises(ValueError):
            populated_fake.search(query, PROTOCOL_LINE_INDEX, bad_top_k)

    for non_integer in (True, 1.5, "10"):
        with pytest.raises(TypeError):
            ensure_top_k_within_search_quota(non_integer)
        with pytest.raises(TypeError):
            populated_fake.search(query, PROTOCOL_LINE_INDEX, non_integer)


@pytest.mark.unit
def test_filter_guard_matches_fake_operator_rejection(populated_fake):
    """Both backends refuse operator keys; flat equality passes."""

    query = [1.0, 0.0]
    flat = {"section_type": "HEADER"}
    assert ensure_equality_only_filters(flat) == flat
    assert populated_fake.search(query, PROTOCOL_LINE_INDEX, 3, flat)

    operator = {"$and": "anything"}
    with pytest.raises(ValueError, match="operator key"):
        ensure_equality_only_filters(operator)
    with pytest.raises(ValueError, match="operator key"):
        populated_fake.search(query, PROTOCOL_LINE_INDEX, 3, operator)


@pytest.mark.unit
def test_filter_guard_rejects_non_scalar_values():
    with pytest.raises(ValueError, match="scalar equality"):
        ensure_equality_only_filters({"row_line_ids": [1, 2]})


@pytest.mark.unit
def test_filter_guard_accepts_empty_and_none():
    assert ensure_equality_only_filters(None) == {}
    assert ensure_equality_only_filters({}) == {}
