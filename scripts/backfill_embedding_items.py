#!/usr/bin/env python3.13
"""Backfill RECEIPT_*_EMBEDDING items on the **dev** table only.

Writes embedding items for golden receipts (plus ``--extra-receipts``),
capped by ``--limit``. Re-runs skip existing keys. Never creates vector
indexes and never touches prod.

    python scripts/backfill_embedding_items.py --limit 2 --allow-under-floor
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))
for package_root in (
    REPOSITORY_ROOT / "receipt_embeddings",
    REPOSITORY_ROOT / "receipt_dynamo",
    REPOSITORY_ROOT / "receipt_chroma",
):
    sys.path.insert(0, str(package_root))

from scripts.similarity_harness.common import MIN_RECEIPTS  # noqa: E402

LINE_ITEM_GOLDEN = (
    REPOSITORY_ROOT
    / "receipt_upload"
    / "tests"
    / "fixtures"
    / "line_items_golden.json"
)

from receipt_dynamo import DynamoClient  # noqa: E402
from receipt_dynamo.constants import ValidationStatus  # noqa: E402
from receipt_dynamo.entities.receipt_line_embedding import (  # noqa: E402
    ReceiptLineEmbedding,
)
from receipt_dynamo.entities.receipt_word_embedding import (  # noqa: E402
    ReceiptWordEmbedding,
)

from receipt_embeddings.formatting.line_format import (  # noqa: E402
    get_row_embedding_inputs,
)
from receipt_embeddings.formatting.word_format import (  # noqa: E402
    format_word_context_embedding_input,
)
from receipt_embeddings.indexes import (  # noqa: E402
    DEV_TABLE_NAME,
    EMBEDDING_DIMENSION,
    LINE_INDEX,
    MAX_SEARCH_VECTORS_TOP_K,
    PROD_TABLE_NAME,
    WORD_INDEX,
)
from receipt_embeddings.writer import (  # noqa: E402
    WriteReport,
    embed_texts_checked,
    owned_keys_in_hits,
    put_embedding_items,
)

_LABEL_MAP = {
    ValidationStatus.VALID.value: "validated",
    ValidationStatus.PENDING.value: "pending",
}


def _load_receipts(
    manifest: Path | None, extra_path: Path | None
) -> list[dict[str, Any]]:
    if manifest is not None:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        values = (
            payload.get("receipts") if isinstance(payload, dict) else payload
        )
    else:
        payload = json.loads(LINE_ITEM_GOLDEN.read_text(encoding="utf-8"))
        values = payload["receipts"]
    receipts = [
        {
            "image_id": str(row["image_id"]),
            "receipt_id": int(row["receipt_id"]),
        }
        for row in values
    ]
    if extra_path is not None:
        extra = json.loads(extra_path.read_text(encoding="utf-8"))
        extra_rows = (
            extra.get("receipts") if isinstance(extra, dict) else extra
        )
        receipts.extend(
            {
                "image_id": str(row["image_id"]),
                "receipt_id": int(row["receipt_id"]),
            }
            for row in extra_rows
        )
    return receipts


def _refuse_prod(table_name: str) -> None:
    if table_name == PROD_TABLE_NAME:
        raise SystemExit("refusing to write embedding items to prod")
    if table_name != DEV_TABLE_NAME:
        raise SystemExit(
            f"refusing table {table_name!r}; only {DEV_TABLE_NAME} is allowed"
        )


def _label_status(labels: Sequence[Any], line_id: int, word_id: int) -> str:
    for label in labels:
        if label.line_id == line_id and label.word_id == word_id:
            return _LABEL_MAP.get(label.validation_status, "none")
    return "none"


def _primary_label(
    labels: Sequence[Any], line_id: int, word_id: int
) -> str | None:
    for label in labels:
        if label.line_id == line_id and label.word_id == word_id:
            return str(label.label)
    return None


def _section_by_line(
    dynamo: Any, image_id: str, receipt_id: int
) -> dict[int, str]:
    mapping: dict[int, str] = {}
    try:
        rows = dynamo.get_receipt_rows_from_receipt(image_id, receipt_id)
        sections = dynamo.get_receipt_sections_from_receipt(
            image_id, receipt_id
        )
    except Exception:  # noqa: BLE001
        return mapping
    rows_by_id = {row.row_id: row for row in rows}
    for section in sections:
        for row_id in getattr(section, "row_ids", None) or []:
            row = rows_by_id.get(row_id)
            if row is None:
                continue
            for line_id in row.line_ids:
                mapping[line_id] = str(section.section_type)
    return mapping


def _build_items(
    dynamo: Any, image_id: str, receipt_id: int
) -> tuple[list[Any], str | None]:
    lines = dynamo.list_receipt_lines_from_receipt(image_id, receipt_id)
    if not lines:
        return [], "receipt_not_found"
    words = dynamo.list_receipt_words_from_receipt(image_id, receipt_id)
    try:
        labels = dynamo.list_receipt_word_labels_for_receipt(
            image_id, receipt_id
        )
    except Exception:  # noqa: BLE001
        labels = []
    try:
        place = dynamo.get_receipt_place(image_id, receipt_id)
        merchant_name = getattr(place, "name", None) or getattr(
            place, "merchant_name", None
        )
    except Exception:  # noqa: BLE001
        merchant_name = None
    section_by_line = _section_by_line(dynamo, image_id, receipt_id)
    items: list[Any] = []
    for text, line_ids in get_row_embedding_inputs(lines):
        primary = line_ids[0]
        items.append(
            (
                f"line:{image_id}#{receipt_id:05d}#{primary:05d}",
                text,
                ReceiptLineEmbedding(
                    image_id=image_id,
                    receipt_id=receipt_id,
                    line_id=primary,
                    line_vector=[0.0],  # replaced after embed
                    text=text,
                    merchant_name=merchant_name,
                    row_line_ids=list(line_ids),
                    section_type=section_by_line.get(primary),
                ),
            )
        )
    for word in words:
        text = format_word_context_embedding_input(word, words)
        items.append(
            (
                f"word:{image_id}#{receipt_id:05d}#{word.line_id:05d}#{word.word_id:05d}",
                text,
                ReceiptWordEmbedding(
                    image_id=image_id,
                    receipt_id=receipt_id,
                    line_id=word.line_id,
                    word_id=word.word_id,
                    word_vector=[0.0],
                    text=text,
                    merchant_name=merchant_name,
                    label_status=_label_status(
                        labels, word.line_id, word.word_id
                    ),
                    primary_label=_primary_label(
                        labels, word.line_id, word.word_id
                    ),
                ),
            )
        )
    return items, None


def _fill_vectors(
    staged: list[tuple[str, str, Any]],
    *,
    openai_client: Any | None,
) -> tuple[list[Any], list[str]]:
    ready: list[Any] = []
    failed: list[str] = []
    texts = [text for _, text, _ in staged]
    try:
        vectors = embed_texts_checked(texts, client=openai_client)
    except Exception as exc:  # noqa: BLE001
        return [], [f"embed_batch:{exc}"]
    for (key, _text, item), vector in zip(staged, vectors, strict=True):
        if isinstance(item, ReceiptLineEmbedding):
            item.line_vector = vector
        else:
            item.word_vector = vector
        ready.append(item)
        del key
    return ready, failed


def _sample_written(written_items: Sequence[Any]) -> list[Any]:
    """One this-run item per index type (searchability wait is sampled)."""
    samples: list[Any] = []
    seen: set[type] = set()
    for item in written_items:
        kind = type(item)
        if kind in seen:
            continue
        seen.add(kind)
        samples.append(item)
    return samples


def _wait_searchable(
    written_items: Sequence[Any],
    timeout_s: float,
) -> dict[str, Any]:
    """Poll SearchVectors until sampled this-run keys appear.

    Foreign embedding items on the shared dev table are ignorable: we
    never compare result counts to the table, only membership of this
    run's sampled keys. Each index type is probed with that item's own
    vector so mixed line/word writes are both covered.
    """
    from receipt_embeddings.dynamo_client import DynamoVectorSearchClient

    samples = _sample_written(written_items)
    owned = [item.harness_key() for item in samples]
    pending = set(owned)
    found: list[str] = []
    if not samples:
        return {
            "ok": True,
            "searchable_keys": [],
            "pending_keys": [],
        }
    client = DynamoVectorSearchClient.from_env()
    deadline = time.time() + timeout_s
    by_key = {item.harness_key(): item for item in samples}
    while time.time() < deadline and pending:
        for key in list(pending):
            sample = by_key[key]
            index = (
                LINE_INDEX
                if isinstance(sample, ReceiptLineEmbedding)
                else WORD_INDEX
            )
            vector = (
                sample.line_vector
                if isinstance(sample, ReceiptLineEmbedding)
                else sample.word_vector
            )
            try:
                hits = client.search(
                    vector, index, top_k=MAX_SEARCH_VECTORS_TOP_K
                )
            except Exception:  # noqa: BLE001
                continue
            newly = owned_keys_in_hits(hits, [key])
            for found_key in newly:
                pending.discard(found_key)
                found.append(found_key)
        if pending:
            time.sleep(2.0)
    return {
        "ok": not pending,
        "searchable_keys": found,
        "pending_keys": sorted(pending),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=2)
    parser.add_argument("--allow-under-floor", action="store_true")
    parser.add_argument("--extra-receipts", type=Path, default=None)
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--min-receipts", type=int, default=MIN_RECEIPTS)
    parser.add_argument("--wait-searchable", type=float, default=60.0)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    table = os.environ.get("DYNAMODB_TABLE_NAME", DEV_TABLE_NAME)
    _refuse_prod(table)
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be at least 1")
    if (
        args.limit is not None
        and args.limit < args.min_receipts
        and not args.allow_under_floor
    ):
        raise SystemExit(
            f"--limit {args.limit} is below --min-receipts "
            f"{args.min_receipts}; pass --allow-under-floor"
        )
    receipts = _load_receipts(args.manifest, args.extra_receipts)
    if args.limit is not None:
        receipts = receipts[: args.limit]
    if args.dry_run:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "table": table,
                    "receipts": len(receipts),
                    "dimension": EMBEDDING_DIMENSION,
                },
                indent=2,
            )
        )
        return 0
    dynamo = DynamoClient(table)
    report = WriteReport()
    skipped_receipts: list[str] = []
    staged_all: list[tuple[str, str, Any]] = []
    for receipt in receipts:
        image_id = str(receipt["image_id"])
        receipt_id = int(receipt["receipt_id"])
        staged, skip = _build_items(dynamo, image_id, receipt_id)
        if skip:
            skipped_receipts.append(f"{image_id}#{receipt_id:05d}:{skip}")
            continue
        staged_all.extend(staged)
    openai_client = None
    ready, failed = _fill_vectors(staged_all, openai_client=openai_client)
    report.failed.extend(failed)
    if ready:
        put = put_embedding_items(dynamo, ready)
        report.merge(put)
    written_items = [
        item
        for item in ready
        if item.harness_key() in set(report.written_keys)
    ]
    searchable = None
    if written_items and args.wait_searchable > 0:
        searchable = _wait_searchable(written_items, args.wait_searchable)
    payload = {
        "table": table,
        "receipts": len(receipts),
        "written": report.written,
        "skipped": report.skipped,
        "written_keys": report.written_keys,
        "skipped_keys": report.skipped_keys,
        "failed": report.failed,
        "skipped_receipts": skipped_receipts,
        "searchable": searchable,
    }
    print(json.dumps(payload, indent=2))
    return 0 if not report.failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
