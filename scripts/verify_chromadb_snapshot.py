#!/usr/bin/env python3
"""
Download the latest lines and words ChromaDB snapshots atomically and verify they open.

Usage:
  python scripts/verify_chromadb_snapshot.py --env dev --out-dir /tmp/chroma_snapshots

Options:
  --collections lines,words  Comma-separated; default=lines,words
  --version-lines VERSION    Optional explicit snapshot version for lines
  --version-words VERSION    Optional explicit snapshot version for words
"""

import argparse
import os
import sys
import tempfile
from pathlib import Path

from receipt_dynamo.data._pulumi import load_env  # noqa: F401
from receipt_label.utils.chroma_s3_helpers import (
    download_snapshot_atomic,
    download_snapshot_from_s3,
)
from receipt_label.utils.chroma_client import ChromaDBClient


def _resolve_bucket(env_name: str) -> str:
    env = load_env(env_name)
    # Prefer chromadb_bucket_name; fall back to embedding_chromadb_bucket_name or CHROMADB_BUCKET env
    return (
        env.get("chromadb_bucket_name")
        or env.get("embedding_chromadb_bucket_name")
        or os.environ.get("CHROMADB_BUCKET")
        or ""
    )


def _download_and_verify(
    bucket: str,
    collection: str,
    out_dir: Path,
    version: str | None,
    image_id: str | None,
    receipt_id: int | None,
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)

    # Atomic download into a temp directory under out_dir
    local_path = str(out_dir)
    # If an explicit version is supplied, bypass pointer and download that version
    if version:
        versioned_key = f"{collection}/snapshot/timestamped/{version}/"
        result = download_snapshot_from_s3(
            bucket=bucket,
            snapshot_key=versioned_key,
            local_snapshot_path=local_path,
            verify_integrity=True,
        )
        # Normalize result keys to match atomic path
        if result.get("status") == "downloaded":
            result = {
                "status": "downloaded",
                "collection": collection,
                "version_id": version,
                "versioned_key": versioned_key,
                "local_path": local_path,
            }
    else:
        result = download_snapshot_atomic(
            bucket=bucket,
            collection=collection,
            local_path=local_path,
            verify_integrity=True,
        )

    if result.get("status") != "downloaded":
        return {
            "collection": collection,
            "status": result.get("status"),
            "error": result,
        }

    # Open the snapshot and count vectors
    client = ChromaDBClient(
        persist_directory=local_path, mode="read", metadata_only=True
    )
    # List available collections in this snapshot (helps debug naming)
    available_collections: list[str] = []
    if client.client is not None:
        try:
            available_collections = [
                c.name for c in client.client.list_collections()
            ]
        except (AttributeError, RuntimeError, TypeError, ValueError):
            # Best-effort only; ignore errors here
            available_collections = []
    coll = client.get_collection(collection)
    # Prefer count() if available; otherwise infer via a small page
    count = None
    if hasattr(coll, "count"):
        count = coll.count()  # type: ignore[attr-defined]
    else:
        page = coll.get(limit=1)
        count = len(page.get("ids", []))

    # Optionally count just this receipt
    receipt_count = None
    if image_id and receipt_id is not None:
        # Try string match first (some snapshots store receipt_id as string)
        where_str = {
            "$and": [
                {"image_id": {"$eq": image_id}},
                {"receipt_id": {"$eq": str(receipt_id)}},
            ]
        }
        filtered = coll.get(where=where_str, limit=100000)
        receipt_count = len(filtered.get("ids", []))

        # Fallback: try numeric match (some snapshots store receipt_id as number)
        if receipt_count == 0:
            where_int = {
                "$and": [
                    {"image_id": {"$eq": image_id}},
                    {"receipt_id": {"$eq": receipt_id}},
                ]
            }
            filtered_int = coll.get(where=where_int, limit=100000)
            receipt_count = len(filtered_int.get("ids", []))

    return {
        "collection": collection,
        "status": "ok",
        "version": result.get("version_id") or result.get("version"),
        "path": local_path,
        "count": count,
        "receipt_count": receipt_count,
        "available_collections": available_collections,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify ChromaDB snapshots (lines/words)"
    )
    parser.add_argument(
        "--env", required=True, help="Pulumi stack/env name (e.g., dev, prod)"
    )
    parser.add_argument(
        "--out-dir",
        default=str(Path(tempfile.gettempdir()) / "chromadb_snapshots"),
        help="Base directory to download snapshots into",
    )
    parser.add_argument(
        "--collections",
        default="lines,words",
        help="Comma-separated list among: lines,words",
    )
    parser.add_argument(
        "--version-lines", default=None, help="Force lines snapshot version"
    )
    parser.add_argument(
        "--version-words", default=None, help="Force words snapshot version"
    )
    parser.add_argument(
        "--image-id", default=None, help="Filter: image_id to verify"
    )
    parser.add_argument(
        "--receipt-id",
        type=int,
        default=None,
        help="Filter: receipt_id to verify",
    )

    args = parser.parse_args()

    bucket = _resolve_bucket(args.env)
    if not bucket:
        print(
            "ERROR: Could not resolve CHROMADB bucket from env or Pulumi outputs."
        )
        return 2

    base = Path(args.out_dir)
    cols = [c.strip() for c in args.collections.split(",") if c.strip()]
    versions = {
        "lines": args.version_lines,
        "words": args.version_words,
    }

    results: list[dict] = []
    for c in cols:
        if c not in ("lines", "words"):
            print(f"Skipping unknown collection: {c}")
            continue
        target = base / c
        res = _download_and_verify(
            bucket,
            c,
            target,
            versions.get(c),
            args.image_id,
            args.receipt_id,
        )
        results.append(res)

    # Print a concise summary
    for r in results:
        if r.get("status") == "ok":
            base_line = (
                f"OK {r['collection']}: version={r.get('version')} count~{r.get('count')}"
                f" path={r.get('path')}"
            )
            ac = r.get("available_collections") or []
            if ac:
                base_line += f" collections={ac}"
            if r.get("receipt_count") is not None:
                base_line += f" receipt_count={r.get('receipt_count')}"
            print(base_line)
        else:
            print(
                f"FAIL {r.get('collection')}: status={r.get('status')} details={r.get('error')}"
            )

    return 0 if all(r.get("status") == "ok" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
