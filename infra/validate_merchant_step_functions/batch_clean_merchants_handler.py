import json
import time
from logging import getLogger, StreamHandler, Formatter, INFO

# Import the necessary functions from merchant_validation
from receipt_label.merchant_validation import (
    list_all_receipt_metadatas,
    cluster_by_metadata,
    choose_canonical_metadata,
    normalize_address,
    update_items_with_canonical,
    collapse_canonical_aliases,
    persist_alias_updates,
    merge_place_id_aliases_by_address,
)

logger = getLogger()
logger.setLevel(INFO)

if len(logger.handlers) == 0:
    handler = StreamHandler()
    handler.setFormatter(
        Formatter(
            "[%(levelname)s] %(asctime)s.%(msecs)dZ %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(handler)


def batch_handler(event, context):
    """
    Process all receipt metadata records to establish canonical representations.

    This handler is designed to run in two contexts:
    1. Manual invocation for one-time cleanups
    2. Scheduled execution via CloudWatch Events for weekly maintenance

    Args:
        event: Lambda event - may contain optional parameters:
               - max_records: Maximum number of records to process (optional)
               - geographic_validation: Whether to perform geographic validation (optional)
        context: Lambda context

    Returns:
        Summary of the processing results
    """
    start_time = time.time()
    logger.info("Starting batch_clean_merchants_handler")

    # Extract optional parameters from the event
    max_records = event.get("max_records", None)
    geographic_validation = event.get("geographic_validation", False)

    if max_records:
        logger.info(f"Will process up to {max_records} records")

    if geographic_validation:
        logger.info("Geographic validation is enabled")

    # 1. Load ALL existing metadata
    try:
        all_metadata = list_all_receipt_metadatas()
        logger.info(
            f"Loaded {len(all_metadata)} total receipt metadata records from DynamoDB."
        )
    except Exception as e:
        logger.error(f"Failed to load metadata from DynamoDB: {e}")
        return {"statusCode": 500, "body": json.dumps("Error loading metadata")}

    if not all_metadata:
        logger.info("No metadata found to clean.")
        return {"statusCode": 200, "body": json.dumps("No metadata to clean")}

    # Pre-merge place IDs that have the same canonical address
    merged_count = merge_place_id_aliases_by_address(all_metadata)
    logger.info(f"Merged {merged_count} alias place_ids based on address.")

    # Apply max_records limit if specified
    if max_records and isinstance(max_records, int) and max_records > 0:
        all_metadata = all_metadata[:max_records]
        logger.info(f"Limited processing to {len(all_metadata)} records")

    # 2. Cluster the metadata
    try:
        clusters = cluster_by_metadata(all_metadata)
        logger.info(f"Created {len(clusters)} clusters from the metadata.")
    except Exception as e:
        logger.error(f"Failed during clustering: {e}")
        return {"statusCode": 500, "body": json.dumps("Error during clustering")}

    total_updated_count = 0
    processed_clusters = 0
    skipped_clusters = 0
    geographic_discrepancies = 0

    # 3. Process each cluster: Choose canonical, normalize, update
    for i, members in enumerate(clusters):
        if not members:
            skipped_clusters += 1
            continue

        logger.info(
            f"Processing cluster {i+1}/{len(clusters)} with {len(members)} members."
        )

        try:
            # 4. Choose canonical record for the cluster
            canonical_record = choose_canonical_metadata(members)
            if not canonical_record:
                logger.warning(
                    f"Could not choose canonical record for cluster {i+1}. Skipping."
                )
                skipped_clusters += 1
                continue

            # 5. Prepare canonical details (original name/phone, normalized address)
            canonical_details = {
                "canonical_place_id": getattr(canonical_record, "place_id", ""),
                "canonical_merchant_name": getattr(
                    canonical_record, "merchant_name", ""
                ),
                "canonical_address": normalize_address(
                    getattr(canonical_record, "address", "")
                ),  # Normalize address
                "canonical_phone_number": getattr(canonical_record, "phone_number", ""),
                # Optional: Add a unique cluster ID if desired
                # "cluster_id": f"cluster-{uuid.uuid4()}"
            }

            # Log the canonical values
            logger.info(
                f"Selected canonical record: place_id={canonical_details['canonical_place_id']}, "
                f"name={canonical_details['canonical_merchant_name']}, "
                f"address={canonical_details['canonical_address']}"
            )

            # Optional: Check for geographic discrepancies if enabled
            if geographic_validation:
                has_discrepancy = check_geographic_discrepancies(members)
                if has_discrepancy:
                    geographic_discrepancies += 1
                    logger.warning(f"Geographic discrepancy detected in cluster {i+1}")

            # 6. Update all members of the cluster in DynamoDB
            updated_in_cluster = update_items_with_canonical(members, canonical_details)
            logger.info(
                f"Updated {updated_in_cluster}/{len(members)} items for cluster {i+1} with canonical data."
            )
            total_updated_count += updated_in_cluster
            processed_clusters += 1

        except Exception as e:
            logger.error(f"Failed processing cluster {i+1}: {e}")
            skipped_clusters += 1
            # Continue with the next cluster

    # 7. Collapse duplicate canonical names within the same place_id
    updated_aliases = collapse_canonical_aliases(all_metadata)
    logger.info(f"Collapsed {len(updated_aliases)} canonical alias inconsistencies.")
    # 8. Persist alias updates to DynamoDB
    persist_alias_updates(updated_aliases)
    logger.info(f"Persisted {len(updated_aliases)} alias updates to DynamoDB.")

    execution_time = time.time() - start_time
    logger.info(
        f"Batch cleanup complete in {execution_time:.2f} seconds. "
        f"Processed {processed_clusters} clusters, skipped {skipped_clusters}. "
        f"Updated {total_updated_count} total items."
    )

    if geographic_validation:
        logger.info(
            f"Detected {geographic_discrepancies} clusters with geographic discrepancies"
        )

    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "total_metadata_records": len(all_metadata),
                "clusters_found": len(clusters),
                "clusters_processed": processed_clusters,
                "clusters_skipped": skipped_clusters,
                "items_updated": total_updated_count,
                "execution_time_seconds": execution_time,
                "geographic_discrepancies": (
                    geographic_discrepancies if geographic_validation else 0
                ),
            }
        ),
    }


def check_geographic_discrepancies(cluster_members):
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

        # Simple heuristic: look for 2-letter state code at the end of the address
        # This is a placeholder - in production you might want a more robust parser
        words = address.split()
        if len(words) >= 2:
            potential_state = words[-2]
            if len(potential_state) == 2 and potential_state.isalpha():
                states.add(potential_state.upper())

    # If we found more than one state, there's a geographic discrepancy
    return len(states) > 1
