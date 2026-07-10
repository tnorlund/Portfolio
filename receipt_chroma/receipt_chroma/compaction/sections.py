"""Section update processing for RECEIPT_SECTION entities.

ReceiptSection rows classify a receipt's lines into sections (HEADER,
LINE_ITEMS, ...). The LINES collection stores one embedding per visual
row whose ``section_label`` metadata is the plurality of the row's
lines' sections (INVALID sections skipped, higher-confidence section
wins a contested line, ties leave the key unset).

Design notes (race-condition driven):

- RECOMPUTE, DON'T APPLY THE EVENT: a single section edit can change the
  correct ``section_label`` for many rows, and the correct value depends
  on ALL of the receipt's sections. Each message triggers a re-read of
  the receipt's *current* section set from DynamoDB and a full label
  recompute, reusing ``sections_to_line_map`` + ``row_section_from_map``
  (the exact logic used at embed time). Duplicate or out-of-order stream
  deliveries are therefore idempotent: last processing wins with fresh
  state, and INSERT/MODIFY/REMOVE events are all handled identically.

- MISSING ROWS ARE A CLEAN NO-OP: a section event can arrive before the
  receipt's rows exist in ChromaDB (embedding batch still at OpenAI).
  The embed itself stamps fresh section state when it lands, so there is
  nothing to do here — no error, no retry.

- STORM COLLAPSE: messages are grouped per (image_id, receipt_id) so a
  bulk-QA burst of N section events for one receipt costs one DynamoDB
  query + one recompute pass within a batch.
"""

import json
from typing import Any, Dict, List, Optional

from receipt_chroma.compaction.models import MetadataUpdateResult
from receipt_chroma.data.chroma_client import ChromaClient
from receipt_chroma.section_labels import (
    row_section_from_map,
    sections_to_line_map,
)
from receipt_dynamo.constants import ChromaDBCollection
from receipt_dynamo.data.dynamo_client import DynamoClient

_SECTION_LABEL_KEY = "section_label"


def apply_section_updates(
    chroma_client: ChromaClient,
    section_messages: List[Any],
    collection: ChromaDBCollection,
    logger: Any,
    metrics: Any = None,
    dynamo_client: Optional[DynamoClient] = None,
) -> List[MetadataUpdateResult]:
    """Recompute row ``section_label`` metadata for changed receipts.

    Processes RECEIPT_SECTION stream messages against an already-open
    ChromaDB client (no S3 I/O — the caller owns snapshot download,
    upload, and locking, exactly like the place/label paths).

    Only the LINES collection carries section metadata; messages for any
    other collection are a no-op.

    Args:
        chroma_client: Open ChromaDB client with snapshot loaded
        section_messages: StreamMessage objects for section changes
        collection: Target collection (only LINES is acted on)
        logger: Logger instance for observability
        metrics: Optional metrics collector
        dynamo_client: DynamoDB client used to fetch the receipt's
            current sections; without it recompute is skipped

    Returns:
        List of MetadataUpdateResult objects (one per affected receipt)
    """
    results: List[MetadataUpdateResult] = []
    if not section_messages:
        return results

    if collection != ChromaDBCollection.LINES:
        logger.debug(
            "Section updates only apply to LINES collection",
            collection=collection.value,
        )
        return results

    if dynamo_client is None:
        logger.warning(
            "No DynamoDB client; skipping section recompute",
            message_count=len(section_messages),
        )
        return results

    database = collection.value
    try:
        collection_obj = chroma_client.get_collection(database)
    except Exception:
        # Collection may not exist yet (fresh environment) — safe no-op,
        # embeds will stamp fresh state when the collection is created.
        logger.warning(
            "Collection not found for sections", collection=database
        )
        return results

    # Collapse to one recompute per receipt: the recompute reads fresh
    # state, so N events for one receipt need only one pass.
    receipts: Dict[tuple, None] = {}
    for update_msg in section_messages:
        entity_data = update_msg.entity_data
        image_id = entity_data.get("image_id")
        receipt_id = entity_data.get("receipt_id")
        if image_id is None or receipt_id is None:
            logger.warning(
                "Section message missing image_id/receipt_id",
                entity_data=dict(entity_data),
            )
            continue
        receipts[(image_id, receipt_id)] = None

    for image_id, receipt_id in receipts:
        try:
            updated_count = _recompute_receipt_section_labels(
                collection_obj=collection_obj,
                image_id=image_id,
                receipt_id=receipt_id,
                dynamo_client=dynamo_client,
                logger=logger,
            )
            results.append(
                MetadataUpdateResult(
                    database=database,
                    collection=database,
                    updated_count=updated_count,
                    image_id=image_id,
                    receipt_id=receipt_id,
                )
            )
            if metrics:
                metrics.count("SectionRecomputeProcessed", 1)

        except Exception as e:
            logger.exception(
                "Error recomputing section labels",
                image_id=image_id,
                receipt_id=receipt_id,
            )
            results.append(
                MetadataUpdateResult(
                    database=database,
                    collection=database,
                    updated_count=0,
                    image_id=image_id,
                    receipt_id=receipt_id,
                    error=str(e),
                )
            )

    return results


def _row_line_ids_from_metadata(metadata: dict) -> List[int]:
    """Extract the visual row's line IDs from row metadata.

    Mirrors the label path: ``row_line_ids`` is a JSON-encoded list;
    legacy embeddings without it fall back to the single ``line_id``.
    """
    row_line_ids_str = metadata.get("row_line_ids", "[]")
    try:
        row_line_ids = json.loads(row_line_ids_str)
    except (json.JSONDecodeError, TypeError):
        row_line_ids = []
    if not row_line_ids:
        line_id = metadata.get("line_id")
        if line_id is not None:
            row_line_ids = [line_id]
    return row_line_ids


def _recompute_receipt_section_labels(
    collection_obj: Any,
    image_id: str,
    receipt_id: int,
    dynamo_client: DynamoClient,
    logger: Any,
) -> int:
    """Recompute ``section_label`` for every row of one receipt.

    Reads the receipt's current ReceiptSection rows from DynamoDB and
    stamps each Chroma row with the plurality section of its lines,
    removing the key where no clear plurality exists.

    Returns the number of rows whose metadata changed. Raises on
    DynamoDB/Chroma failures so the caller records an error result and
    the message is retried (recomputing from a failed read could
    otherwise wrongly strip labels).
    """
    existing_results = collection_obj.get(
        where={
            "$and": [
                {"image_id": {"$eq": image_id}},
                {"receipt_id": {"$eq": receipt_id}},
            ]
        },
        include=["metadatas"],
    )

    if not existing_results["ids"]:
        # Receipt not embedded yet (e.g. batch still at OpenAI). The
        # embed stamps fresh section state when it lands — clean no-op.
        logger.debug(
            "No existing line embeddings for receipt; skipping sections",
            image_id=image_id,
            receipt_id=receipt_id,
        )
        return 0

    sections = dynamo_client.get_receipt_sections_from_receipt(
        image_id=image_id,
        receipt_id=receipt_id,
    )
    section_by_line = sections_to_line_map(sections)

    ids_to_update: List[str] = []
    new_metadatas: List[dict] = []
    for idx, chromadb_id in enumerate(existing_results["ids"]):
        metadata = existing_results["metadatas"][idx] or {}
        row_line_ids = _row_line_ids_from_metadata(metadata)
        row_section = row_section_from_map(row_line_ids, section_by_line)

        if metadata.get(_SECTION_LABEL_KEY) == row_section:
            continue  # includes both-absent (None == None)

        new_metadata = {
            k: v for k, v in metadata.items() if k != _SECTION_LABEL_KEY
        }
        if row_section is not None:
            new_metadata[_SECTION_LABEL_KEY] = row_section
        ids_to_update.append(chromadb_id)
        new_metadatas.append(new_metadata)

    if ids_to_update:
        collection_obj.update(ids=ids_to_update, metadatas=new_metadatas)

    logger.debug(
        "Recomputed section labels",
        image_id=image_id,
        receipt_id=receipt_id,
        rows_total=len(existing_results["ids"]),
        rows_updated=len(ids_to_update),
        section_count=len(sections),
    )

    return len(ids_to_update)
