"""ChromaDB similarity search validation for PENDING labels."""

from typing import Optional, Dict, Any, List
import logging

from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel
from receipt_label.langchain.state.currency_validation import CurrencyAnalysisState
from receipt_label.vector_store.client.base import VectorStoreInterface

logger = logging.getLogger(__name__)


async def validate_labels_chromadb(
    state: CurrencyAnalysisState,
    chroma_client: Optional[VectorStoreInterface] = None,
    line_context_range: int = 2,
    similarity_threshold: float = 0.75,
    min_matches: int = 3,
    conflict_threshold: float = 0.65,
) -> CurrencyAnalysisState:
    """
    Validate PENDING labels using ChromaDB similarity search with line context.

    This node queries ChromaDB for similar words with VALID labels and uses
    similarity scores to determine if PENDING labels should be marked as VALID
    or INVALID.

    Args:
        state: Current workflow state with proposed labels
        chroma_client: ChromaDB client for similarity search (optional)
        line_context_range: Number of lines above/below to include in context (default: 2)
        similarity_threshold: Minimum similarity to mark as VALID (default: 0.75)
        min_matches: Minimum number of similar words needed (default: 3)
        conflict_threshold: If conflicts found above this similarity, mark as INVALID (default: 0.65)

    Returns:
        Updated state with validation_status updated for labels
    """

    if not chroma_client:
        logger.info("   ⏭️  ChromaDB client not provided - skipping validation")
        return {
            "chromadb_validation_stats": {
                "validated": 0,
                "invalidated": 0,
                "skipped": 0,
                "reason": "no_chroma_client",
            }
        }

    # Get PENDING labels from both adds and updates
    pending_labels: List[ReceiptWordLabel] = []

    # Check labels to add
    labels_to_add = state.receipt_word_labels_to_add or []
    for label in labels_to_add:
        status = str(getattr(label, "validation_status", "") or "").upper()
        if status in {"PENDING", "NONE", ""}:
            pending_labels.append(label)

    # Check labels to update
    labels_to_update = state.receipt_word_labels_to_update or []
    for label in labels_to_update:
        status = str(getattr(label, "validation_status", "") or "").upper()
        if status in {"PENDING", "NONE", ""}:
            pending_labels.append(label)

    if not pending_labels:
        logger.info("   ✅ No PENDING labels to validate")
        return {
            "chromadb_validation_stats": {
                "validated": 0,
                "invalidated": 0,
                "skipped": 0,
                "reason": "no_pending_labels",
            }
        }

    logger.info(f"   🔍 Validating {len(pending_labels)} PENDING labels with ChromaDB")

    validated_count = 0
    invalidated_count = 0
    skipped_count = 0

    # Get merchant name for filtering (if available)
    merchant_name = None
    if state.receipt_metadata and hasattr(state.receipt_metadata, "merchant_name"):
        merchant_name = getattr(state.receipt_metadata, "merchant_name", None)
        if merchant_name:
            merchant_name = str(merchant_name).strip().title()

    # Validate each PENDING label
    for label in pending_labels:
        try:
            # Build ChromaDB ID for this word
            chroma_id = (
                f"IMAGE#{label.image_id}#"
                f"RECEIPT#{int(label.receipt_id):05d}#"
                f"LINE#{int(label.line_id):05d}#"
                f"WORD#{int(label.word_id):05d}"
            )

            # Get word embedding from ChromaDB
            # Pattern matches validate_currency.py: get_by_ids returns {"ids": [...], "embeddings": [[...]], "metadatas": [...]}
            try:
                results = chroma_client.get_by_ids(
                    collection_name="words",
                    ids=[chroma_id],
                    include=["metadatas", "embeddings"],  # Need embeddings for query
                )

                # Check if we got results
                if not results or not results.get("ids") or not results["ids"]:
                    logger.debug(f"   ⚠️  No embedding found for {chroma_id}")
                    skipped_count += 1
                    continue

                # Find the index of our chroma_id
                idx = results["ids"].index(chroma_id) if chroma_id in results["ids"] else -1
                if idx < 0:
                    logger.debug(f"   ⚠️  ChromaID {chroma_id} not found in results")
                    skipped_count += 1
                    continue

                # Extract embedding vector at the index
                embeddings = results.get("embeddings") or []
                if not embeddings or idx >= len(embeddings):
                    logger.debug(f"   ⚠️  No embedding vector at index {idx} for {chroma_id}")
                    skipped_count += 1
                    continue

                vector = embeddings[idx]

            except Exception as e:
                logger.debug(f"   ⚠️  Could not get embedding for {chroma_id}: {e}")
                skipped_count += 1
                continue

            # Calculate line context range
            target_line_id = int(label.line_id)
            min_line_id = max(0, target_line_id - line_context_range)
            max_line_id = target_line_id + line_context_range

            # Build where filter with line context and label type
            # Note: ChromaDB uses "valid_labels" (not "validated_labels") based on existing code
            where_conditions = [
                {"valid_labels": {"$in": [label.label]}},  # Use $in for exact match
                {"line_id": {"$gte": min_line_id}},
                {"line_id": {"$lte": max_line_id}},
            ]

            # Add merchant filter if available
            if merchant_name:
                where_conditions.append({"merchant_name": {"$eq": merchant_name}})

            where_filter = {"$and": where_conditions} if len(where_conditions) > 1 else where_conditions[0]

            # Query ChromaDB for similar words with VALID labels
            try:
                query_results = chroma_client.query(
                    collection_name="words",
                    query_embeddings=[vector],
                    n_results=20,
                    where=where_filter,
                    include=["metadatas", "distances"],
                )
            except Exception as e:
                logger.debug(f"   ⚠️  Query failed for {chroma_id}: {e}")
                # Try without line context filter as fallback
                where_filter_fallback = {"valid_labels": {"$in": [label.label]}}
                if merchant_name:
                    where_filter_fallback = {
                        "$and": [
                            {"valid_labels": {"$in": [label.label]}},
                            {"merchant_name": {"$eq": merchant_name}},
                        ]
                    }

                try:
                    query_results = chroma_client.query(
                        collection_name="words",
                        query_embeddings=[vector],
                        n_results=20,
                        where=where_filter_fallback,
                        include=["metadatas", "distances"],
                    )
                except Exception as e2:
                    logger.debug(f"   ⚠️  Fallback query also failed: {e2}")
                    skipped_count += 1
                    continue

            # Process query results
            matches = []
            if query_results and "ids" in query_results and query_results["ids"]:
                for i, _ in enumerate(query_results["ids"][0]):
                    distance = query_results["distances"][0][i] if "distances" in query_results and query_results["distances"] else 1.0
                    similarity = 1.0 - distance

                    metadata = {}
                    if "metadatas" in query_results and query_results["metadatas"]:
                        metadata = query_results["metadatas"][0][i] if i < len(query_results["metadatas"][0]) else {}

                    matches.append({
                        "similarity": similarity,
                        "metadata": metadata,
                    })

            if not matches:
                logger.debug(f"   ⏸️  No matches found for label {label.label} on word {label.word_id}")
                skipped_count += 1
                continue

            # Calculate average similarity
            avg_similarity = sum(m["similarity"] for m in matches) / len(matches)

            # Check for conflicting labels (similar words with different VALID labels)
            conflicting_labels = set()
            for match in matches:
                # Handle both list and comma-delimited string formats
                valid_labels_raw = match["metadata"].get("valid_labels") or match["metadata"].get("validated_labels") or ""
                if isinstance(valid_labels_raw, list):
                    valid_labels = set(valid_labels_raw)
                else:
                    # Comma-delimited string format: ",LABEL1,LABEL2,"
                    valid_labels = {lbl.strip() for lbl in str(valid_labels_raw).strip(",").split(",") if lbl.strip()}

                if label.label not in valid_labels and valid_labels:
                    conflicting_labels.update(valid_labels)

            # Decision logic
            has_conflicts = len(conflicting_labels) > 0
            has_enough_matches = len(matches) >= min_matches

            if has_conflicts and avg_similarity >= conflict_threshold:
                # Conflict detected - mark as INVALID
                label.validation_status = "INVALID"
                invalidated_count += 1
                logger.debug(
                    f"   ❌ Invalidated {label.label} on word {label.word_id}: "
                    f"conflict (similarity={avg_similarity:.2f}, conflicts={conflicting_labels})"
                )
            elif has_enough_matches and avg_similarity >= similarity_threshold and not has_conflicts:
                # High similarity, no conflicts - mark as VALID
                label.validation_status = "VALID"
                validated_count += 1
                logger.debug(
                    f"   ✅ Validated {label.label} on word {label.word_id}: "
                    f"similarity={avg_similarity:.2f}, matches={len(matches)}"
                )
            else:
                # Keep as PENDING - insufficient evidence
                logger.debug(
                    f"   ⏸️  Keeping {label.label} on word {label.word_id} as PENDING: "
                    f"similarity={avg_similarity:.2f}, matches={len(matches)}, "
                    f"conflicts={has_conflicts}"
                )
                skipped_count += 1

        except Exception as e:
            logger.warning(f"   ⚠️  Error validating label {label.label} on word {label.word_id}: {e}")
            skipped_count += 1
            continue

    logger.info(
        f"   📊 ChromaDB Validation Results: "
        f"✅ {validated_count} validated, "
        f"❌ {invalidated_count} invalidated, "
        f"⏸️  {skipped_count} kept as PENDING"
    )

    return {
        "receipt_word_labels_to_add": labels_to_add,
        "receipt_word_labels_to_update": labels_to_update,
        "chromadb_validation_stats": {
            "validated": validated_count,
            "invalidated": invalidated_count,
            "skipped": skipped_count,
            "total_processed": len(pending_labels),
        },
    }

