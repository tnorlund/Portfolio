"""M4 pilot: measured-typography face selection.

Replaces text-rule face guessing with MEASUREMENT: for a receipt being
mimicked, derive each row's face (regular/heavy), scale tier, and underline
from :mod:`glyphstudio.stylescan` measurements of the REAL receipt (primary
signal), with weaker rungs below it. The full ladder, strongest first:

1. **letters** -- per-letter cap/stroke statistics (stylescan);
2. **box** -- the OCR line-box height, when letter stats are missing;
3. **section prior** -- the M2 (merchant, section) -> face map
   (:mod:`glyphstudio.section_face_map`), when the line has no usable
   geometry at all;
4. **stylemap rules** -- rows absent from the output map fall through to the
   renderer's existing text rules (the production default).

Output shape (consumed by the renderer's opt-in ``face_source="measured"``
path via ``RenderConfig.row_faces``)::

    {normalize_face_key(row_text): {
        "face": "regular" | "heavy",
        "scale": float,          # row cap multiplier vs body (1.0 = body)
        "underline": bool,
        "reverse_video": bool,   # carried for audit; renderer ignores today
        "source": "measured" | "measured_box" | "prior",
    }}

Face rule: a row is HEAVY when its strokes are both absolutely thicker than
body AND disproportionate to its cap height. A large row whose strokes scale
WITH its caps (Wild Fork's "Thousand Oaks CA" header: cap_rel ~1.7,
stroke_rel ~1.6) is the SAME face enlarged, not a bold face -- using
stylescan's raw ``tier`` alone would misread every enlarged header as
needing the heavy atlas.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

# A row is heavy when its strokes are BOTH absolutely thicker than body
# (>= 1.30x) AND disproportionate to its cap height (>= 1.30x the cap
# ratio). Each alone false-positives: proportional growth = same face
# enlarged (WF header), and small-cap rows inflate stroke/cap (WF's phone
# line, stroke_rel 1.17 at cap_rel 0.88 -- hand-checked as plain body).
BOLD_STROKE_REL = 1.30
BOLD_STROKE_TO_CAP = 1.30
# Per-row scale is applied only above this cap ratio; body-scale jitter
# (OCR boxes wobble ~+/-10%) stays at 1.0 so the receipt keeps one body size.
LARGE_CAP = 1.30
# Sanity clamp for measured scales (the WF logo measures ~4.2x but is drawn
# by the logo overlay, not text).
SCALE_MAX = 2.5
# Fewer measured letters than this = unreliable stats -> next rung.
MIN_LETTERS = 4
# Cap height needs >= this many upper/digit letter samples to size a row
# (WF's "Tender:" has ONE cap sample, an OCR smear read as 1.34x body --
# hand-checked as plain body height).
MIN_CAP_SAMPLES = 3
# A single printed row has ONE cap height, so its cap samples must agree:
# when p75/p25 of the samples reaches this ratio, the letter set mixes
# physical rows (OCR leakage from a neighboring line) and the median sizes
# a row that does not exist. In-N-Out 9afeb902#2's small footer
# "Questions/Comments: ... Call" measured cap 65px = 1.51x body because
# leaked 83-84px digits sat beside its true 46-47px caps -- hand-checked
# as plain small print. Sizing is suppressed on such rows; face/underline
# keep their own guards. (Clean enlarged headers measure tight: WF's
# "Thousand Oaks CA" disperses < 1.1; body-row OCR noise reaches ~1.3.)
CAP_DISPERSION_MAX = 1.4


def normalize_face_key(text: str) -> str:
    """Canonical row key: MUST match the renderer's ``normalize_face_key``.

    (receipt_agent...rendering.receipt_stylemap.normalize_face_key; the
    parity is pinned by a test. Duplicated so glyphstudio has no import-time
    dependency on receipt_agent.)
    """
    return " ".join(str(text).upper().split())[:60]


def _cap_heights(line: Mapping[str, Any]) -> list[float]:
    return sorted(
        float(c["h"])
        for c in line.get("letters") or ()
        if (str(c.get("ch", ""))[:1].isupper() or str(c.get("ch", ""))[:1].isdigit())
        and c.get("h")
    )


def _caps_consistent(line: Mapping[str, Any]) -> bool:
    """True when the row's cap samples describe ONE letter size.

    Enough samples (>= MIN_CAP_SAMPLES) and unimodal heights
    (p75/p25 < CAP_DISPERSION_MAX) -- see the constants' rationale.
    """
    heights = _cap_heights(line)
    n = len(heights)
    if n < MIN_CAP_SAMPLES:
        return False
    p25 = heights[n // 4]
    p75 = heights[(3 * n) // 4]
    return p75 / max(p25, 1e-6) < CAP_DISPERSION_MAX


def measured_style_for_line(
    line: Mapping[str, Any],
    body_cap: Optional[float],
    body_stroke: Optional[float],
) -> Optional[dict[str, Any]]:
    """The letters rung: per-letter cap/stroke measurements -> row style.

    Returns None when the line's letter statistics are too thin to trust
    (callers fall to the box rung / section prior / stylemap rules). Also
    the pilot's ground-truth extractor, so truth and selector cannot drift.
    """
    cap = line.get("cap_px")
    if not cap or not body_cap or (line.get("n_letters") or 0) < MIN_LETTERS:
        return None
    cap_rel = float(cap) / float(body_cap)
    stroke = line.get("stroke_med")
    stroke_rel = float(stroke) / float(body_stroke) if stroke and body_stroke else None
    heavy = bool(
        stroke_rel is not None
        and stroke_rel >= BOLD_STROKE_REL
        and stroke_rel / max(cap_rel, 1e-6) >= BOLD_STROKE_TO_CAP
    )
    scale = 1.0
    if cap_rel >= LARGE_CAP and _caps_consistent(line):
        scale = round(min(cap_rel, SCALE_MAX), 2)
    underline = bool(line.get("underline"))
    reverse = bool(line.get("reverse_video"))
    # A dark scan band (shadow/fold) trips BOTH the underline and the
    # reverse-video probes on an otherwise body-weight line (hand-checked on
    # ccc09736's payment block). A printed underline never coexists with
    # reverse video, so treat the pair as one artifact.
    if underline and reverse and not heavy:
        underline = reverse = False
    return {
        "face": "heavy" if heavy else "regular",
        "scale": scale,
        "underline": underline,
        "reverse_video": reverse,
        "source": "measured",
    }


def _style_from_box(
    line: Mapping[str, Any],
    body_box_h: Optional[float],
) -> Optional[dict[str, Any]]:
    """The box rung: OCR line-box height when letter stats are missing.

    Weaker than the letters rung (boxes include descenders and OCR padding)
    but still a MEASUREMENT of this receipt -- it beats the section prior,
    which inherits text-rule misclassification (stylescan's WF store_header
    regex matches the footer URL; the prior then blew the URL row up 1.7x
    while the real print is body-size).
    """
    box = line.get("box_h")
    if not box or not body_box_h:
        return None
    rel = float(box) / float(body_box_h)
    scale = round(min(rel, SCALE_MAX), 2) if rel >= LARGE_CAP else 1.0
    return {
        "face": "regular",
        "scale": scale,
        "underline": False,
        "reverse_video": bool(line.get("reverse_video")),
        "source": "measured_box",
    }


def _style_from_prior(prior: Any) -> dict[str, Any]:
    """Map an M2 ``section_face_map.Face`` (or dict alike) to a row style."""
    get = (
        prior.get
        if isinstance(prior, Mapping)
        else lambda k, d=None: getattr(prior, k, d)
    )
    weight = str(get("weight", "normal") or "normal").lower()
    return {
        "face": "heavy" if weight == "bold" else "regular",
        "scale": float(get("scale", 1.0) or 1.0),
        "underline": float(get("underline_rate", 0.0) or 0.0) >= 0.5,
        "reverse_video": False,
        "source": "prior",
    }


def _same_style(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    return (
        a["face"] == b["face"]
        and abs(a["scale"] - b["scale"]) < 0.11
        and a["underline"] == b["underline"]
    )


def select_row_faces(
    measurement: Mapping[str, Any],
    *,
    section_priors: Mapping[str, Any] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    """stylescan ``measure()`` output -> (row_faces map, ladder stats).

    ``section_priors`` maps canonical section names to M2 ``Face`` priors
    (e.g. ``section_face_map.load_merchant_faces(font_dir)``). Lines whose
    normalized text collides with a DIFFERENT style are dropped entirely
    (both fall back to the stylemap rules) -- a text key must be unambiguous
    to be trusted.

    Stats are KEY-level over the returned map (``measured``/``measured_box``
    /``prior`` count retained keys; ``conflicts`` counts dropped keys), plus
    line-level ``skipped`` for lines no rung could style.
    """
    body_cap = measurement.get("body_cap_px")
    body_stroke = measurement.get("body_stroke_px")
    body_box_h = measurement.get("body_box_h")
    stats = {
        "measured": 0,
        "measured_box": 0,
        "prior": 0,
        "skipped": 0,
        "conflicts": 0,
    }
    out: dict[str, dict[str, Any] | None] = {}
    for line in measurement.get("lines") or ():
        key = normalize_face_key(line.get("text") or "")
        if not key:
            stats["skipped"] += 1
            continue
        style = measured_style_for_line(line, body_cap, body_stroke)
        if style is None:
            style = _style_from_box(line, body_box_h)
        if style is None and section_priors:
            prior = section_priors.get(line.get("section_canonical"))
            if prior is not None:
                style = _style_from_prior(prior)
        if style is None:
            stats["skipped"] += 1
            continue
        if key in out:
            prev = out[key]
            if prev is not None and not _same_style(prev, style):
                out[key] = None  # ambiguous key -> stylemap fallback
        else:
            out[key] = style
    kept = {k: v for k, v in out.items() if v is not None}
    for style in kept.values():
        stats[style["source"]] += 1
    stats["conflicts"] = len(out) - len(kept)
    return kept, stats
