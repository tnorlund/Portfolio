"""Cross-backend neighbor-metadata contracts for vector consumers.

The real MerchantResolver reads a fixed set of metadata fields from every
line-index neighbor (``RESOLVER_NEIGHBOR_METADATA_KEYS``). The Chroma
path's metadata shape is the contract: whatever keys Chroma's own
metadata builders surface for a neighbor, the Dynamo fetch-join path must
surface identically for the same neighbor — including the sparseness of
the two normalized anchor keys, which exist only when the row carries the
corresponding anchor.

Both sides of the comparison are produced by the REAL production code:
Chroma metadata by ``receipt_chroma``'s row-metadata builders, Dynamo
metadata by ``ReceiptLineEmbedding`` items round-tripped through a
botocore-stubbed SearchVectors + BatchGetItem join.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import boto3
import pytest
from botocore.stub import Stubber

pytest.importorskip(
    "chromadb",
    reason="imports the backfill script's chroma source; the CI receipt_embeddings leg is chromadb-free",
)

from receipt_chroma.embedding.metadata.line_metadata import (
    create_row_metadata,
    enrich_row_metadata_with_anchors,
)
from receipt_dynamo.entities import EMBEDDING_DIMENSIONS

from receipt_embeddings import (
    RESOLVER_NEIGHBOR_METADATA_KEYS,
    ChromaVectorSearchClient,
)
from receipt_embeddings.dynamo_client import DynamoVectorSearchClient
from receipt_embeddings.writer import EmbeddingWriteRequest

TABLE = "ReceiptsTable-dc5be22"
IMAGE_ID = "2f1e7204-84f1-4ab3-9b05-7dc6edebc1b7"


@dataclass
class _Line:
    image_id: str
    receipt_id: int
    line_id: int
    text: str
    confidence: float = 0.98
    bounding_box: dict[str, float] = field(
        default_factory=lambda: {
            "x": 0.1,
            "y": 0.8,
            "width": 0.5,
            "height": 0.05,
        }
    )


@dataclass
class _Word:
    image_id: str
    receipt_id: int
    line_id: int
    word_id: int
    text: str
    extracted_data: dict[str, Any] | None = None


def _row(anchored: bool) -> tuple[list[_Line], list[_Word]]:
    line = _Line(IMAGE_ID, 1, 2, "CALL 555-123-4567")
    words = [
        _Word(
            IMAGE_ID,
            1,
            2,
            1,
            "555-123-4567",
            extracted_data=(
                {"type": "phone", "value": "555-123-4567"}
                if anchored
                else None
            ),
        ),
        _Word(
            IMAGE_ID,
            1,
            2,
            2,
            "123 Main St, Henderson NV 89014",
            extracted_data=(
                {
                    "type": "address",
                    "value": "123 Main St, Henderson NV 89014",
                }
                if anchored
                else None
            ),
        ),
    ]
    return [line], words


def _chroma_neighbor_metadata(anchored: bool) -> dict[str, Any]:
    row_lines, row_words = _row(anchored)
    metadata = create_row_metadata(row_lines, merchant_name="Fixture Mart")
    metadata = dict(enrich_row_metadata_with_anchors(metadata, row_words))
    key = f"IMAGE#{IMAGE_ID}#RECEIPT#00001#LINE#00002"
    client = ChromaVectorSearchClient(
        _FakeChromaCollectionClient(metadata, key)
    )
    result = client.search(
        [0.01] * EMBEDDING_DIMENSIONS, "line-embeddings", 10
    )[0]
    return dict(result.metadata)


class _FakeChromaCollectionClient:
    def __init__(self, metadata: dict[str, Any], key: str) -> None:
        self._metadata = metadata
        self._key = key

    def query(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "ids": [[self._key]],
            "metadatas": [[self._metadata]],
            "distances": [[0.125]],
        }


def _search_dynamo(anchored: bool) -> dict[str, Any]:
    """The write path (request -> entity) and the read path (SearchVectors
    projection -> BatchGetItem join) exactly as production runs them."""
    _row_lines, row_words = _row(anchored)
    anchors = enrich_row_metadata_with_anchors({}, row_words)
    request = EmbeddingWriteRequest(
        kind="line",
        image_id=IMAGE_ID,
        receipt_id=1,
        line_id=2,
        text="CALL 555-123-4567",
        merchant_name="Fixture Mart",
        place_id="place-1",
        row_line_ids=(2,),
        section_type="",
        normalized_phone_10=str(anchors.get("normalized_phone_10", "")),
        normalized_full_address=str(
            anchors.get("normalized_full_address", "")
        ),
    )
    entity = request.build_entity([0.01] * EMBEDDING_DIMENSIONS)
    item = entity.to_item()
    projected_names = {
        "PK",
        "SK",
        "line_vector",
        "text",
        "merchant_name",
        "place_id",
        "image_id",
        "receipt_id",
        "line_id",
        "row_line_ids",
        "section_type",
    }
    projection_item = {
        name: value for name, value in item.items() if name in projected_names
    }
    base_item = {
        name: value
        for name, value in item.items()
        if name not in {"PK", "SK", "TYPE", "line_vector"}
    }
    boto_client = boto3.client(
        "dynamodb",
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    adapter = DynamoVectorSearchClient(boto_client, TABLE)
    with Stubber(boto_client) as stubber:
        stubber.add_response(
            "search_vectors",
            {"SearchResults": [{"Item": projection_item, "Score": 0.125}]},
        )
        stubber.add_response(
            "batch_get_item", {"Responses": {TABLE: [base_item]}}
        )
        results = adapter.search(
            [0.01] * EMBEDDING_DIMENSIONS, "line-embeddings", 10
        )
    return dict(results[0].metadata)


@pytest.mark.unit
def test_backends_surface_identical_resolver_metadata_keys_with_anchors() -> (
    None
):
    chroma_metadata = _chroma_neighbor_metadata(anchored=True)
    dynamo_metadata = _search_dynamo(anchored=True)

    chroma_keys = set(chroma_metadata) & RESOLVER_NEIGHBOR_METADATA_KEYS
    dynamo_keys = set(dynamo_metadata) & RESOLVER_NEIGHBOR_METADATA_KEYS
    assert chroma_keys == RESOLVER_NEIGHBOR_METADATA_KEYS
    assert dynamo_keys == RESOLVER_NEIGHBOR_METADATA_KEYS
    for name in RESOLVER_NEIGHBOR_METADATA_KEYS:
        assert chroma_metadata[name] == dynamo_metadata[name], name
    assert json.loads(chroma_metadata["row_line_ids"]) == [2]
    assert dynamo_metadata["row_line_ids"] == [2]


@pytest.mark.unit
def test_backends_omit_anchor_keys_identically_without_anchors() -> None:
    """Sparseness is part of the contract: a row with no phone/address
    anchor has NO normalized_* keys on either backend, so the resolver's
    truthiness checks behave identically."""
    chroma_metadata = _chroma_neighbor_metadata(anchored=False)
    dynamo_metadata = _search_dynamo(anchored=False)

    chroma_keys = set(chroma_metadata) & RESOLVER_NEIGHBOR_METADATA_KEYS
    dynamo_keys = set(dynamo_metadata) & RESOLVER_NEIGHBOR_METADATA_KEYS
    assert chroma_keys == dynamo_keys
    assert "normalized_phone_10" not in dynamo_metadata
    assert "normalized_full_address" not in dynamo_metadata
    for name in chroma_keys:
        assert chroma_metadata[name] == dynamo_metadata[name], name


@pytest.mark.unit
def test_anchor_values_match_the_chroma_writer_computation() -> None:
    """The Dynamo item's anchors come from the SAME enrichment function the
    Chroma line-delta writer uses, so the stored values are byte-equal."""
    chroma_metadata = _chroma_neighbor_metadata(anchored=True)
    dynamo_metadata = _search_dynamo(anchored=True)

    assert (
        dynamo_metadata["normalized_phone_10"]
        == chroma_metadata["normalized_phone_10"]
        == "5551234567"
    )
    assert (
        dynamo_metadata["normalized_full_address"]
        == chroma_metadata["normalized_full_address"]
    )
