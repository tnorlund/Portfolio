"""Deterministic synthetic corpus for offline golden fixtures.

Live capture against Chroma Cloud is time-sensitive and requires
``CHROMA_CLOUD_*``. This generator produces ≥40 receipts covering all
three query families so ``evaluate.py --backend fake`` is a pure,
bitwise-repeatable scorecard. The winning Round A recaptures once
from live Chroma as the canonical set.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from receipt_embeddings.fixtures import (
    SCHEMA_VERSION,
    repo_root,
    write_fixture_bundle,
)
from receipt_embeddings.harness import capture_from_client
from receipt_embeddings.testing.fake_index import FakeVectorIndex
from receipt_embeddings.vector_client import (
    LINE_EMBEDDINGS_INDEX,
    WORD_EMBEDDINGS_INDEX,
    line_item_key,
    word_item_key,
)

SYNTHETIC_DIM = 16
SYNTHETIC_SEED = 26
MAY26_COUNT = 43
MAY26_NS = uuid.UUID("00000000-0000-4000-8000-000000000026")

# Visual-row section types used by the section verifier.
_SECTION_CYCLE = ("HEADER", "ITEMS", "ITEMS", "TOTAL", "FOOTER")

_EXTRA_MERCHANTS = (
    "Sprouts Farmers Market",
    "Trader Joe's",
    "Costco Wholesale",
    "Vons",
    "Target",
    "In-N-Out Burger",
    "The Home Depot",
    "Amazon Fresh",
    "Gelson's Westlake Village",
    "Wild Fork",
)


def _unit(vec: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vec))
    if norm == 0.0:
        out = np.zeros_like(vec)
        out[0] = 1.0
        return out
    return vec / norm


def _line_items_receipts() -> list[dict[str, Any]]:
    path = (
        repo_root()
        / "receipt_upload"
        / "tests"
        / "fixtures"
        / "line_items_golden.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    receipts: list[dict[str, Any]] = []
    for entry in payload["receipts"]:
        receipts.append(
            {
                "image_id": entry["image_id"],
                "receipt_id": int(entry.get("receipt_id", 1)),
                "merchant": entry.get("merchant") or "Unknown",
                "source": "line_items_golden",
                "local_only": bool(entry.get("local_only", False)),
            }
        )
    return receipts


def may26_image_id(n: int) -> str:
    """Stable UUID5 for catalog slot ``n`` of the May-26 batch."""
    return str(uuid.uuid5(MAY26_NS, f"may26-{n:02d}"))


def _may26_receipts() -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for n in range(1, MAY26_COUNT + 1):
        merchant = _EXTRA_MERCHANTS[(n - 1) % len(_EXTRA_MERCHANTS)]
        receipts.append(
            {
                "image_id": may26_image_id(n),
                "receipt_id": 1,
                "merchant": merchant,
                "source": "may26_batch",
                "local_only": False,
            }
        )
    return receipts


def golden_receipts() -> list[dict[str, Any]]:
    """Line-item goldens + 43-image May-26 catalog (AGENT_PLAN)."""
    return _line_items_receipts() + _may26_receipts()


def _phone_for(merchant: str) -> str:
    digest = hashlib.sha256(merchant.encode("utf-8")).hexdigest()
    return f"{int(digest[:10], 16) % 10_000_000_000:010d}"


def _address_for(merchant: str) -> str:
    return f"100 {merchant.split()[0].upper()} AVE"


def build_synthetic_index(
    receipts: Sequence[Mapping[str, Any]],
    *,
    dim: int = SYNTHETIC_DIM,
    seed: int = SYNTHETIC_SEED,
) -> tuple[FakeVectorIndex, dict[str, list[str]], dict[str, list[str]]]:
    """Exact-NN corpus: same-merchant receipts cluster, so decisions agree."""
    rng = np.random.default_rng(seed)
    merchants = sorted({str(r["merchant"]) for r in receipts})
    centroids = {name: _unit(rng.normal(size=dim)) for name in merchants}
    index = FakeVectorIndex()
    line_keys: dict[str, list[str]] = {}
    word_keys: dict[str, list[str]] = {}

    for receipt_ordinal, receipt in enumerate(receipts):
        image_id = str(receipt["image_id"])
        receipt_id = int(receipt["receipt_id"])
        merchant = str(receipt["merchant"])
        centroid = centroids[merchant]
        phone = _phone_for(merchant)
        address = _address_for(merchant)
        place_id = f"ChIJ{merchant.replace(' ', '')[:16]}"
        jitter = 0.04 * rng.normal(size=dim)
        receipt_shift = 0.01 * rng.normal(size=dim)
        lines: list[str] = []
        words: list[str] = []
        for line_id in range(1, 6):
            vec = _unit(
                centroid
                + jitter
                + receipt_shift
                + 0.005 * rng.normal(size=dim)
            )
            key = line_item_key(image_id, receipt_id, line_id)
            section = _SECTION_CYCLE[(line_id - 1) % len(_SECTION_CYCLE)]
            index.add(
                key,
                vec.tolist(),
                LINE_EMBEDDINGS_INDEX,
                {
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "line_id": line_id,
                    "merchant_name": merchant,
                    "place_id": place_id,
                    "normalized_phone_10": phone,
                    "normalized_full_address": address,
                    "section_type": section,
                    "text": f"{merchant} line {line_id}",
                },
            )
            lines.append(key)
            for word_id in range(1, 4):
                word_vec = _unit(
                    centroid
                    + 0.08 * rng.normal(size=dim)
                    + 0.002 * receipt_ordinal
                )
                wkey = word_item_key(image_id, receipt_id, line_id, word_id)
                index.add(
                    wkey,
                    word_vec.tolist(),
                    WORD_EMBEDDINGS_INDEX,
                    {
                        "image_id": image_id,
                        "receipt_id": receipt_id,
                        "line_id": line_id,
                        "word_id": word_id,
                        "merchant_name": merchant,
                        "label_status": "validated",
                        "text": f"w{word_id}",
                    },
                )
                words.append(wkey)
        line_keys[image_id] = lines
        word_keys[image_id] = words
    return index, line_keys, word_keys


def vectors_payload(index: FakeVectorIndex) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for stored in index.stored_items():
        items.append(
            {
                "key": stored.key,
                "index": stored.index,
                "vector": [round(float(x), 8) for x in stored.vector.tolist()],
                "metadata": stored.metadata,
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "dim": SYNTHETIC_DIM,
        "distance": "cosine",
        "items": items,
    }


def generate_synthetic_bundle(
    *,
    seed: int = SYNTHETIC_SEED,
    dim: int = SYNTHETIC_DIM,
) -> dict[str, Any]:
    """Capture all three query families from the exact-NN fake."""
    receipts = golden_receipts()
    index, line_keys, word_keys = build_synthetic_index(
        receipts, dim=dim, seed=seed
    )
    # Capture against JSON-round-tripped vectors so evaluate.py --backend
    # fake is 1.0 after dump_json (IEEE floats are not bit-stable at 8 d.p.
    # until they have been through json.dumps / json.loads).
    vectors = json.loads(json.dumps(vectors_payload(index), sort_keys=True))
    quantized = FakeVectorIndex.from_fixture_items(vectors["items"])
    captured = capture_from_client(
        quantized, receipts, line_keys=line_keys, word_keys=word_keys
    )
    return {
        "golden_set": {
            "schema_version": SCHEMA_VERSION,
            "seed": seed,
            "dim": dim,
            "n_receipts": len(receipts),
            "sources": {
                "line_items_golden": sum(
                    1 for r in receipts if r["source"] == "line_items_golden"
                ),
                "may26_batch": sum(
                    1 for r in receipts if r["source"] == "may26_batch"
                ),
            },
            "receipts": [
                {
                    "image_id": r["image_id"],
                    "receipt_id": r["receipt_id"],
                    "merchant": r["merchant"],
                    "source": r["source"],
                    "local_only": r["local_only"],
                }
                for r in receipts
            ],
        },
        "merchant_resolution": captured["merchant_resolution"],
        "word_neighbors": captured["word_neighbors"],
        "section_verifier": captured["section_verifier"],
        "vectors": vectors,
    }


def write_synthetic_fixtures(
    out_dir: Path, *, seed: int = SYNTHETIC_SEED
) -> None:
    write_fixture_bundle(out_dir, generate_synthetic_bundle(seed=seed))
