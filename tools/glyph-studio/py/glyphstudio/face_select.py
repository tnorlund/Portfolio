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
# At/above this cap ratio a row is the merchant WORDMARK, not text: Costco's
# OCR'd "COSTCO" logo line measures cap_rel 2.30-3.32 across the pilot
# corpus while its largest genuine text heading (SELF-CHECKOUT) is ~1.5 and
# WF's store header tops out at 2.0. The renderer draws the wordmark via the
# logo overlay; a measured entry would ALSO scale the OCR text row into a
# giant duplicate (hand-checked: 165b9d15/20576ddd renders grew a second
# clipped COSTCO above the pasted logo). Such rows get NO entry at all --
# they fall through to the production rules.
WORDMARK_SCALE = 2.1
# Fewer measured letters than this = unreliable stats -> next rung.
MIN_LETTERS = 4
# Cap height needs >= this many upper/digit letter samples to size a row
# (WF's "Tender:" has ONE cap sample, an OCR smear read as 1.34x body --
# hand-checked as plain body height).
MIN_CAP_SAMPLES = 3
# Sizing also requires CONSISTENT cap samples: relative p10-p90 spread <=
# this. Deciles, not quartiles: contamination is often bimodal-minority (a
# green marker stroke through 165b9d15's "ORG ATAULFO" row inflated part of
# the row to 44-85px and rendered a body item line at 1.6x; 5592edb9's
# masked-PAN line merged boxed-amount digits, 27px x4 vs 44px x16, which an
# IQR misses because both quartiles land on the majority mode). Genuine
# enlarged rows measure <= 0.02 (SELF-CHECKOUT all 63px, VOID all 79px,
# Items Sold 63-64px) while the hand-checked false positives sit at
# 0.39-0.52; a single OCR-clipped letter on a well-sampled row still passes
# (the deciles skip one outlier per end from n=10 up).
CAP_SPREAD_MAX = 0.12


def normalize_face_key(text: str) -> str:
    """Canonical row key: MUST match the renderer's ``normalize_face_key``.

    (receipt_agent...rendering.receipt_stylemap.normalize_face_key; the
    parity is pinned by a test. Duplicated so glyphstudio has no import-time
    dependency on receipt_agent.)
    """
    return " ".join(str(text).upper().split())[:60]


def _cap_heights(line: Mapping[str, Any]) -> list[float]:
    return [
        float(c.get("h") or 0)
        for c in line.get("letters") or ()
        if str(c.get("ch", ""))[:1].isupper() or str(c.get("ch", ""))[:1].isdigit()
    ]


def _cap_spread(heights: list[float]) -> float:
    """Relative p10-p90 spread of the cap-letter heights."""
    hs = sorted(heights)
    n = len(hs)
    if n < 2:
        return 0.0
    med = hs[n // 2]
    if med <= 0:
        return 0.0
    return (hs[(9 * n) // 10] - hs[n // 10]) / med


def is_wordmark_line(
    line: Mapping[str, Any],
    body_cap: Optional[float],
    body_box_h: Optional[float],
) -> bool:
    """True when the row's print is logo artwork, not sizable text."""
    cap = line.get("cap_px")
    if cap and body_cap:
        return float(cap) / float(body_cap) >= WORDMARK_SCALE
    box = line.get("box_h")
    if box and body_box_h:
        return float(box) / float(body_box_h) >= WORDMARK_SCALE
    return False


def measured_style_for_line(
    line: Mapping[str, Any],
    body_cap: Optional[float],
    body_stroke: Optional[float],
    *,
    next_reverse: bool = False,
) -> Optional[dict[str, Any]]:
    """The letters rung: per-letter cap/stroke measurements -> row style.

    Returns None when the line's letter statistics are too thin to trust
    (callers fall to the box rung / section prior / stylemap rules) or when
    the row is wordmark artwork (see :func:`is_wordmark_line`). Also the
    pilot's ground-truth extractor, so truth and selector cannot drift.

    ``next_reverse``: whether the FOLLOWING visual line is reverse-video --
    the underline probe's window extends below the row bottom, so a black
    band on the next row reads as an underline (hand-checked: 57cb7f2c's
    "INSTANT SAVINGS" sits directly above Costco's boxed date row; the real
    print has no rule under it).
    """
    cap = line.get("cap_px")
    if not cap or not body_cap or (line.get("n_letters") or 0) < MIN_LETTERS:
        return None
    cap_rel = float(cap) / float(body_cap)
    if cap_rel >= WORDMARK_SCALE:
        return None
    stroke = line.get("stroke_med")
    stroke_rel = float(stroke) / float(body_stroke) if stroke and body_stroke else None
    heavy = bool(
        stroke_rel is not None
        and stroke_rel >= BOLD_STROKE_REL
        and stroke_rel / max(cap_rel, 1e-6) >= BOLD_STROKE_TO_CAP
    )
    scale = 1.0
    caps = _cap_heights(line)
    if (
        cap_rel >= LARGE_CAP
        and len(caps) >= MIN_CAP_SAMPLES
        and _cap_spread(caps) <= CAP_SPREAD_MAX
    ):
        scale = round(min(cap_rel, SCALE_MAX), 2)
    underline = bool(line.get("underline"))
    reverse = bool(line.get("reverse_video"))
    # A dark scan band (shadow/fold) trips BOTH the underline and the
    # reverse-video probes on an otherwise body-weight line (hand-checked on
    # ccc09736's payment block). A printed underline never coexists with
    # reverse video, so treat the pair as one artifact.
    if underline and reverse and not heavy:
        underline = reverse = False
    # The next row's reverse-video band bleeds into this row's underline
    # probe window (see docstring).
    if underline and next_reverse and not reverse:
        underline = False
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
        "wordmark": 0,
    }
    out: dict[str, dict[str, Any] | None] = {}
    lines = list(measurement.get("lines") or ())
    for idx, line in enumerate(lines):
        key = normalize_face_key(line.get("text") or "")
        if not key:
            stats["skipped"] += 1
            continue
        if is_wordmark_line(line, body_cap, body_box_h):
            # Logo artwork: no rung may size it (the box rung would re-inflate
            # what the letters rung just refused); production rules own it.
            stats["wordmark"] += 1
            continue
        next_reverse = bool(
            idx + 1 < len(lines) and lines[idx + 1].get("reverse_video")
        )
        style = measured_style_for_line(
            line, body_cap, body_stroke, next_reverse=next_reverse
        )
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
