"""Unit tests for merchant metadata clustering."""

# pylint: disable=import-outside-toplevel
from datetime import datetime, timezone
from typing import List

from receipt_dynamo.entities import ReceiptMetadata


def _build_metadata(
    receipt_id: int,
    *,
    place_id: str = "",
    name: str = "",
    address: str = "",
    phone: str = "",
) -> ReceiptMetadata:
    return ReceiptMetadata(
        image_id="00000000-0000-0000-0000-000000000000",
        receipt_id=receipt_id,
        place_id=place_id,
        merchant_name=name,
        matched_fields=[],
        timestamp=datetime.now(timezone.utc),
        address=address,
        phone_number=phone,
    )


def _cluster(records: List[ReceiptMetadata]) -> List[List[ReceiptMetadata]]:
    """Import function after setting environment variables."""
    import os

    os.environ.setdefault("DYNAMO_TABLE_NAME", "test")
    from receipt_label.merchant_validation.clustering import (
        cluster_by_metadata,
    )

    return cluster_by_metadata(records)


def test_cluster_by_metadata_groups_similar_records() -> None:
    """Records with overlapping data should be clustered together."""

    records = [
        _build_metadata(
            1,
            place_id="PID1",
            name="Starbucks",
            address="123 Main St",
            phone="111-222-3333",
        ),
        _build_metadata(
            2,
            place_id="PID1",
            name="Starbucks Coffee",
            address="123 Main Street",
            phone="1112223333",
        ),
        _build_metadata(
            3,
            name="Starbucks Coffee",
            address="123 Main St.",
            phone="111 222 3333",
        ),
        _build_metadata(
            4,
            name="Dunkin",
            address="456 Elm St",
            phone="444-555-6666",
        ),
        _build_metadata(
            5,
            name="Dunkin Donuts",
            address="456 Elm Street",
            phone="4445556666",
        ),
    ]

    clusters = [sorted(r.receipt_id for r in c) for c in _cluster(records)]

    assert sorted(clusters) == [[1, 2, 3], [4, 5]]
