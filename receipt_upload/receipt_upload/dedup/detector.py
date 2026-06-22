"""Tier 0 exact-duplicate detection (raw-pixel ``sha256``).

Pure functions over receipt records (no I/O), so they're testable and reusable
in a batch Lambda or a one-off script.

Group receipts by raw-pixel ``sha256``: byte-identical pixels cannot coincide
across distinct receipts, so these are safe to AUTO-MERGE (100% precision; the
hash is precomputed on the Receipt entity). A "keeper" is chosen per group as
the highest-resolution receipt (width*height), tie-broken deterministically;
the others are the duplicates.

Cross-image NEAR-duplicates (different pixels, same transaction) are handled
separately by :mod:`receipt_upload.dedup.near_dup` + ``stage5_plan``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

Key = Tuple[str, int]  # (image_id, receipt_id)


@dataclass
class DupGroup:
    method: str  # "exact"
    signature: str
    keeper: Key  # the receipt to keep
    duplicates: List[Key]  # receipts that are duplicates of the keeper
    action: str  # "auto-merge"
    detail: Dict = field(default_factory=dict)


def _resolution(r) -> int:
    return (getattr(r, "width", 0) or 0) * (getattr(r, "height", 0) or 0)


def _key(r) -> Key:
    return (r.image_id, r.receipt_id)


def _pick_keeper(records: List) -> object:
    # Highest resolution wins; deterministic tie-break by (image_id,
    # receipt_id).
    return max(
        records, key=lambda r: (_resolution(r), r.image_id, r.receipt_id)
    )


def find_exact_duplicates(receipts: List) -> List[DupGroup]:
    """Group receipts by identical raw-pixel ``sha256`` + dims.

    The stored sha hashes ``image.tobytes()`` only, so identical bytes across
    *different* dims (e.g. blank/uniform failed crops of equal area) would
    otherwise be auto-merged as duplicates. Keying on ``(sha, width, height)``
    keeps that safe; true duplicates share dimensions anyway.
    """
    by_key: Dict[Tuple, List] = {}
    for r in receipts:
        sha = getattr(r, "sha256", None)
        if sha:
            by_key.setdefault((sha, r.width, r.height), []).append(r)

    groups: List[DupGroup] = []
    for (sha, _w, _h), records in by_key.items():
        keys = {_key(r) for r in records}
        if len(keys) < 2:
            continue
        keeper = _pick_keeper(records)
        dups = sorted(k for k in keys if k != _key(keeper))
        groups.append(
            DupGroup(
                method="exact",
                signature=f"sha256:{sha[:12]}",
                keeper=_key(keeper),
                duplicates=dups,
                action="auto-merge",
                detail={"sha256": sha, "size": len(keys)},
            )
        )
    return groups
