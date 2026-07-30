"""Shared section-label logic for embedding and compaction paths.

ReceiptSection rows classify a receipt's lines into sections; the LINES
collection stores one embedding per visual row whose ``section_label``
metadata must reflect those sections. Embed-time stamping (records /
line_delta) and stream-driven recompute (compaction.sections) MUST agree
exactly, so both import these helpers rather than forking the logic.

This module is a dependency leaf (stdlib only) so it is importable from
``receipt_chroma.compaction`` during package init without triggering the
embedding package's import chain (which cycles back to the top-level
``receipt_chroma`` namespace).
"""

from collections import Counter
from typing import Any, Dict, Iterable, Optional, Sequence


def sections_to_line_map(sections: Sequence[Any]) -> Dict[int, str]:
    """Build a ``line_id -> section_type`` map from ReceiptSection rows.

    Each section holds its ``line_ids``; sections partition a receipt's
    lines, but if a line appears in more than one (e.g. overlapping seed
    generations) human-VALID evidence wins before confidence is considered.
    """
    out: Dict[int, str] = {}
    best: Dict[int, tuple[int, float]] = {}
    for s in sections or []:
        # skip QA-rejected rows — an INVALID section must not stamp a line
        status = str(getattr(s, "validation_status", "") or "").upper()
        if status == "INVALID":
            continue
        conf = getattr(s, "confidence", None) or 0.0
        rank = ({"VALID": 2, "PENDING": 1}.get(status, 0), float(conf))
        for line_id in getattr(s, "line_ids", []) or []:
            if line_id not in out or rank > best.get(line_id, (-1, -1.0)):
                out[line_id] = s.section_type
                best[line_id] = rank
    return out


def row_section_from_map(
    line_ids: Iterable[int],
    section_by_line: Optional[Dict[int, str]],
) -> Optional[str]:
    """Plurality section for a visual row's lines.

    A row can span multiple ReceiptLines while sections are line-level, so
    the row's ``section_label`` is the majority section among its lines.
    Ties or no mapped lines are ambiguous and return ``None`` (callers must
    then leave the metadata key unset).
    """
    if not section_by_line:
        return None
    votes = Counter(
        section_by_line[lid] for lid in line_ids if lid in section_by_line
    )
    top = votes.most_common(2)
    if top and (len(top) == 1 or top[0][1] > top[1][1]):
        return top[0][0]
    return None


#: Sections that never contain a purchased product line. Product search
#: excludes these rather than requiring ``section_label == "ITEMS"``.
#:
#: Measured on the dev LINES collection (34k rows): 30% of rows carry NO
#: ``section_label`` at all, and of the 516 hand-verified product lines in
#: the golden set only 90.9% are labeled ITEMS -- 7% sit in no section at
#: all. Those unlabeled rows are overwhelmingly real products
#: ("RAW WHOLE MILK 10.99 F", "ORGANIC POPCORN 6.49"), so an ``$in ITEMS``
#: filter silently drops them. Chroma metadata filters do not match rows
#: whose key is absent, but ``$nin`` DOES retain them -- verified against
#: Chroma Cloud -- which is why the exclusion form is used here.
NON_ITEM_SECTION_LABELS = (
    "ADDRESS",
    "BARCODE",
    "FOOTER",
    "PAYMENT",
    "SECTION_HEADER",
    "STOREFRONT",
    "SUMMARY",
    "SURVEY",
    "TOTAL_LINE",
    "TRANSACTION_INFO",
)


def non_item_section_filter() -> Dict[str, Any]:
    """Chroma ``where`` clause excluding sections that hold no products.

    Use for product/line-item search over the LINES collection. Rows with no
    ``section_label`` are deliberately KEPT: on under-sectioned receipts the
    product lines are exactly the unlabeled ones.
    """
    return {"section_label": {"$nin": list(NON_ITEM_SECTION_LABELS)}}


__all__ = [
    "NON_ITEM_SECTION_LABELS",
    "non_item_section_filter",
    "row_section_from_map",
    "sections_to_line_map",
]
