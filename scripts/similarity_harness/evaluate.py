#!/usr/bin/env python3
"""Score a vector backend against committed golden fixtures.

``--backend fake`` is pure given the fixture JSON (no network).
``--backend chroma`` requires CHROMA_CLOUD_* and is the self-parity
sanity check against live Cloud. ``--backend dynamo`` is wired but
raises until Round C/D (Round A does not create indexes).

Writes scorecard.json with neighbor recall@k, merchant agreement %,
tier distribution, p50/p95 latency, and est. $/query (SPEC §8).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from receipt_embeddings.chroma_adapter import ChromaVectorSearchClient
from receipt_embeddings.fixtures import (
    default_fixture_dir,
    load_fixture_bundle,
)
from receipt_embeddings.harness import (
    chroma_cloud_credentials,
    client_for_backend,
    evaluate_backend,
)


def _live_chroma_client() -> ChromaVectorSearchClient:
    creds = chroma_cloud_credentials()
    if creds is None:
        raise SystemExit(
            "evaluate --backend chroma requires CHROMA_CLOUD_API_KEY, "
            "CHROMA_CLOUD_TENANT, and CHROMA_CLOUD_DATABASE"
        )
    from receipt_chroma import ChromaClient

    chroma = ChromaClient(
        mode="read",
        cloud_api_key=creds["api_key"],
        cloud_tenant=creds["tenant"],
        cloud_database=creds["database"],
    )
    return ChromaVectorSearchClient(chroma)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend",
        choices=("fake", "chroma", "dynamo"),
        required=True,
    )
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=None,
        help="Fixture directory (default: tests/fixtures/similarity)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("scorecard.json"),
        help="Scorecard JSON path",
    )
    args = parser.parse_args(argv)
    bundle = load_fixture_bundle(args.fixtures or default_fixture_dir())
    chroma = None
    try:
        if args.backend == "chroma":
            chroma = _live_chroma_client()
            client = chroma
        else:
            client = client_for_backend(args.backend, bundle)
        scorecard = evaluate_backend(client, bundle, backend=args.backend)
    finally:
        if chroma is not None:
            inner = getattr(chroma, "_chroma", None)
            close = getattr(inner, "close", None)
            if close is not None:
                close()
    args.out.write_text(
        json.dumps(scorecard, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(scorecard, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
