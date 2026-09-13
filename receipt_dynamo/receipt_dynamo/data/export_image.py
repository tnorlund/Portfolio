# infra/lambda_layer/python/dynamo/data/export_image.py
import json
import os
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from typing import Any

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities.receipt_summary import (
    MonetaryTotals,
    ReceiptSummary,
)
from receipt_dynamo.entities.receipt_summary_record import (
    ReceiptSummaryRecord,
)
from receipt_dynamo.entities.util import assert_valid_uuid


def datetime_handler(obj: Any) -> Any:
    """JSON encoder for types DynamoDB hands back that json cannot encode.

    DynamoDB returns every numeric attribute as ``Decimal``. Most entity
    converters cast those to ``int``/``float``, but not all of them do on
    every field, so a single un-cast attribute anywhere in an image would
    otherwise abort the whole export.
    """
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        # Exact integers stay integers so ids and counts do not gain a
        # ".0"; everything else becomes a float, which is what the entity
        # constructors on the copy side expect.
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    if isinstance(obj, (set, frozenset)):
        return sorted(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def receipt_summary_record_from_export(
    raw: dict[str, Any],
) -> ReceiptSummaryRecord:
    """Rebuild a ReceiptSummaryRecord from its exported ``asdict`` form.

    ``asdict()`` flattens nested dataclasses into plain dicts and
    ``Class(**d)`` does not rebuild them, so a record has to be
    reassembled level by level: ReceiptSummaryRecord wraps a
    ReceiptSummary, which holds a MonetaryTotals.

    This lives here, next to the exporter that produced the dict, so the
    dev->prod copier and its tests share one implementation. Rebuilding
    the record as a bare ReceiptSummary, or the summary without restoring
    MonetaryTotals, is what broke a 749-image promotion after 661 prod
    partitions had already been deleted.
    """
    inner = dict(raw["summary"])
    totals = inner.get("totals")
    if isinstance(totals, dict):
        inner["totals"] = MonetaryTotals(**totals)
    # JSON turned the datetimes into ISO strings. The copier's loader
    # converts them back before calling here, import_image does not; be
    # correct for both rather than depend on the caller.
    for name in ("date", "bank_date"):
        if isinstance(inner.get(name), str):
            inner[name] = datetime.fromisoformat(inner[name])
    return ReceiptSummaryRecord(
        summary=ReceiptSummary(**inner),
        timestamp_computed=raw.get("timestamp_computed"),
        overrides_applied=raw.get("overrides_applied") or [],
    )


def export_image(table_name: str, image_id: str, output_dir: str) -> None:
    """
    Exports all DynamoDB data related to an image as JSON.

    Args:
        table_name (str): The DynamoDB table name where receipt data is stored
        image_id (str): UUID of the image to export
        output_dir (str): Directory where JSON file should be exported

    Raises:
        ValueError: If table_name is not provided and the environment variable
            DYNAMO_DB_TABLE is not set
        Exception: If there are errors accessing DynamoDB

    Example:
        >>> export_image(
        ...     "ReceiptsTable",
        ...     "550e8400-e29b-41d4-a716-446655440000",
        ...     "./export"
        ... )
    """

    # Initialize DynamoDB client
    dynamo_client = DynamoClient(table_name)

    # Create output directory
    os.makedirs(output_dir, exist_ok=True)

    # Get all data from DynamoDB
    details = dynamo_client.get_image_details(image_id)

    images = details.images
    lines = details.lines
    words = details.words
    letters = details.letters
    receipts = details.receipts
    receipt_lines = details.receipt_lines
    receipt_words = details.receipt_words
    receipt_letters = details.receipt_letters
    receipt_word_labels = details.receipt_word_labels
    receipt_places = details.receipt_places
    receipt_barcodes = details.receipt_barcodes
    receipt_rows = details.receipt_rows
    receipt_sections = details.receipt_sections
    receipt_line_items = details.receipt_line_items
    receipt_summaries = details.receipt_summaries
    receipt_fact_overrides = details.receipt_fact_overrides
    receipt_embeddings = details.receipt_embeddings
    ocr_jobs = details.ocr_jobs
    ocr_routing_decisions = details.ocr_routing_decisions

    if not images:
        raise ValueError(f"No image found for image_id {image_id}")

    # Export DynamoDB data as JSON
    results = {
        "images": [asdict(image) for image in images],
        "lines": [asdict(line) for line in lines],
        "words": [asdict(word) for word in words],
        "letters": [asdict(letter) for letter in letters],
        "receipts": [asdict(receipt) for receipt in receipts],
        "receipt_lines": [asdict(line) for line in receipt_lines],
        "receipt_words": [asdict(word) for word in receipt_words],
        "receipt_letters": [asdict(letter) for letter in receipt_letters],
        "receipt_word_labels": [
            asdict(label) for label in receipt_word_labels
        ],
        "receipt_places": [asdict(place) for place in receipt_places],
        "receipt_barcodes": [asdict(bc) for bc in receipt_barcodes],
        "receipt_rows": [asdict(row) for row in receipt_rows],
        "receipt_sections": [asdict(section) for section in receipt_sections],
        "receipt_line_items": [asdict(li) for li in receipt_line_items],
        "receipt_summaries": [asdict(s) for s in receipt_summaries],
        "receipt_fact_overrides": [asdict(o) for o in receipt_fact_overrides],
        "receipt_embeddings": [asdict(e) for e in receipt_embeddings],
        "ocr_jobs": [asdict(job) for job in ocr_jobs],
        "ocr_routing_decisions": [
            asdict(decision) for decision in ocr_routing_decisions
        ],
    }

    with open(
        os.path.join(output_dir, f"{image_id}.json"), "w", encoding="utf-8"
    ) as f:
        json.dump(results, f, indent=4, default=datetime_handler)


# TYPE attribute -> the collection name delete_image_data has always reported.
_TYPE_TO_COLLECTION: dict[str, str] = {
    "IMAGE": "images",
    "LINE": "lines",
    "WORD": "words",
    "LETTER": "letters",
    "RECEIPT": "receipts",
    "RECEIPT_LINE": "receipt_lines",
    "RECEIPT_WORD": "receipt_words",
    "RECEIPT_LETTER": "receipt_letters",
    "RECEIPT_WORD_LABEL": "receipt_word_labels",
    "RECEIPT_PLACE": "receipt_places",
    "RECEIPT_METADATA": "receipt_metadatas",
    "RECEIPT_BARCODE": "receipt_barcodes",
    "OCR_JOB": "ocr_jobs",
    "OCR_ROUTING_DECISION": "ocr_routing_decisions",
    "RECEIPT_ROW": "receipt_rows",
    "RECEIPT_SECTION": "receipt_sections",
    "RECEIPT_LINE_ITEM": "receipt_line_items",
    "RECEIPT_SUMMARY": "receipt_summaries",
    "RECEIPT_FACT_OVERRIDE": "receipt_fact_overrides",
    "RECEIPT_LINE_EMBEDDING": "receipt_embeddings",
    "RECEIPT_WORD_EMBEDDING": "receipt_embeddings",
}


def delete_image_data(table_name: str, image_id: str) -> dict[str, int]:
    """
    Deletes ALL DynamoDB records for a given image_id.

    Sweeps the whole ``IMAGE#{image_id}`` partition via
    ``delete_image_details`` regardless of entity TYPE. It used to iterate
    an explicit per-type allowlist, which silently left behind every type
    added after it (rows, sections, line items, summaries, overrides,
    vectors) — so a restore over existing data kept stale rows and a
    restore into an empty table was incomplete.

    Args:
        table_name: The DynamoDB table name
        image_id: UUID of the image whose data should be deleted

    Returns:
        A dict mapping collection name (``images``, ``receipt_words``, ...)
        to the number of records deleted, as this function always has.
        Both embedding TYPEs report under ``receipt_embeddings``. An id
        that is not a valid UUIDv4 cannot address any row and returns ``{}``.
    """
    # Same validator the DAL applies. uuid.UUID(x, version=4) would coerce
    # the version/variant bits and accept compact or v6 text that the sweep
    # then rejects with OperationError instead of the documented {}.
    try:
        assert_valid_uuid(image_id)
    except (ValueError, TypeError):
        return {}
    by_type = DynamoClient(table_name).delete_image_details(image_id)
    counts: dict[str, int] = {}
    for entity_type, n in by_type.items():
        name = _TYPE_TO_COLLECTION.get(entity_type, entity_type.lower())
        counts[name] = counts.get(name, 0) + n
    return counts
