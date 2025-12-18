"""
Merchant clustering entity for Places data deduplication.

PlaceCluster groups multiple ReceiptPlace records that refer to the same
merchant (e.g., different Starbucks locations, or Starbucks vs STARBUCKS COFFEE).

This entity separates canonical merchant data (name, phone, website) from
location-specific data (coordinates, hours, status) which lives on ReceiptPlace.

Structure:
- One PlaceCluster per unique merchant
- Multiple place_ids per cluster (each location)
- Multiple ReceiptPlace records per place_id (each receipt visit)

Example:
    Cluster "Starbucks Downtown"
      ├─ place_id: ChIJ1234...  (40.7128, -74.0060)
      │   └─ ReceiptPlace records (each visit)
      ├─ place_id: ChIJ5678...  (40.7135, -74.0070)
      │   └─ ReceiptPlace records (each visit)
      └─ place_id: ChIJ9999...  (same location, alternate ID)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from receipt_dynamo.entities.entity_mixins import SerializationMixin
from receipt_dynamo.entities.util import (
    _repr_str,
    assert_valid_uuid,
)


class ClusterType(Enum):
    """Type of merchant cluster."""
    SINGLE = "SINGLE"          # Single location merchant
    FRANCHISE = "FRANCHISE"    # Franchise (multiple locations, same brand)
    CHAIN = "CHAIN"            # Large chain (many locations)
    UNKNOWN = "UNKNOWN"        # Unclassified


@dataclass(eq=True, unsafe_hash=False)
class PlaceCluster(SerializationMixin):
    """
    Canonical merchant data cluster.

    Groups multiple ReceiptPlace records that refer to the same merchant,
    consolidating canonical information (name, address, phone, etc).

    Each cluster can contain multiple place_ids (same merchant with multiple
    Google Place IDs) and multiple ReceiptPlace records (multiple visits).

    Attributes:
        cluster_id (str): UUID for this cluster
        place_ids (list[str]): All Google Place IDs in this cluster

        canonical_name (str): Canonical merchant name (e.g., "Starbucks")
        canonical_category (str): Primary category (e.g., "Coffee Shop")
        canonical_types (list[str]): All Google Place types
        canonical_address (str): Canonical formatted address
        canonical_phone (str): Canonical phone number
        canonical_phone_intl (str): International format
        canonical_website (str): Official website
        canonical_maps_url (str): Google Maps link

        cluster_type (str): SINGLE, FRANCHISE, CHAIN, or UNKNOWN
        confidence (float): How sure we are these are the same merchant (0-1)

        receipt_count (int): Total receipts tagged to this cluster
        distinct_locations (int): Number of unique place_ids

        created_at (datetime): When cluster was created
        updated_at (datetime): When last updated
        source (str): How cluster was created (inference, manual, merge)
    """

    # === Identity ===
    cluster_id: str

    # === Place IDs ===
    place_ids: List[str] = field(default_factory=list)

    # === Canonical Merchant Data ===
    canonical_name: str = ""
    canonical_category: str = ""
    canonical_types: List[str] = field(default_factory=list)
    canonical_address: str = ""
    canonical_phone: str = ""
    canonical_phone_intl: str = ""
    canonical_website: str = ""
    canonical_maps_url: str = ""

    # === Cluster Classification ===
    cluster_type: str = ClusterType.UNKNOWN.value
    confidence: float = 0.0  # 0-1, how sure we are of the cluster

    # === Statistics ===
    receipt_count: int = 0
    distinct_locations: int = 0

    # === Timestamps ===
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    # === Metadata ===
    source: str = ""  # inference, manual, merge, etc.
    notes: str = ""   # Human notes about the cluster

    def __post_init__(self) -> None:
        """Validate and normalize initialization arguments."""
        # Validate cluster_id is UUID
        assert_valid_uuid(self.cluster_id, "cluster_id")

        # Normalize cluster type
        if self.cluster_type not in {t.value for t in ClusterType}:
            self.cluster_type = ClusterType.UNKNOWN.value

        # Validate confidence
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"confidence must be between 0.0 and 1.0, got {self.confidence}"
            )

        # Ensure lists are unique
        if self.place_ids:
            self.place_ids = list(set(self.place_ids))
        if self.canonical_types:
            self.canonical_types = list(set(self.canonical_types))

        # Ensure distinct_locations matches place_ids count
        if not self.distinct_locations and self.place_ids:
            self.distinct_locations = len(self.place_ids)

    @property
    def key(self) -> dict[str, dict[str, str]]:
        """Get DynamoDB key for this entity."""
        return {
            "PK": {"S": f"CLUSTER#{self.cluster_id}"},
            "SK": {"S": "METADATA"},
        }

    @property
    def gsi1_key(self) -> dict[str, dict[str, str]]:
        """
        Get GSI1 key (query by canonical name for deduplication).

        Helps find existing clusters when assigning new places.
        """
        normalized_name = self.canonical_name.upper().replace(" ", "_")
        return {
            "GSI1PK": {"S": f"CLUSTER_NAME#{normalized_name}"},
            "GSI1SK": {"S": f"CLUSTER#{self.cluster_id}"},
        }

    @property
    def gsi2_key(self) -> dict[str, dict[str, str]]:
        """
        Get GSI2 key (query by place_id to find cluster).

        When we see a place_id, find which cluster it belongs to.
        """
        # Note: In real implementation, each place_id would be a separate GSI2 record
        # For now, this represents the concept
        if self.place_ids:
            return {
                "GSI2PK": {"S": f"PLACE_TO_CLUSTER#{self.place_ids[0]}"},
                "GSI2SK": {"S": f"CLUSTER#{self.cluster_id}"},
            }
        return {}

    @property
    def gsi3_key(self) -> dict[str, dict[str, str]]:
        """Get GSI3 key (query by cluster type for analytics)."""
        return {
            "GSI3PK": {"S": f"CLUSTER_TYPE#{self.cluster_type}"},
            "GSI3SK": {"S": f"CREATED#{self.created_at.isoformat()}"},
        }

    def add_place_id(self, place_id: str) -> None:
        """Add a place_id to this cluster (merge operation)."""
        if place_id not in self.place_ids:
            self.place_ids.append(place_id)
            self.distinct_locations = len(self.place_ids)
            self.updated_at = datetime.utcnow()

    def merge_from(self, other: PlaceCluster) -> None:
        """
        Merge another cluster into this one.

        Used when we discover two clusters are actually the same merchant.
        """
        # Add all place_ids from other cluster
        for place_id in other.place_ids:
            self.add_place_id(place_id)

        # Update statistics
        self.receipt_count += other.receipt_count

        # Update canonical data if other has higher confidence
        if other.confidence > self.confidence:
            self.canonical_name = other.canonical_name
            self.canonical_category = other.canonical_category
            self.canonical_types = other.canonical_types
            self.canonical_address = other.canonical_address
            self.canonical_phone = other.canonical_phone
            self.canonical_website = other.canonical_website
            self.confidence = other.confidence

        self.updated_at = datetime.utcnow()

    def __repr__(self) -> str:
        """Return string representation."""
        return (
            f"PlaceCluster("
            f"cluster_id={_repr_str(self.cluster_id)}, "
            f"canonical_name={_repr_str(self.canonical_name)}, "
            f"places={len(self.place_ids)}, "
            f"type={self.cluster_type}, "
            f"confidence={self.confidence:.2f}"
            f")"
        )
