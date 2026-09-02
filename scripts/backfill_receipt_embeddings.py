#!/usr/bin/env python3.13
"""Safely backfill receipt embedding items into the judge's dev table.

The command is read-only unless ``--apply`` is passed. Applied runs require an
explicit ``--limit`` and refuse every table except ``ReceiptsTable-dc5be22``.
Verification is scoped to canonical keys written by this invocation; foreign
embedding items in the shared dev table are ignored.
"""

from __future__ import annotations

import argparse
import dataclasses
import gzip
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
from receipt_dynamo.data.shared_exceptions import (  # noqa: E402
    EntityNotFoundError,
)
from receipt_embeddings.quotas import (  # noqa: E402
    MAX_GET_LIMIT,
    ensure_get_ids_within_quota,
)
from scripts.similarity_harness.capture_golden import (  # noqa: E402
    CHROMA_ENVIRONMENT,
    DEV_DATABASE,
)
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
    manifest_only: bool = False,
) -> list[dict[str, Any]]:
    if manifest_only and extra_receipts is None:
        raise SystemExit("--manifest-only requires --extra-receipts")
    selected = (
        [] if manifest_only else [dict(value) for value in fixture["receipts"]]
    )
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


class ChromaVectorSource:
    """Reuse vectors already stored in Chroma Cloud dev (OpenAI-free).

    Cherry-picked from the ``bakeoff/C/claude`` entry's vector-source
    abstraction (scripts/embedding_backfill/backfill_embeddings.py).
    Chroma document ids equal the canonical item keys, so lookup is a
    batched read-only ``get`` per collection. Reused vectors preserve
    identity with what the receipts embedded at ingest — OpenAI
    embeddings are not bit-stable across calls, so reuse is also the
    higher-fidelity path.
    """

    def __init__(self, chroma_client: Any = None) -> None:
        if chroma_client is None:
            from receipt_chroma import ChromaClient

            chroma_client = ChromaClient(
                mode="read",
                cloud_api_key=os.environ["CHROMA_CLOUD_API_KEY"],
                cloud_tenant=os.environ["CHROMA_CLOUD_TENANT"],
                cloud_database=os.environ["CHROMA_CLOUD_DATABASE"],
            )
        self._chroma = chroma_client

    def close(self) -> None:
        self._chroma.close()

    def vectors_for(self, keys: Sequence[str]) -> dict[str, list[float]]:
        by_collection: dict[str, list[str]] = {"lines": [], "words": []}
        for key in keys:
            collection = "words" if "#WORD#" in key else "lines"
            by_collection[collection].append(key)
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
                    vectors[str(key)] = [float(value) for value in embedding]
        return vectors


def _chroma_env_ready() -> bool:
    return all(os.environ.get(name) for name in CHROMA_ENVIRONMENT)


def resolve_vector_source(choice: str) -> str:
    """Resolve ``auto`` to a concrete source name (no clients built)."""
    if choice == "auto":
        return "chroma" if _chroma_env_ready() else "openai"
    return choice


def build_chroma_source() -> ChromaVectorSource:
    """Validate credentials + dev-database guard, then open the client."""
    missing = [name for name in CHROMA_ENVIRONMENT if not os.environ.get(name)]
    if missing:
        raise SystemExit("vector source 'chroma' needs " + ", ".join(missing))
    database = os.environ["CHROMA_CLOUD_DATABASE"].strip()
    if database != DEV_DATABASE:
        raise SystemExit(
            f"refusing to touch Chroma database {database!r}; "
            f"only {DEV_DATABASE!r} is allowed"
        )
    return ChromaVectorSource()


def apply_stored_vectors(
    requests: list[EmbeddingWriteRequest],
    stored_vectors: Mapping[str, Sequence[float]],
    *,
    missing_reason: str,
) -> tuple[list[EmbeddingWriteRequest], list[dict[str, str]]]:
    """Fill uncovered requests from stored vectors; skip-report the rest.

    Used by the OpenAI-free sources (``chroma``, ``fixture``): a request
    whose vector cannot be sourced is dropped with a per-item skip
    reason instead of falling through to realtime embedding.
    """
    covered: list[EmbeddingWriteRequest] = []
    skips: list[dict[str, str]] = []
    for request in requests:
        if request.vector is not None:
            covered.append(request)
            continue
        vector = stored_vectors.get(request.canonical_key)
        if vector is None:
            skips.append(
                {"key": request.canonical_key, "reason": missing_reason}
            )
            continue
        covered.append(dataclasses.replace(request, vector=list(vector)))
    return covered, skips


def _classify_receipt_skip(exc: Exception) -> str:
    if isinstance(exc, EntityNotFoundError):
        return "receipt_not_found"
    if isinstance(exc, ValueError):
        return "incomplete_receipt_data"
    return f"error:{type(exc).__name__}"


def _label_statuses(labels: Sequence[Any]) -> dict[tuple[int, int], str]:
    """Same rule as the stream freshener: any terminal human verdict
    (VALID or INVALID) -> validated, else any PENDING -> pending, else
    none. INVALID-only words must stay in the validated population or
    the word index's filter would drop exactly the counterexamples
    similar_labeled_words needs for evidence_against (E3 review P1-2).
    """
    by_word: dict[tuple[int, int], list[str]] = {}
    for label in labels:
        key = (int(label.line_id), int(label.word_id))
        by_word.setdefault(key, []).append(str(label.validation_status))
    statuses: dict[tuple[int, int], str] = {}
    for key, values in by_word.items():
        if (
            ValidationStatus.VALID.value in values
            or ValidationStatus.INVALID.value in values
        ):
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
            skips.append(
                {
                    "receipt": receipt_key,
                    "reason": str(exc),
                    "category": _classify_receipt_skip(exc),
                }
            )
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
                    "category": "section_metadata_unavailable",
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
            skips.append(
                {
                    "receipt": receipt_key,
                    "reason": str(exc),
                    "category": _classify_receipt_skip(exc),
                }
            )
    return requests, skips


def repair_label_status(
    dynamo: DynamoClient,
    receipts: Sequence[Mapping[str, Any]],
    *,
    apply: bool,
) -> dict[str, Any]:
    """Recompute ``label_status`` on EXISTING word embedding items.

    Bounded metadata repair (E3 review P1-B): the writer skips existing
    ``...#EMBEDDING`` keys and the stream freshener fires only on future
    label events, so items backfilled before the terminal-verdict rule
    (INVALID-only -> validated) keep their stale classification forever.
    This mode re-aggregates each in-scope word's current
    ``ReceiptWordLabel`` rows with the SAME ``_label_statuses`` rule the
    writer uses and UpdateItems only the ``label_status`` attribute
    where it differs — idempotent (a second run plans zero updates), no
    vector writes, never creates items (``attribute_exists`` guard),
    per-receipt skip-and-report.
    """
    from receipt_dynamo.entities.receipt_embedding import (
        ReceiptWordEmbedding,
    )

    words_examined = 0
    unchanged = 0
    applied = 0
    planned: list[dict[str, str]] = []
    update_errors: list[dict[str, str]] = []
    receipt_skips: list[dict[str, str]] = []
    for receipt in receipts:
        image_id = str(receipt["image_id"])
        receipt_id = int(receipt["receipt_id"])
        receipt_key = f"{image_id}#{receipt_id:05d}"
        try:
            labels: list[Any] = []
            last_key: dict[str, Any] | None = None
            while True:
                page, last_key = dynamo.list_receipt_word_labels_for_receipt(
                    image_id,
                    receipt_id,
                    limit=1000,
                    last_evaluated_key=last_key,
                )
                labels.extend(page)
                if last_key is None:
                    break
            embeddings = dynamo.get_receipt_embeddings(image_id, receipt_id)
        except Exception as exc:  # noqa: BLE001 - isolate one receipt
            receipt_skips.append({"receipt": receipt_key, "reason": str(exc)})
            continue
        statuses = _label_statuses(labels)
        for item in embeddings:
            if not isinstance(item, ReceiptWordEmbedding):
                continue
            words_examined += 1
            desired = statuses.get(
                (int(item.line_id), int(item.word_id)), "none"
            )
            if desired == item.label_status:
                unchanged += 1
                continue
            planned.append(
                {
                    "key": item.canonical_key,
                    "from": item.label_status,
                    "to": desired,
                }
            )
            if not apply:
                continue
            try:
                dynamo._client.update_item(
                    TableName=dynamo.table_name,
                    Key=item.key,
                    UpdateExpression="SET label_status = :s",
                    ExpressionAttributeValues={":s": {"S": desired}},
                    ConditionExpression="attribute_exists(PK)",
                )
                applied += 1
            except Exception as exc:  # noqa: BLE001 - skip-and-report
                update_errors.append(
                    {"key": item.canonical_key, "reason": str(exc)}
                )
    exit_code = 0
    if apply and planned and applied == 0 and update_errors:
        # Same fail-closed pattern as the writer: every planned update
        # failing is a global failure, not a partial one.
        exit_code = EXIT_GLOBAL_WRITE_FAILURE
    return {
        "mode": "repair_label_status",
        "dry_run": not apply,
        "receipt_scope": len(receipts),
        "words_examined": words_examined,
        "unchanged": unchanged,
        "updates_planned": len(planned),
        "updates_applied": applied,
        "planned_updates": planned[:50],
        "update_errors": update_errors,
        "receipt_skips": receipt_skips,
        "exit_code": exit_code,
    }


# Fail-closed exit semantics (Round C vacatur gate): a run that writes
# nothing while planned work failed is a global-failure pattern (bad
# credentials, region outage) and must not exit 0; a post-write existence
# check that cannot account for every written key is likewise an error.
# Per-item failures alongside successful writes still skip-and-continue.
EXIT_GLOBAL_WRITE_FAILURE = 3
EXIT_VERIFICATION_FAILURE = 4


def determine_exit_code(
    write_report: Any, item_verification: Mapping[str, Any]
) -> int:
    """Map an applied run's outcome to its exit code.

    Zero written with nonzero failures AND nothing skipped-as-existing is
    the global-failure pattern -> ``EXIT_GLOBAL_WRITE_FAILURE``: every
    attempt failed and there is no evidence the corpus is already there.
    Skipped-existing items are that evidence — an idempotent rerun over a
    completed corpus skips everything as existing while the same residual
    unfillable items the first run tolerated fail again, and that rerun
    must exit 0 exactly like the first run did. Written keys the
    strong-consistency check could not find -> a distinct
    ``EXIT_VERIFICATION_FAILURE``.
    """
    if (
        write_report.written == 0
        and write_report.failures
        and not write_report.skipped_existing_keys
    ):
        return EXIT_GLOBAL_WRITE_FAILURE
    if item_verification.get("status") == "missing":
        return EXIT_VERIFICATION_FAILURE
    return 0


def _item_key_from_canonical(key: str) -> dict[str, Any]:
    image_part, _, item_part = key.partition("#RECEIPT#")
    return {
        "PK": {"S": image_part},
        "SK": {"S": f"RECEIPT#{item_part}#EMBEDDING"},
    }


def verify_written_items(
    dynamodb_client: Any,
    table_name: str,
    written_keys: Sequence[str],
    *,
    max_retries: int = 3,
    sleep: Any = time.sleep,
) -> dict[str, Any]:
    """Strongly consistent existence check over EVERY written key.

    Uses BatchGetItem with ConsistentRead so a written item can never
    false-pass through eventual consistency or a swallowed lookup error:
    a key is verified only when the base table returns it, and any key
    left unaccounted for (missing, or unprocessed after bounded retries)
    is reported and fails the run.
    """
    if not written_keys:
        return {"status": "not_needed", "checked": 0, "missing_keys": []}
    unaccounted = {key: _item_key_from_canonical(key) for key in written_keys}
    all_keys = list(unaccounted.items())
    for offset in range(0, len(all_keys), 100):
        chunk = all_keys[offset : offset + 100]
        pending = [item_key for _, item_key in chunk]
        by_table_key = {
            (item_key["PK"]["S"], item_key["SK"]["S"]): canonical
            for canonical, item_key in chunk
        }
        try:
            for attempt in range(max_retries + 1):
                response = dynamodb_client.batch_get_item(
                    RequestItems={
                        table_name: {
                            "Keys": pending,
                            "ProjectionExpression": "PK, SK",
                            "ConsistentRead": True,
                        }
                    }
                )
                for item in response.get("Responses", {}).get(table_name, []):
                    canonical = by_table_key.get(
                        (item["PK"]["S"], item["SK"]["S"])
                    )
                    if canonical is not None:
                        unaccounted.pop(canonical, None)
                pending = (
                    response.get("UnprocessedKeys", {})
                    .get(table_name, {})
                    .get("Keys", [])
                )
                if not pending:
                    break
                if attempt < max_retries:
                    sleep(0.1 * (2**attempt))
        except Exception:  # noqa: BLE001 - unaccounted keys fail the check
            continue
    missing = sorted(unaccounted)
    return {
        "status": "verified" if not missing else "missing",
        "checked": len(written_keys),
        "missing_keys": missing,
    }


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
    parser.add_argument(
        "--repair-label-status",
        action="store_true",
        help=(
            "metadata-only repair: recompute label_status on EXISTING "
            "word embedding items from their current label rows "
            "(terminal-verdict rule); honors --apply/--limit, writes "
            "no vectors, never creates items"
        ),
    )
    parser.add_argument(
        "--manifest-only",
        action="store_true",
        help=(
            "select receipts ONLY from --extra-receipts, ignoring the "
            "fixture's receipt list (the fixture still supplies vectors "
            "for --vector-source fixture/auto); required shape for a "
            "non-dev table, where fixture receipts may not exist"
        ),
    )
    parser.add_argument(
        "--allow-table",
        help=(
            "explicit opt-in for a non-dev table: must EXACTLY repeat "
            "the --table-name value to run against it (e.g. the prod "
            "table during corpus promotion); every other safety rail "
            "(--apply requires --limit, skip-existing, fail-closed "
            "exits) still applies"
        ),
    )
    parser.add_argument(
        "--vector-source",
        choices=("auto", "chroma", "openai", "fixture"),
        default="auto",
        help=(
            "where uncovered vectors come from: 'chroma' reuses stored "
            "Chroma Cloud dev vectors (OpenAI-free), 'openai' re-embeds "
            "realtime, 'fixture' uses only the fixture corpus (offline), "
            "'auto' picks chroma when CHROMA_CLOUD_* is set, else openai"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.table_name != DEV_TABLE and args.allow_table != args.table_name:
        raise SystemExit(
            f"refusing table {args.table_name!r}; only {DEV_TABLE!r} is "
            "allowed unless --allow-table exactly repeats the table name"
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
        manifest_only=args.manifest_only,
    )
    dynamo = DynamoClient(table_name=args.table_name, region=args.region)
    if args.repair_label_status:
        repair_report = repair_label_status(dynamo, receipts, apply=args.apply)
        repair_report["table_name"] = args.table_name
        print(json.dumps(repair_report, indent=2, sort_keys=True))
        return int(repair_report["exit_code"])
    requests, receipt_skips = collect_requests(
        dynamo, receipts, fixture_vectors(fixture)
    )
    vector_source = resolve_vector_source(args.vector_source)

    report: dict[str, Any] = {
        "mode": "apply" if args.apply else "dry_run",
        "table_name": args.table_name,
        "vector_source": vector_source,
        "receipt_scope": len(receipts),
        "embedding_scope": len(requests),
        "fixture_vector_reuse": sum(
            request.vector is not None for request in requests
        ),
        "uncovered_vector_scope": sum(
            request.vector is None for request in requests
        ),
        "receipt_skips": receipt_skips,
        "receipt_skip_reasons": dict(
            Counter(skip["category"] for skip in receipt_skips)
        ),
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

    vector_skips: list[dict[str, str]] = []
    close_source = lambda: None  # noqa: E731 - trivial closer
    if vector_source == "chroma":
        source = build_chroma_source()
        close_source = source.close
        try:
            stored = source.vectors_for(
                [
                    request.canonical_key
                    for request in requests
                    if request.vector is None
                ]
            )
            requests, vector_skips = apply_stored_vectors(
                requests, stored, missing_reason="missing_stored_vector"
            )
        except SystemExit:
            raise
        except Exception:
            close_source()
            raise
    elif vector_source == "fixture":
        requests, vector_skips = apply_stored_vectors(
            requests, {}, missing_reason="not_in_fixture_corpus"
        )
    elif not os.environ.get("OPENAI_API_KEY") and any(
        request.vector is None for request in requests
    ):
        raise SystemExit(
            "vector source 'openai' needs OPENAI_API_KEY for "
            f"{sum(r.vector is None for r in requests)} uncovered "
            "vectors; set CHROMA_CLOUD_* and --vector-source chroma "
            "for an OpenAI-free run"
        )
    report["vector_skips"] = vector_skips
    report["vector_skip_reasons"] = dict(
        Counter(skip["reason"] for skip in vector_skips)
    )

    try:
        writer = EmbeddingWriter(dynamo._client, args.table_name)
        write_report = writer.write(requests)
    finally:
        close_source()
    report["write_report"] = write_report.as_dict()
    report["item_failure_reasons"] = dict(
        Counter(failure.stage for failure in write_report.failures)
    )
    # Two-phase verification (Round C vacatur gate): first a strongly
    # consistent existence check over every written key — the pass/fail
    # signal — then the bounded SearchVectors probe, reported separately
    # because indexing is asynchronous and a probe timeout is not an
    # existence failure.
    report["item_verification"] = verify_written_items(
        dynamo._client, args.table_name, write_report.written_keys
    )
    search_client = DynamoVectorSearchClient(dynamo._client, args.table_name)
    report["searchability_probe"] = wait_for_written_keys(
        search_client,
        write_report.written_keys,
        timeout_seconds=args.wait_seconds,
        sample_size=args.sample_size,
    )
    exit_code = determine_exit_code(write_report, report["item_verification"])
    report["exit_code"] = exit_code
    print(json.dumps(report, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
