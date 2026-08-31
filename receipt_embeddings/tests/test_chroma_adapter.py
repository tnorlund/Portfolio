"""Chroma adapter: same VectorSearchClient surface as the fake."""

from __future__ import annotations

from typing import Any

import pytest

from receipt_embeddings.chroma_adapter import (
    COLLECTION_TO_INDEX,
    ChromaVectorSearchClient,
)
from receipt_embeddings.harness import evaluate_backend
from receipt_embeddings.synthetic import generate_synthetic_bundle
from receipt_embeddings.testing.fake_index import FakeVectorIndex
from receipt_embeddings.vector_client import VectorSearchClient

pytestmark = pytest.mark.unit


class _FakeChroma:
    """Duck-typed Chroma query/get over FakeVectorIndex."""

    def __init__(self, index: FakeVectorIndex) -> None:
        self._index = index

    def query(self, **kwargs: Any) -> dict[str, Any]:
        collection = kwargs["collection_name"]
        index_name = COLLECTION_TO_INDEX[collection]
        vector = kwargs["query_embeddings"][0]
        top_k = int(kwargs["n_results"])
        where = kwargs.get("where")
        filters = None
        if where and "$and" not in where:
            filters = where
        hits = self._index.search(vector, index_name, top_k, filters)
        return {
            "ids": [[item.key for item in hits]],
            "distances": [[item.distance for item in hits]],
            "metadatas": [[dict(item.metadata) for item in hits]],
        }

    def get(self, **kwargs: Any) -> dict[str, Any]:
        ids = kwargs.get("ids") or []
        embeddings = []
        found_ids = []
        for key in ids:
            if key in self._index:
                embeddings.append(list(self._index.get_vector(key)))
                found_ids.append(key)
        return {"ids": found_ids, "embeddings": embeddings}


def test_chroma_adapter_is_vector_search_client() -> None:
    index = FakeVectorIndex()
    client = ChromaVectorSearchClient(_FakeChroma(index))
    assert isinstance(client, VectorSearchClient)


def test_chroma_adapter_self_parity_against_synthetic_fixtures() -> None:
    bundle = generate_synthetic_bundle()
    fake = FakeVectorIndex.from_fixture_items(bundle["vectors"]["items"])
    chroma = ChromaVectorSearchClient(_FakeChroma(fake))
    scorecard = evaluate_backend(chroma, bundle, backend="chroma")
    recall = scorecard["neighbor_recall"]
    assert recall["recall@10"] == pytest.approx(1.0)
    assert recall["merchant_recall@10"] == pytest.approx(1.0)
    assert recall["words_recall@30"] == pytest.approx(1.0)
    assert scorecard["merchant_agreement_pct"] == pytest.approx(100.0)
    assert scorecard["tier_decision_agreement_pct"] == pytest.approx(100.0)
    assert scorecard["section_vote_agreement_pct"] == pytest.approx(100.0)
    assert scorecard["tier_distribution"]["max_abs_delta"] == pytest.approx(
        0.0
    )
