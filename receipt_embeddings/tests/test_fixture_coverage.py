"""Committed fixture coverage (BAKEOFF Round A rubric item 1)."""

from __future__ import annotations

from typing import get_protocol_members

import pytest

from receipt_embeddings.fixtures import (
    default_fixture_dir,
    load_fixture_bundle,
)
from receipt_embeddings.vector_client import VectorSearchClient

pytestmark = pytest.mark.unit


def test_committed_fixtures_cover_forty_receipts_and_three_families() -> None:
    root = default_fixture_dir()
    bundle = load_fixture_bundle(root)
    n = len(bundle["golden_set"]["receipts"])
    assert n >= 40, f"golden set has {n} receipts; need ≥40"
    assert len(bundle["merchant_resolution"]["queries"]) >= 40
    assert len(bundle["word_neighbors"]["queries"]) >= 40
    assert len(bundle["section_verifier"]["queries"]) >= 40
    merchants = bundle["merchant_resolution"]["queries"]
    assert all(
        "neighbors" in query and "tier" in query and "decision" in query
        for query in merchants
    )
    words = bundle["word_neighbors"]["queries"]
    assert all("neighbors" in query for query in words)
    assert all(
        "distance" in neighbor
        for query in words
        for neighbor in query["neighbors"][:1]
    )
    votes = bundle["section_verifier"]["queries"]
    assert all(receipt["votes"] for receipt in votes)


def test_protocol_exposes_only_search_and_get_vector() -> None:
    assert get_protocol_members(VectorSearchClient) == {
        "search",
        "get_vector",
    }
