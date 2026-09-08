"""Neighbor-metadata contract for vector consumers.

The real MerchantResolver reads a fixed set of metadata fields from every
line-index neighbor (``RESOLVER_NEIGHBOR_METADATA_KEYS``). The row-metadata
builders define the contract: whatever keys they surface for a neighbor,
the Dynamo fetch-join path must surface identically for the same neighbor
— including the sparseness of the two normalized anchor keys, which exist
only when the row carries the corresponding anchor.

Both sides of the comparison are produced by the REAL production code:
reference metadata by the row-metadata builders, Dynamo metadata by
``ReceiptLineEmbedding`` items round-tripped through a botocore-stubbed
SearchVectors + BatchGetItem join.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import boto3
import pytest
from botocore.stub import Stubber
from receipt_dynamo.entities import EMBEDDING_DIMENSIONS

from receipt_embeddings import RESOLVER_NEIGHBOR_METADATA_KEYS
from receipt_embeddings.dynamo_client import DynamoVectorSearchClient
from receipt_embeddings.line_metadata import (
    create_row_metadata,
    enrich_row_metadata_with_anchors,
)
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


def _reference_neighbor_metadata(anchored: bool) -> dict[str, Any]:
    row_lines, row_words = _row(anchored)
    metadata = create_row_metadata(row_lines, merchant_name="Fixture Mart")
    return dict(enrich_row_metadata_with_anchors(metadata, row_words))


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
    reference_metadata = _reference_neighbor_metadata(anchored=True)
    dynamo_metadata = _search_dynamo(anchored=True)

    reference_keys = set(reference_metadata) & RESOLVER_NEIGHBOR_METADATA_KEYS
    dynamo_keys = set(dynamo_metadata) & RESOLVER_NEIGHBOR_METADATA_KEYS
    assert reference_keys == RESOLVER_NEIGHBOR_METADATA_KEYS
    assert dynamo_keys == RESOLVER_NEIGHBOR_METADATA_KEYS
    for name in RESOLVER_NEIGHBOR_METADATA_KEYS:
        assert reference_metadata[name] == dynamo_metadata[name], name
    assert json.loads(reference_metadata["row_line_ids"]) == [2]
    assert dynamo_metadata["row_line_ids"] == [2]


@pytest.mark.unit
def test_backends_omit_anchor_keys_identically_without_anchors() -> None:
    """Sparseness is part of the contract: a row with no phone/address
    anchor has NO normalized_* keys on either backend, so the resolver's
    truthiness checks behave identically."""
    reference_metadata = _reference_neighbor_metadata(anchored=False)
    dynamo_metadata = _search_dynamo(anchored=False)

    reference_keys = set(reference_metadata) & RESOLVER_NEIGHBOR_METADATA_KEYS
    dynamo_keys = set(dynamo_metadata) & RESOLVER_NEIGHBOR_METADATA_KEYS
    assert reference_keys == dynamo_keys
    assert "normalized_phone_10" not in dynamo_metadata
    assert "normalized_full_address" not in dynamo_metadata
    for name in reference_keys:
        assert reference_metadata[name] == dynamo_metadata[name], name


@pytest.mark.unit
def test_anchor_values_match_the_metadata_builder_computation() -> None:
    """The Dynamo item's anchors come from the SAME enrichment function the
    row-metadata builder uses, so the stored values are byte-equal."""
    reference_metadata = _reference_neighbor_metadata(anchored=True)
    dynamo_metadata = _search_dynamo(anchored=True)

    assert (
        dynamo_metadata["normalized_phone_10"]
        == reference_metadata["normalized_phone_10"]
        == "5551234567"
    )
    assert (
        dynamo_metadata["normalized_full_address"]
        == reference_metadata["normalized_full_address"]
    )
