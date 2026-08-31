#!/usr/bin/env python3
"""Capture golden similarity fixtures for the three vector query families.

Default: live Chroma Cloud (dev). Refuses to run without CHROMA_CLOUD_*
credentials unless ``--synthetic`` is passed — Round A does not hit AWS
tables and does not invent a live capture.

    python scripts/similarity_harness/capture_golden.py --synthetic
    python scripts/similarity_harness/capture_golden.py   # needs Chroma creds

Output (committed under tests/fixtures/similarity/):

* ``golden.json`` — per-receipt merchant neighbors+tier+decision, word
  top-30 neighbors, section-verifier votes
* ``corpus.json`` — compact vector dump so ``evaluate.py --backend fake``
  can run offline (synthetic captures only; live capture omits vectors
  other than the per-query embedding)
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from receipt_embeddings.testing import FakeVectorIndex
from receipt_embeddings.vector_client import INDEX_LINES, INDEX_WORDS

# Allow ``python scripts/similarity_harness/capture_golden.py``.
_SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _SCRIPTS_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.similarity_harness.backends import (  # noqa: E402
    ChromaVectorClient,
)
from scripts.similarity_harness.common import (  # noqa: E402
    DEFAULT_FIXTURE_DIR,
    MAY26_BATCH_SIZE,
    MERCHANT_TOP_K,
    QUERY_KINDS,
    ROWS_PER_RECEIPT,
    SECTION_TOP_K,
    SECTION_TYPES,
    SYNTHETIC_DIM,
    SYNTHETIC_SEED,
    WORD_TOP_K,
    WORDS_PER_RECEIPT,
    chroma_cloud_configured,
    dump_json,
    fixture_meta,
    golden_receipt_set,
    line_key,
    merchant_decision_from_neighbors,
    round_vector,
    scored_to_neighbor,
    section_vote_from_neighbors,
    word_key,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _unit(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if norm == 0.0:
        return vector
    return vector / norm


def _stable_seed(text: str) -> int:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "little")


def _merchant_basis(merchant: str, dim: int) -> np.ndarray:
    rng = np.random.default_rng(_stable_seed(f"merchant:{merchant}"))
    return _unit(rng.normal(size=dim))


def _build_synthetic_corpus(
    receipts: Sequence[Mapping[str, Any]],
    *,
    dim: int,
    seed: int,
) -> tuple[FakeVectorIndex, dict[str, Any], list[dict[str, Any]]]:
    rng = np.random.default_rng(seed)
    index = FakeVectorIndex()
    corpus_items: list[dict[str, Any]] = []
    annotated: list[dict[str, Any]] = []

    def add_item(
        *,
        key: str,
        which: str,
        vector: np.ndarray,
        metadata: dict[str, Any],
    ) -> None:
        unit = _unit(vector)
        stored = round_vector(unit.tolist())
        index.upsert(key=key, vector=stored, index=which, metadata=metadata)
        corpus_items.append(
            {
                "key": key,
                "index": which,
                "vector": stored,
                "metadata": metadata,
            }
        )

    for receipt_index, receipt in enumerate(receipts):
        image_id = str(receipt["image_id"])
        receipt_id = int(receipt["receipt_id"])
        merchant = str(receipt["merchant_truth"])
        query_kind = QUERY_KINDS[receipt_index % len(QUERY_KINDS)]
        basis = _merchant_basis(merchant, dim)
        jitter = rng.normal(scale=0.05, size=dim)
        header_vec = basis + jitter
        header_line_id = 1
        header_key = line_key(image_id, receipt_id, header_line_id)
        header_meta = {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "line_id": header_line_id,
            "merchant_name": merchant,
            "place_id": f"place-{merchant.replace(' ', '-').lower()}",
            "section_type": "HEADER",
            "query_kind": query_kind,
        }
        add_item(
            key=header_key,
            which=INDEX_LINES,
            vector=header_vec,
            metadata=header_meta,
        )

        row_keys: list[tuple[int, str, str]] = []
        for row_id in range(ROWS_PER_RECEIPT):
            section_type = SECTION_TYPES[row_id % len(SECTION_TYPES)]
            line_id = 10 + row_id
            key = line_key(image_id, receipt_id, line_id)
            # Nudge the row off the merchant header so neighbors aren't
            # trivially the header of the same receipt for every query.
            section_jitter = rng.normal(scale=0.08, size=dim)
            section_axis = np.zeros(dim)
            section_axis[row_id % dim] = 0.15
            vector = basis + section_jitter + section_axis
            meta = {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "line_id": line_id,
                "row_id": row_id,
                "merchant_name": merchant,
                "place_id": header_meta["place_id"],
                "section_type": section_type,
            }
            add_item(key=key, which=INDEX_LINES, vector=vector, metadata=meta)
            row_keys.append((row_id, key, section_type))

        word_keys: list[str] = []
        for word_offset in range(WORDS_PER_RECEIPT):
            line_id = 10
            word_id = word_offset + 1
            key = word_key(image_id, receipt_id, line_id, word_id)
            word_jitter = rng.normal(scale=0.04, size=dim)
            vector = basis + word_jitter
            meta = {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "line_id": line_id,
                "word_id": word_id,
                "merchant_name": merchant,
                "label_status": "validated" if word_offset == 0 else "pending",
                "text": f"{merchant.split()[0].lower()}-{word_id}",
            }
            add_item(key=key, which=INDEX_WORDS, vector=vector, metadata=meta)
            word_keys.append(key)

        annotated.append(
            {
                **receipt,
                "header_key": header_key,
                "query_kind": query_kind,
                "row_keys": row_keys,
                "word_keys": word_keys,
            }
        )

    corpus = {
        "schema_version": 1,
        "embedding_dim": dim,
        "n_items": len(corpus_items),
        "items": corpus_items,
    }
    return index, corpus, annotated


def _query_record(
    client: Any,
    *,
    query_key: str,
    query_vector: Sequence[float],
    index: str,
    top_k: int,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    results = client.search(query_vector, index, top_k)
    record: dict[str, Any] = {
        "query_key": query_key,
        "query_vector": round_vector(query_vector),
        "index": index,
        "top_k": top_k,
        "neighbors": [scored_to_neighbor(item) for item in results],
    }
    if extra:
        record.update(dict(extra))
    return record


def capture_from_index(
    client: Any,
    annotated: Sequence[Mapping[str, Any]],
    *,
    source: str,
    embedding_dim: int,
) -> dict[str, Any]:
    receipts_out: list[dict[str, Any]] = []
    for receipt in annotated:
        image_id = str(receipt["image_id"])
        receipt_id = int(receipt["receipt_id"])
        header_key = str(receipt["header_key"])
        header_vec = client.get_vector(header_key)
        if header_vec is None:
            continue
        query_kind = str(receipt["query_kind"])
        merchant_query = _query_record(
            client,
            query_key=header_key,
            query_vector=header_vec,
            index=INDEX_LINES,
            top_k=MERCHANT_TOP_K,
            extra={"query_kind": query_kind},
        )
        decision = merchant_decision_from_neighbors(
            merchant_query["neighbors"],
            image_id=image_id,
            receipt_id=receipt_id,
            query_kind=query_kind,
        )
        merchant_query.update(decision)

        word_queries = []
        for word_query_key in receipt["word_keys"]:
            vector = client.get_vector(str(word_query_key))
            if vector is None:
                continue
            word_queries.append(
                _query_record(
                    client,
                    query_key=str(word_query_key),
                    query_vector=vector,
                    index=INDEX_WORDS,
                    top_k=WORD_TOP_K,
                )
            )

        row_queries = []
        vote_counts = {"AGREED": 0, "DISAGREED": 0, "ABSTAINED": 0}
        for row_id, row_key, proposed in receipt["row_keys"]:
            vector = client.get_vector(str(row_key))
            if vector is None:
                continue
            row_query = _query_record(
                client,
                query_key=str(row_key),
                query_vector=vector,
                index=INDEX_LINES,
                top_k=SECTION_TOP_K,
                extra={
                    "row_id": int(row_id),
                    "proposed_section_type": proposed,
                },
            )
            vote = section_vote_from_neighbors(
                row_query["neighbors"],
                image_id=image_id,
                receipt_id=receipt_id,
                proposed_section_type=str(proposed),
            )
            row_query.update(vote)
            vote_counts[str(vote["vote"])] += 1
            row_queries.append(row_query)

        receipts_out.append(
            {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "merchant_truth": receipt["merchant_truth"],
                "source_set": receipt["source_set"],
                "merchant_resolution": merchant_query,
                "word_queries": word_queries,
                "section_verifier": {
                    "row_queries": row_queries,
                    "votes": vote_counts,
                },
            }
        )

    meta = fixture_meta(
        source=source,
        embedding_dim=embedding_dim,
        n_receipts=len(receipts_out),
    )
    meta["captured_at"] = _now()
    return {"meta": meta, "receipts": receipts_out}


def capture_synthetic(
    *,
    seed: int = SYNTHETIC_SEED,
    dim: int = SYNTHETIC_DIM,
    limit: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    receipts = golden_receipt_set()
    if limit is not None:
        receipts = receipts[:limit]
    index, corpus, annotated = _build_synthetic_corpus(
        receipts, dim=dim, seed=seed
    )
    golden = capture_from_index(
        index,
        annotated,
        source="synthetic_offline",
        embedding_dim=dim,
    )
    golden["meta"]["seed"] = seed
    golden["meta"]["may26_batch_size"] = MAY26_BATCH_SIZE
    golden["meta"]["note"] = (
        "Offline placeholder captured from FakeVectorIndex. The bake-off "
        "winner recaptures once against live Chroma Cloud; that run is the "
        "canonical committed set."
    )
    return golden, corpus


def _chroma_get_receipt_items(
    chroma: Any,
    collection: str,
    image_id: str,
    receipt_id: int,
) -> tuple[list[str], list[list[float]], list[dict[str, Any]]]:
    result = chroma.get(
        collection_name=collection,
        where={"image_id": image_id, "receipt_id": receipt_id},
        include=["embeddings", "metadatas"],
    )
    ids = list(result.get("ids") or [])
    embeddings = list(result.get("embeddings") or [])
    metadatas = list(result.get("metadatas") or [])
    return ids, embeddings, metadatas


def capture_chroma(limit: int | None = None) -> dict[str, Any]:
    """Live Chroma Cloud capture. Read-only; no Dynamo writes."""
    if not chroma_cloud_configured():
        raise SystemExit(
            "CHROMA_CLOUD_API_KEY / CHROMA_CLOUD_TENANT / "
            "CHROMA_CLOUD_DATABASE are not set. Refusing to invent a live "
            "capture. Pass --synthetic for the offline fixture builder."
        )
    from receipt_chroma import ChromaClient

    receipts = golden_receipt_set()
    if limit is not None:
        receipts = receipts[:limit]

    chroma = ChromaClient(
        cloud_api_key=os.environ["CHROMA_CLOUD_API_KEY"].strip(),
        cloud_tenant=os.environ["CHROMA_CLOUD_TENANT"].strip(),
        cloud_database=os.environ["CHROMA_CLOUD_DATABASE"].strip(),
        mode="read",
    )
    client = ChromaVectorClient(chroma)
    annotated: list[dict[str, Any]] = []
    try:
        for receipt in receipts:
            image_id = str(receipt["image_id"])
            receipt_id = int(receipt["receipt_id"])
            ids, _embeddings, metadatas = _chroma_get_receipt_items(
                chroma, "lines", image_id, receipt_id
            )
            if not ids:
                continue
            header_key = ids[0]
            header_meta = metadatas[0] if metadatas else {}
            query_kind = "text"
            if header_meta.get("normalized_phone_10"):
                query_kind = "phone"
            elif header_meta.get("normalized_full_address"):
                query_kind = "address"

            # Sample up to WORDS_PER_RECEIPT words and ROWS_PER_RECEIPT lines.
            row_keys = []
            for offset, (key, meta) in enumerate(zip(ids, metadatas)):
                if offset >= ROWS_PER_RECEIPT:
                    break
                row_keys.append(
                    (
                        offset,
                        key,
                        str(meta.get("section_type") or "ITEMS"),
                    )
                )

            word_ids, _, _ = _chroma_get_receipt_items(
                chroma, "words", image_id, receipt_id
            )
            word_keys = word_ids[:WORDS_PER_RECEIPT]

            annotated.append(
                {
                    **receipt,
                    "header_key": header_key,
                    "query_kind": query_kind,
                    "row_keys": row_keys,
                    "word_keys": word_keys,
                }
            )

        golden = capture_from_index(
            client,
            annotated,
            source="chroma_cloud",
            embedding_dim=1536,
        )
    finally:
        close = getattr(chroma, "close", None)
        if callable(close):
            close()
    return golden


def write_fixtures(
    fixture_dir: Path,
    golden: Mapping[str, Any],
    corpus: Mapping[str, Any] | None,
) -> None:
    dump_json(fixture_dir / "golden.json", golden)
    if corpus is not None:
        dump_json(fixture_dir / "corpus.json", corpus)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_FIXTURE_DIR,
        help="Directory for golden.json / corpus.json",
    )
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="Build offline FakeVectorIndex fixtures (no Chroma, no AWS)",
    )
    parser.add_argument("--seed", type=int, default=SYNTHETIC_SEED)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)

    if args.synthetic:
        golden, corpus = capture_synthetic(seed=args.seed, limit=args.limit)
        write_fixtures(args.out, golden, corpus)
        n_receipts = golden["meta"]["n_receipts"]
        print(
            f"Wrote synthetic fixtures for {n_receipts} receipts to {args.out}"
        )
        return 0

    golden = capture_chroma(limit=args.limit)
    write_fixtures(args.out, golden, corpus=None)
    print(
        f"Wrote Chroma fixtures for {golden['meta']['n_receipts']} "
        f"receipts to {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
