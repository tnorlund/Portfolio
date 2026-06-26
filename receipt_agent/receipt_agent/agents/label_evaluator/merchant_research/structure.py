"""Structural fingerprint + data-driven archetype clustering (M6).

Derives a per-receipt STRUCTURE FINGERPRINT (which regions are present and how
they are arranged: header, line-item grid vs single service line, totals block,
tip line, payment; a coarse row/column geometry signature; a label-set profile)
and clusters real receipts ACROSS merchants into a small set of data-driven
archetypes — ``line_item_retail``, ``service``, ``restaurant_tip`` — so synthesis
can work for ANY merchant, including service receipts with no line-item grid.

Organizing principle (CHARTER): this PRODUCES taxonomy DATA; the archetype an
artifact reports comes from a receipt's own STRUCTURE, never a hard-coded
merchant→archetype map. The fingerprint is a lightweight structural summary
expressed over the same label/geometry concepts the unified pattern builder uses,
at the coarser granularity archetype clustering needs (so this module stays
self-contained and free of heavy entity dependencies).

The classifier is deterministic and interpretable, and reports a confidence so a
new/ambiguous structural assignment can be parked by the approval gate (M7/M8)
rather than auto-trusted.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

# Archetype names (also the cluster keys).
LINE_ITEM_RETAIL = "line_item_retail"
SERVICE = "service"
RESTAURANT_TIP = "restaurant_tip"
UNKNOWN = "unknown"

ARCHETYPES = (LINE_ITEM_RETAIL, SERVICE, RESTAURANT_TIP, UNKNOWN)

# Operations each structure type can support. Service receipts have NO line-item
# grid, so line-item ops are excluded; orchestration consumes this (M7).
STRUCTURE_TYPE_OPERATIONS: dict[str, tuple[str, ...]] = {
    "line_item": (
        "add_line_item",
        "remove_line_item",
        "replace_field",
        "amount_mutation",
        "compose_header",
        "hard_negative",
    ),
    "service": (
        "replace_field",
        "amount_mutation",
        "compose_header",
        "hard_negative",
    ),
    "hybrid": (
        "replace_field",
        "amount_mutation",
        "compose_header",
        "hard_negative",
    ),
}

# Labels that mark each region.
_HEADER_LABELS = frozenset({"MERCHANT_NAME", "ADDRESS_LINE", "PHONE_NUMBER", "WEBSITE", "STORE_HOURS"})
_ITEM_LABELS = frozenset({"PRODUCT_NAME", "LINE_TOTAL", "UNIT_PRICE", "QUANTITY"})
_TOTALS_LABELS = frozenset({"SUBTOTAL", "TAX", "GRAND_TOTAL"})
_TIP_LABELS = frozenset({"TIP", "GRATUITY"})
_PAYMENT_LABELS = frozenset({"PAYMENT_METHOD", "TENDER", "CHANGE"})

# At least this many aligned price rows constitute a line-item GRID (vs a single
# service line). A receipt with 0-1 priced rows is a service-style receipt.
MIN_GRID_ITEMS = 2
# Max stdev of the price column's right edge (normalized x) for it to count as a
# real aligned column rather than scattered amounts.
PRICE_COLUMN_ALIGNED_STDEV = 0.06


@dataclass(frozen=True)
class StructureFingerprint:
    """The structural features of ONE receipt, archetype-relevant."""

    label_profile: dict[str, int]
    line_item_count: int
    has_price_column: bool
    has_totals_block: bool
    has_grand_total: bool
    has_tip_line: bool
    has_payment: bool
    region_sequence: tuple[str, ...]
    # Coarse geometry signature (normalized; None when not derivable).
    price_column_x: float | None
    row_spacing: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "label_profile": dict(sorted(self.label_profile.items())),
            "line_item_count": self.line_item_count,
            "has_price_column": self.has_price_column,
            "has_totals_block": self.has_totals_block,
            "has_grand_total": self.has_grand_total,
            "has_tip_line": self.has_tip_line,
            "has_payment": self.has_payment,
            "region_sequence": list(self.region_sequence),
            "price_column_x": self.price_column_x,
            "row_spacing": self.row_spacing,
        }


@dataclass(frozen=True)
class ArchetypeAssignment:
    """A receipt's archetype with a confidence and the reasons behind it."""

    archetype: str
    confidence: str  # "high" | "medium" | "low"
    reasons: tuple[str, ...]


def _word_key(rec: dict[str, Any]) -> tuple:
    return (
        rec.get("receipt_id"),
        rec.get("line_id"),
        rec.get("word_id"),
    )


def _right_edge_x(word: dict[str, Any]) -> float | None:
    bb = word.get("bounding_box") or {}
    x = bb.get("x")
    w = bb.get("width")
    if x is None or w is None:
        # fall back to the geometric corners
        tr = word.get("top_right") or {}
        return tr.get("x")
    return float(x) + float(w)


def _center_y(word: dict[str, Any]) -> float | None:
    bb = word.get("bounding_box") or {}
    y = bb.get("y")
    h = bb.get("height")
    if y is None or h is None:
        return None
    return float(y) + float(h) / 2.0


def fingerprint_from_labeled_words(
    words: Sequence[dict[str, Any]],
    labels: Sequence[dict[str, Any]],
) -> StructureFingerprint:
    """Build a :class:`StructureFingerprint` from export-shaped words + labels.

    ``words`` carry geometry (bounding_box / corners) and (receipt_id, line_id,
    word_id); ``labels`` carry the same key plus ``label``. Robust to missing
    geometry (geometry signatures become ``None``).
    """
    label_of: dict[tuple, str] = {}
    for lab in labels:
        key = _word_key(lab)
        name = str(lab.get("label") or "").strip().upper()
        if name:
            label_of[key] = name

    label_profile: dict[str, int] = {}
    # Right edge of the rightmost LINE_TOTAL token PER ROW, keyed by line — so a
    # single amount split into "$" + "20.00" tokens counts as ONE priced row, not
    # two (codex M6 review).
    line_total_right_by_row: dict[tuple, float | None] = {}
    product_line_ids: set = set()
    region_y: dict[str, list[float]] = {}

    for word in words:
        key = _word_key(word)
        label = label_of.get(key)
        if not label:
            continue
        label_profile[label] = label_profile.get(label, 0) + 1
        if label == "LINE_TOTAL":
            row = (word.get("receipt_id"), word.get("line_id"))
            edge = _right_edge_x(word)
            if edge is not None:
                prev = line_total_right_by_row.get(row)
                if prev is None or edge > prev:
                    line_total_right_by_row[row] = edge
            else:
                line_total_right_by_row.setdefault(row, None)  # row seen, no geom
        # Count an ITEM by its product-name row, not by every unit-price/quantity
        # word, so a verbose item does not inflate the count.
        if label == "PRODUCT_NAME":
            product_line_ids.add((word.get("receipt_id"), word.get("line_id")))
        cy = _center_y(word)
        if cy is None:
            continue
        if label in _HEADER_LABELS:
            region_y.setdefault("header", []).append(cy)
        elif label in _ITEM_LABELS:
            region_y.setdefault("items", []).append(cy)
        elif label in _TOTALS_LABELS:
            region_y.setdefault("totals", []).append(cy)
        elif label in _PAYMENT_LABELS:
            region_y.setdefault("payment", []).append(cy)

    # Item count = distinct priced ROWS (LINE_TOTAL lines) or product-name rows.
    line_total_rows = len(line_total_right_by_row)
    line_item_count = max(line_total_rows, len(product_line_ids))

    # Price column alignment from the per-row LINE_TOTAL right edges.
    right_edges = [x for x in line_total_right_by_row.values() if x is not None]
    price_column_x: float | None = None
    has_price_column = False
    if len(right_edges) >= MIN_GRID_ITEMS:
        price_column_x = statistics.median(right_edges)
        stdev = statistics.pstdev(right_edges) if len(right_edges) > 1 else 0.0
        has_price_column = stdev <= PRICE_COLUMN_ALIGNED_STDEV

    # Row spacing from item-region y centers.
    item_ys = sorted(region_y.get("items", []))
    row_spacing: float | None = None
    if len(item_ys) >= 2:
        gaps = [abs(b - a) for a, b in zip(item_ys, item_ys[1:]) if abs(b - a) > 1e-6]
        if gaps:
            row_spacing = statistics.median(gaps)

    # Region sequence top -> bottom. In this corpus y is high-at-top, so order by
    # descending median y.
    region_medians = {
        region: statistics.median(ys) for region, ys in region_y.items() if ys
    }
    region_sequence = tuple(
        region for region, _ in sorted(region_medians.items(), key=lambda kv: -kv[1])
    )

    return StructureFingerprint(
        label_profile=label_profile,
        line_item_count=line_item_count,
        has_price_column=has_price_column,
        has_totals_block=bool(_TOTALS_LABELS & label_profile.keys()),
        has_grand_total="GRAND_TOTAL" in label_profile,
        has_tip_line=bool(_TIP_LABELS & label_profile.keys()),
        has_payment=bool(_PAYMENT_LABELS & label_profile.keys()),
        region_sequence=region_sequence,
        price_column_x=price_column_x,
        row_spacing=row_spacing,
    )


def classify_archetype(fp: StructureFingerprint) -> ArchetypeAssignment:
    """Deterministically assign a receipt to a structural archetype.

    Driven only by the receipt's own STRUCTURE (never its merchant). Confidence
    reflects how cleanly the fingerprint matches, so a borderline assignment can
    be parked by the approval gate rather than auto-trusted.
    """
    reasons: list[str] = []
    # A line-item GRID is >=2 priced/product rows. Column alignment is a QUALITY
    # signal (raises confidence), not a gate — a clearly itemized receipt whose
    # OCR'd amounts are slightly ragged is still line-item.
    grid = fp.line_item_count >= MIN_GRID_ITEMS

    # restaurant_tip: an itemized receipt that also carries a tip/gratuity line.
    if fp.has_tip_line and fp.line_item_count >= MIN_GRID_ITEMS:
        conf = "high" if fp.has_price_column else "medium"
        reasons.append(f"tip line present with {fp.line_item_count} item rows")
        return ArchetypeAssignment(RESTAURANT_TIP, conf, tuple(reasons))

    # line_item_retail: a grid of >=2 items.
    if grid:
        conf = (
            "high"
            if fp.line_item_count >= 4 and fp.has_totals_block and fp.has_price_column
            else "medium"
        )
        reasons.append(
            f"line-item grid of {fp.line_item_count} items"
            + ("; aligned price column" if fp.has_price_column else "")
            + ("; totals block" if fp.has_totals_block else "")
        )
        return ArchetypeAssignment(LINE_ITEM_RETAIL, conf, tuple(reasons))

    # service: 0-1 priced rows but a real amount (grand total / totals block).
    if fp.line_item_count <= 1 and (fp.has_grand_total or fp.has_totals_block):
        conf = "high" if fp.line_item_count == 0 and fp.has_grand_total else "medium"
        reasons.append(
            f"single service amount ({fp.line_item_count} item row(s)) with a "
            f"{'grand total' if fp.has_grand_total else 'totals block'}, no item grid"
        )
        return ArchetypeAssignment(SERVICE, conf, tuple(reasons))

    reasons.append(
        f"ambiguous structure: {fp.line_item_count} item row(s), "
        f"price_column={fp.has_price_column}, totals={fp.has_totals_block}"
    )
    return ArchetypeAssignment(UNKNOWN, "low", tuple(reasons))


@dataclass(frozen=True)
class ArchetypeCluster:
    """A data-driven cluster (one per archetype) with its aggregate prior."""

    cluster_id: str
    archetype: str
    size: int
    member_indices: tuple[int, ...]
    structural_prior: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "archetype": self.archetype,
            "size": self.size,
            "structural_prior": self.structural_prior,
        }


def _aggregate_prior(fps: Sequence[StructureFingerprint]) -> dict[str, Any]:
    """Aggregate STRUCTURE-only prior across a cluster's receipts (never content).

    Layout, spacing, and label-arrangement statistics — NOT items/prices/text.
    """

    def _mean(vals: Iterable[float | None]) -> float | None:
        nums = [v for v in vals if v is not None]
        return round(statistics.fmean(nums), 6) if nums else None

    # Most common region sequence and a normalized union label arrangement.
    seqs: dict[tuple[str, ...], int] = {}
    label_union: dict[str, int] = {}
    for fp in fps:
        seqs[fp.region_sequence] = seqs.get(fp.region_sequence, 0) + 1
        for lab in fp.label_profile:
            label_union[lab] = label_union.get(lab, 0) + 1
    typical_region_sequence = (
        list(max(seqs.items(), key=lambda kv: kv[1])[0]) if seqs else []
    )
    n = len(fps)
    label_arrangement = {
        lab: round(count / n, 3) for lab, count in sorted(label_union.items())
    }
    return {
        "receipt_count": n,
        "typical_region_sequence": typical_region_sequence,
        "mean_line_item_count": _mean(fp.line_item_count for fp in fps),
        "mean_row_spacing": _mean(fp.row_spacing for fp in fps),
        "mean_price_column_x": _mean(fp.price_column_x for fp in fps),
        "label_arrangement": label_arrangement,
    }


# A merchant is a clean single structure type when its primary archetype holds
# at least this share of its receipts; otherwise it is hybrid.
PRIMARY_SHARE_FOR_CLEAN_TYPE = 0.6
PRIMARY_SHARE_FOR_HIGH_CONFIDENCE = 0.8
MIN_RECEIPTS_FOR_HIGH_CONFIDENCE = 3


@dataclass(frozen=True)
class MerchantStructure:
    """A merchant's structural summary, derived from its receipts' archetypes."""

    primary_archetype: str
    archetype_mix: dict[str, int]
    structure_type: str  # "line_item" | "service" | "hybrid"
    applicable_operations: tuple[str, ...]
    cluster_id: str
    cluster_size: int
    confidence: str
    provenance: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_archetype": self.primary_archetype,
            "archetype_mix": dict(sorted(self.archetype_mix.items())),
            "structure_type": self.structure_type,
            "applicable_operations": list(self.applicable_operations),
            "cluster_id": self.cluster_id,
            "cluster_size": self.cluster_size,
            "confidence": self.confidence,
            "provenance": list(self.provenance),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MerchantStructure":
        return cls(
            primary_archetype=str(d.get("primary_archetype") or UNKNOWN),
            archetype_mix={str(k): int(v) for k, v in (d.get("archetype_mix") or {}).items()},
            structure_type=str(d.get("structure_type") or "hybrid"),
            applicable_operations=tuple(d.get("applicable_operations") or ()),
            cluster_id=str(d.get("cluster_id") or ""),
            cluster_size=int(d.get("cluster_size") or 0),
            confidence=str(d.get("confidence") or "low"),
            provenance=tuple(str(p) for p in (d.get("provenance") or [])),
        )


def archetype_mix_hash(archetype_mix: dict[str, int]) -> str:
    """Stable content hash of a merchant's archetype mix (the structure evidence).

    A human structure sign-off is keyed by this hash, so any change to the mix
    (re-fingerprinting that shifts the archetype distribution) yields a new hash
    and the old approval no longer applies — the merchant reverts to
    ``needs_review`` automatically, mirroring the tax-block hash.
    """
    import hashlib
    import json

    normalized = {str(k): int(v) for k, v in (archetype_mix or {}).items() if int(v) > 0}
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def structure_review_status(confidence: str) -> str:
    """Approval-gate status for a structural assignment (mirrors the tax gate).

    Only a HIGH-confidence assignment auto-approves; a new/low/medium-confidence
    archetype is parked at ``needs_review`` so a structural prior we are unsure of
    is not auto-trusted (CHARTER: apply the approval gate to structure too).
    Recomputed by the loader — never read from a stored field.
    """
    return "auto_approved" if str(confidence).strip().lower() == "high" else "needs_review"


def _structure_type_for(primary: str) -> str:
    if primary in (LINE_ITEM_RETAIL, RESTAURANT_TIP):
        return "line_item"
    if primary == SERVICE:
        return "service"
    return "hybrid"


def summarize_merchant_structure(
    archetype_mix: dict[str, int],
    *,
    cluster_size: int | None = None,
) -> MerchantStructure:
    """Summarize a merchant's structure from the archetype mix of its receipts.

    The primary archetype is the most common one across the merchant's receipts;
    structure_type is ``service`` / ``line_item`` only when the primary holds a
    clear majority, else ``hybrid``. Confidence reflects how pure and deep the
    mix is — a thin or split merchant is low/medium so the approval gate can park
    a structural assignment we are unsure of.
    """
    mix = {k: int(v) for k, v in archetype_mix.items() if int(v) > 0}
    total = sum(mix.values())
    if total == 0:
        return MerchantStructure(
            primary_archetype=UNKNOWN,
            archetype_mix={},
            structure_type="hybrid",
            applicable_operations=STRUCTURE_TYPE_OPERATIONS["hybrid"],
            cluster_id=f"cluster:{UNKNOWN}",
            cluster_size=0,
            confidence="low",
            provenance=("no classified receipts",),
        )

    # Primary = highest count; ties broken by a stable archetype order so the
    # result is deterministic.
    primary = max(mix, key=lambda a: (mix[a], -ARCHETYPES.index(a) if a in ARCHETYPES else -99))
    share = mix[primary] / total
    structure_type = _structure_type_for(primary) if share >= PRIMARY_SHARE_FOR_CLEAN_TYPE else "hybrid"

    if share >= PRIMARY_SHARE_FOR_HIGH_CONFIDENCE and total >= MIN_RECEIPTS_FOR_HIGH_CONFIDENCE:
        confidence = "high"
    elif share >= PRIMARY_SHARE_FOR_CLEAN_TYPE:
        confidence = "medium"
    else:
        confidence = "low"

    provenance = (
        f"primary archetype {primary} ({mix[primary]}/{total} receipts, "
        f"share {share:.2f})",
        f"archetype mix {dict(sorted(mix.items()))}",
    )
    return MerchantStructure(
        primary_archetype=primary,
        archetype_mix=mix,
        structure_type=structure_type,
        applicable_operations=STRUCTURE_TYPE_OPERATIONS[structure_type],
        cluster_id=f"cluster:{primary}",
        cluster_size=cluster_size if cluster_size is not None else total,
        confidence=confidence,
        provenance=provenance,
    )


def service_grounding_contract(structure: dict[str, Any] | None) -> dict[str, Any]:
    """Contract a consumer (orchestration / source-quality gate) reads to decide
    whether a merchant's receipts are valid grounding for SERVICE synthesis.

    This is the hook the CHARTER (M7) calls for: it lets the source-quality
    contract recognize a SERVICE receipt as valid grounding so a receipt with no
    line items is NOT rejected for "missing line items" — it is valid for
    field/amount/header synthesis. It is pure DATA; the deterministic gate
    remains the arbiter. A consumer should:

      * treat ``no_labeled_line_items`` / ``no_line_items`` as an expected
        LIMITATION (not a hard blocker / "blocked" status) when
        ``valid_grounding_without_line_items`` is True, so readiness can be
        "partial" and the train-only loader accepts the example; and
      * request only ``applicable_operations`` (service excludes line-item ops).

    Returns a benign no-op contract (no override) when the structure is absent,
    not service-type, or not approved — so a parked/uncertain structural
    assignment never grants the service-grounding override.
    """
    if not isinstance(structure, dict):
        return {
            "is_service": False,
            "valid_grounding_without_line_items": False,
            "applicable_operations": [],
            "reason": "no/invalid structure",
        }
    raw_ops = structure.get("applicable_operations")
    ops = [str(o) for o in raw_ops] if isinstance(raw_ops, (list, tuple)) else []
    if structure.get("structure_type") != "service":
        return {
            "is_service": False,
            "valid_grounding_without_line_items": False,
            "applicable_operations": ops,
            "reason": "not a service-type merchant",
        }
    enabling = str(structure.get("status") or "") in ("auto_approved", "approved")
    return {
        "is_service": True,
        # Only an APPROVED service structure grants the override; a parked
        # (needs_review) service merchant is not auto-trusted.
        "valid_grounding_without_line_items": enabling,
        "applicable_operations": ops,
        "reason": (
            "approved service merchant: a single service line + total is valid "
            "grounding; line-item ops excluded"
            if enabling
            else "service merchant pending structure approval (parked)"
        ),
    }


def cluster_fingerprints(
    fingerprints: Sequence[StructureFingerprint],
) -> list[ArchetypeCluster]:
    """Cluster receipts (across merchants) by archetype into data-driven groups.

    Clusters are keyed by archetype; ``cluster_id`` is deterministic. Each cluster
    exposes an aggregate STRUCTURAL prior (used by M8 as a cross-merchant prior).
    """
    members: dict[str, list[int]] = {}
    for idx, fp in enumerate(fingerprints):
        archetype = classify_archetype(fp).archetype
        members.setdefault(archetype, []).append(idx)

    clusters: list[ArchetypeCluster] = []
    for archetype in sorted(members):
        idxs = members[archetype]
        clusters.append(
            ArchetypeCluster(
                cluster_id=f"cluster:{archetype}",
                archetype=archetype,
                size=len(idxs),
                member_indices=tuple(idxs),
                structural_prior=_aggregate_prior([fingerprints[i] for i in idxs]),
            )
        )
    return clusters
