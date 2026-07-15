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


__all__ = ["row_section_from_map", "sections_to_line_map"]
