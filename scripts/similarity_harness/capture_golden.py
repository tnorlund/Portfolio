#!/usr/bin/env python3
"""Capture golden similarity fixtures from live Chroma Cloud (dev).

Time-sensitive (AGENT_PLAN principle 1): once Chroma is torn down these
reference answers are unobtainable. For every golden receipt this captures the
three query families the harness grades:

1. **Merchant resolution** — the resolver's Tier-2 query lines (phone /
   address / merchant line, chosen by the real resolver helpers), top-20
   line-index neighbors with distances, and the decision (tier + merchant)
   computed by the same pure scoring ``evaluate.py`` replays.
2. **Word neighbors** — top-30 word-index neighbors with distances for a
   deterministic sample of words per receipt.
3. **Section-verifier votes** — the real ``verify_receipt_sections`` run
   against live Chroma with a read-only store wrapper (would-be section
   updates are captured, never written).

All reads only: DynamoDB reads, Chroma reads. Nothing is written to AWS or
Chroma. Query vectors come from Chroma's own stored embeddings (``get`` by
id) — no OpenAI calls, which keeps capture deterministic and cheap.

Golden set: the line-item golden receipts
(``receipt_upload/tests/fixtures/line_items_golden.json``, ``local_only``
entries skipped — they have no Dynamo/Chroma presence) topped up to
``--min-receipts`` via ``--extra-receipts`` (a JSON list of
``{"image_id", "receipt_id"}`` — e.g. the May-26 known-merchant batch).

Requires ``CHROMA_CLOUD_API_KEY`` / ``CHROMA_CLOUD_TENANT`` /
``CHROMA_CLOUD_DATABASE`` plus AWS credentials for the dev table. Fails fast
without them.

Usage:
    python scripts/similarity_harness/capture_golden.py \
        --table ReceiptsTable-dc5be22 \
        --extra-receipts may26_batch.json \
        --out tests/fixtures/similarity
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
for _pkg in (
    "receipt_dynamo",
    "receipt_chroma",
    "receipt_upload",
    "receipt_agent",
    "receipt_embeddings",
):
    _path = _REPO_ROOT / _pkg
    if _path.is_dir():
        sys.path.insert(0, str(_path))
sys.path.insert(0, str(_REPO_ROOT))

from scripts.similarity_harness import decision, fixtures_io  # noqa: E402

MERCHANT_TOP_K = 20  # resolver's n_results
WORD_TOP_K = 30  # SPEC: top-30 word neighbors
DEFAULT_WORDS_PER_RECEIPT = 10
DEFAULT_MIN_RECEIPTS = 40
GOLDEN_JSON = (
    _REPO_ROOT
    / "receipt_upload"
    / "tests"
    / "fixtures"
    / "line_items_golden.json"
)

# Neighbor metadata keys worth committing (projection the future DynamoDB
# indexes will also carry; everything else in Chroma metadata is noise here).
_LINE_META_KEYS = (
    "image_id",
    "receipt_id",
    "line_id",
    "row_line_ids",
    "merchant_name",
    "normalized_phone_10",
    "normalized_full_address",
    "section_type",
)
_WORD_META_KEYS = (
    "image_id",
    "receipt_id",
    "line_id",
    "word_id",
    "text",
    "merchant_name",
)


def _require_creds() -> None:
    missing = [
        name
        for name in (
            "CHROMA_CLOUD_API_KEY",
            "CHROMA_CLOUD_TENANT",
            "CHROMA_CLOUD_DATABASE",
        )
        if not os.environ.get(name, "").strip()
    ]
    if missing:
        sys.exit(
            "capture_golden.py needs live Chroma Cloud credentials; "
            f"missing: {', '.join(missing)}. Capture is online-only by "
            "design — run evaluate.py --backend fake for offline work."
        )


def _line_key(image_id: str, receipt_id: int, line_id: int) -> str:
    return f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}"


def _word_key(
    image_id: str, receipt_id: int, line_id: int, word_id: int
) -> str:
    return (
        f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}"
        f"#LINE#{line_id:05d}#WORD#{word_id:05d}"
    )


def _load_golden_receipts(
    golden_path: Path, extra_path: Optional[Path]
) -> list[dict[str, Any]]:
    golden = json.load(open(golden_path, encoding="utf-8"))
    receipts = [
        {
            "image_id": entry["image_id"],
            "receipt_id": int(entry["receipt_id"]),
            "merchant": entry.get("merchant"),
        }
        for entry in golden["receipts"]
        if not entry.get("local_only")
    ]
    if extra_path is not None:
        for entry in json.load(open(extra_path, encoding="utf-8")):
            receipts.append(
                {
                    "image_id": entry["image_id"],
                    "receipt_id": int(entry["receipt_id"]),
                    "merchant": entry.get("merchant"),
                }
            )
    seen: set[tuple[str, int]] = set()
    unique: list[dict[str, Any]] = []
    for entry in sorted(
        receipts, key=lambda r: (r["image_id"], r["receipt_id"])
    ):
        key = (entry["image_id"], entry["receipt_id"])
        if key not in seen:
            seen.add(key)
            unique.append(entry)
    return unique


def _trim_metadata(metadata: dict[str, Any], keys: tuple[str, ...]) -> dict:
    return {k: metadata[k] for k in keys if metadata.get(k) is not None}


class _CaptureStore:
    """Read-only VerificationStore: captures updates instead of writing."""

    def __init__(self, dynamo: Any) -> None:
        self._dynamo = dynamo
        self.updates: list[Any] = []

    def get_receipt_sections_from_receipt(
        self, image_id: str, receipt_id: int
    ) -> list[Any]:
        return self._dynamo.get_receipt_sections_from_receipt(
            image_id, receipt_id
        )

    def update_receipt_section(self, section: Any) -> None:
        self.updates.append(section)  # never persisted


class _Capture:
    def __init__(self, table: str, words_per_receipt: int) -> None:
        # Imports deferred so --help works without the full dependency set.
        from receipt_agent.clients.factory import (  # noqa: PLC0415
            create_chroma_client,
        )
        from receipt_dynamo import DynamoClient  # noqa: PLC0415
        from receipt_upload.merchant_resolution.resolver import (  # noqa: PLC0415
            INVALID_PLACE_IDS,
            MerchantResolver,
        )

        self.dynamo = DynamoClient(table)
        self.chroma = create_chroma_client(mode="read")
        self.resolver = MerchantResolver(
            dynamo_client=self.dynamo,
            places_client=None,
            openai_client=None,
        )
        self.invalid_place_ids = INVALID_PLACE_IDS
        self.words_per_receipt = words_per_receipt
        self.vectors: dict[str, dict[str, list[float]]] = {
            "lines": {},
            "words": {},
        }
        self._place_cache: dict[
            tuple[str, int], tuple[Optional[str], Optional[str]]
        ] = {}
        self._section_cache: dict[tuple[str, int], dict[int, str]] = {}

    # -- Chroma access -----------------------------------------------------

    def _get_embeddings(
        self, collection: str, ids: list[str]
    ) -> dict[str, list[float]]:
        if not ids:
            return {}
        result = self.chroma.get(
            collection_name=collection, ids=ids, include=["embeddings"]
        )
        found: dict[str, list[float]] = {}
        for key, emb in zip(
            result.get("ids", []), result.get("embeddings", [])
        ):
            if emb is not None:
                found[key] = [float(v) for v in emb]
        return found

    def _query_neighbors(
        self,
        collection: str,
        embedding: list[float],
        top_k: int,
        meta_keys: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        result = self.chroma.query(
            collection_name=collection,
            query_embeddings=[embedding],
            n_results=top_k,
            include=["metadatas", "distances"],
        )
        ids = (result.get("ids") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        neighbors = []
        for key, dist, meta in zip(ids, distances, metadatas):
            neighbors.append(
                {
                    "key": key,
                    "distance": fixtures_io.round_distance(dist),
                    "metadata": _trim_metadata(dict(meta or {}), meta_keys),
                }
            )
        return neighbors

    def _fetch_neighbor_vectors(
        self, collection: str, neighbors: list[dict[str, Any]]
    ) -> None:
        store = self.vectors[collection]
        missing = [n["key"] for n in neighbors if n["key"] not in store]
        for key, emb in self._get_embeddings(collection, missing).items():
            store[key] = fixtures_io.round_vector(emb)

    # -- Dynamo enrichment -------------------------------------------------

    def _place_for(
        self, image_id: str, receipt_id: int
    ) -> tuple[Optional[str], Optional[str]]:
        cache_key = (image_id, receipt_id)
        if cache_key not in self._place_cache:
            place_id = merchant_name = None
            try:
                place = self.dynamo.get_receipt_place(image_id, receipt_id)
                if (
                    place
                    and place.place_id
                    and place.place_id not in self.invalid_place_ids
                ):
                    place_id = place.place_id
                    merchant_name = getattr(place, "merchant_name", None)
            except Exception:  # noqa: BLE001 - absent place is normal
                pass
            self._place_cache[cache_key] = (place_id, merchant_name)
        return self._place_cache[cache_key]

    def _enrich_with_place(self, neighbors: list[dict[str, Any]]) -> None:
        """Materialize the resolver's per-candidate Dynamo place lookup."""
        for neighbor in neighbors:
            meta = neighbor["metadata"]
            image_id = meta.get("image_id")
            receipt_id = meta.get("receipt_id")
            if not image_id or receipt_id is None:
                continue
            place_id, merchant_name = self._place_for(
                str(image_id), int(receipt_id)
            )
            if place_id:
                meta["dynamo_place_id"] = place_id
                if merchant_name:
                    meta["dynamo_merchant_name"] = merchant_name

    def _valid_section_label(self, meta: dict[str, Any]) -> Optional[str]:
        """The verifier's candidate label for one neighbor (VALID sections)."""
        from receipt_upload.section_verifier import (  # noqa: PLC0415
            _candidate_label,
        )

        return _candidate_label(meta, self._section_cache, self.dynamo)

    # -- families ----------------------------------------------------------

    def capture_merchant(
        self, image_id: str, receipt_id: int
    ) -> dict[str, Any]:
        lines = self.dynamo.list_receipt_lines_from_receipt(
            image_id, receipt_id
        )
        words = self.dynamo.list_receipt_words_from_receipt(
            image_id, receipt_id
        )
        labels = []
        last_key = None
        while True:
            page, last_key = self.dynamo.list_receipt_word_labels_for_receipt(
                image_id, receipt_id, last_evaluated_key=last_key
            )
            labels.extend(page)
            if not last_key:
                break
        resolver = self.resolver
        phone = resolver._extract_phone(words)
        address = resolver._extract_address(words) or (
            resolver._extract_labeled_text(
                words, labels, "ADDRESS_LINE", require_valid=False
            )
        )
        query_lines: list[tuple[str, Any]] = []
        if phone:
            line = resolver._get_line_for_phone(words, lines, phone)
            if line:
                query_lines.append(("chroma_phone", line))
        if address:
            line = resolver._get_line_for_address(words, lines, address)
            if line:
                query_lines.append(("chroma_address", line))
        merchant_line = resolver._get_merchant_line(lines)
        if merchant_line:
            query_lines.append(("chroma_text", merchant_line))

        line_texts = [
            ln.text for ln in sorted(lines, key=lambda l: l.line_id) if ln.text
        ]
        queries = []
        for tier, line in query_lines:
            key = _line_key(image_id, receipt_id, line.line_id)
            embeddings = self._get_embeddings("lines", [key])
            if key not in embeddings:
                continue  # line is not a visual-row primary; no stored vector
            self.vectors["lines"][key] = fixtures_io.round_vector(
                embeddings[key]
            )
            neighbors = self._query_neighbors(
                "lines", embeddings[key], MERCHANT_TOP_K, _LINE_META_KEYS
            )
            self._enrich_with_place(neighbors)
            self._fetch_neighbor_vectors("lines", neighbors)
            queries.append(
                {
                    "tier": tier,
                    "query_key": key,
                    "line_id": line.line_id,
                    "line_text": line.text,
                    "neighbors": neighbors,
                }
            )

        context = {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "expected_phone": phone,
            "expected_address": address,
            "line_texts": line_texts,
        }
        decided = decision.decide_merchant(queries, context)
        own_place_id, own_merchant = self._place_for(image_id, receipt_id)
        return {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "context": context,
            "queries": queries,
            "decision": decided,
            "dynamo_place": (
                {"place_id": own_place_id, "merchant_name": own_merchant}
                if own_place_id
                else None
            ),
        }

    def capture_words(self, image_id: str, receipt_id: int) -> dict[str, Any]:
        words = self.dynamo.list_receipt_words_from_receipt(
            image_id, receipt_id
        )
        ordered = sorted(words, key=lambda w: (w.line_id, w.word_id))
        candidate_keys = {
            _word_key(image_id, receipt_id, w.line_id, w.word_id): w
            for w in ordered
        }
        stored = self._get_embeddings("words", list(candidate_keys))
        queries = []
        for word in ordered:
            if len(queries) >= self.words_per_receipt:
                break
            key = _word_key(image_id, receipt_id, word.line_id, word.word_id)
            embedding = stored.get(key)
            if embedding is None:
                continue
            self.vectors["words"][key] = fixtures_io.round_vector(embedding)
            neighbors = self._query_neighbors(
                "words", embedding, WORD_TOP_K, _WORD_META_KEYS
            )
            self._fetch_neighbor_vectors("words", neighbors)
            queries.append(
                {
                    "query_key": key,
                    "line_id": word.line_id,
                    "word_id": word.word_id,
                    "text": word.text,
                    "neighbors": neighbors,
                }
            )
        return {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "queries": queries,
        }

    def capture_sections(
        self, image_id: str, receipt_id: int
    ) -> dict[str, Any]:
        from receipt_upload.section_verifier import (  # noqa: PLC0415
            KNN_NEIGHBORS,
            verify_receipt_sections,
        )

        rows = self.dynamo.get_receipt_rows_from_receipt(image_id, receipt_id)
        rows = sorted(rows, key=lambda r: r.row_id)
        row_entries = []
        row_objects = []
        row_embeddings = []
        for row in rows:
            primary_line = min(row.line_ids) if row.line_ids else None
            if primary_line is None:
                continue
            key = _line_key(image_id, receipt_id, primary_line)
            embeddings = self._get_embeddings("lines", [key])
            if key not in embeddings:
                continue
            self.vectors["lines"][key] = fixtures_io.round_vector(
                embeddings[key]
            )
            neighbors = self._query_neighbors(
                "lines", embeddings[key], KNN_NEIGHBORS, _LINE_META_KEYS
            )
            self._fetch_neighbor_vectors("lines", neighbors)
            neighbor_labels = {}
            for neighbor in neighbors:
                label = self._valid_section_label(neighbor["metadata"])
                if label is not None:
                    neighbor_labels[neighbor["key"]] = label
            row_entries.append(
                {
                    "row_id": row.row_id,
                    "query_key": key,
                    "neighbors": neighbors,
                    "neighbor_labels": neighbor_labels,
                }
            )
            row_objects.append(row)
            row_embeddings.append(embeddings[key])

        store = _CaptureStore(self.dynamo)
        votes = []
        if row_objects:
            for vote in verify_receipt_sections(
                self.chroma, store, row_objects, row_embeddings
            ):
                votes.append(
                    {
                        "row_id": vote.row_id,
                        "section_type": vote.section_type,
                        "confidence": round(vote.confidence, 6),
                    }
                )
        section_updates = [
            {
                "section_type": str(section.section_type),
                "verification_status": section.verification_status,
                "predicted": section.verification_section_type,
                "disagreement_row_ids": section.disagreement_row_ids,
            }
            for section in store.updates
        ]
        return {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "rows": row_entries,
            "votes": sorted(votes, key=lambda v: v["row_id"]),
            "section_updates": section_updates,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--table", default="ReceiptsTable-dc5be22")
    parser.add_argument(
        "--out",
        type=Path,
        default=_REPO_ROOT / "tests" / "fixtures" / "similarity",
    )
    parser.add_argument("--golden", type=Path, default=GOLDEN_JSON)
    parser.add_argument(
        "--extra-receipts",
        type=Path,
        default=None,
        help="JSON list of {image_id, receipt_id[, merchant]} to top up the "
        "golden set (e.g. the May-26 known-merchant batch)",
    )
    parser.add_argument(
        "--words-per-receipt", type=int, default=DEFAULT_WORDS_PER_RECEIPT
    )
    parser.add_argument(
        "--min-receipts",
        type=int,
        default=DEFAULT_MIN_RECEIPTS,
        help="fail if fewer receipts produce fixtures (rubric: >= 40)",
    )
    args = parser.parse_args()

    _require_creds()
    receipts = _load_golden_receipts(args.golden, args.extra_receipts)
    print(f"Golden set: {len(receipts)} receipts")
    capture = _Capture(args.table, args.words_per_receipt)

    merchant_fixtures = []
    word_fixtures = []
    section_fixtures = []
    captured = []
    for entry in receipts:
        image_id, receipt_id = entry["image_id"], entry["receipt_id"]
        try:
            merchant = capture.capture_merchant(image_id, receipt_id)
            words = capture.capture_words(image_id, receipt_id)
            sections = capture.capture_sections(image_id, receipt_id)
        except Exception as exc:  # noqa: BLE001 - report and continue
            print(f"  SKIP {image_id}#{receipt_id}: {exc}")
            continue
        if not (merchant["queries"] or words["queries"]):
            print(f"  SKIP {image_id}#{receipt_id}: no stored vectors")
            continue
        merchant_fixtures.append(merchant)
        word_fixtures.append(words)
        section_fixtures.append(sections)
        captured.append(entry)
        print(
            f"  OK {image_id}#{receipt_id}: "
            f"{len(merchant['queries'])} merchant q, "
            f"{len(words['queries'])} word q, "
            f"{len(sections['rows'])} rows"
        )

    if len(captured) < args.min_receipts:
        print(
            f"ERROR: only {len(captured)} receipts captured "
            f"(< {args.min_receipts}). Add --extra-receipts.",
            file=sys.stderr,
        )
        return 1

    manifest = {
        "captured_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
        "table": args.table,
        "distance_decimals": fixtures_io.DISTANCE_DECIMALS,
        "vector_decimals": fixtures_io.VECTOR_DECIMALS,
        "merchant_top_k": MERCHANT_TOP_K,
        "word_top_k": WORD_TOP_K,
        "words_per_receipt": args.words_per_receipt,
        "receipts": captured,
        "counts": {
            "receipts": len(captured),
            "merchant_queries": sum(
                len(m["queries"]) for m in merchant_fixtures
            ),
            "word_queries": sum(len(w["queries"]) for w in word_fixtures),
            "section_rows": sum(len(s["rows"]) for s in section_fixtures),
        },
    }
    fixtures_io.write_fixtures(
        args.out,
        manifest=manifest,
        merchant=merchant_fixtures,
        words=word_fixtures,
        sections=section_fixtures,
        vectors=capture.vectors,
    )
    print(f"Wrote fixtures for {len(captured)} receipts to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
