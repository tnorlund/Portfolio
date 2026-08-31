#!/usr/bin/env python3.13
"""Backfill DynamoDB embedding items for the golden receipts.

Writes only ``…#EMBEDDING`` items, only to the dev table, only for the
golden/extra receipts (Round C write discipline). Safe to re-run: the
embed-and-put writer skips existing items, so a second run writes
nothing and calls no embedding service.

Vector sources, in ``--vector-source auto`` preference order:

1. ``chroma`` — reuse the vectors already stored in Chroma Cloud dev
   (OpenAI-free; requires ``CHROMA_CLOUD_*`` credentials, read-only).
2. ``openai`` — realtime re-embedding (requires ``OPENAI_API_KEY``).

``fixture`` reuses vectors from a captured similarity fixture's corpus
(fully offline and free, but only covers keys present in the fixture;
the rest are skip-reported).

The run ends with a written/skipped report and a bounded searchability
wait: SearchVectors is polled until a sampled written item comes back,
or the timeout elapses (indexing is asynchronous; a timeout is reported,
not fatal).
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

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT))
for package_root in (
    REPOSITORY_ROOT / "receipt_embeddings",
    REPOSITORY_ROOT / "receipt_chroma",
    REPOSITORY_ROOT / "receipt_dynamo",
    REPOSITORY_ROOT / "receipt_upload",
):
    sys.path.insert(0, str(package_root))

# With the repo root on sys.path, the OUTER receipt_embeddings/ directory
# can already be cached as an empty namespace package that shadows the
# real package one level down. Evict any such stub before importing.
_cached = sys.modules.get("receipt_embeddings")
if _cached is not None and getattr(_cached, "__file__", None) is None:
    for _name in [
        name
        for name in list(sys.modules)
        if name == "receipt_embeddings"
        or name.startswith("receipt_embeddings.")
    ]:
        del sys.modules[_name]

# The golden-set manifest logic (default cohorts, --extra-receipts
# merging, duplicate rejection) is shared with the Round A capture
# script so both tools always agree on what "the golden receipts" are.
from scripts.similarity_harness.capture_golden import (  # noqa: E402
    CHROMA_ENVIRONMENT,
    DEV_DATABASE,
    DEV_TABLE,
    _load_manifest,
)
from scripts.similarity_harness.common import (  # noqa: E402
    load_fixture,
    receipt_key,
)

from receipt_embeddings.dynamo_client import (  # noqa: E402
    DynamoVectorSearchClient,
)
from receipt_embeddings.dynamo_quotas import (  # noqa: E402
    PROTOCOL_LINE_INDEX,
    PROTOCOL_WORD_INDEX,
)
from receipt_embeddings.quotas import (  # noqa: E402
    MAX_GET_LIMIT,
    ensure_get_ids_within_quota,
)
from receipt_embeddings.writer import (  # noqa: E402
    EmbedAndPutWriter,
    EmbeddingRequest,
    OpenAIVectorSource,
)

DEFAULT_FIXTURE = (
    REPOSITORY_ROOT / "tests" / "fixtures" / "similarity" / "golden.json"
)
_SKIP_NOT_FOUND = "receipt_not_found"
_SKIP_INCOMPLETE = "incomplete_receipt_data"


class ChromaVectorSource:
    """Reuse vectors already stored in Chroma Cloud (read-only, no OpenAI).

    Chroma document ids equal the protocol keys, so the lookup is a
    batched ``get`` per collection. Vectors preserve identity with what
    the receipts embedded at ingest — OpenAI embeddings are not
    bit-stable across calls, so reuse is also the higher-fidelity path.
    """

    def __init__(self) -> None:
        from receipt_chroma import ChromaClient

        self._chroma = ChromaClient(
            mode="read",
            cloud_api_key=os.environ["CHROMA_CLOUD_API_KEY"],
            cloud_tenant=os.environ["CHROMA_CLOUD_TENANT"],
            cloud_database=os.environ["CHROMA_CLOUD_DATABASE"],
        )

    def close(self) -> None:
        self._chroma.close()

    def vectors_for(
        self, requests: Sequence[EmbeddingRequest]
    ) -> Mapping[str, list[float]]:
        by_collection: dict[str, list[str]] = {"lines": [], "words": []}
        for request in requests:
            collection = "words" if "#WORD#" in request.key else "lines"
            by_collection[collection].append(request.key)
        vectors: dict[str, list[float]] = {}
        for collection, ids in by_collection.items():
            for start in range(0, len(ids), MAX_GET_LIMIT):
                batch = ids[start : start + MAX_GET_LIMIT]
                ensure_get_ids_within_quota(batch)
                result = self._chroma.get(
                    collection_name=collection,
                    ids=batch,
                    include=["embeddings"],
                )
                found_ids = list(result.get("ids") or [])
                embeddings = result.get("embeddings")
                if embeddings is None:
                    continue
                for key, embedding in zip(found_ids, embeddings):
                    vectors[str(key)] = [
                        float(value) for value in embedding
                    ]
        return vectors


class FixtureVectorSource:
    """Serve vectors from a captured fixture corpus (offline, partial)."""

    def __init__(self, fixture: Mapping[str, Any]) -> None:
        self._vectors = {
            str(item["key"]): [float(value) for value in item["vector"]]
            for item in fixture["corpus"]
        }

    def vectors_for(
        self, requests: Sequence[EmbeddingRequest]
    ) -> Mapping[str, list[float]]:
        return {
            request.key: list(self._vectors[request.key])
            for request in requests
            if request.key in self._vectors
        }


def _chroma_env_ready() -> bool:
    return all(os.environ.get(name) for name in CHROMA_ENVIRONMENT)


def _build_vector_source(args: argparse.Namespace):
    """Pick the vector source; returns (source, name, closer)."""

    choice = args.vector_source
    if choice == "auto":
        if _chroma_env_ready():
            choice = "chroma"
        elif os.environ.get("OPENAI_API_KEY"):
            choice = "openai"
        else:
            raise SystemExit(
                "no vector source available: set CHROMA_CLOUD_* to reuse "
                "stored vectors (OpenAI-free), set OPENAI_API_KEY to "
                "re-embed, or pass --vector-source fixture for offline "
                "fixture vectors"
            )
    if choice == "chroma":
        if not _chroma_env_ready():
            raise SystemExit(
                "vector source 'chroma' needs "
                + ", ".join(CHROMA_ENVIRONMENT)
            )
        database = os.environ["CHROMA_CLOUD_DATABASE"].strip()
        if database != DEV_DATABASE:
            raise SystemExit(
                f"refusing to touch Chroma database {database!r}; "
                f"only {DEV_DATABASE!r} is allowed"
            )
        source = ChromaVectorSource()
        return source, "chroma", source.close
    if choice == "openai":
        if not os.environ.get("OPENAI_API_KEY"):
            raise SystemExit("vector source 'openai' needs OPENAI_API_KEY")
        return OpenAIVectorSource(), "openai", lambda: None
    if choice == "fixture":
        fixture = load_fixture(args.fixture, minimum_receipts=0)
        return FixtureVectorSource(fixture), "fixture", lambda: None
    raise SystemExit(f"unsupported vector source: {choice}")


def _classify_receipt_skip(exc: Exception, not_found: type) -> str:
    if isinstance(exc, not_found):
        return _SKIP_NOT_FOUND
    if isinstance(exc, ValueError):
        return _SKIP_INCOMPLETE
    return f"error:{type(exc).__name__}"


def _wait_for_searchability(
    search_client: DynamoVectorSearchClient,
    samples: Sequence[tuple[str, str]],
    *,
    timeout_seconds: float,
    poll_seconds: float,
) -> dict[str, Any]:
    """Poll SearchVectors until each sampled written item is returned.

    A written item's own vector must return the item itself once the
    asynchronous index has caught up. Bounded by ``timeout_seconds``.
    """

    pending = {key: index for key, index in samples}
    vectors: dict[str, list[float]] = {}
    for key in list(pending):
        try:
            vectors[key] = search_client.get_vector(key)
        except KeyError:
            # The base-table item vanished between write and wait; treat
            # as unsearchable rather than crashing the report.
            del pending[key]
    started = time.monotonic()
    found: list[str] = []
    while pending and (time.monotonic() - started) < timeout_seconds:
        for key, index in list(pending.items()):
            results = search_client.search(vectors[key], index, 3)
            if any(result.key == key for result in results):
                found.append(key)
                del pending[key]
        if pending:
            time.sleep(poll_seconds)
    return {
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "found": found,
        "timed_out": sorted(pending),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--table-name",
        default=os.environ.get("DYNAMODB_TABLE_NAME", DEV_TABLE),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        help="authoritative receipt manifest; defaults to the golden sets",
    )
    parser.add_argument(
        "--extra-receipts",
        type=Path,
        help="JSON file of [{image_id, receipt_id}] to top up the golden set",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="cap the number of receipts processed (judge cost control)",
    )
    parser.add_argument(
        "--vector-source",
        choices=("auto", "chroma", "openai", "fixture"),
        default="auto",
        help="where vectors come from (auto: chroma if credentialed, "
        "else openai)",
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=DEFAULT_FIXTURE,
        help="fixture whose corpus vectors back --vector-source fixture",
    )
    parser.add_argument(
        "--wait-timeout",
        type=float,
        default=300.0,
        help="max seconds to wait for written items to become searchable",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=10.0,
        help="seconds between searchability polls",
    )
    parser.add_argument(
        "--skip-wait",
        action="store_true",
        help="skip the searchability wait",
    )
    parser.add_argument(
        "--report-out",
        type=Path,
        help="also write the end-of-run report as JSON",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be at least 1")
    if args.table_name != DEV_TABLE:
        raise SystemExit(
            f"refusing to write to DynamoDB table {args.table_name!r}; "
            f"only {DEV_TABLE!r} is allowed"
        )

    receipts = _load_manifest(args.manifest, extra_path=args.extra_receipts)
    if args.limit is not None:
        receipts = receipts[: args.limit]

    from receipt_dynamo import DynamoClient
    from receipt_dynamo.data.shared_exceptions import EntityNotFoundError

    dynamo = DynamoClient(args.table_name)
    source, source_name, close_source = _build_vector_source(args)
    writer = EmbedAndPutWriter(dynamo, vector_source=source)
    print(
        f"backfilling {len(receipts)} receipts into {args.table_name} "
        f"(vector source: {source_name})"
    )

    written_lines = written_words = existing_lines = existing_words = 0
    item_failures: list[dict[str, str]] = []
    receipt_skips: list[dict[str, str]] = []
    last_written_line: str | None = None
    last_written_word: str | None = None
    try:
        for receipt in receipts:
            key = receipt_key(
                str(receipt["image_id"]), int(receipt["receipt_id"])
            )
            try:
                report = writer.embed_receipt(
                    str(receipt["image_id"]), int(receipt["receipt_id"])
                )
            except Exception as exc:  # noqa: BLE001 - skip, report, go on
                reason = _classify_receipt_skip(exc, EntityNotFoundError)
                print(f"SKIP {key}: [{reason}] {exc}", file=sys.stderr)
                receipt_skips.append(
                    {"detail": str(exc), "key": key, "reason": reason}
                )
                continue
            written_lines += len(report.written_line_keys)
            written_words += len(report.written_word_keys)
            existing_lines += len(report.existing_line_keys)
            existing_words += len(report.existing_word_keys)
            if report.written_line_keys:
                last_written_line = report.written_line_keys[-1]
            if report.written_word_keys:
                last_written_word = report.written_word_keys[-1]
            item_failures.extend(
                {
                    "detail": failure.detail,
                    "key": failure.key,
                    "reason": failure.reason,
                }
                for failure in report.failures
            )
            print(
                f"{key}: wrote {report.written_count} "
                f"(lines {len(report.written_line_keys)}, "
                f"words {len(report.written_word_keys)}), "
                f"skipped existing {report.skipped_existing_count}, "
                f"failures {len(report.failures)}"
            )
    finally:
        close_source()

    report: dict[str, Any] = {
        "existing_line_items_skipped": existing_lines,
        "existing_word_items_skipped": existing_words,
        "item_failures": sorted(
            item_failures, key=lambda failure: failure["key"]
        ),
        "item_failure_reasons": dict(
            Counter(failure["reason"] for failure in item_failures)
        ),
        "receipts_processed": len(receipts) - len(receipt_skips),
        "receipts_skipped": receipt_skips,
        "receipt_skip_reasons": dict(
            Counter(skip["reason"] for skip in receipt_skips)
        ),
        "table": args.table_name,
        "vector_source": source_name,
        "written_line_items": written_lines,
        "written_word_items": written_words,
    }

    print("\n=== backfill report ===")
    print(f"receipts processed: {report['receipts_processed']}")
    print(f"receipts skipped:   {len(receipt_skips)}")
    for reason, count in sorted(report["receipt_skip_reasons"].items()):
        print(f"  {count} x {reason}")
    print(f"line items written: {written_lines} (existing {existing_lines})")
    print(f"word items written: {written_words} (existing {existing_words})")
    print(f"item failures:      {len(item_failures)}")
    for reason, count in sorted(report["item_failure_reasons"].items()):
        print(f"  {count} x {reason}")

    if not args.skip_wait and (last_written_line or last_written_word):
        samples = []
        if last_written_line:
            samples.append((last_written_line, PROTOCOL_LINE_INDEX))
        if last_written_word:
            samples.append((last_written_word, PROTOCOL_WORD_INDEX))
        print(
            f"\nwaiting up to {args.wait_timeout:.0f}s for "
            f"{len(samples)} sampled item(s) to become searchable..."
        )
        wait = _wait_for_searchability(
            DynamoVectorSearchClient(args.table_name),
            samples,
            timeout_seconds=args.wait_timeout,
            poll_seconds=args.poll_interval,
        )
        report["searchability_wait"] = wait
        if wait["timed_out"]:
            print(
                f"WARNING: not searchable after "
                f"{wait['elapsed_seconds']}s (indexing is asynchronous): "
                f"{wait['timed_out']}"
            )
        else:
            print(
                f"searchable after {wait['elapsed_seconds']}s: "
                f"{wait['found']}"
            )
    elif not args.skip_wait:
        print("\nnothing newly written; skipping searchability wait")

    if args.report_out:
        args.report_out.parent.mkdir(parents=True, exist_ok=True)
        args.report_out.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"report written to {args.report_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
