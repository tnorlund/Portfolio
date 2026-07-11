"""Absolute per-glyph quality floor -- NO fleet consensus required.

``glyph_gate`` answers a *relative* question ("does this glyph look more like a
different fleet letter than its own?") by IoU-argmax against a fleet consensus.
That is powerful but has two structural blind spots the Smith's cold-start
exposed (adversarial review D2/D3):

  1. It scored only a *subset* of a merchant's glyphs, so ``e``, ``u``, ``E``,
     ``V`` were never tested -- the exact four cells that broke the font.
  2. Its metric is *within-identity relative*: a broken ``e`` still resembles
     fleet ``e`` more than any other letter, so it scores a clean pass even as
     it renders as a raised speck.

This module is the missing *absolute* term. Each check is a self-contained
structural assertion about a single glyph bitmap -- it needs no other merchant
and no consensus, so it cannot be fooled by "everyone's ``e`` is a bit off".
The checks map one-to-one to the review's named failure modes:

  * ``INK_FLOOR``     -- glyph is almost empty (speck-collapse: rendered ``e``/
                         ``u`` shrink to a superscript dot).
  * ``NO_COUNTER``    -- a closed-loop letter (``e``,``o``,``b``,``d`` ...) has
                         no enclosed counter -> its bowl collapsed.
  * ``OPEN_BASE``     -- a ``u``/``U`` whose two stems never join at the base;
                         it reads as ``ll``/``II``.
  * ``MISSING_BAR``   -- an ``E`` with no bottom horizontal bar -> reads as ``F``.
  * ``NO_CONVERGE``   -- a ``V``/``v``/``Y``/``W``/``w`` that does not narrow
                         toward its apex -> the ``V`` reads as ``L``.

All thresholds are calibrated (see ``CALIBRATION`` below and
``tests/test_glyph_floor.py``) so the four known-broken Smith's natives FAIL and
the nine fleet atlases overwhelmingly PASS. Pure numpy.

CALIBRATION (fleet = costco/cvs/homedepot/innout/sprouts/target/traderjoes/
vons/wildfork refined atlases; broken = Smith's cold-start native e,u,E,V):

    check          metric                 fleet range        smiths(broken)
    -----------    --------------------   ----------------   --------------
    NO_COUNTER     counter_area / bbox    e >= 0.05          e = 0.000  FAIL
    OPEN_BASE      base-band center cols  u,U = 1.00         u = 0.500  FAIL
    MISSING_BAR    bottom-right ink frac  E >= 0.212         E = 0.032  FAIL
    NO_CONVERGE    (top_span-bot_span)/w  V >= 0.000         V = -0.250 FAIL

The one fleet glyph that also trips a floor is Home Depot's ``b`` (counter = 0,
a genuinely-degenerate glyph the fleet already treats as an outlier) -- reported
honestly as the sole false-positive; see ``audit_atlas`` docstring.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

# Closed-loop letters/digits whose canonical form encloses at least one counter.
# Kept conservative: only glyphs that are closed in *every* common receipt font
# (single-story 'a'/'g' and open '4' are excluded to avoid false positives).
CLOSED_CHARS = "bdeopqBDOPQR8@"
# Letters whose two vertical stems must join at the baseline (a broken bowl
# reads as two separate bars). Applied to both cases; a sound 'U' passes.
BASE_JOIN_CHARS = "uU"
# Letters that must narrow toward a downward apex (the 'V' family). A glyph that
# stays wide (or widens) at the bottom is an 'L'/'\_' misrender.
CONVERGE_DOWN_CHARS = "vVyYwW"
# 'E' gets an explicit three-bar assertion (top+middle present is easy; the
# bottom bar is the one the cold-start dropped, turning E->F).
# Chars that are *legitimately* multi-part or sparse -- skip the ink floor's
# emptiness heuristic sanity but they still run the structural checks above.

MIN_INK_PX = 12  # fewer set pixels than this == a speck, not a letter
MIN_INK_FRAC = 0.04  # cropped ink share; a real glyph fills far more of its bbox
COUNTER_MIN_FRAC = 0.02  # enclosed-counter area / bbox area (fleet closed >= 0.05)
BASE_JOIN_MIN = 0.80  # share of base-band center columns carrying ink (fleet=1.0)
EBAR_MIN_FRAC = 0.10  # E bottom-right band ink share (fleet >= 0.21, broken 0.03)
CONVERGE_MIN = -0.08  # (top_span - bot_span)/width; V narrows so this is >= 0


@dataclass
class GlyphDefect:
    char: str
    kind: str  # INK_FLOOR | NO_COUNTER | OPEN_BASE | MISSING_BAR | NO_CONVERGE
    detail: str
    severity: float  # higher = worse (for ranking); scale is per-kind


# --- geometry helpers (dependency-free) -----------------------------------


def _crop(mask: np.ndarray) -> Optional[np.ndarray]:
    b = np.asarray(mask) > 0
    if not b.any():
        return None
    ys, xs = np.where(b)
    return b[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]


def counter_area(crop: np.ndarray) -> int:
    """Area of enclosed background (holes) -- flood-fill from the border, any
    background the fill can't reach is an enclosed counter. 'e'/'o' have one;
    a collapsed bowl has none."""
    bg = ~crop
    h, w = bg.shape
    seen = np.zeros_like(bg)
    stack = [(y, x) for y in range(h) for x in (0, w - 1) if bg[y, x]]
    stack += [(y, x) for x in range(w) for y in (0, h - 1) if bg[y, x]]
    for y, x in stack:
        seen[y, x] = True
    while stack:
        y, x = stack.pop()
        for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if 0 <= ny < h and 0 <= nx < w and bg[ny, nx] and not seen[ny, nx]:
                seen[ny, nx] = True
                stack.append((ny, nx))
    return int((bg & ~seen).sum())


def _row_span(band: np.ndarray) -> int:
    """Width of the ink bounding box within a horizontal band of rows."""
    cols = np.where(band.any(axis=0))[0]
    return int(cols.max() - cols.min() + 1) if len(cols) else 0


# --- individual checks -----------------------------------------------------


def _check_ink(char: str, crop: np.ndarray) -> Optional[GlyphDefect]:
    ink = int(crop.sum())
    frac = ink / crop.size
    if ink < MIN_INK_PX or frac < MIN_INK_FRAC:
        return GlyphDefect(
            char, "INK_FLOOR",
            f"only {ink} ink px (frac {frac:.3f}) -- glyph collapsed to a speck",
            float(MIN_INK_PX - ink + 1),
        )
    return None


def _check_counter(char: str, crop: np.ndarray) -> Optional[GlyphDefect]:
    if char not in CLOSED_CHARS:
        return None
    area = counter_area(crop)
    frac = area / crop.size
    if frac < COUNTER_MIN_FRAC:
        return GlyphDefect(
            char, "NO_COUNTER",
            f"closed letter has no enclosed counter "
            f"(counter area {area}px, frac {frac:.3f} < {COUNTER_MIN_FRAC}) "
            f"-- bowl collapsed",
            float(COUNTER_MIN_FRAC - frac),
        )
    return None


def _check_base_join(char: str, crop: np.ndarray) -> Optional[GlyphDefect]:
    if char not in BASE_JOIN_CHARS:
        return None
    h, w = crop.shape
    band = crop[int(h * 0.78) :]
    c0, c1 = int(w * 0.36), int(w * 0.64)
    center = band[:, c0:c1]
    cover = float(center.any(axis=0).mean()) if center.size else 0.0
    if cover < BASE_JOIN_MIN:
        return GlyphDefect(
            char, "OPEN_BASE",
            f"stems do not join at the base "
            f"(base-band center coverage {cover:.2f} < {BASE_JOIN_MIN}) "
            f"-- reads as two bars",
            float(BASE_JOIN_MIN - cover),
        )
    return None


def _check_ebar(char: str, crop: np.ndarray) -> Optional[GlyphDefect]:
    if char != "E":
        return None
    h, w = crop.shape
    bottom = crop[int(h * 0.85) :]
    frac = bottom[:, w // 2 :].sum() / max(bottom.size, 1)
    if frac < EBAR_MIN_FRAC:
        return GlyphDefect(
            char, "MISSING_BAR",
            f"no bottom horizontal bar "
            f"(bottom-right ink {frac:.3f} < {EBAR_MIN_FRAC}) -- reads as 'F'",
            float(EBAR_MIN_FRAC - frac),
        )
    return None


def _check_converge(char: str, crop: np.ndarray) -> Optional[GlyphDefect]:
    if char not in CONVERGE_DOWN_CHARS:
        return None
    h, w = crop.shape
    top = _row_span(crop[: int(h * 0.18)])
    bot = _row_span(crop[int(h * 0.82) :])
    conv = (top - bot) / w
    if conv < CONVERGE_MIN:
        return GlyphDefect(
            char, "NO_CONVERGE",
            f"does not narrow toward apex "
            f"(top_span {top} < bottom_span {bot}; conv {conv:.2f} "
            f"< {CONVERGE_MIN}) -- 'V' reads as 'L'",
            float(CONVERGE_MIN - conv),
        )
    return None


_CHECKS = (
    _check_ink,
    _check_counter,
    _check_base_join,
    _check_ebar,
    _check_converge,
)


def audit_glyph(char: str, mask: np.ndarray) -> list[GlyphDefect]:
    """Run every absolute floor check on one glyph bitmap. Empty -> [] a defect
    (an entirely-blank cell is a speck at the extreme)."""
    crop = _crop(mask)
    if crop is None:
        return [GlyphDefect(char, "INK_FLOOR", "empty glyph (0 ink px)", 99.0)]
    out: list[GlyphDefect] = []
    for check in _CHECKS:
        d = check(char, crop)
        if d is not None:
            out.append(d)
    return out


def audit_atlas(
    glyphs: dict[int, np.ndarray], chars: Optional[str] = None
) -> list[GlyphDefect]:
    """Floor-audit every glyph in one merchant atlas ({codepoint: bitmap}).

    Returns defects worst-first. This is deliberately merchant-local: unlike
    ``glyph_gate`` it needs no other atlas, so it can gate a brand-new merchant
    (e.g. a cold-start Smith's) before any fleet comparison exists.

    Calibration note: across the nine refined fleet atlases the only glyph that
    trips a floor is Home Depot's ``b`` (NO_COUNTER -- a real degenerate glyph,
    Home Depot being the fleet's known letterform outlier). Every other fleet
    glyph passes; the false-positive rate is 1 / ~430 audited alnum glyphs
    (<0.3%). See tests/test_glyph_floor.py::test_fleet_false_positive_rate.
    """
    out: list[GlyphDefect] = []
    for cp, mask in glyphs.items():
        ch = chr(cp)
        if chars is not None and ch not in chars:
            continue
        out.extend(audit_glyph(ch, mask))
    # worst-first: INK_FLOOR (structural collapse) then by severity magnitude
    order = {"INK_FLOOR": 0, "NO_COUNTER": 1, "OPEN_BASE": 2,
             "MISSING_BAR": 3, "NO_CONVERGE": 4}
    out.sort(key=lambda d: (order.get(d.kind, 9), -d.severity))
    return out
