"""Lambda handler for batch cleaning and canonicalizing merchant metadata."""

import json
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

# Import AWS exceptions
import botocore.exceptions

# Import the necessary functions from merchant_validation
from receipt_label.merchant_validation import (
    choose_canonical_metadata, cluster_by_metadata, collapse_canonical_aliases,
    list_all_receipt_metadatas, merge_place_id_aliases_by_address,
    normalize_address, persist_alias_updates, update_items_with_canonical)

from .common import setup_logger

logger = setup_logger(__name__)


@dataclass
class ClusterProcessingResult:
    """Result from processing a single cluster."""

    updated_count: int
    skipped: bool
    has_geographic_discrepancy: bool


@dataclass
class ClusterStats:
    """Statistics for all cluster processing."""

    total_updated: int
    processed: int
    skipped: int
    geographic_discrepancies: int


def extract_and_validate_parameters(
    event: Dict[str, Any],
) -> Tuple[Optional[int], bool]:
    """
    Extract and log processing parameters from the event.

    Args:
        event: Lambda event with optional max_records and geographic_validation

    Returns:
        Tuple of (max_records, geographic_validation)
    """
    max_records = event.get("max_records", None)
    geographic_validation = event.get("geographic_validation", False)

    if max_records:
        logger.info("Will process up to %s records", max_records)

    if geographic_validation:
        logger.info("Geographic validation is enabled")

    return max_records, geographic_validation


def load_metadata_with_error_handling() -> Tuple[List[Any], Optional[Dict[str, Any]]]:
    """
    Load all metadata from DynamoDB with comprehensive error handling.

    Returns:
        Tuple of (metadata_list, error_response)
        If error_response is not None, it should be returned immediately
    """
    try:
        all_metadata = list_all_receipt_metadatas()
        logger.info(
            "Loaded %s total receipt metadata records from DynamoDB.",
            len(all_metadata),
        )
        return all_metadata, None

    except botocore.exceptions.ClientError as e:
        logger.error("AWS service error loading metadata from DynamoDB: %s", e)
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        return [], {
            "statusCode": (
                503
                if error_code
                in [
                    "ProvisionedThroughputExceededException",
                    "ThrottlingException",
                ]
                else 500
            ),
            "body": json.dumps(f"DynamoDB error: {error_code}"),
        }

    except botocore.exceptions.BotoCoreError as e:
        logger.error("Client-side error loading metadata: %s", e)
        return [], {
            "statusCode": 500,
            "body": json.dumps("Client configuration error"),
        }

    except KeyError as e:
        logger.error("Missing required environment variable: %s", e)
        return [], {
            "statusCode": 500,
            "body": json.dumps("Configuration error: missing environment variable"),
        }


def pre_process_and_limit_metadata(
    all_metadata: List[Any], max_records: Optional[int]
) -> List[Any]:
    """
    Pre-process metadata by merging aliases and applying limits.

    Args:
        all_metadata: List of all metadata records
        max_records: Optional limit on number of records to process

    Returns:
        Processed metadata list
    """
    # Pre-merge place IDs that have the same canonical address
    merged_count = merge_place_id_aliases_by_address(all_metadata)
    logger.info("Merged %s alias place_ids based on address.", merged_count)

    # Apply max_records limit if specified
    if max_records and isinstance(max_records, int) and max_records > 0:
        all_metadata = all_metadata[:max_records]
        logger.info("Limited processing to %s records", len(all_metadata))

    return all_metadata


def create_metadata_clusters(
    all_metadata: List[Any],
) -> Tuple[List[List[Any]], Optional[Dict[str, Any]]]:
    """
    Create clusters from metadata with error handling.

    Args:
        all_metadata: List of metadata records

    Returns:
        Tuple of (clusters, error_response)
    """
    try:
        clusters = cluster_by_metadata(all_metadata)
        logger.info("Created %s clusters from the metadata.", len(clusters))
        return clusters, None

    except (AttributeError, TypeError) as e:
        logger.error("Invalid metadata format during clustering: %s", e)
        return [], {
            "statusCode": 500,
            "body": json.dumps("Invalid metadata format"),
        }

    except ValueError as e:
        logger.error("Invalid data values during clustering: %s", e)
        return [], {
            "statusCode": 500,
            "body": json.dumps("Invalid data values in metadata"),
        }


def build_canonical_details_from_metadata(
    canonical_record: Any,
) -> Dict[str, Any]:
    """
    Build canonical details dictionary from a metadata record.

    Args:
        canonical_record: The chosen canonical record

    Returns:
        Dictionary with canonical details
    """
    return {
        "canonical_place_id": getattr(canonical_record, "place_id", ""),
        "canonical_merchant_name": getattr(canonical_record, "merchant_name", ""),
        "canonical_address": normalize_address(
            getattr(canonical_record, "address", "")
        ),
        "canonical_phone_number": getattr(canonical_record, "phone_number", ""),
    }


def process_single_cluster(
    cluster_index: int,
    members: List[Any],
    total_clusters: int,
    geographic_validation: bool,
) -> ClusterProcessingResult:
    """
    Process a single cluster of metadata records.

    Args:
        cluster_index: Zero-based index of the cluster
        members: List of metadata records in the cluster
        total_clusters: Total number of clusters being processed
        geographic_validation: Whether to check for geographic discrepancies

    Returns:
        ClusterProcessingResult with processing statistics
    """
    cluster_num = cluster_index + 1

    if not members:
        return ClusterProcessingResult(
            updated_count=0, skipped=True, has_geographic_discrepancy=False
        )

    logger.info(
        "Processing cluster %s/%s with %s members.",
        cluster_num,
        total_clusters,
        len(members),
    )

    try:
        # Choose canonical record for the cluster
        canonical_record = choose_canonical_metadata(members)
        if not canonical_record:
            logger.warning(
                "Could not choose canonical record for cluster %s. Skipping.",
                cluster_num,
            )
            return ClusterProcessingResult(
                updated_count=0, skipped=True, has_geographic_discrepancy=False
            )

        # Build canonical details
        canonical_details = build_canonical_details_from_metadata(canonical_record)

        # Log the canonical values
        logger.info(
            "Selected canonical record: place_id=%s, name=%s, address=%s",
            canonical_details["canonical_place_id"],
            canonical_details["canonical_merchant_name"],
            canonical_details["canonical_address"],
        )

        # Check for geographic discrepancies if enabled
        has_discrepancy = False
        if geographic_validation:
            has_discrepancy = check_geographic_discrepancies(members)
            if has_discrepancy:
                logger.warning(
                    "Geographic discrepancy detected in cluster %s",
                    cluster_num,
                )

        # Update all members of the cluster in DynamoDB
        updated_in_cluster = update_items_with_canonical(members, canonical_details)
        logger.info(
            "Updated %s/%s items for cluster %s with canonical data.",
            updated_in_cluster,
            len(members),
            cluster_num,
        )

        return ClusterProcessingResult(
            updated_count=updated_in_cluster,
            skipped=False,
            has_geographic_discrepancy=has_discrepancy,
        )

    except botocore.exceptions.ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        logger.error(
            "DynamoDB error processing cluster %s: %s - %s",
            cluster_num,
            error_code,
            e,
        )
        return ClusterProcessingResult(
            updated_count=0, skipped=True, has_geographic_discrepancy=False
        )

    except (AttributeError, TypeError) as e:
        logger.error("Invalid data structure in cluster %s: %s", cluster_num, e)
        return ClusterProcessingResult(
            updated_count=0, skipped=True, has_geographic_discrepancy=False
        )

    except ValueError as e:
        logger.error("Invalid values in cluster %s: %s", cluster_num, e)
        return ClusterProcessingResult(
            updated_count=0, skipped=True, has_geographic_discrepancy=False
        )


def process_all_clusters(
    clusters: List[List[Any]], geographic_validation: bool
) -> ClusterStats:
    """
    Process all clusters and collect statistics.

    Args:
        clusters: List of clusters to process
        geographic_validation: Whether to check for geographic discrepancies

    Returns:
        ClusterStats with overall processing statistics
    """
    stats = ClusterStats(
        total_updated=0, processed=0, skipped=0, geographic_discrepancies=0
    )

    for i, members in enumerate(clusters):
        result = process_single_cluster(
            i, members, len(clusters), geographic_validation
        )

        if result.skipped:
            stats.skipped += 1
        else:
            stats.processed += 1
            stats.total_updated += result.updated_count

        if result.has_geographic_discrepancy:
            stats.geographic_discrepancies += 1

    return stats


def finalize_batch_processing(
    all_metadata: List[Any],
    clusters: List[List[Any]],
    execution_time: float,
    stats: ClusterStats,
    geographic_validation: bool,
) -> Dict[str, Any]:
    """
    Perform final processing steps and build response.

    Args:
        all_metadata: All metadata records
        clusters: All clusters that were processed
        execution_time: Total execution time in seconds
        stats: Processing statistics
        geographic_validation: Whether geographic validation was enabled

    Returns:
        Lambda response dictionary
    """
    # Collapse duplicate canonical names within the same place_id
    updated_aliases = collapse_canonical_aliases(all_metadata)
    logger.info("Collapsed %s canonical alias inconsistencies.", len(updated_aliases))

    # Persist alias updates to DynamoDB
    persist_alias_updates(updated_aliases)
    logger.info("Persisted %s alias updates to DynamoDB.", len(updated_aliases))

    logger.info(
        "Batch cleanup complete in %.2f seconds. "
        "Processed %s clusters, skipped %s. "
        "Updated %s total items.",
        execution_time,
        stats.processed,
        stats.skipped,
        stats.total_updated,
    )

    if geographic_validation:
        logger.info(
            "Detected %s clusters with geographic discrepancies",
            stats.geographic_discrepancies,
        )

    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "total_metadata_records": len(all_metadata),
                "clusters_found": len(clusters),
                "clusters_processed": stats.processed,
                "clusters_skipped": stats.skipped,
                "items_updated": stats.total_updated,
                "execution_time_seconds": execution_time,
                "geographic_discrepancies": (
                    stats.geographic_discrepancies if geographic_validation else 0
                ),
            }
        ),
    }


def batch_handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    """
    Process all receipt metadata records to establish canonical
    representations.

    This handler is designed to run in two contexts:
    1. Manual invocation for one-time cleanups
    2. Scheduled execution via CloudWatch Events for weekly maintenance

    Args:
        event: Lambda event - may contain optional parameters:
               - max_records: Maximum number of records to process (optional)
               - geographic_validation: Whether to perform geographic
                 validation (optional)
        context: Lambda context

    Returns:
        Summary of the processing results
    """
    start_time = time.time()
    logger.info("Starting batch_clean_merchants_handler")

    # Extract parameters
    max_records, geographic_validation = extract_and_validate_parameters(event)

    # Load metadata
    all_metadata, error_response = load_metadata_with_error_handling()
    if error_response:
        return error_response

    if not all_metadata:
        logger.info("No metadata found to clean.")
        return {"statusCode": 200, "body": json.dumps("No metadata to clean")}

    # Pre-process metadata
    all_metadata = pre_process_and_limit_metadata(all_metadata, max_records)

    # Create clusters
    clusters, error_response = create_metadata_clusters(all_metadata)
    if error_response:
        return error_response

    # Process all clusters
    stats = process_all_clusters(clusters, geographic_validation)

    # Finalize and return response
    return finalize_batch_processing(
        all_metadata,
        clusters,
        time.time() - start_time,
        stats,
        geographic_validation,
    )


def check_geographic_discrepancies(cluster_members: List[Any]) -> bool:
    """
    Check for geographic discrepancies within a cluster.

    Looks for cases where records in the same cluster have addresses
    in different states or regions, which might indicate incorrect matching.

    Args:
        cluster_members: List of ReceiptMetadata records in the cluster

    Returns:
        bool: True if geographic discrepancies detected, False otherwise
    """
    # Extract state/region from normalized addresses
    states = set()
    for member in cluster_members:
        address = getattr(member, "address", "")
        if not address:
            continue

        # Simple heuristic: look for 2-letter state code at end of address
        # Placeholder - production might want a more robust parser
        words = address.split()
        if len(words) >= 2:
            potential_state = words[-2]
            if len(potential_state) == 2 and potential_state.isalpha():
                states.add(potential_state.upper())

    # If we found more than one state, there's a geographic discrepancy
    return len(states) > 1
