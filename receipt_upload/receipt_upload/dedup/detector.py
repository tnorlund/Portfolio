"""Tier 0 + Tier 1 receipt duplicate detection.

Pure functions over receipt records + lookups (no I/O), so they're testable and
reusable in a batch Lambda or a one-off script.

- **Tier 0 — exact:** group receipts by their raw-pixel ``sha256``. Byte-identical
  pixels cannot coincide across distinct receipts, so these are safe to
  AUTO-MERGE (100% precision; the hash is precomputed on the Receipt entity).
- **Tier 1 — content signature:** group by ``(canonical_place_id, grand_total,
  item_count)``. This catches re-cropped / re-scanned copies that have a
  different ``sha256`` but the same transaction. It can false-positive (two
  real visits to the same store with the same total + item count), so it only
  generates candidates to FLAG FOR REVIEW — never auto-merge. (Date would
  tighten this further but isn't in the bulk summary; add it as a Tier-1.5
  refinement.)

A "keeper" is chosen per group as the highest-resolution receipt (width*height),
tie-broken deterministically; the others are the duplicates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from typing import Dict, List, Optional, Tuple

Key = Tuple[str, int]  # (image_id, receipt_id)


@dataclass
class DupGroup:
    method: str  # "exact" | "signature"
    signature: str
    keeper: Key  # the receipt to keep
    duplicates: List[Key]  # receipts that are duplicates of the keeper
    action: str  # "auto-merge" | "review"
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
    """Tier 0: group receipts with identical raw-pixel ``sha256`` AND dimensions.

    The stored sha hashes ``image.tobytes()`` only, so identical bytes across
    *different* dimensions (e.g. blank/uniform failed crops of equal area) would
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


def normalize_merchant_key(
    place_id: Optional[str], merchant_name: Optional[str]
) -> Optional[str]:
    """Stable merchant identity: prefer the Google place_id, else normalized name."""
    if place_id:
        return f"pid:{place_id}"
    if merchant_name and merchant_name.strip():
        return "name:" + "".join(merchant_name.lower().split())
    return None


def find_signature_candidates(
    receipts: List,
    signature_lookup: Dict[Key, Optional[str]],
    exclude_pairs: Optional[set] = None,
) -> List[DupGroup]:
    """Tier 1: group receipts that share a content ``signature``.

    ``signature_lookup`` maps each receipt key to a precomputed signature
    string
    (e.g. ``"pid:...|42.54|2024-03-13|7"``) or ``None`` when the receipt lacks the
    fields to form one. ``exclude_pairs`` is a set of ``frozenset({keyA,
    keyB})`` already merged by Tier 0 — groups whose every pair is already
    exact are skipped so this only surfaces NEW candidates.
    """
    exclude_pairs = exclude_pairs or set()
    by_sig: Dict[str, List] = {}
    for r in receipts:
        sig = signature_lookup.get(_key(r))
        if not sig:
            continue
        by_sig.setdefault(sig, []).append(r)

    groups: List[DupGroup] = []
    for sig, records in by_sig.items():
        keys = sorted({_key(r) for r in records})
        if len(keys) < 2:
            continue
        # Skip if every pair is already an exact (Tier-0) duplicate.
        if all(
            frozenset((a, b)) in exclude_pairs
            for a, b in combinations(keys, 2)
        ):
            continue
        keeper = _pick_keeper(records)
        dups = [k for k in keys if k != _key(keeper)]
        groups.append(
            DupGroup(
                method="signature",
                signature=sig,
                keeper=_key(keeper),
                duplicates=dups,
                action="review",
                detail={"size": len(keys)},
            )
        )
    return groups


def detect_duplicates(
    receipts: List,
    signature_lookup: Dict[Key, Optional[str]],
) -> Dict:
    """Run Tier 0 + Tier 1 and return a structured report."""
    exact = find_exact_duplicates(receipts)
    exclude_pairs = set()
    # exclude every within-exact-group pair so Tier 1 only surfaces NEW
    # candidates
    for g in exact:
        members = [g.keeper] + g.duplicates
        for a, b in combinations(members, 2):
            exclude_pairs.add(frozenset((a, b)))

    signature = find_signature_candidates(
        receipts, signature_lookup, exclude_pairs
    )

    exact_dups = sum(len(g.duplicates) for g in exact)
    sig_dups = sum(len(g.duplicates) for g in signature)
    return {
        "total_receipts": len(receipts),
        "exact_groups": exact,
        "signature_candidate_groups": signature,
        "summary": {
            "exact_groups": len(exact),
            "exact_redundant_receipts": exact_dups,
            "signature_candidate_groups": len(signature),
            "signature_candidate_receipts": sig_dups,
        },
    }
