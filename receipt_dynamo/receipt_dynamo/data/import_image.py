# infra/lambda_layer/python/dynamo/data/import_image.py
import json
import os
from datetime import datetime, timezone
from typing import Any

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data.export_image import (
    receipt_summary_record_from_export,
)
from receipt_dynamo.data.shared_exceptions import EntityValidationError
from receipt_dynamo.entities import (
    Image,
    Letter,
    Line,
    OCRJob,
    OCRRoutingDecision,
    Receipt,
    ReceiptBarcode,
    ReceiptFactOverride,
    ReceiptLetter,
    ReceiptLine,
    ReceiptLineItem,
    ReceiptPlace,
    ReceiptRow,
    ReceiptSection,
    ReceiptWord,
    ReceiptWordLabel,
    Word,
)
from receipt_dynamo.entities.receipt_embedding import (
    ReceiptLineEmbedding,
    ReceiptWordEmbedding,
)


def _parse_datetimes(item: dict, fields: list[str]) -> dict:
    """Parse ISO datetime strings to datetime objects for specified fields."""
    item = dict(item)
    for field in fields:
        val = item.get(field)
        if isinstance(val, str):
            item[field] = datetime.fromisoformat(val)
    return item


def import_image(table_name: str, json_path: str) -> None:
    """
    Imports data from a JSON file into DynamoDB.
    The JSON file should be in the format produced by the export() function.

    Args:
        table_name (str): The DynamoDB table name where data should be imported
        json_path (str): Path to the JSON file containing the data

    Raises:
        ValueError: If table_name is not provided and the environment
            variable DYNAMO_DB_TABLE is not set
        FileNotFoundError: If the JSON file doesn't exist
        Exception: If there are errors accessing DynamoDB

    Example:
        >>> import_image("ReceiptsTable", "./export/image-id.json")
    """

    if not os.path.exists(json_path):
        raise FileNotFoundError(f"JSON file not found: {json_path}")

    # Initialize DynamoDB client
    dynamo_client = DynamoClient(table_name)

    # Read the JSON file
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Convert dictionaries back to entity objects
    entities: dict[str, list[Any]] = {
        "images": [Image(**item) for item in data["images"]],
        "lines": [Line(**item) for item in data["lines"]],
        "words": [Word(**item) for item in data["words"]],
        "letters": [Letter(**item) for item in data["letters"]],
        "receipts": [Receipt(**item) for item in data["receipts"]],
        "receipt_lines": [
            ReceiptLine(**item) for item in data["receipt_lines"]
        ],
        "receipt_words": [
            ReceiptWord(**item) for item in data["receipt_words"]
        ],
        "receipt_letters": [
            ReceiptLetter(**item) for item in data["receipt_letters"]
        ],
        "receipt_word_labels": [
            ReceiptWordLabel(**item)
            for item in data.get("receipt_word_labels", [])
        ],
        "receipt_places": [
            ReceiptPlace(**_parse_datetimes(item, ["timestamp"]))
            for item in data.get("receipt_places", [])
        ],
        "receipt_barcodes": [
            ReceiptBarcode(**item) for item in data.get("receipt_barcodes", [])
        ],
        "ocr_jobs": [
            OCRJob(**_parse_datetimes(item, ["created_at", "updated_at"]))
            for item in data.get("ocr_jobs", [])
        ],
        "ocr_routing_decisions": [
            OCRRoutingDecision(
                **_parse_datetimes(item, ["created_at", "updated_at"])
            )
            for item in data.get("ocr_routing_decisions", [])
        ],
        # Derived rows and vectors exported since 2026-09 (#1649). Flat
        # dataclasses rebuild with **item; the summary record is nested and
        # goes through the shared reconstruction next to the exporter.
        "receipt_rows": [
            ReceiptRow(**item) for item in data.get("receipt_rows", [])
        ],
        "receipt_sections": [
            ReceiptSection(**item) for item in data.get("receipt_sections", [])
        ],
        "receipt_line_items": [
            ReceiptLineItem(**item)
            for item in data.get("receipt_line_items", [])
        ],
        "receipt_summaries": [
            receipt_summary_record_from_export(item)
            for item in data.get("receipt_summaries", [])
        ],
        "receipt_fact_overrides": [
            ReceiptFactOverride(**item)
            for item in data.get("receipt_fact_overrides", [])
        ],
        "receipt_embeddings": [
            (
                ReceiptWordEmbedding(**item)
                if "word_vector" in item
                else ReceiptLineEmbedding(**item)
            )
            for item in data.get("receipt_embeddings", [])
        ],
    }

    # Import data in batches using existing DynamoClient methods
    if entities["images"]:
        dynamo_client.add_images(entities["images"])  # type: ignore[arg-type]

    if entities["lines"]:
        dynamo_client.add_lines(entities["lines"])  # type: ignore[arg-type]

    if entities["words"]:
        dynamo_client.add_words(entities["words"])  # type: ignore[arg-type]

    if entities["letters"]:
        # type: ignore[arg-type]
        dynamo_client.add_letters(entities["letters"])

    if entities["receipts"]:
        # type: ignore[arg-type]
        dynamo_client.add_receipts(entities["receipts"])

    if entities["receipt_lines"]:
        # type: ignore[arg-type]
        dynamo_client.add_receipt_lines(entities["receipt_lines"])

    if entities["receipt_words"]:
        # type: ignore[arg-type]
        dynamo_client.add_receipt_words(entities["receipt_words"])

    if entities["receipt_letters"]:
        # type: ignore[arg-type]
        dynamo_client.add_receipt_letters(entities["receipt_letters"])

    if entities["receipt_word_labels"]:
        # type: ignore[arg-type]
        # Imports restore an existing snapshot rather than minting new labels.
        # Preserve legacy keys verbatim; ordinary application/model writes
        # remain restricted to CORE_LABELS by the default add policy.
        dynamo_client.add_receipt_word_labels(
            entities["receipt_word_labels"],
            allow_non_core_labels=True,
        )

    if entities["receipt_places"]:
        # type: ignore[arg-type]
        dynamo_client.add_receipt_places(entities["receipt_places"])

    if entities["receipt_barcodes"]:
        # type: ignore[arg-type]
        dynamo_client.add_receipt_barcodes(entities["receipt_barcodes"])

    if entities["ocr_jobs"]:
        # type: ignore[arg-type]
        dynamo_client.add_ocr_jobs(entities["ocr_jobs"])

    if entities["ocr_routing_decisions"]:
        # type: ignore[arg-type]
        dynamo_client.add_ocr_routing_decisions(
            entities["ocr_routing_decisions"]
        )

    # Dependency order: rows -> sections (reference row_ids) -> line items.
    if entities["receipt_rows"]:
        dynamo_client.add_receipt_rows(entities["receipt_rows"])
    if entities["receipt_sections"]:
        dynamo_client.add_receipt_sections(entities["receipt_sections"])
    if entities["receipt_line_items"]:
        dynamo_client.add_receipt_line_items(entities["receipt_line_items"])
    if entities["receipt_summaries"]:
        dynamo_client.add_receipt_summaries(entities["receipt_summaries"])
    for override in entities["receipt_fact_overrides"]:
        # Owner facts are stated on the dev table only; the DAL refuses
        # them elsewhere. A restore into a protected table keeps the rest
        # of the snapshot rather than aborting on this row.
        try:
            dynamo_client.restore_receipt_fact_override(override)
        except EntityValidationError as exc:
            if "owner facts are stated on the dev table only" not in str(exc):
                raise
    if entities["receipt_embeddings"]:
        dynamo_client.add_receipt_embeddings(entities["receipt_embeddings"])


# Every TYPE import_image can write back. restore_image deletes ONLY these,
# so a row of any other type in the partition (e.g. a nutrition snapshot
# the export does not carry) survives the restore instead of being swept
# and never re-created.
IMPORTABLE_TYPES: frozenset[str] = frozenset(
    {
        "IMAGE",
        "LINE",
        "WORD",
        "LETTER",
        "RECEIPT",
        "RECEIPT_LINE",
        "RECEIPT_WORD",
        "RECEIPT_LETTER",
        "RECEIPT_WORD_LABEL",
        "RECEIPT_PLACE",
        "RECEIPT_BARCODE",
        "OCR_JOB",
        "OCR_ROUTING_DECISION",
        "RECEIPT_ROW",
        "RECEIPT_SECTION",
        "RECEIPT_LINE_ITEM",
        "RECEIPT_SUMMARY",
        "RECEIPT_FACT_OVERRIDE",
        "RECEIPT_LINE_EMBEDDING",
        "RECEIPT_WORD_EMBEDDING",
    }
)


def restore_image(table_name: str, json_path: str) -> None:
    """Delete the records the backup can re-create, then import it.

    Only TYPEs in :data:`IMPORTABLE_TYPES` are deleted first; anything else
    under the partition is left in place because the import could not
    bring it back. (``delete_image_data`` remains the unconditional sweep
    for callers that want everything gone.)

    Warning: not atomic — if import fails after deletion, data may be lost.
    Re-run with the same JSON to recover.
    """
    image_id = os.path.splitext(os.path.basename(json_path))[0]
    DynamoClient(table_name).delete_image_details(
        image_id, entity_types=set(IMPORTABLE_TYPES)
    )
    import_image(table_name, json_path)
