"""Clustering and canonicalization logic for merchant validation."""

import logging
from collections import defaultdict
from typing import Any, List, Optional

from receipt_dynamo.entities import ReceiptMetadata

from .utils import (format_canonical_merchant_name, get_address_similarity,
                    get_name_similarity, get_phone_similarity, normalize_text)

# Initialize logger
logger = logging.getLogger(__name__)


def cluster_by_metadata(
    metadata_list: List[ReceiptMetadata],
) -> List[List[ReceiptMetadata]]:
    """
    Cluster ReceiptMetadata records by similarity of merchant information.

    Groups records that likely represent the same merchant based on:
    - Same place_id (if available)
    - Similar merchant names, addresses, and phone numbers

    Uses an efficient O(n²) algorithm that ensures each record is only
    assigned to one cluster.

    Args:
        metadata_list: List of ReceiptMetadata records to cluster

    Returns:
        List of clusters, where each cluster is a list of similar records

    Raises:
        ValueError: If metadata_list is invalid

    Example:
        >>> records = [metadata1, metadata2, metadata3]
        >>> clusters = cluster_by_metadata(records)
        >>> print(f"Created {len(clusters)} clusters from {len(records)} records")
    """
    # Input validation
    if not metadata_list:
        logger.warning("Empty metadata list provided for clustering")
        return []
    if not isinstance(metadata_list, list):
        raise ValueError("metadata_list must be a list")
    if not all(isinstance(record, ReceiptMetadata) for record in metadata_list):
        raise ValueError("All items in metadata_list must be ReceiptMetadata objects")

    logger.info(f"Clustering {len(metadata_list)} metadata records")

    clusters = []
    used_indices = set()

    for i, record1 in enumerate(metadata_list):
        if i in used_indices:
            continue

        # Start new cluster with this record
        cluster = [record1]
        used_indices.add(i)

        # Find all similar records
        for j, record2 in enumerate(metadata_list):
            if j in used_indices or i == j:
                continue

            try:
                if are_records_similar(record1, record2):
                    cluster.append(record2)
                    used_indices.add(j)
            except Exception as e:
                logger.warning(f"Error comparing records {i} and {j}: {e}")
                continue

        clusters.append(cluster)

    logger.info(
        f"Created {len(clusters)} clusters (avg size: {len(metadata_list)/len(clusters):.1f})"
    )
    return clusters


def are_records_similar(record1: ReceiptMetadata, record2: ReceiptMetadata) -> bool:
    """
    Determine if two ReceiptMetadata records represent the same merchant.

    Args:
        record1: First ReceiptMetadata record
        record2: Second ReceiptMetadata record

    Returns:
        True if records likely represent the same merchant
    """
    # If both have place_id and they match, they're the same
    if record1.place_id and record2.place_id:
        return record1.place_id == record2.place_id

    # Compare names using fuzzy matching
    name_similarity = get_name_similarity(
        normalize_text(record1.merchant_name),
        normalize_text(record2.merchant_name),
    )

    # Compare phone numbers
    phone_similarity = get_phone_similarity(record1.phone_number, record2.phone_number)

    # Compare addresses
    address_similarity = get_address_similarity(record1.address, record2.address)

    # Records are similar if:
    # - Name similarity > 80% AND (phone match OR address similarity > 70%)
    # - OR phone match AND address similarity > 70%
    if name_similarity > 80 and (phone_similarity == 100 or address_similarity > 70):
        return True
    elif phone_similarity == 100 and address_similarity > 70:
        return True

    return False


def get_score(record):
    """
    Calculate a score for a ReceiptMetadata record to determine canonicalization priority.

    Higher scores indicate better candidates for canonical representation.

    Args:
        record: ReceiptMetadata record to score

    Returns:
        Numeric score for the record
    """
    score = 0

    # Prefer records with place_id (Google-validated)
    if record.place_id:
        score += 1000

    # Prefer records with phone numbers
    has_phone = bool(getattr(record, "phone_number", ""))
    if has_phone:
        score += 100

    # Prefer longer names (more descriptive)
    name_len_score = -len(normalize_text(getattr(record, "merchant_name", "")))
    score += name_len_score

    # Prefer records with addresses
    if getattr(record, "address", ""):
        score += 50

    # Prefer records validated by GPT+GooglePlaces
    validated_by = getattr(record, "validated_by", "")
    if "GPT" in validated_by and "GooglePlaces" in validated_by:
        score += 200
    elif "GooglePlaces" in validated_by:
        score += 150
    elif "GPT" in validated_by:
        score += 75

    return score


def choose_canonical_metadata(cluster_members: List[ReceiptMetadata]):
    """
    Choose the best representative (canonical) metadata from a cluster.

    Args:
        cluster_members: List of ReceiptMetadata records in the same cluster

    Returns:
        The ReceiptMetadata record that should serve as the canonical representation
    """
    if not cluster_members:
        return None

    if len(cluster_members) == 1:
        return cluster_members[0]

    # Sort by score (higher is better)
    sorted_members = sorted(cluster_members, key=get_score, reverse=True)
    return sorted_members[0]


def update_items_with_canonical(
    all_records: list[ReceiptMetadata], records_by_place_id: dict
):
    """
    Update ReceiptMetadata records with canonical information.

    For each place_id group, choose the best canonical metadata and update
    all records in that group with the canonical values.

    Args:
        all_records: List of all ReceiptMetadata records
        records_by_place_id: Dictionary mapping place_id to list of records

    Returns:
        Tuple of (updated_record_count, total_place_id_groups)
    """
    updated_count = 0

    for place_id, place_records in records_by_place_id.items():
        if len(place_records) <= 1:
            continue

        # Choose canonical record for this place_id
        canonical_record = choose_canonical_metadata(place_records)
        if not canonical_record:
            continue

        # Extract canonical details
        canonical_details = {
            "canonical_place_id",
            "canonical_merchant_name",
            "canonical_address",
            "canonical_phone_number",
        }

        canonical_fields = [
            "canonical_place_id",
            "canonical_merchant_name",
            "canonical_address",
            "canonical_phone_number",
        ]

        # Update all records in this group
        for record in place_records:
            updated = False

            # Update canonical place_id
            if not getattr(record, "canonical_place_id", ""):
                record.canonical_place_id = canonical_record.place_id
                updated = True

            # Update canonical merchant name
            if not getattr(record, "canonical_merchant_name", ""):
                record.canonical_merchant_name = canonical_details.get(
                    "canonical_merchant_name", ""
                )
                updated = True

            # Update canonical address
            if not getattr(record, "canonical_address", ""):
                record.canonical_address = canonical_record.address
                updated = True

            # Update canonical phone number
            if not getattr(record, "canonical_phone_number", ""):
                record.canonical_phone_number = canonical_details.get(
                    "canonical_phone_number", ""
                )
                updated = True

            if updated:
                updated_count += 1

    return updated_count, len(records_by_place_id)


def collapse_canonical_aliases(
    all_records: List[ReceiptMetadata],
) -> int:
    """
    Collapse canonical aliases by choosing preferred names for each place_id.

    For records with the same place_id, choose the most common/preferred
    canonical merchant name and update all records to use it.

    Args:
        all_records: List of all ReceiptMetadata records

    Returns:
        Number of records updated
    """
    # Group by place_id and canonical_merchant_name
    place_name_groups = defaultdict(list)

    for rec in all_records:
        if rec.place_id:
            name = getattr(rec, "canonical_merchant_name", "").strip()
            if name:
                place_name_groups[(rec.place_id, name)].append(rec)

    # For each place_id, find the most preferred name
    place_preferred_names = {}
    for place_id in set(key[0] for key in place_name_groups.keys()):
        place_names = [key[1] for key in place_name_groups.keys() if key[0] == place_id]
        if place_names:
            # Choose the most common name, or alphabetically first if tie
            name_counts = {
                name: len(place_name_groups[(place_id, name)]) for name in place_names
            }
            preferred = max(name_counts.items(), key=lambda x: (x[1], -ord(x[0][0])))[0]
            place_preferred_names[place_id] = preferred

    # Update records with preferred names
    updated_count = 0
    for rec in all_records:
        if rec.place_id in place_preferred_names:
            preferred_name = place_preferred_names[rec.place_id]
            if rec.canonical_merchant_name != preferred_name:
                rec.canonical_merchant_name = preferred_name
                updated_count += 1

    return updated_count


def merge_place_id_aliases_by_address(records: List[ReceiptMetadata]) -> int:
    """
    Merge place_id aliases by grouping records with similar addresses.

    Identifies records that likely represent the same physical location
    but have different place_ids, and consolidates them under a single place_id.

    Args:
        records: List of ReceiptMetadata records

    Returns:
        Number of records updated with consolidated place_ids
    """
    # Group records by normalized address
    address_groups = defaultdict(list)

    for record in records:
        if record.address and record.place_id:
            normalized_addr = normalize_text(record.address)
            address_groups[normalized_addr].append(record)

    updated_count = 0

    # For each address group, merge place_ids if addresses are very similar
    for address, group_records in address_groups.items():
        if len(group_records) <= 1:
            continue

        # Find all unique place_ids in this group
        place_ids = list(set(r.place_id for r in group_records if r.place_id))

        if len(place_ids) <= 1:
            continue

        # Choose the "primary" place_id (most common, or alphabetically first)
        place_id_counts = {
            pid: sum(1 for r in group_records if r.place_id == pid) for pid in place_ids
        }
        primary_place_id = max(place_id_counts.items(), key=lambda x: (x[1], x[0]))[0]

        # Update all records to use the primary place_id
        for record in group_records:
            if record.place_id != primary_place_id:
                record.place_id = primary_place_id
                updated_count += 1

    return updated_count
