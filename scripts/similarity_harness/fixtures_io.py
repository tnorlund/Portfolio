"""Deterministic fixture I/O for the similarity harness.

Fixture layout (``tests/fixtures/similarity/`` by default):

- ``manifest.json``  — capture provenance, counts, rounding tolerances
- ``merchant.json``  — per-receipt merchant-resolution queries, neighbors,
  decision (tier + merchant), and the receipt's DynamoDB place reference
- ``words.json``     — per-receipt sampled words with top-30 neighbors
- ``sections.json``  — per-receipt row queries, neighbor labels, verifier votes
- ``vectors.json.gz``— sidecar: every query + neighbor vector, rounded. Feeds
  the fake backend for offline replay. Gzipped (vectors dominate size).

Determinism contract (rubric item 3): JSON is written with sorted keys and
fixed indentation; all distances are rounded to ``DISTANCE_DECIMALS`` and all
vector components to ``VECTOR_DECIMALS``; every list has an explicit
deterministic order (receipts by (image_id, receipt_id), queries by tier
order/word id, neighbors by backend rank). Two captures minutes apart are
byte-identical **provided the corpus did not change between runs**; ingest
writes between runs legitimately change neighbors, which is the one tolerance
we document rather than mask.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

DISTANCE_DECIMALS = 6
VECTOR_DECIMALS = 6

MANIFEST_FILE = "manifest.json"
MERCHANT_FILE = "merchant.json"
WORDS_FILE = "words.json"
SECTIONS_FILE = "sections.json"
VECTORS_FILE = "vectors.json.gz"


def round_distance(value: float) -> float:
    """Round a distance to the documented fixture tolerance."""
    return round(float(value), DISTANCE_DECIMALS)


def round_vector(vector: Sequence[float]) -> list[float]:
    """Round vector components to the documented fixture tolerance."""
    return [round(float(v), VECTOR_DECIMALS) for v in vector]


def _dump(path: Path, payload: Any) -> None:
    text = json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False)
    path.write_text(text + "\n", encoding="utf-8")


def write_fixtures(
    out_dir: Path,
    *,
    manifest: Mapping[str, Any],
    merchant: Sequence[Mapping[str, Any]],
    words: Sequence[Mapping[str, Any]],
    sections: Sequence[Mapping[str, Any]],
    vectors: Mapping[str, Mapping[str, Sequence[float]]],
) -> None:
    """Write the whole fixture set deterministically."""
    out_dir.mkdir(parents=True, exist_ok=True)
    _dump(out_dir / MANIFEST_FILE, manifest)
    _dump(out_dir / MERCHANT_FILE, list(merchant))
    _dump(out_dir / WORDS_FILE, list(words))
    _dump(out_dir / SECTIONS_FILE, list(sections))
    payload = json.dumps(
        {
            index: {key: list(vec) for key, vec in sorted(store.items())}
            for index, store in sorted(vectors.items())
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    # mtime=0 keeps the gzip container byte-stable across identical captures.
    with gzip.GzipFile(
        out_dir / VECTORS_FILE, "wb", compresslevel=9, mtime=0
    ) as handle:
        handle.write(payload)


def load_fixtures(fixtures_dir: Path) -> dict[str, Any]:
    """Load the fixture set; ``vectors`` is None when the sidecar is absent."""
    out: dict[str, Any] = {}
    for name, filename in (
        ("manifest", MANIFEST_FILE),
        ("merchant", MERCHANT_FILE),
        ("words", WORDS_FILE),
        ("sections", SECTIONS_FILE),
    ):
        out[name] = json.loads((fixtures_dir / filename).read_text("utf-8"))
    vectors_path = fixtures_dir / VECTORS_FILE
    if vectors_path.exists():
        with gzip.open(vectors_path, "rt", encoding="utf-8") as handle:
            out["vectors"] = json.load(handle)
    else:
        out["vectors"] = None
    return out


__all__ = [
    "DISTANCE_DECIMALS",
    "VECTOR_DECIMALS",
    "MANIFEST_FILE",
    "MERCHANT_FILE",
    "WORDS_FILE",
    "SECTIONS_FILE",
    "VECTORS_FILE",
    "load_fixtures",
    "round_distance",
    "round_vector",
    "write_fixtures",
]
