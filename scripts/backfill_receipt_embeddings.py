#!/usr/bin/env python3.13
"""Safely backfill receipt embedding items into the judge's dev table.

The command is read-only unless ``--apply`` is passed. Applied runs require an
explicit ``--limit`` and refuse every table except ``ReceiptsTable-dc5be22``.
Verification is scoped to canonical keys written by this invocation; foreign
embedding items in the shared dev table are ignored.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import time
from collections.abc import Mapping, Sequence
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

from receipt_embeddings import (  # noqa: E402
    DynamoVectorSearchClient,
    EmbeddingWriter,
    EmbeddingWriteRequest,
)
from receipt_embeddings.formatting import (  # noqa: E402
    format_visual_row,
    format_word_context_embedding_input,
    get_row_embedding_inputs,
    group_lines_into_visual_rows,
)
from receipt_embeddings.service_limits import (  # noqa: E402
    EMBEDDING_DIMENSIONS,
    LINE_INDEX,
    MAX_SEARCH_RESULTS,
    WORD_INDEX,
)

from receipt_chroma.embedding.metadata.line_metadata import (  # noqa: E402
    enrich_row_metadata_with_anchors,
)
from receipt_dynamo import DynamoClient  # noqa: E402
from receipt_dynamo.constants import ValidationStatus  # noqa: E402
from scripts.similarity_harness.common import validate_fixture  # noqa: E402

DEV_TABLE = "ReceiptsTable-dc5be22"
DEFAULT_REGION = "us-east-1"
DEFAULT_FIXTURE = (
    REPOSITORY_ROOT / "tests" / "fixtures" / "similarity" / "golden.json"
)


def _load_json(path: Path) -> Any:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as source:
            return json.load(source)
    return json.loads(path.read_text(encoding="utf-8"))


def load_golden_fixture(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    validate_fixture(payload, minimum_receipts=1)
    return dict(payload)


def _load_extra_receipts(path: Path) -> list[dict[str, Any]]:
    payload = _load_json(path)
    if not isinstance(payload, list):
        raise ValueError("extra receipts must be a JSON array")
    receipts: list[dict[str, Any]] = []
    for position, value in enumerate(payload):
        if not isinstance(value, Mapping):
            raise ValueError(f"extra receipt {position} must be an object")
        image_id = value.get("image_id")
        receipt_id = value.get("receipt_id")
        if not isinstance(image_id, str) or not image_id:
            raise ValueError(f"extra receipt {position} needs image_id")
        if (
            not isinstance(receipt_id, int)
            or isinstance(receipt_id, bool)
            or receipt_id < 1
        ):
            raise ValueError(f"extra receipt {position} needs receipt_id")
        receipts.append(
            {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "merchant_name": str(
                    value.get("merchant_name") or value.get("merchant") or ""
                ),
            }
        )
    return receipts


def select_receipts(
    fixture: Mapping[str, Any],
    *,
    extra_receipts: Path | None,
    limit: int | None,
) -> list[dict[str, Any]]:
    selected = [dict(value) for value in fixture["receipts"]]
    seen = {
        (str(value["image_id"]), int(value["receipt_id"]))
        for value in selected
    }
    if extra_receipts is not None:
        for value in _load_extra_receipts(extra_receipts):
            key = (str(value["image_id"]), int(value["receipt_id"]))
            if key not in seen:
                selected.append(value)
                seen.add(key)
    selected.sort(
        key=lambda value: (
            str(value["image_id"]),
            int(value["receipt_id"]),
        )
    )
    return selected[:limit] if limit is not None else selected


def fixture_vectors(fixture: Mapping[str, Any]) -> dict[str, list[float]]:
    """Return only production-shaped vectors, keyed by canonical item key."""
    result: dict[str, list[float]] = {}
    for value in fixture["corpus"]:
        vector = value.get("vector")
        if isinstance(vector, list) and len(vector) == EMBEDDING_DIMENSIONS:
            result[str(value["key"])] = [float(number) for number in vector]
    return result


def _label_statuses(labels: Sequence[Any]) -> dict[tuple[int, int], str]:
    by_word: dict[tuple[int, int], list[str]] = {}
    for label in labels:
        key = (int(label.line_id), int(label.word_id))
        by_word.setdefault(key, []).append(str(label.validation_status))
    statuses: dict[tuple[int, int], str] = {}
    for key, values in by_word.items():
        if ValidationStatus.VALID.value in values:
            statuses[key] = "validated"
        elif ValidationStatus.PENDING.value in values:
            statuses[key] = "pending"
        else:
            statuses[key] = "none"
    return statuses


def _section_by_line(sections: Sequence[Any]) -> dict[int, str]:
    result: dict[int, str] = {}
    for section in sections:
        for line_id in section.line_ids:
            result[int(line_id)] = str(section.section_type)
    return result


def build_requests(
    details: Any,
    sections: Sequence[Any],
    known_vectors: Mapping[str, Sequence[float]],
    *,
    fallback_merchant_name: str = "",
) -> list[EmbeddingWriteRequest]:
    place = details.place
    merchant_name = (
        str(getattr(place, "merchant_name", "") or "")
        if place is not None
        else fallback_merchant_name
    )
    place_id = (
        str(getattr(place, "place_id", "") or "") if place is not None else ""
    )
    section_by_line = _section_by_line(sections)
    requests: list[EmbeddingWriteRequest] = []

    row_inputs = get_row_embedding_inputs(details.lines)
    visual_rows = group_lines_into_visual_rows(details.lines)
    for (embedding_input, line_ids), row in zip(
        row_inputs, visual_rows, strict=True
    ):
        primary_line_id = int(line_ids[0])
        canonical_key = (
            f"IMAGE#{details.receipt.image_id}#"
            f"RECEIPT#{details.receipt.receipt_id:05d}#"
            f"LINE#{primary_line_id:05d}"
        )
        section_values = {
            section_by_line.get(int(line_id), "") for line_id in line_ids
        }
        section_values.discard("")
        section_type = (
            next(iter(section_values)) if len(section_values) == 1 else ""
        )
        # Fetch-join metadata: the same anchor enrichment the Chroma line
        # delta writer applies to a visual row's words populates the
        # resolver's normalized phone/address fields on the Dynamo item.
        row_line_id_set = {int(value) for value in line_ids}
        anchors = enrich_row_metadata_with_anchors(
            {},
            [
                word
                for word in details.words
                if int(word.line_id) in row_line_id_set
            ],
        )
        requests.append(
            EmbeddingWriteRequest(
                kind="line",
                image_id=details.receipt.image_id,
                receipt_id=details.receipt.receipt_id,
                line_id=primary_line_id,
                text=format_visual_row(row),
                embedding_input=embedding_input,
                merchant_name=merchant_name,
                place_id=place_id,
                row_line_ids=tuple(int(value) for value in line_ids),
                section_type=section_type,
                normalized_phone_10=str(
                    anchors.get("normalized_phone_10", "")
                ),
                normalized_full_address=str(
                    anchors.get("normalized_full_address", "")
                ),
                vector=known_vectors.get(canonical_key),
            )
        )

    statuses = _label_statuses(details.labels)
    for word in details.words:
        canonical_key = (
            f"IMAGE#{word.image_id}#RECEIPT#{word.receipt_id:05d}#"
            f"LINE#{word.line_id:05d}#WORD#{word.word_id:05d}"
        )
        requests.append(
            EmbeddingWriteRequest(
                kind="word",
                image_id=word.image_id,
                receipt_id=word.receipt_id,
                line_id=word.line_id,
                word_id=word.word_id,
                text=word.text,
                embedding_input=format_word_context_embedding_input(
                    word, details.words, context_size=2
                ),
                merchant_name=merchant_name,
                label_status=statuses.get(
                    (int(word.line_id), int(word.word_id)), "none"
                ),
                vector=known_vectors.get(canonical_key),
            )
        )
    return requests


def collect_requests(
    dynamo: DynamoClient,
    receipts: Sequence[Mapping[str, Any]],
    known_vectors: Mapping[str, Sequence[float]],
) -> tuple[list[EmbeddingWriteRequest], list[dict[str, str]]]:
    requests: list[EmbeddingWriteRequest] = []
    skips: list[dict[str, str]] = []
    for receipt in receipts:
        image_id = str(receipt["image_id"])
        receipt_id = int(receipt["receipt_id"])
        receipt_key = f"{image_id}#{receipt_id:05d}"
        try:
            details = dynamo.get_receipt_details(image_id, receipt_id)
        except (
            Exception
        ) as exc:  # noqa: BLE001 - one absent receipt is isolated
            skips.append({"receipt": receipt_key, "reason": str(exc)})
            continue
        try:
            sections = dynamo.get_receipt_sections_from_receipt(
                image_id, receipt_id
            )
        except (
            Exception
        ) as exc:  # noqa: BLE001 - sections are optional metadata
            sections = []
            skips.append(
                {
                    "receipt": receipt_key,
                    "reason": f"section metadata unavailable: {exc}",
                }
            )
        try:
            requests.extend(
                build_requests(
                    details,
                    sections,
                    known_vectors,
                    fallback_merchant_name=str(
                        receipt.get("merchant_name")
                        or receipt.get("merchant_truth")
                        or ""
                    ),
                )
            )
        except Exception as exc:  # noqa: BLE001 - isolate malformed receipt
            skips.append({"receipt": receipt_key, "reason": str(exc)})
    return requests, skips


def _sample_written_keys(keys: Sequence[str], sample_size: int) -> list[str]:
    if sample_size <= 0:
        return []
    lines = [key for key in keys if "#WORD#" not in key]
    words = [key for key in keys if "#WORD#" in key]
    sampled = lines[:1] + words[:1]
    for key in keys:
        if len(sampled) >= sample_size:
            break
        if key not in sampled:
            sampled.append(key)
    return sampled[:sample_size]


def wait_for_written_keys(
    client: DynamoVectorSearchClient,
    written_keys: Sequence[str],
    *,
    timeout_seconds: float,
    sample_size: int,
    sleep_seconds: float = 2.0,
) -> dict[str, Any]:
    """Poll SearchVectors only for exact keys written by this invocation."""
    sampled = _sample_written_keys(written_keys, sample_size)
    if not sampled:
        return {"status": "not_needed", "sampled_keys": [], "results": []}
    deadline = time.monotonic() + timeout_seconds
    pending = set(sampled)
    results: dict[str, dict[str, Any]] = {
        key: {"key": key, "searchable": False, "attempts": 0}
        for key in sampled
    }
    while pending:
        for key in sampled:
            if key not in pending:
                continue
            result = results[key]
            result["attempts"] += 1
            try:
                vector = client.get_vector(key)
                index = WORD_INDEX if "#WORD#" in key else LINE_INDEX
                neighbors = client.search(
                    vector, index=index, top_k=MAX_SEARCH_RESULTS
                )
                result["request_bytes"] = client.last_request_bytes
                if key in {neighbor.key for neighbor in neighbors}:
                    result["searchable"] = True
                    result.pop("last_error", None)
                    pending.remove(key)
            except (
                Exception
            ) as exc:  # noqa: BLE001 - retry throttles/transients
                result["last_error"] = str(exc)
        if not pending or time.monotonic() >= deadline:
            break
        time.sleep(min(sleep_seconds, max(0.0, deadline - time.monotonic())))
    return {
        "status": "searchable" if not pending else "timed_out",
        "sampled_keys": sampled,
        "results": [results[key] for key in sampled],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--extra-receipts", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--table-name",
        default=os.environ.get("DYNAMODB_TABLE_NAME", DEV_TABLE),
    )
    parser.add_argument(
        "--region",
        default=os.environ.get(
            "AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", DEFAULT_REGION)
        ),
    )
    parser.add_argument("--wait-seconds", type=float, default=120.0)
    parser.add_argument("--sample-size", type=int, default=2)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.table_name != DEV_TABLE:
        raise SystemExit(
            f"refusing table {args.table_name!r}; only {DEV_TABLE!r} is allowed"
        )
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be at least 1")
    if args.apply and args.limit is None:
        raise SystemExit("--apply requires an explicit --limit")
    if args.wait_seconds < 0:
        raise SystemExit("--wait-seconds must not be negative")
    if args.sample_size < 0:
        raise SystemExit("--sample-size must not be negative")

    fixture = load_golden_fixture(args.fixture)
    receipts = select_receipts(
        fixture,
        extra_receipts=args.extra_receipts,
        limit=args.limit,
    )
    dynamo = DynamoClient(table_name=args.table_name, region=args.region)
    requests, receipt_skips = collect_requests(
        dynamo, receipts, fixture_vectors(fixture)
    )

    report: dict[str, Any] = {
        "mode": "apply" if args.apply else "dry_run",
        "table_name": args.table_name,
        "receipt_scope": len(receipts),
        "embedding_scope": len(requests),
        "fixture_vector_reuse": sum(
            request.vector is not None for request in requests
        ),
        "realtime_embedding_scope": sum(
            request.vector is None for request in requests
        ),
        "receipt_skips": receipt_skips,
    }
    if not args.apply:
        report["write_report"] = {
            "written": 0,
            "skipped": len(receipt_skips),
            "planned_embedding_keys": [
                request.canonical_key for request in requests
            ],
        }
        report["searchability"] = {
            "status": "not_run_dry_run",
            "sampled_keys": [],
            "results": [],
        }
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    writer = EmbeddingWriter(dynamo._client, args.table_name)
    write_report = writer.write(requests)
    report["write_report"] = write_report.as_dict()
    search_client = DynamoVectorSearchClient(dynamo._client, args.table_name)
    report["searchability"] = wait_for_written_keys(
        search_client,
        write_report.written_keys,
        timeout_seconds=args.wait_seconds,
        sample_size=args.sample_size,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
