"""Render round-trip gate: OCR the FINAL rendered receipt and reconcile it
against the composed token stream.

Every gate in the M6 cold-start scored either minted atlas crops or the compose
0-1000 coordinate graph. **None read the pixels a human sees.** The adversarial
review's single biggest coverage hole (process-change #1): the two seed defects
-- a cashier line printed twice and glyphs so broken whole words garble -- are
only visible in the rendered PNG.

This module closes that hole. It runs the repo's local Apple-Vision OCR
(``receipt_upload.ocr.apple_vision_ocr``; the same engine that read every real
receipt in the corpus) over the final render, groups the composed tokens into
lines, and reconciles the two. It reports, per line:

  * ``matched``    -- composed line found in the render (with a character error
                      rate vs the OCR readback);
  * ``missing``    -- composed line the OCR could not recover (dropped block /
                      garbled beyond reading -- e.g. the card-auth band);
  * ``unexpected`` -- OCR line with no composed counterpart (injected at render
                      time -- e.g. a baked-in barcode/tagline);

and two duplication signals that are the actual seed defects:

  * ``DUPLICATE_ROLE``   -- a singleton role (cashier/phone/register) appears in
                            more than one *composed* line, so the render prints
                            it twice (seed #1: the twin cashier line);
  * ``RENDER_DUPLICATE`` -- one composed line is matched by two+ OCR lines, so
                            the *render* drew it twice (the doubled "FRESH FOR
                            EVERYONE" storefront tagline).

PASS/FAIL thresholds (justified inline at ``roundtrip_gate``) are set so a
faithful render of a clean font passes and the v1 Smith's render fails, citing
the doubled cashier.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional

# --- text + geometry helpers ----------------------------------------------

_KEEP = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Case-fold, drop punctuation, collapse whitespace. OCR and composer
    disagree on punctuation/spacing constantly; identity should not hinge on
    it."""
    return _WS.sub(" ", _KEEP.sub(" ", text.lower())).strip()


def _cer(reference: str, hypothesis: str) -> float:
    """Character error rate = Levenshtein(ref, hyp) / len(ref)."""
    ref = normalize(reference).replace(" ", "")
    hyp = normalize(hypothesis).replace(" ", "")
    if not ref:
        return 0.0 if not hyp else 1.0
    # iterative DP Levenshtein (no deps)
    prev = list(range(len(hyp) + 1))
    for i, rc in enumerate(ref, 1):
        cur = [i]
        for j, hc in enumerate(hyp, 1):
            cur.append(
                min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rc != hc))
            )
        prev = cur
    return prev[-1] / len(ref)


def _ratio(a: str, b: str) -> float:
    """Strict two-way similarity -- used for duplicate detection where each OCR
    line must be a near-*full* copy of the composed line."""
    return SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def _match_score(comp: str, ocr: str) -> float:
    """Merge-tolerant match score. OCR frequently fuses or splits lines
    relative to the composer's grouping; a short composed line fully contained
    in a longer OCR line should still count as found. Returns the max of the
    symmetric ratio and the composed line's containment (longest common block /
    composed length)."""
    c = normalize(comp).replace(" ", "")
    o = normalize(ocr).replace(" ", "")
    if not c:
        return 0.0
    sm = SequenceMatcher(None, c, o)
    block = sm.find_longest_match(0, len(c), 0, len(o)).size
    return max(sm.ratio(), block / len(c))


# Singleton roles: a context line that on every one of the 12 real Smith's
# receipts prints exactly once, in the header band. Only "cashier" qualifies as
# a robust singleton -- phone numbers legitimately recur (store + product-recall
# + customer-service lines all print a distinct number), so a phone-cardinality
# rule would false-positive. Patterns match normalized composed line text.
ROLE_PATTERNS = {
    "cashier": re.compile(r"cashier"),
}


@dataclass
class Line:
    text: str
    y: float  # normalized 0..1, 1 = top of page (Vision / composer convention)


@dataclass
class LineMatch:
    composed: str
    ocr: Optional[str]
    y: float
    cer: float
    n_ocr_hits: int  # how many OCR lines matched this composed line


@dataclass
class RoundTripReport:
    matches: list[LineMatch] = field(default_factory=list)
    missing: list[Line] = field(default_factory=list)
    unexpected: list[Line] = field(default_factory=list)
    duplicate_roles: list[str] = field(default_factory=list)
    render_duplicates: list[str] = field(default_factory=list)
    coverage: float = 1.0
    mean_cer: float = 0.0
    passed: bool = True
    reasons: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"round-trip: {'PASS' if self.passed else 'FAIL'}  "
            f"coverage={self.coverage:.2f} mean_cer={self.mean_cer:.3f} "
            f"missing={len(self.missing)} unexpected={len(self.unexpected)} "
            f"dup_roles={self.duplicate_roles} "
            f"render_dups={len(self.render_duplicates)}"
        ]
        for r in self.reasons:
            lines.append(f"  FAIL: {r}")
        return "\n".join(lines)


# --- composed-token -> line grouping --------------------------------------


def composed_lines(candidate: dict, y_tol: float = 0.010) -> list[Line]:
    """Group a composed candidate's tokens into visual lines.

    ``candidate`` is one entry of composed.json: parallel ``tokens`` and
    ``bboxes`` ([x0, y0, x1, y1]; y is bottom-origin, higher = nearer the top).
    Tokens are clustered by y-centre (within ``y_tol`` of the running line) and
    ordered left-to-right within a line.
    """
    tokens = candidate["tokens"]
    bboxes = candidate["bboxes"]
    page_h = max(b[3] for b in bboxes)
    page_w = max(b[2] for b in bboxes) or 1
    items = []
    for tok, b in zip(tokens, bboxes):
        yc = (b[1] + b[3]) / 2 / page_h
        xc = b[0] / page_w
        items.append((yc, xc, tok))
    items.sort(key=lambda t: (-t[0], t[1]))  # top-to-bottom, then left-to-right
    lines: list[Line] = []
    cur: list[tuple[float, float, str]] = []
    cur_y = None
    for yc, xc, tok in items:
        if cur_y is None or abs(yc - cur_y) <= y_tol:
            cur.append((yc, xc, tok))
            cur_y = yc if cur_y is None else (cur_y + yc) / 2
        else:
            lines.append(_flush(cur))
            cur = [(yc, xc, tok)]
            cur_y = yc
    if cur:
        lines.append(_flush(cur))
    return lines


def _flush(group: list[tuple[float, float, str]]) -> Line:
    group = sorted(group, key=lambda t: t[1])
    text = " ".join(t[2] for t in group)
    y = sum(t[0] for t in group) / len(group)
    return Line(text=text, y=y)


# --- OCR of the rendered PNG ----------------------------------------------


def ocr_render(png_path: str) -> list[Line]:
    """OCR a rendered receipt into lines. Prefers the in-repo Apple-Vision
    engine (the corpus's own OCR); falls back to pytesseract only if that is
    unavailable (non-Darwin / missing swift)."""
    try:
        return _ocr_apple_vision(png_path)
    except Exception:  # pragma: no cover - platform fallback
        return _ocr_pytesseract(png_path)


def _ocr_apple_vision(png_path: str) -> list[Line]:
    import os
    import sys

    root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
    )
    up = os.path.join(root, "receipt_upload")
    if up not in sys.path:
        sys.path.insert(0, up)
    from receipt_upload.ocr import apple_vision_ocr

    result = apple_vision_ocr([png_path])
    lines: list[Line] = []
    for _iid, (ln, _w, _l) in result.items():
        for obj in ln:
            bb = obj.bounding_box
            yc = bb["y"] + bb["height"] / 2
            lines.append(Line(text=obj.text, y=float(yc)))
    return lines


def _ocr_pytesseract(png_path: str) -> list[Line]:  # pragma: no cover
    import pytesseract
    from PIL import Image

    img = Image.open(png_path)
    h = img.size[1]
    data = pytesseract.image_to_data(
        img, output_type=pytesseract.Output.DICT
    )
    rows: dict[tuple, list] = {}
    for i, txt in enumerate(data["text"]):
        if not txt.strip():
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        rows.setdefault(key, []).append(i)
    lines: list[Line] = []
    for idxs in rows.values():
        text = " ".join(data["text"][i] for i in idxs)
        yc = sum(data["top"][i] + data["height"][i] / 2 for i in idxs) / len(
            idxs
        )
        lines.append(Line(text=text, y=1.0 - yc / h))  # top-origin -> bottom
    return lines


# --- reconciliation --------------------------------------------------------


def reconcile(
    comp: list[Line],
    ocr: list[Line],
    y_tol: float = 0.03,
    match_min: float = 0.34,
    dup_min: float = 0.60,
) -> RoundTripReport:
    """Match composed lines to OCR lines by position + text similarity.

    ``y_tol`` -- vertical search window (Vision y jitters a little vs compose).
    ``match_min`` -- similarity to accept a primary match (garbled but readable
    lines still pair; genuinely-dropped lines fall below it).
    ``dup_min`` -- similarity for an *additional* OCR line to count as a second
    print of the same composed line (higher, so a split price fragment does not
    masquerade as a duplicate).
    """
    rep = RoundTripReport()
    used = [False] * len(ocr)
    total_ref = 0
    total_err = 0.0
    matched_lines = 0
    # punctuation-only composed lines (asterisk rows, lone '.') carry no
    # readable identity -- exclude them from matching, coverage, and dup checks
    comp = [cl for cl in comp if len(normalize(cl.text)) >= 2]

    for cl in comp:
        # candidate OCR lines within the vertical window
        cands = [
            (j, _match_score(cl.text, ocr[j].text))
            for j in range(len(ocr))
            if abs(ocr[j].y - cl.y) <= y_tol
        ]
        best = max(cands, key=lambda t: t[1], default=(None, 0.0))
        # duplicate print: 2+ distinct OCR lines each strongly matching this
        # composed line (each a near-full copy, not a fragment). Uses the strict
        # symmetric ratio so a merged/fragment OCR line is not miscounted.
        strong = [
            j
            for j in range(len(ocr))
            if abs(ocr[j].y - cl.y) <= y_tol
            and _ratio(cl.text, ocr[j].text) >= dup_min
        ]
        n_hits = len(strong)
        if best[0] is not None and best[1] >= match_min:
            j = best[0]
            used[j] = True
            cer = _cer(cl.text, ocr[j].text)
            rep.matches.append(
                LineMatch(cl.text, ocr[j].text, cl.y, cer, n_hits)
            )
            total_ref += len(normalize(cl.text).replace(" ", ""))
            total_err += cer * len(normalize(cl.text).replace(" ", ""))
            matched_lines += 1
            if n_hits >= 2:
                rep.render_duplicates.append(normalize(cl.text))
        else:
            rep.missing.append(cl)

    for j, ln in enumerate(ocr):
        if not used[j] and len(normalize(ln.text)) >= 3:
            rep.unexpected.append(ln)

    # singleton-role cardinality on the COMPOSED stream (reads the tokens
    # directly, as instructed) -- catches the twin cashier line
    for role, pat in ROLE_PATTERNS.items():
        hits = sum(1 for cl in comp if pat.search(normalize(cl.text)))
        if hits > 1:
            rep.duplicate_roles.append(f"{role}x{hits}")

    rep.coverage = matched_lines / len(comp) if comp else 1.0
    rep.mean_cer = (total_err / total_ref) if total_ref else 0.0
    return rep


# --- the gate --------------------------------------------------------------


def roundtrip_gate(
    png_path: str,
    candidate: dict,
    coverage_min: float = 0.80,
    cer_max: float = 0.20,
) -> RoundTripReport:
    """Full gate: OCR the render, reconcile, decide PASS/FAIL.

    Thresholds (calibrated against a faithful clean render vs the v1 Smith's
    render):

      * ``coverage_min`` = 0.80 -- Apple Vision recovers essentially every line
        of a clean synthetic receipt; dropping >20% of composed lines means a
        block failed to render or is garbled past reading.
      * ``cer_max`` = 0.20 -- a faithful render OCRs back at CER well under 0.10;
        0.20 leaves head-room for Vision's own noise while still failing a font
        whose letterforms are broken enough to garble one char in five.
      * ANY duplicate singleton role or render-duplicated line is an automatic
        fail -- these are structural (a doubled cashier / tagline is never
        acceptable), not a matter of degree.
    """
    comp = composed_lines(candidate)
    ocr = ocr_render(png_path)
    rep = reconcile(comp, ocr)

    if rep.duplicate_roles:
        rep.reasons.append(
            f"duplicate singleton role(s): {', '.join(rep.duplicate_roles)}"
        )
    if rep.render_duplicates:
        rep.reasons.append(
            f"{len(rep.render_duplicates)} composed line(s) rendered twice: "
            f"{rep.render_duplicates[:3]}"
        )
    if rep.coverage < coverage_min:
        rep.reasons.append(
            f"coverage {rep.coverage:.2f} < {coverage_min} "
            f"({len(rep.missing)} composed lines not recovered)"
        )
    if rep.mean_cer > cer_max:
        rep.reasons.append(
            f"mean CER {rep.mean_cer:.3f} > {cer_max} (garbled letterforms)"
        )
    rep.passed = not rep.reasons
    return rep
