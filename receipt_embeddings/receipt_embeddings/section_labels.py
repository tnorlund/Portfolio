"""Shared section-label logic for embedding metadata and section search.

ReceiptSection rows classify a receipt's lines into sections; line/row
embedding metadata must reflect those sections consistently everywhere it
is stamped or recomputed, so all callers import these helpers rather than
forking the logic.

Promoted verbatim from ``receipt_chroma.section_labels`` (Chroma teardown).
This module is a dependency leaf (stdlib only).
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
#: excludes these rather than requiring ``section_label == "ITEMS"``:
#: measured on 34k line rows, 30% carry NO section label at all and those
#: unlabeled rows are overwhelmingly real products, so an ``in ITEMS``
#: filter silently drops them while an exclusion filter retains them.
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


__all__ = [
    "NON_ITEM_SECTION_LABELS",
    "row_section_from_map",
    "sections_to_line_map",
]
