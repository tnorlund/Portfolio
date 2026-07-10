"""Face-map v2: (merchant, section) -> face priors MEASURED over QA'd sections.

The M2 map (:mod:`glyphstudio.section_face_map`) reads each merchant's
``stylemap.json`` — faces measured once per merchant, sections attributed by
stylescan's per-merchant RULE tables. v2 replaces the section attribution with
the QA'd ``ReceiptSection`` ground truth (line-level, ``validation_status=
VALID``) and replaces the one-shot stylemap faces with per-line MEASUREMENTS
(:func:`glyphstudio.stylescan.measure`) aggregated across every vetted receipt
of the merchant:

1. **join** — stylescan's visual lines carry the OCR ``line_ids`` that formed
   them; a ``ReceiptSection`` row is a set of ``line_ids``. A visual line
   belongs to a section when its line_ids hit exactly ONE section type;
   straddlers (line_ids claimed by two different section types) are dropped as
   ambiguous rather than voting for either.
2. **body baseline** — cap/stroke medians over the receipt's QA'd ITEMS lines
   (>= :data:`MIN_BODY_LINES` measurable lines), falling back to stylescan's
   own rule-based body when the receipt has too few measurable items. Using
   the QA'd items block keeps the baseline honest for merchants stylescan has
   no rule table for.
3. **per-line face** — the LETTERS-rung rules from face_select (branch
   ``feat/m4-measured-faces``), constants duplicated here and kept in sync by
   comment: a line is BOLD only when its strokes are both absolutely thicker
   than body and disproportionate to its cap height (an enlarged header whose
   strokes scale with its caps is the same face enlarged, not bold); scale
   engages only above ``LARGE_CAP`` with enough cap samples (OCR jitter stays
   1.0); a joint underline+reverse-video hit on a non-bold line is one dark
   scan band, not print.
4. **aggregate** — M2's ``Face`` semantics per (merchant, section): scale =
   median, weight = mode with the LIGHTER weight winning ties, and
   underline_rate = the measured rate over lines (v1's max-over-subfaces was
   a stylemap fold artifact; here the rate is observed directly).

Cells with fewer than :data:`LOW_CONFIDENCE_LINES` measured lines are emitted
with ``low_confidence: true`` — present for coverage accounting, but not a
prior anyone should trust (the locked M3 lesson: distributions, not n=1).

Everything in this module is pure (no Dynamo, no S3); the CLI adapter
(``face_map_v2_cli.py``) feeds it.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from statistics import median
from typing import Any, Iterable, Mapping, Optional

from glyphstudio.section_face_map import _WEIGHT_ORDER, Face
from glyphstudio.sections import CANONICAL_SECTION_SET

# --- per-line face rules -----------------------------------------------------
# Constants mirror glyphstudio.face_select (branch feat/m4-measured-faces);
# see that module's docstring for the hand-checked receipts behind each. If
# face_select lands on main, import from it instead of redefining.
BOLD_STROKE_REL = 1.30
BOLD_STROKE_TO_CAP = 1.30
LARGE_CAP = 1.30
SCALE_MAX = 2.5
MIN_LETTERS = 4
MIN_CAP_SAMPLES = 3

# --- aggregation thresholds --------------------------------------------------
MIN_BODY_LINES = 3  # measurable ITEMS lines needed to trust the QA'd baseline
LOW_CONFIDENCE_LINES = 3  # cells with fewer measured lines are low-confidence


@dataclass(frozen=True)
class LineFace:
    """One measured line's face vote plus its receipt provenance."""

    scale: float
    weight: str  # "normal" | "bold"
    underline: bool
    reverse_video: bool
    receipt: tuple[str, int]  # (image_id, receipt_id)


def assign_sections(
    lines: Iterable[Mapping[str, Any]],
    sections: Iterable[Mapping[str, Any]],
) -> tuple[list[tuple[Mapping[str, Any], Optional[str]]], int]:
    """Join stylescan visual lines to QA'd sections by OCR line id.

    ``sections`` are ``{"section_type": "TOTAL_LINE", "line_ids": [...]}``
    mappings (already filtered to VALID). Returns ``([(line, canonical_section
    | None), ...], n_ambiguous)`` — a line whose line_ids are claimed by MORE
    than one section type is dropped from the pairs entirely and counted
    (straddler; letting it vote for either section corrupts both cells).
    """
    claim: dict[int, set[str]] = {}
    for sec in sections:
        canon = str(sec["section_type"]).lower()
        if canon not in CANONICAL_SECTION_SET:
            continue
        for lid in sec["line_ids"]:
            claim.setdefault(int(lid), set()).add(canon)

    out: list[tuple[Mapping[str, Any], Optional[str]]] = []
    ambiguous = 0
    for line in lines:
        hit: set[str] = set()
        for lid in line.get("line_ids") or ():
            hit |= claim.get(int(lid), set())
        if len(hit) > 1:
            ambiguous += 1
            continue
        out.append((line, next(iter(hit)) if hit else None))
    return out, ambiguous


def _measurable(line: Mapping[str, Any]) -> bool:
    return bool(line.get("cap_px")) and (line.get("n_letters") or 0) >= MIN_LETTERS


def items_body_baseline(
    assigned: Iterable[tuple[Mapping[str, Any], Optional[str]]],
    fallback_cap: Optional[float],
    fallback_stroke: Optional[float],
) -> tuple[Optional[float], Optional[float], str]:
    """Receipt body (cap, stroke) from the QA'd ITEMS lines.

    Falls back to stylescan's rule-based body when fewer than
    ``MIN_BODY_LINES`` items lines are measurable (returns which source was
    used as the third element: ``"items"`` / ``"stylescan"``).
    """
    caps = [
        float(l["cap_px"])
        for l, sec in assigned
        if sec == "items" and _measurable(l)
    ]
    strokes = [
        float(l["stroke_med"])
        for l, sec in assigned
        if sec == "items" and _measurable(l) and l.get("stroke_med")
    ]
    if len(caps) >= MIN_BODY_LINES:
        return (
            median(caps),
            median(strokes) if strokes else fallback_stroke,
            "items",
        )
    return fallback_cap, fallback_stroke, "stylescan"


def _cap_samples(line: Mapping[str, Any]) -> int:
    return sum(
        1
        for c in line.get("letters") or ()
        if str(c.get("ch", ""))[:1].isupper() or str(c.get("ch", ""))[:1].isdigit()
    )


def line_face(
    line: Mapping[str, Any],
    body_cap: Optional[float],
    body_stroke: Optional[float],
    receipt: tuple[str, int],
) -> Optional[LineFace]:
    """The LETTERS-rung face for one measured line, or None if too thin.

    Same decision surface as face_select.measured_style_for_line (see module
    docstring): thin letter statistics yield None rather than a noisy vote.
    """
    if not _measurable(line) or not body_cap:
        return None
    cap_rel = float(line["cap_px"]) / float(body_cap)
    stroke = line.get("stroke_med")
    stroke_rel = (
        float(stroke) / float(body_stroke) if stroke and body_stroke else None
    )
    bold = bool(
        stroke_rel is not None
        and stroke_rel >= BOLD_STROKE_REL
        and stroke_rel / max(cap_rel, 1e-6) >= BOLD_STROKE_TO_CAP
    )
    scale = 1.0
    if cap_rel >= LARGE_CAP and _cap_samples(line) >= MIN_CAP_SAMPLES:
        scale = round(min(cap_rel, SCALE_MAX), 2)
    underline = bool(line.get("underline"))
    reverse = bool(line.get("reverse_video"))
    if underline and reverse and not bold:
        # One dark scan band trips both probes on a body-weight line
        # (hand-checked, see face_select); a printed underline never
        # coexists with reverse video.
        underline = reverse = False
    return LineFace(
        scale=scale,
        weight="bold" if bold else "normal",
        underline=underline,
        reverse_video=reverse,
        receipt=receipt,
    )


def aggregate_cell(members: list[LineFace]) -> dict[str, Any]:
    """M2 Face aggregation over one (merchant, section) cell's line votes.

    scale = median; weight = mode with the LIGHTER weight winning ties (a
    rare emphasis line must not bold the whole section); underline_rate =
    observed rate. Adds sample accounting: ``n_lines``, ``n_receipts``,
    ``low_confidence`` (n_lines < LOW_CONFIDENCE_LINES) and the audit-only
    ``reverse_video_rate``.
    """
    if not members:
        raise ValueError("aggregate_cell needs at least one member line")
    counts = Counter(m.weight for m in members)
    top = max(counts.values())
    weight = min(
        (w for w, c in counts.items() if c == top),
        key=lambda w: _WEIGHT_ORDER.get(w, 0),
    )
    face = Face(
        scale=round(median(m.scale for m in members), 3),
        weight=weight,
        underline_rate=round(
            sum(1 for m in members if m.underline) / len(members), 4
        ),
    )
    return {
        "face": face,
        "n_lines": len(members),
        "n_receipts": len({m.receipt for m in members}),
        "low_confidence": len(members) < LOW_CONFIDENCE_LINES,
        "reverse_video_rate": round(
            sum(1 for m in members if m.reverse_video) / len(members), 4
        ),
    }


def aggregate_map(
    cells: Mapping[tuple[str, str], list[LineFace]],
) -> list[dict[str, Any]]:
    """All cells -> sorted v2 map entries (merchant, section, face, counts)."""
    out = []
    for (merchant, section), members in sorted(cells.items()):
        if not members:
            continue
        agg = aggregate_cell(members)
        out.append({"merchant": merchant, "section": section, **agg})
    return out


def merchant_section_priors(
    map_obj: Mapping[str, Any],
    merchant: str,
    *,
    include_low_confidence: bool = False,
) -> dict[str, dict[str, Any]]:
    """v2 map JSON -> {canonical_section: face dict} for one merchant.

    The face dicts are Mapping-shaped priors accepted by face_select's
    ``_style_from_prior`` (keys ``scale`` / ``weight`` / ``underline_rate``),
    i.e. a drop-in replacement for M2's ``load_merchant_faces``. Low-
    confidence cells are excluded by default — a prior measured from fewer
    than LOW_CONFIDENCE_LINES lines misleads more than it helps.
    """
    out: dict[str, dict[str, Any]] = {}
    for entry in map_obj.get("map") or ():
        if entry.get("merchant") != merchant:
            continue
        if entry.get("low_confidence") and not include_low_confidence:
            continue
        out[str(entry["section"])] = dict(entry["face"])
    return out
