#!/usr/bin/env python3
"""Capture golden similarity fixtures (three query families).

Live Chroma Cloud capture (requires CHROMA_CLOUD_API_KEY / TENANT /
DATABASE). Refuses to run without those credentials.

Offline / CI: ``--synthetic`` writes the committed exact-NN fixtures
from :mod:`receipt_embeddings.synthetic` (no AWS, no Chroma). Two
synthetic runs are bitwise identical. Live ANN runs minutes apart may
differ at rank boundaries; see tests/fixtures/similarity/README.md.

Never creates DynamoDB vector indexes and never writes the receipts
table.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from receipt_embeddings.chroma_adapter import (
    INDEX_TO_COLLECTION,
    ChromaVectorSearchClient,
)
from receipt_embeddings.fixtures import (
    SCHEMA_VERSION,
    default_fixture_dir,
    write_fixture_bundle,
)
from receipt_embeddings.harness import (
    capture_from_client,
    chroma_cloud_credentials,
)
from receipt_embeddings.synthetic import (
    golden_receipts,
    write_synthetic_fixtures,
)
from receipt_embeddings.vector_client import (
    LINE_EMBEDDINGS_INDEX,
    WORD_EMBEDDINGS_INDEX,
    index_for_key,
)


def _live_chroma_client() -> Any:
    """Construct a read-only Cloud client. receipt_chroma at function scope
    so ``--synthetic`` never imports chromadb.
    """
    creds = chroma_cloud_credentials()
    if creds is None:
        raise SystemExit(
            "Refusing live capture: set CHROMA_CLOUD_API_KEY, "
            "CHROMA_CLOUD_TENANT, and CHROMA_CLOUD_DATABASE. "
            "Use --synthetic for the offline exact-NN fixtures."
        )
    from receipt_chroma import ChromaClient

    return ChromaClient(
        mode="read",
        cloud_api_key=creds["api_key"],
        cloud_tenant=creds["tenant"],
        cloud_database=creds["database"],
    )


def _ids_for_receipt(
    chroma: Any, collection: str, image_id: str, receipt_id: int
) -> list[str]:
    where: dict[str, Any] = {
        "$and": [
            {"image_id": image_id},
            {"receipt_id": receipt_id},
        ]
    }
    result = chroma.get(
        collection_name=collection,
        where=where,
        include=["metadatas"],
    )
    ids = list(result.get("ids") or [])
    if ids:
        return ids
    # Some Cloud records store receipt_id as a string.
    result = chroma.get(
        collection_name=collection,
        where={
            "$and": [
                {"image_id": image_id},
                {"receipt_id": str(receipt_id)},
            ]
        },
        include=["metadatas"],
    )
    return list(result.get("ids") or [])


def capture_live(out_dir: Path) -> None:
    chroma = _live_chroma_client()
    client = ChromaVectorSearchClient(chroma)
    receipts = golden_receipts()
    line_keys: dict[str, list[str]] = {}
    word_keys: dict[str, list[str]] = {}
    present: list[dict[str, Any]] = []
    try:
        for receipt in receipts:
            image_id = str(receipt["image_id"])
            receipt_id = int(receipt["receipt_id"])
            lines = _ids_for_receipt(
                chroma,
                INDEX_TO_COLLECTION[LINE_EMBEDDINGS_INDEX],
                image_id,
                receipt_id,
            )
            words = _ids_for_receipt(
                chroma,
                INDEX_TO_COLLECTION[WORD_EMBEDDINGS_INDEX],
                image_id,
                receipt_id,
            )
            if not lines:
                continue
            line_keys[image_id] = lines
            word_keys[image_id] = words
            present.append(receipt)
        if len(present) < 40:
            raise SystemExit(
                f"Live capture found {len(present)} receipts with line "
                "embeddings; need ≥40. Check Cloud database / golden ids."
            )
        captured = capture_from_client(
            client, present, line_keys=line_keys, word_keys=word_keys
        )
        keys: set[str] = set()
        for query in captured["merchant_resolution"]["queries"]:
            keys.add(query["query_key"])
            keys.update(n["key"] for n in query["neighbors"])
        for query in captured["word_neighbors"]["queries"]:
            keys.add(query["query_key"])
            keys.update(n["key"] for n in query["neighbors"])
        for receipt in captured["section_verifier"]["queries"]:
            for vote in receipt["votes"]:
                keys.add(vote["query_key"])
                keys.update(n["key"] for n in vote["neighbors"])
        items: list[dict[str, Any]] = []
        for key in sorted(keys):
            try:
                vector = list(client.get_vector(key))
            except KeyError:
                continue
            items.append(
                {
                    "key": key,
                    "index": index_for_key(key),
                    "vector": [round(float(x), 8) for x in vector],
                    "metadata": {},
                }
            )
        bundle = {
            "golden_set": {
                "schema_version": SCHEMA_VERSION,
                "source": "chroma_cloud",
                "n_receipts": len(present),
                "receipts": present,
            },
            "merchant_resolution": captured["merchant_resolution"],
            "word_neighbors": captured["word_neighbors"],
            "section_verifier": captured["section_verifier"],
            "vectors": {
                "schema_version": SCHEMA_VERSION,
                "distance": "cosine",
                "items": items,
            },
        }
        write_fixture_bundle(out_dir, bundle)
    finally:
        close = getattr(chroma, "close", None)
        if close is not None:
            close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=default_fixture_dir(),
        help="Fixture directory (default: tests/fixtures/similarity)",
    )
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="Write exact-NN fixtures (no Chroma, no AWS)",
    )
    args = parser.parse_args(argv)
    if args.synthetic:
        write_synthetic_fixtures(args.out)
        print(f"Wrote synthetic fixtures to {args.out}")
        return 0
    capture_live(args.out)
    print(f"Wrote live Chroma fixtures to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
