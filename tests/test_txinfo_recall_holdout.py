from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from txinfo_recall_holdout import (  # noqa: E402
    canonical_manifest_hash,
    manifest_keys,
    rank,
    select_stratum,
    spent_keys,
)


def test_spent_keys_includes_mapped_and_pinned_receipts() -> None:
    payload = {
        "mapping_opaque_to_source": {
            "a": {"image_id": "mapped", "receipt_id": 1}
        },
        "excluded_pinned_goldens": [
            {"image_id": "pinned", "receipt_id": 2}
        ],
    }
    assert spent_keys(payload) == {("mapped", 1), ("pinned", 2)}


def test_select_stratum_prefers_unique_merchants() -> None:
    candidates = [
        {"rank": "1", "image_id": "a", "receipt_id": 1, "merchant_key": "same"},
        {"rank": "2", "image_id": "b", "receipt_id": 1, "merchant_key": "same"},
        {"rank": "3", "image_id": "c", "receipt_id": 1, "merchant_key": "other"},
    ]
    selected = select_stratum(candidates, 2)
    assert [item["image_id"] for item in selected] == ["a", "c"]


def test_manifest_hash_ignores_metadata_fields() -> None:
    first = [{"image_id": "a", "receipt_id": 1, "merchant": "one"}]
    second = [{"image_id": "a", "receipt_id": 1, "merchant": "two"}]
    assert canonical_manifest_hash(first) == canonical_manifest_hash(second)


def test_manifest_keys_and_salted_rank_support_spent_holdouts() -> None:
    payload = [{"image_id": "a", "receipt_id": 1, "merchant": "one"}]

    assert manifest_keys(payload) == {("a", 1)}
    assert rank(("a", 1), "first") != rank(("a", 1), "second")
