"""Offline contract tests for the exact cosine fake."""

from __future__ import annotations

import math

import pytest

from receipt_embeddings import VectorItem, VectorSearchClient
from receipt_embeddings.testing import FakeVectorIndex


def _index() -> FakeVectorIndex:
    return FakeVectorIndex(
        [
            VectorItem(
                key="a",
                index="lines-vectors",
                vector=[1.0, 0.0],
                metadata={"section_type": "ITEMS", "validated": True},
            ),
            VectorItem(
                key="b",
                index="lines-vectors",
                vector=[1.0, 1.0],
                metadata={"section_type": "ITEMS", "validated": False},
            ),
            VectorItem(
                key="c",
                index="lines-vectors",
                vector=[0.0, 1.0],
                metadata={"section_type": "SUMMARY", "validated": True},
            ),
        ]
    )


@pytest.mark.unit
def test_fake_implements_minimal_protocol() -> None:
    assert isinstance(_index(), VectorSearchClient)


@pytest.mark.unit
def test_search_is_exact_cosine_distance() -> None:
    results = _index().search([1.0, 0.0], "lines-vectors", 3)

    assert [item.key for item in results] == ["a", "b", "c"]
    assert results[0].distance == pytest.approx(0.0)
    assert results[1].distance == pytest.approx(1.0 - (1.0 / math.sqrt(2)))
    assert results[2].distance == pytest.approx(1.0)


@pytest.mark.unit
def test_search_applies_and_combined_equality_filters() -> None:
    results = _index().search(
        [1.0, 0.0],
        "lines-vectors",
        10,
        {"section_type": "ITEMS", "validated": True},
    )

    assert [item.key for item in results] == ["a"]


@pytest.mark.unit
def test_filter_does_not_treat_bool_as_integer() -> None:
    assert not _index().search(
        [1.0, 0.0], "lines-vectors", 10, {"validated": 1}
    )


@pytest.mark.unit
def test_filter_treats_equal_real_numeric_types_as_equal() -> None:
    index = FakeVectorIndex(
        [VectorItem("a", "lines-vectors", [1.0, 0.0], {"rank": 1})]
    )

    assert index.search([1.0, 0.0], "lines-vectors", 1, {"rank": 1.0})


@pytest.mark.unit
def test_ties_are_broken_by_key() -> None:
    index = FakeVectorIndex(
        [
            VectorItem("z", "words-vectors", [1.0, 1.0]),
            VectorItem("a", "words-vectors", [1.0, 1.0]),
        ]
    )

    assert [
        item.key for item in index.search([1.0, 1.0], "words-vectors", 2)
    ] == [
        "a",
        "z",
    ]


@pytest.mark.unit
def test_unknown_index_returns_no_neighbors() -> None:
    assert _index().search([1.0, 0.0], "missing", 10) == []


@pytest.mark.unit
def test_get_vector_and_result_metadata_are_defensive_copies() -> None:
    index = _index()
    vector = index.get_vector("a")
    metadata = index.search([1.0, 0.0], "lines-vectors", 1)[0].metadata

    vector[0] = 99.0
    metadata["section_type"] = "CHANGED"

    assert index.get_vector("a") == [1.0, 0.0]
    assert (
        index.search([1.0, 0.0], "lines-vectors", 1)[0].metadata[
            "section_type"
        ]
        == "ITEMS"
    )


@pytest.mark.unit
@pytest.mark.parametrize("top_k", [0, 101, -1])
def test_search_rejects_out_of_range_top_k(top_k: int) -> None:
    with pytest.raises(ValueError, match="between 1 and 100"):
        _index().search([1.0, 0.0], "lines-vectors", top_k)


@pytest.mark.unit
@pytest.mark.parametrize("top_k", [True, 1.5, "1"])
def test_search_rejects_non_integer_top_k(top_k: object) -> None:
    with pytest.raises(TypeError, match="integer"):
        _index().search([1.0, 0.0], "lines-vectors", top_k)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("vector", "message"),
    [
        ([0.0, 0.0], "must not be zero"),
        ([1.0], "dimension"),
        ([float("nan"), 1.0], "finite"),
        ([[1.0, 0.0]], "one-dimensional"),
    ],
)
def test_search_rejects_invalid_query_vectors(
    vector: list, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _index().search(vector, "lines-vectors", 1)


@pytest.mark.unit
def test_constructor_rejects_duplicate_keys_across_indexes() -> None:
    with pytest.raises(ValueError, match="duplicate vector key"):
        FakeVectorIndex(
            [
                VectorItem("same", "lines-vectors", [1.0, 0.0]),
                VectorItem("same", "words-vectors", [0.0, 1.0]),
            ]
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "item",
    [
        VectorItem("", "lines-vectors", [1.0, 0.0]),
        VectorItem("a", "", [1.0, 0.0]),
    ],
)
def test_constructor_rejects_empty_keys_and_indexes(item: VectorItem) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        FakeVectorIndex([item])


@pytest.mark.unit
def test_constructor_rejects_mixed_dimensions_and_zero_vectors() -> None:
    with pytest.raises(ValueError, match="dimension"):
        FakeVectorIndex(
            [
                VectorItem("a", "lines-vectors", [1.0, 0.0]),
                VectorItem("b", "lines-vectors", [1.0, 0.0, 0.0]),
            ]
        )
    with pytest.raises(ValueError, match="must not be zero"):
        FakeVectorIndex([VectorItem("zero", "lines-vectors", [0.0, 0.0])])


@pytest.mark.unit
def test_get_vector_reports_unknown_key() -> None:
    with pytest.raises(KeyError, match="unknown vector key"):
        _index().get_vector("missing")
