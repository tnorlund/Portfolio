"""VECTOR_BACKEND selection and retrieval-seam behavior identity.

Round C item 5: merchant resolution's retrieval goes through
``VectorSearchClient`` only, defaulting to chroma; thresholds and tier
logic are untouched, so identical neighbors must produce identical
decisions on either backend.
"""

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from receipt_embeddings import ScoredItem
from receipt_embeddings.dynamo_client import DynamoVectorSearchClient
from receipt_upload.merchant_resolution.resolver import MerchantResolver
from receipt_upload.merchant_resolution.vector_retrieval import (
    LINES_VECTOR_INDEX,
    ChromaLinesSearchClient,
    build_lines_search_client,
    resolve_vector_backend,
)


class TestBackendSelection:
    def test_default_backend_is_chroma(self, monkeypatch):
        monkeypatch.delenv("VECTOR_BACKEND", raising=False)
        assert resolve_vector_backend() == "chroma"
        client = build_lines_search_client(MagicMock())
        assert isinstance(client, ChromaLinesSearchClient)

    def test_dynamodb_backend_selected_by_env(self, monkeypatch):
        monkeypatch.setenv("VECTOR_BACKEND", "dynamodb")
        sentinel = MagicMock(spec=DynamoVectorSearchClient)
        with patch.object(
            DynamoVectorSearchClient, "from_env", return_value=sentinel
        ):
            assert build_lines_search_client(MagicMock()) is sentinel

    def test_unknown_backend_rejected(self, monkeypatch):
        monkeypatch.setenv("VECTOR_BACKEND", "pinecone")
        with pytest.raises(ValueError, match="VECTOR_BACKEND"):
            resolve_vector_backend()


class TestChromaAdapter:
    def test_distances_and_metadata_pass_through_untouched(self):
        lines_client = MagicMock()
        lines_client.query.return_value = {
            "ids": [["IMAGE#img#RECEIPT#00002#LINE#00001"]],
            "metadatas": [
                [
                    {
                        "image_id": "other-img",
                        "receipt_id": 2,
                        "merchant_name": "Costco",
                        "normalized_phone_10": "5551234567",
                    }
                ]
            ],
            "distances": [[0.125]],
        }
        adapter = ChromaLinesSearchClient(lines_client)

        results = adapter.search([0.1] * 1536, LINES_VECTOR_INDEX, 20)

        lines_client.query.assert_called_once()
        call = lines_client.query.call_args.kwargs
        assert call["collection_name"] == "lines"
        assert call["n_results"] == 20
        assert call["where"] is None
        assert results == [
            ScoredItem(
                key="IMAGE#img#RECEIPT#00002#LINE#00001",
                distance=0.125,
                metadata={
                    "image_id": "other-img",
                    "receipt_id": 2,
                    "merchant_name": "Costco",
                    "normalized_phone_10": "5551234567",
                },
            )
        ]

    def test_results_without_ids_still_flow(self):
        """Test doubles (and the pre-seam consumer) never used ids."""
        lines_client = MagicMock()
        lines_client.query.return_value = {
            "metadatas": [[{"merchant_name": "Costco"}]],
            "distances": [[0.2]],
        }
        results = ChromaLinesSearchClient(lines_client).search(
            [0.1], LINES_VECTOR_INDEX, 5
        )
        assert len(results) == 1
        assert results[0].distance == 0.2
        assert results[0].metadata["merchant_name"] == "Costco"

    def test_empty_results_return_empty_list(self):
        lines_client = MagicMock()
        lines_client.query.return_value = {
            "metadatas": [[]],
            "distances": [[]],
        }
        assert (
            ChromaLinesSearchClient(lines_client).search(
                [0.1], LINES_VECTOR_INDEX, 5
            )
            == []
        )

    def test_get_vector_missing_raises_key_error(self):
        lines_client = MagicMock()
        lines_client.get.return_value = {"ids": [], "embeddings": None}
        with pytest.raises(KeyError, match="unknown vector key"):
            ChromaLinesSearchClient(lines_client).get_vector("nope")


def _neighbor_metadata():
    return {
        "image_id": "other-img",
        "receipt_id": 2,
        "merchant_name": "Coffee House",
        "normalized_phone_10": "5551234567",
    }


def _make_resolver():
    dynamo_client = MagicMock()
    place = MagicMock()
    place.place_id = "ChIJ_coffee"
    place.merchant_name = "Coffee House"
    dynamo_client.get_receipt_place.return_value = place
    resolver = MerchantResolver(dynamo_client=dynamo_client, places_client=None)
    resolver._line_embeddings = {1: [0.1] * 1536}
    line = MagicMock(line_id=1, text="Coffee House")
    line.calculate_centroid.return_value = (0.5, 0.1)
    resolver._receipt_lines = [line]
    return resolver, line


def _run_similarity(resolver, line, lines_client):
    return resolver._similarity_search_impl(
        lines_client=lines_client,
        query_line=line,
        current_image_id="current-img",
        current_receipt_id=1,
        expected_phone="5551234567",
        expected_address=None,
        resolution_tier="chroma_phone",
    )


class TestBackendBehaviorIdentity:
    def test_same_neighbors_same_decision_on_both_backends(self, monkeypatch):
        """Identical neighbors -> identical MerchantResult fields."""

        monkeypatch.delenv("VECTOR_BACKEND", raising=False)
        resolver, line = _make_resolver()
        chroma_client = MagicMock()
        chroma_client.query.return_value = {
            "metadatas": [[_neighbor_metadata()]],
            "distances": [[0.1]],
        }
        chroma_result = _run_similarity(resolver, line, chroma_client)

        class FakeDynamoBackend:
            def search(self, vector, index, top_k, filters=None):
                assert index == LINES_VECTOR_INDEX
                assert top_k == 20
                return [
                    ScoredItem(
                        key="IMAGE#other-img#RECEIPT#00002#LINE#00001",
                        distance=0.1,
                        metadata=_neighbor_metadata(),
                    )
                ]

            def get_vector(self, key):  # pragma: no cover - unused
                raise KeyError(key)

        monkeypatch.setenv("VECTOR_BACKEND", "dynamodb")
        resolver_dynamo, line_dynamo = _make_resolver()
        with patch.object(
            DynamoVectorSearchClient,
            "from_env",
            return_value=FakeDynamoBackend(),
        ):
            dynamo_result = _run_similarity(
                resolver_dynamo, line_dynamo, MagicMock()
            )

        for attribute in (
            "place_id",
            "merchant_name",
            "phone",
            "confidence",
            "resolution_tier",
            "source_image_id",
            "source_receipt_id",
        ):
            assert getattr(chroma_result, attribute) == getattr(
                dynamo_result, attribute
            ), attribute
        assert chroma_result.place_id == "ChIJ_coffee"

    def test_search_failure_degrades_to_empty_result(self, monkeypatch):
        """A throttled backend yields MerchantResult(), never a crash."""

        monkeypatch.setenv("VECTOR_BACKEND", "dynamodb")
        resolver, line = _make_resolver()

        class ThrottledBackend:
            def search(self, vector, index, top_k, filters=None):
                raise ClientError(
                    {
                        "Error": {
                            "Code": "ThrottlingException",
                            "Message": "slow down",
                        }
                    },
                    "SearchVectors",
                )

            def get_vector(self, key):  # pragma: no cover - unused
                raise KeyError(key)

        with patch.object(
            DynamoVectorSearchClient,
            "from_env",
            return_value=ThrottledBackend(),
        ):
            result = _run_similarity(resolver, line, MagicMock())

        assert result.place_id is None
        assert result.merchant_name is None
