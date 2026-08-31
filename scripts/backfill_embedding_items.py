#!/usr/bin/env python3.13
"""Backfill golden-receipt embedding items into the judge-provisioned dev table.

Writes only ``#EMBEDDING`` sort keys on ``ReceiptsTable-dc5be22``. Re-runs
skip existing keys. Prefers stored Chroma vectors so a default run spends no
OpenAI budget; ``--embed-missing`` fills gaps via realtime embeddings.

The dev table is shared: idempotency and searchability checks are scoped to
the exact keys THIS run attempted. Other entrants' embedding items are
ignored. Graded runs happen on a wiped table.

Example (judge-capped)::

    python scripts/backfill_embedding_items.py --limit 5 --allow-under-floor
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))
for package_root in (
    REPOSITORY_ROOT / "receipt_embeddings",
    REPOSITORY_ROOT / "receipt_chroma",
    REPOSITORY_ROOT / "receipt_dynamo",
    REPOSITORY_ROOT / "receipt_upload",
):
    sys.path.insert(0, str(package_root))

_cached = sys.modules.get("receipt_embeddings")
if _cached is not None and getattr(_cached, "__file__", None) is None:
    for _name in [
        name
        for name in list(sys.modules)
        if name == "receipt_embeddings"
        or name.startswith("receipt_embeddings.")
    ]:
        del sys.modules[_name]

from receipt_embeddings.dynamo_client import (  # noqa: E402
    DynamoVectorSearchClient,
)
from receipt_embeddings.formatting import (  # noqa: E402
    get_primary_line_id,
    group_lines_into_visual_rows,
)
from receipt_embeddings.quotas import (  # noqa: E402
    DEV_TABLE_NAME,
    MAX_GET_LIMIT,
    MAX_SEARCH_RESULTS,
    PROTOCOL_LINE_INDEX,
    PROTOCOL_WORD_INDEX,
    require_dev_table,
)
from receipt_embeddings.writer import (  # noqa: E402
    embed_missing_texts,
    prepare_embedding_items,
    texts_needing_openai,
    write_embedding_items,
)

from receipt_dynamo import DynamoClient, EntityNotFoundError  # noqa: E402
from receipt_dynamo.entities.embedding_codec import (  # noqa: E402
    vector_search_line_key,
    vector_search_word_key,
)
from scripts.similarity_harness.capture_golden import (  # noqa: E402
    _load_manifest,
)
from scripts.similarity_harness.common import (  # noqa: E402
    MIN_RECEIPTS,
    receipt_key,
)

_CHROMA_ENV = (
    "CHROMA_CLOUD_API_KEY",
    "CHROMA_CLOUD_TENANT",
    "CHROMA_CLOUD_DATABASE",
)


def _print_report(title: str, rows: Sequence[Mapping[str, str]]) -> None:
    print(f"{title}: {len(rows)}", flush=True)
    counts = Counter(str(row.get("reason", "ok")) for row in rows)
    for reason, count in sorted(counts.items()):
        print(f"  {count} x {reason}", flush=True)


def _load_fixture_vectors(path: Path) -> dict[str, list[float]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    vectors: dict[str, list[float]] = {}
    for item in payload.get("corpus", []):
        key = str(item["key"])
        vectors[key] = [float(value) for value in item["vector"]]
    return vectors


def _fetch_chroma_vectors(
    chroma: Any, keys: Sequence[str], *, collection: str
) -> dict[str, list[float]]:
    found: dict[str, list[float]] = {}
    remaining = list(keys)
    while remaining:
        chunk = remaining[:MAX_GET_LIMIT]
        remaining = remaining[MAX_GET_LIMIT:]
        result = chroma.get(
            collection_name=collection,
            ids=chunk,
            include=["embeddings"],
        )
        ids = list(result.get("ids") or [])
        embeddings = result.get("embeddings")
        if embeddings is None:
            continue
        for key, vector in zip(ids, embeddings):
            if vector is None:
                continue
            found[str(key)] = [float(value) for value in vector]
    return found


def _needed_keys(details: Any) -> tuple[list[str], list[str]]:
    line_keys: list[str] = []
    for row in group_lines_into_visual_rows(details.lines):
        line_keys.append(
            vector_search_line_key(
                details.receipt.image_id,
                details.receipt.receipt_id,
                get_primary_line_id(row),
            )
        )
    word_keys = [
        vector_search_word_key(
            word.image_id, word.receipt_id, word.line_id, word.word_id
        )
        for word in details.words
    ]
    return line_keys, word_keys


def _wait_searchable(
    client: DynamoVectorSearchClient,
    item: Any,
    *,
    timeout_s: float,
    interval_s: float,
) -> dict[str, object]:
    """Poll SearchVectors until THIS run's sampled key appears.

    Neighbor lists on the shared dev table may contain other entrants'
    items; those are ignored. Success is exact-key match only.
    """

    vector = getattr(item, "line_vector", None)
    index = PROTOCOL_LINE_INDEX
    if vector is None:
        vector = item.word_vector
        index = PROTOCOL_WORD_INDEX
    target = item.vector_search_key
    deadline = time.monotonic() + timeout_s
    attempts = 0
    last = "no search attempted"
    while time.monotonic() < deadline:
        attempts += 1
        try:
            neighbors = client.search(vector, index, MAX_SEARCH_RESULTS)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            last = str(exc)
            time.sleep(interval_s)
            continue
        foreign = sum(1 for neighbor in neighbors if neighbor.key != target)
        if any(neighbor.key == target for neighbor in neighbors):
            return {
                "searchable": True,
                "attempts": attempts,
                "key": target,
                "ignored_foreign_neighbors": foreign,
            }
        last = (
            f"this-run key not yet in SearchVectors "
            f"(saw {len(neighbors)} neighbors, {foreign} foreign)"
        )
        time.sleep(interval_s)
    return {
        "searchable": False,
        "attempts": attempts,
        "key": target,
        "reason": last if attempts else "no search attempted",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--extra-receipts", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--min-receipts", type=int, default=MIN_RECEIPTS)
    parser.add_argument("--allow-under-floor", action="store_true")
    parser.add_argument(
        "--table-name",
        default=os.environ.get("DYNAMODB_TABLE_NAME", DEV_TABLE_NAME),
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        help="optional golden.json whose corpus vectors are reused",
    )
    parser.add_argument(
        "--embed-missing",
        action="store_true",
        help="call OpenAI realtime for keys Chroma/fixture did not supply",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--wait-seconds", type=float, default=90.0)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        require_dev_table(args.table_name)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be at least 1")
    if (
        args.limit is not None
        and args.limit < args.min_receipts
        and not args.allow_under_floor
    ):
        raise SystemExit(
            f"--limit {args.limit} is below --min-receipts "
            f"{args.min_receipts}; pass --allow-under-floor to accept a "
            "smaller backfill"
        )

    receipts = _load_manifest(args.manifest, extra_path=args.extra_receipts)
    if args.limit is not None:
        receipts = receipts[: args.limit]

    dynamo = DynamoClient(args.table_name)
    chroma = None
    if all(os.environ.get(name) for name in _CHROMA_ENV):
        if os.environ.get("CHROMA_CLOUD_DATABASE") == "receipt_dev":
            from receipt_chroma import ChromaClient

            chroma = ChromaClient(
                mode="read",
                cloud_api_key=os.environ["CHROMA_CLOUD_API_KEY"],
                cloud_tenant=os.environ["CHROMA_CLOUD_TENANT"],
                cloud_database=os.environ["CHROMA_CLOUD_DATABASE"],
            )
        else:
            print(
                "skipping Chroma reuse: CHROMA_CLOUD_DATABASE is not "
                "receipt_dev",
                flush=True,
            )
    fixture_vectors = (
        _load_fixture_vectors(args.fixture) if args.fixture else {}
    )
    openai_client = None
    if args.embed_missing:
        from openai import OpenAI

        openai_client = OpenAI()

    receipt_skips: list[dict[str, str]] = []
    item_skips: list[dict[str, str]] = []
    written = 0
    skipped_existing = 0
    failed: list[dict[str, str]] = []
    this_run_written_keys: list[str] = []
    this_run_skipped_keys: list[str] = []
    sample_item = None

    try:
        for receipt in receipts:
            image_id = str(receipt["image_id"])
            receipt_id = int(receipt["receipt_id"])
            key = receipt_key(image_id, receipt_id)
            try:
                details = dynamo.get_receipt_details(image_id, receipt_id)
                sections = dynamo.get_receipt_sections_from_receipt(
                    image_id, receipt_id
                )
            except EntityNotFoundError:
                receipt_skips.append(
                    {"key": key, "reason": "receipt_not_found"}
                )
                continue
            except Exception as exc:  # pylint: disable=broad-exception-caught
                receipt_skips.append({"key": key, "reason": str(exc)})
                continue
            if not details.lines or not details.words:
                receipt_skips.append(
                    {"key": key, "reason": "incomplete_receipt_data"}
                )
                continue

            line_keys, word_keys = _needed_keys(details)
            vectors: dict[str, list[float]] = {}
            for needed_key in line_keys + word_keys:
                fixture_vector = fixture_vectors.get(needed_key)
                if fixture_vector is not None:
                    vectors[needed_key] = fixture_vector
            if chroma is not None:
                missing_lines = [k for k in line_keys if k not in vectors]
                missing_words = [k for k in word_keys if k not in vectors]
                vectors.update(
                    _fetch_chroma_vectors(
                        chroma, missing_lines, collection="lines"
                    )
                )
                vectors.update(
                    _fetch_chroma_vectors(
                        chroma, missing_words, collection="words"
                    )
                )
            if openai_client is not None:
                missing = texts_needing_openai(details, vectors)
                vectors.update(
                    embed_missing_texts(missing, openai_client=openai_client)
                )

            prepared = prepare_embedding_items(
                details, sections=sections, vectors_by_key=vectors
            )
            item_skips.extend(prepared.skipped)
            if args.dry_run:
                written += len(prepared.items)
                if sample_item is None and prepared.items:
                    sample_item = prepared.items[0]
                continue
            report = write_embedding_items(dynamo, prepared.items)
            written += report.written
            skipped_existing += report.skipped_existing
            failed.extend(report.failed)
            this_run_written_keys.extend(report.written_keys)
            this_run_skipped_keys.extend(report.skipped_keys)
            if sample_item is None:
                newly_written = set(report.written_keys)
                for entity in prepared.items:
                    if entity.vector_search_key in newly_written:
                        sample_item = entity
                        break
    finally:
        if chroma is not None:
            chroma.close()

    print(
        json.dumps(
            {
                "scope": "this_run_keys_only",
                "written": written,
                "written_keys_sample": this_run_written_keys[:20],
                "skipped_existing": skipped_existing,
                "skipped_existing_keys_sample": this_run_skipped_keys[:20],
                "failed": len(failed),
                "receipts": len(receipts),
                "dry_run": bool(args.dry_run),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    _print_report("receipt skips", receipt_skips)
    _print_report("item skips", item_skips)
    if failed:
        _print_report("write failures", failed)

    wait_report: dict[str, object] = {
        "searchable": None,
        "skipped": True,
        "reason": "no keys written this run (existing same-SK items ignored)",
    }
    if (
        not args.dry_run
        and sample_item is not None
        and this_run_written_keys
        and args.wait_seconds > 0
    ):
        search_client = DynamoVectorSearchClient(table_name=args.table_name)
        wait_report = _wait_searchable(
            search_client,
            sample_item,
            timeout_s=args.wait_seconds,
            interval_s=args.poll_interval,
        )
    print(f"searchability wait: {wait_report}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
