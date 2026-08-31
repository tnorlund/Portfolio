"""Load and save similarity-harness JSON fixtures."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

MERCHANT_FILE = "merchant_resolution.json"
WORD_NEIGHBORS_FILE = "word_neighbors.json"
SECTION_FILE = "section_verifier.json"
VECTORS_FILE = "vectors.json"
GOLDEN_SET_FILE = "golden_set.json"

# Distances in committed JSON are rounded so two capture runs minutes
# apart serialize identically on the fake backend. Live ANN backends
# may still differ at rank boundaries; see tests/fixtures/similarity.
DISTANCE_QUANTUM = 1e-8
DISTANCE_ATOL = 1e-5
# Live Chroma ANN vs itself minutes later: neighbor-set Jaccard at k=10
# must stay at or above this. Fake captures are bitwise identical (1.0).
LIVE_CAPTURE_JACCARD_MIN = 0.95


def repo_root() -> Path:
    """Repository root (parent of the ``receipt_embeddings`` project)."""
    return Path(__file__).resolve().parents[2]


def default_fixture_dir() -> Path:
    return repo_root() / "tests" / "fixtures" / "similarity"


def round_distance(distance: float) -> float:
    """Quantize cosine distance for stable JSON."""
    return round(float(distance), 8)


def dump_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_fixture_bundle(fixture_dir: Path | None = None) -> dict[str, Any]:
    """Load the four committed fixture documents."""
    root = fixture_dir or default_fixture_dir()
    return {
        "golden_set": load_json(root / GOLDEN_SET_FILE),
        "merchant_resolution": load_json(root / MERCHANT_FILE),
        "word_neighbors": load_json(root / WORD_NEIGHBORS_FILE),
        "section_verifier": load_json(root / SECTION_FILE),
        "vectors": load_json(root / VECTORS_FILE),
    }


def write_fixture_bundle(fixture_dir: Path, bundle: Mapping[str, Any]) -> None:
    dump_json(fixture_dir / GOLDEN_SET_FILE, bundle["golden_set"])
    dump_json(fixture_dir / MERCHANT_FILE, bundle["merchant_resolution"])
    dump_json(fixture_dir / WORD_NEIGHBORS_FILE, bundle["word_neighbors"])
    dump_json(fixture_dir / SECTION_FILE, bundle["section_verifier"])
    dump_json(fixture_dir / VECTORS_FILE, bundle["vectors"])
