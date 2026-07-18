"""
Place data utilities for receipt combination.

This module contains functions for selecting and migrating place data and labels
when combining receipts.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import fields
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import MerchantValidationStatus
from receipt_dynamo.data.shared_exceptions import EntityAlreadyExistsError
from receipt_dynamo.entities import ReceiptPlace, ReceiptWordLabel

logger = logging.getLogger(__name__)

# Mutable container fields that must be deep-copied when cloning a place so
# the source entity and the merged entity cannot share list/dict identity.
_MUTABLE_PLACE_FIELDS = (
    "merchant_types",
    "matched_fields",
    "hours_summary",
    "photo_references",
    "address_components",
    "hours_data",
)


def get_best_receipt_place(
    client: DynamoClient,
    image_id: str,
    receipt_ids: List[int],
) -> Optional[ReceiptPlace]:
    """
    Get the best ReceiptPlace from the original receipts.

    Returns the live Dynamo entity for the winning source receipt. Callers that
    need to re-key the place onto a new receipt_id must clone via
    ``clone_receipt_place_for_receipt`` — never mutate ``receipt_id`` in place.

    Args:
        client: DynamoDB client
        image_id: Image ID containing the receipts
        receipt_ids: List of receipt IDs to consider

    Returns:
        Best ReceiptPlace or None if no valid place data found
    """
    places = []
    for receipt_id in receipt_ids:
        try:
            place = client.get_receipt_place(image_id, receipt_id)
            if place and place.merchant_name and place.merchant_name.strip():
                places.append(place)
        except Exception as e:  # pylint: disable=broad-except
            logger.error(
                "Failed to fetch place data for image_id=%s receipt_id=%s: %s",
                image_id,
                receipt_id,
                e,
                exc_info=True,
            )
            continue

    if not places:
        return None

    def score_place(place: ReceiptPlace) -> int:
        """Score place data based on completeness and validation status."""
        score = 0
        if place.place_id and place.place_id.strip():
            score += 10
        if place.merchant_name and place.merchant_name.strip():
            score += 5
        if place.formatted_address and place.formatted_address.strip():
            score += 3
        if place.phone_number and place.phone_number.strip():
            score += 2
        if place.validation_status == MerchantValidationStatus.MATCHED.value:
            score += 5
        elif place.validation_status == MerchantValidationStatus.UNSURE.value:
            score += 2
        return score

    places.sort(key=lambda p: (score_place(p), p.timestamp), reverse=True)
    return places[0]


def clone_receipt_place_for_receipt(
    source: ReceiptPlace,
    *,
    new_receipt_id: int,
    reasoning_prefix: str | None = None,
    refresh_timestamp: bool = True,
) -> ReceiptPlace:
    """
    Deep-copy a ReceiptPlace onto a new receipt_id without mutating ``source``.

    Preserves all Google Places fields (geo, hours, confidence, media, etc.)
    that partial field-by-field constructors historically dropped.
    """
    kwargs = {f.name: getattr(source, f.name) for f in fields(source)}
    kwargs["receipt_id"] = new_receipt_id
    for name in _MUTABLE_PLACE_FIELDS:
        if name in kwargs:
            kwargs[name] = deepcopy(kwargs[name])
    if refresh_timestamp:
        kwargs["timestamp"] = datetime.now(timezone.utc)
    if reasoning_prefix:
        original = kwargs.get("reasoning") or "N/A"
        kwargs["reasoning"] = f"{reasoning_prefix}{original}"
    return ReceiptPlace(**kwargs)


def upsert_receipt_place(client: DynamoClient, place: ReceiptPlace) -> str:
    """
    Persist a ReceiptPlace, treating an existing key as an overwrite.

    Merge/combine retries can leave a place row for ``new_receipt_id`` after a
    partial write. ``add_receipt_place`` is create-only and would otherwise
    abort the pipeline before embeddings and source deletion.

    Returns:
        ``"added"`` if the place was created, ``"updated"`` if it already existed.
    """
    try:
        client.add_receipt_place(place)
        return "added"
    except EntityAlreadyExistsError:
        logger.warning(
            "ReceiptPlace already exists for image_id=%s receipt_id=%s; "
            "updating in place (merge/combine retry)",
            place.image_id,
            place.receipt_id,
        )
        client.update_receipt_place(place)
        return "updated"


def migrate_receipt_word_labels(
    client: DynamoClient,
    image_id: str,
    original_receipt_ids: List[int],
    word_id_map: Dict[Tuple[int, int, int], int],
    line_id_map: Dict[Tuple[int, int], int],
    new_receipt_id: int,
) -> List[ReceiptWordLabel]:
    """
    Migrate ReceiptWordLabel entities from original receipts to the new combined receipt.

    Args:
        client: DynamoDB client
        image_id: Image ID containing the receipts
        original_receipt_ids: List of original receipt IDs
        word_id_map: Mapping from (word_id, line_id, receipt_id) to new word_id
        line_id_map: Mapping from (line_id, receipt_id) to new line_id
        new_receipt_id: ID of the new combined receipt

    Returns:
        List of migrated ReceiptWordLabel entities
    """
    new_labels = []
    for receipt_id in original_receipt_ids:
        try:
            # Paginate through all labels for this receipt
            last_evaluated_key = None
            while True:
                labels, last_evaluated_key = (
                    client.list_receipt_word_labels_for_receipt(
                        image_id,
                        receipt_id,
                        last_evaluated_key=last_evaluated_key,
                    )
                )

                for label in labels:
                    original_key = (label.word_id, label.line_id, receipt_id)
                    new_word_id = word_id_map.get(original_key)
                    new_line_id = line_id_map.get((label.line_id, receipt_id))
                    if new_word_id is None or new_line_id is None:
                        continue
                    new_label = ReceiptWordLabel(
                        image_id=image_id,
                        receipt_id=new_receipt_id,
                        line_id=new_line_id,
                        word_id=new_word_id,
                        label=label.label,
                        reasoning=label.reasoning
                        or f"Migrated from receipt {receipt_id}, word {label.word_id}",
                        timestamp_added=datetime.now(timezone.utc),
                        validation_status=label.validation_status,
                        label_proposed_by=label.label_proposed_by
                        or "receipt_combination",
                        label_consolidated_from=f"receipt_{receipt_id}_word_{label.word_id}",
                    )
                    new_labels.append(new_label)

                # Continue if there are more pages
                if not last_evaluated_key:
                    break
        except Exception as e:  # pylint: disable=broad-except
            logger.error(
                "Failed to migrate word labels for image_id=%s receipt_id=%s: %s",
                image_id,
                receipt_id,
                e,
                exc_info=True,
            )
            continue
    return new_labels
