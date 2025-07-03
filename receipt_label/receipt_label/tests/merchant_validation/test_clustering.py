"""Unit tests for merchant metadata clustering."""

# Set up test environment before imports
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_helpers import setup_test_environment

setup_test_environment()

import uuid as uuid_module
from datetime import datetime, timezone
from typing import List, Optional
from unittest.mock import MagicMock, patch

import pytest
from receipt_dynamo.entities import ReceiptMetadata


def _build_metadata(
    receipt_id: int,
    *,
    image_id: Optional[str] = None,
    place_id: str = "",
    name: str = "",
    address: str = "",
    phone: str = "",
) -> ReceiptMetadata:
    """Build a ReceiptMetadata object with deterministic test data."""
    # Generate a deterministic but valid UUID v4 based on receipt_id
    namespace = uuid_module.UUID("12345678-1234-5678-1234-567812345678")
    generated_uuid = uuid_module.uuid5(namespace, f"test-receipt-{receipt_id}")
    # Convert to UUID v4 format by setting version bits
    uuid_hex = generated_uuid.hex
    uuid_v4_hex = uuid_hex[:12] + "4" + uuid_hex[13:16] + "8" + uuid_hex[17:]
    deterministic_uuid = str(uuid_module.UUID(uuid_v4_hex))

    return ReceiptMetadata(
        image_id=image_id or deterministic_uuid,
        receipt_id=receipt_id,
        place_id=place_id,
        merchant_name=name,
        matched_fields=[],
        timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        address=address,
        phone_number=phone,
        validated_by="INFERENCE",
    )


from receipt_label.merchant_validation.clustering import cluster_by_metadata


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

    clusters = cluster_by_metadata(records)

    # Extract receipt IDs from each cluster
    result = [set(r.receipt_id for r in cluster) for cluster in clusters]

    # Use set-based comparison for order-independence
    cluster_sets = {frozenset(cluster_ids) for cluster_ids in result}
    expected_sets = {frozenset([1, 2, 3]), frozenset([4, 5])}

    assert (
        cluster_sets == expected_sets
    ), f"Unexpected clustering result: {result}"
    assert len(clusters) == 2, f"Expected 2 clusters but got {len(clusters)}"


def test_cluster_empty_list() -> None:
    """Empty input should return empty clusters."""
    assert cluster_by_metadata([]) == []


def test_cluster_single_record() -> None:
    """Single record should return single cluster."""
    record = _build_metadata(1, name="Test Store", address="123 Test St")
    clusters = cluster_by_metadata([record])

    assert len(clusters) == 1
    assert len(clusters[0]) == 1
    assert clusters[0][0].receipt_id == 1


def test_cluster_no_similarity() -> None:
    """Records with no similarity should form separate clusters."""
    records = [
        _build_metadata(
            1, name="McDonalds", address="100 A St", phone="111-111-1111"
        ),
        _build_metadata(
            2, name="Pizza Hut", address="200 B St", phone="222-222-2222"
        ),
        _build_metadata(
            3, name="Subway", address="300 C St", phone="333-333-3333"
        ),
    ]

    clusters = cluster_by_metadata(records)

    assert (
        len(clusters) == 3
    ), "Each dissimilar record should form its own cluster"
    for cluster in clusters:
        assert (
            len(cluster) == 1
        ), "Each cluster should contain exactly one record"


def test_cluster_by_place_id_takes_precedence() -> None:
    """Records with same place_id should cluster even if other fields differ."""
    records = [
        _build_metadata(
            1,
            place_id="SAME_PLACE_ID",
            name="Different Name 1",
            address="Different Address 1",
            phone="111-111-1111",
        ),
        _build_metadata(
            2,
            place_id="SAME_PLACE_ID",
            name="Different Name 2",
            address="Different Address 2",
            phone="222-222-2222",
        ),
    ]

    clusters = cluster_by_metadata(records)

    assert (
        len(clusters) == 1
    ), "Records with same place_id should cluster together"
    assert len(clusters[0]) == 2, "Both records should be in the same cluster"


def test_cluster_partial_similarity() -> None:
    """Test clustering with various levels of field similarity."""
    records = [
        # These should cluster - similar name and address
        _build_metadata(
            1,
            name="Walmart Supercenter",
            address="1234 Market Street",
            phone="555-1234",
        ),
        _build_metadata(
            2,
            name="Walmart",
            address="1234 Market St",
            phone="555-9999",  # Different phone
        ),
        # This should be separate - completely different
        _build_metadata(
            3, name="Target", address="5678 Broadway", phone="555-5678"
        ),
    ]

    clusters = cluster_by_metadata(records)
    result = [set(r.receipt_id for r in cluster) for cluster in clusters]

    # Find which cluster contains record 1
    walmart_cluster = next(c for c in result if 1 in c)
    target_cluster = next(c for c in result if 3 in c)

    assert (
        2 in walmart_cluster
    ), "Similar Walmart records should cluster together"
    assert (
        walmart_cluster != target_cluster
    ), "Walmart and Target should be in different clusters"


def test_cluster_with_missing_fields() -> None:
    """Test clustering when some fields are empty or missing."""
    records = [
        # Records with same name and address should cluster
        _build_metadata(
            1, name="Starbucks", address="123 Main St", phone=""  # No phone
        ),
        _build_metadata(
            2,
            name="Starbucks",
            address="123 Main Street",  # Similar address
            phone="",  # No phone
        ),
        # Records with same name and phone should cluster
        _build_metadata(
            3, name="Costa Coffee", address="", phone="555-5678"  # No address
        ),
        _build_metadata(
            4, name="Costa Coffee", address="", phone="555-5678"  # No address
        ),
        # Different merchant entirely
        _build_metadata(
            5, name="Dunkin", address="789 Oak St", phone="555-9999"
        ),
    ]

    clusters = cluster_by_metadata(records)
    result = [set(r.receipt_id for r in cluster) for cluster in clusters]

    # Find clusters
    starbucks_cluster = next(c for c in result if 1 in c)
    costa_cluster = next(c for c in result if 3 in c)

    # Starbucks records should cluster (same name + similar address > 70%)
    assert (
        2 in starbucks_cluster
    ), "Starbucks records should cluster based on name and address"

    # Costa records should cluster (same name + same phone)
    assert (
        4 in costa_cluster
    ), "Costa records should cluster based on name and phone"

    # Different merchants should be in different clusters
    assert 5 not in starbucks_cluster and 5 not in costa_cluster


def test_cluster_phone_number_variations() -> None:
    """Test that different phone number formats cluster correctly."""
    records = [
        _build_metadata(1, name="Best Buy", phone="(555) 123-4567"),
        _build_metadata(2, name="Best Buy", phone="555-123-4567"),
        _build_metadata(3, name="Best Buy", phone="5551234567"),
        _build_metadata(4, name="Best Buy", phone="555.123.4567"),
    ]

    clusters = cluster_by_metadata(records)

    assert (
        len(clusters) == 1
    ), "All phone number variations should cluster together"
    assert len(clusters[0]) == 4, "All records should be in the same cluster"


def test_cluster_address_variations() -> None:
    """Test that address variations cluster correctly."""
    records = [
        _build_metadata(1, name="Target", address="123 Main Street"),
        _build_metadata(2, name="Target", address="123 Main St"),
        _build_metadata(3, name="Target", address="123 Main St."),
        _build_metadata(
            4, name="Target", address="123 MAIN STREET"
        ),  # Case variation
    ]

    clusters = cluster_by_metadata(records)

    assert len(clusters) == 1, "All address variations should cluster together"
    assert len(clusters[0]) == 4, "All records should be in the same cluster"


def test_cluster_large_dataset_performance() -> None:
    """Test clustering performance with a larger dataset."""
    # Create distinct groups that shouldn't cluster together
    records = []

    # Group 1: Starbucks variations (5 records)
    starbucks_phones = [
        "415-555-0001",
        "(415) 555-0001",
        "4155550001",
        "415.555.0001",
        "415-555-0001",
    ]
    starbucks_addresses = [
        "100 Market St",
        "100 Market Street",
        "100 Market St.",
        "100 MARKET ST",
        "100 Market St",
    ]
    for i in range(5):
        records.append(
            _build_metadata(
                i + 1,
                name="Starbucks" if i % 2 == 0 else "Starbucks Coffee",
                address=starbucks_addresses[i],
                phone=starbucks_phones[i],
            )
        )

    # Group 2: McDonald's variations (5 records)
    for i in range(5):
        records.append(
            _build_metadata(
                i + 6,
                name="McDonald's" if i % 2 == 0 else "McDonalds",
                address=f"200 Oak Ave{' #' + str(i) if i > 0 else ''}",
                phone="925-555-0002",
            )
        )

    # Group 3: Target variations (5 records)
    for i in range(5):
        records.append(
            _build_metadata(
                i + 11,
                name="Target",
                address=f"300 Pine Blvd{' Suite ' + str(i) if i > 0 else ''}",
                phone="510-555-0003",
            )
        )

    # Add 5 more distinct single-record merchants
    distinct_merchants = [
        ("Whole Foods", "400 Elm St", "650-555-0004"),
        ("Trader Joe's", "500 Cedar Ln", "408-555-0005"),
        ("CVS Pharmacy", "600 Maple Dr", "707-555-0006"),
        ("Walgreens", "700 Birch Way", "916-555-0007"),
        ("Home Depot", "800 Spruce Ct", "209-555-0008"),
    ]

    for i, (name, addr, phone) in enumerate(distinct_merchants):
        records.append(
            _build_metadata(16 + i, name=name, address=addr, phone=phone)
        )

    # Total: 20 records that should form 8 clusters
    clusters = cluster_by_metadata(records)

    # Should have 8 clusters: 3 groups + 5 singles
    assert (
        7 <= len(clusters) <= 9
    ), f"Expected ~8 clusters but got {len(clusters)}"

    # Verify all records are accounted for
    all_ids = []
    for cluster in clusters:
        all_ids.extend(r.receipt_id for r in cluster)
    assert len(all_ids) == 20, f"Expected 20 records but got {len(all_ids)}"
    assert len(set(all_ids)) == 20, "No duplicate records in clusters"

    # Verify the three main groups clustered correctly
    cluster_sizes = sorted([len(c) for c in clusters], reverse=True)
    assert cluster_sizes[0] == 5, "Largest cluster should have 5 records"
    assert (
        cluster_sizes[1] == 5
    ), "Second largest cluster should have 5 records"
    assert cluster_sizes[2] == 5, "Third largest cluster should have 5 records"
