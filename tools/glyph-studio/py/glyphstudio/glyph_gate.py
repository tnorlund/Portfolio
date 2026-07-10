"""Deterministic handcraft gate: which glyphs are broken, fleet-referenced.

The mint's per-glyph quality gates look at a glyph in isolation, so a
degenerate skeleton can pass (Home Depot shipped an 'A' that is literally a
vertical bar — receipts rendered "CHANGE" as "CHINGE"). Source-anatomy audits
(`font_audit`) count strokes/nodes but cannot tell that a glyph *reads as the
wrong character*.

This gate asks the question that matters, deterministically: **does each
merchant's rendered glyph look like its own character, as printed by the rest
of the fleet?** For every (merchant, char):

1. ``MISRENDER`` — the glyph matches some *other* character's fleet consensus
   better than its own (by a margin). Names the confusion target: HD's 'A'
   matches fleet 'I'. The sharpest, baseline-free signal.
2. ``LOW_AGREEMENT`` — same-char fleet IoU sits far below the merchant's own
   median agreement (merchant-relative, so a legitimately distinct family
   like Home Depot is not blanket-flagged).
3. ``MISSING`` — chars most of the fleet has but this merchant lacks
   (renders fall through to substitutes: Sprouts prints '*' as 'X').

Pure numpy on top of the M2 toolchain (aspect-preserving shape
normalization). No thresholds hand-tuned per char — statistics come from the
fleet itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Optional

import numpy as np
from glyphstudio.family_cluster import glyph_iou, load_normalized_merchant

# Compare on everything merchants commonly print. Punctuation is included:
# broken '?', '@', '*' are exactly the class the mint's gates missed.
GATE_CHARS = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "0123456789"
    "$%&*#@?!:;,.-/()"
)
MIN_FLEET_SUPPORT = 4  # a char needs >=4 merchants for a meaningful consensus


@dataclass
class Finding:
    merchant: str
    char: str
    kind: str  # MISRENDER | LOW_AGREEMENT | MISSING
    detail: str
    score: float  # severity, higher = worse (for ranking)


def hole_count(mask: np.ndarray) -> int:
    """Number of enclosed background regions (topological holes).

    Flood-fills the background from the border; any background component the
    fill can't reach is a hole. 'A' has 1, 'B' 2, 'H'/'l' 0 — a cheap
    discriminator that IoU can't see: a straight-sided narrow 'A' overlaps
    fleet-'H' heavily in shape space but still has its counter, while a
    degenerate bar-'A' has none.
    """
    bg = ~mask.astype(bool)
    h, w = bg.shape
    seen = np.zeros_like(bg)
    stack = [(y, x) for y in range(h) for x in (0, w - 1) if bg[y, x]] + [
        (y, x) for x in range(w) for y in (0, h - 1) if bg[y, x]
    ]
    for y, x in stack:
        seen[y, x] = True
    while stack:
        y, x = stack.pop()
        for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if 0 <= ny < h and 0 <= nx < w and bg[ny, nx] and not seen[ny, nx]:
                seen[ny, nx] = True
                stack.append((ny, nx))
    interior = bg & ~seen
    # count connected components of the interior background
    holes = 0
    visited = np.zeros_like(interior)
    for y in range(h):
        for x in range(w):
            if interior[y, x] and not visited[y, x]:
                holes += 1
                comp = [(y, x)]
                visited[y, x] = True
                while comp:
                    cy, cx = comp.pop()
                    for ny, nx in (
                        (cy - 1, cx),
                        (cy + 1, cx),
                        (cy, cx - 1),
                        (cy, cx + 1),
                    ):
                        if (
                            0 <= ny < h
                            and 0 <= nx < w
                            and interior[ny, nx]
                            and not visited[ny, nx]
                        ):
                            visited[ny, nx] = True
                            comp.append((ny, nx))
    return holes


def fleet_consensus(
    normalized: dict[str, dict[int, np.ndarray]], char: str, exclude: str
) -> Optional[np.ndarray]:
    """Mean-then-threshold consensus of a char across the fleet (excluding
    one merchant), in normalized shape space.

    The support threshold is exclusion-aware: when the excluded merchant has
    the char, self-exclusion removes one contributor, so N-1 remaining still
    represents MIN_FLEET_SUPPORT merchants; when it does not, no contributor
    was removed and the full MIN_FLEET_SUPPORT is required.
    """
    stacks = [
        m[ord(char)]
        for name, m in normalized.items()
        if name != exclude and ord(char) in m
    ]
    excluded_had_char = ord(char) in normalized.get(exclude, {})
    required = MIN_FLEET_SUPPORT - (1 if excluded_had_char else 0)
    if len(stacks) < required:
        return None
    mean = np.mean([s.astype(np.float32) for s in stacks], axis=0)
    return mean >= 0.5


def _append_missing(
    findings: list[Finding],
    normalized: dict[str, dict[int, np.ndarray]],
    merchant: str,
    glyphs: dict[int, np.ndarray],
    chars: str,
) -> None:
    """Flag chars the fleet prints but this merchant lacks. Runs even when the
    merchant has no scoreable glyphs at all (no same-char baseline)."""
    for ch in chars:
        if ord(ch) in glyphs:
            continue
        support = sum(1 for m in normalized.values() if ord(ch) in m)
        if support >= MIN_FLEET_SUPPORT:
            findings.append(
                Finding(
                    merchant,
                    ch,
                    "MISSING",
                    f"{support}/{len(normalized)} merchants have it",
                    float(support),
                )
            )


def audit_fleet(
    font_dirs: dict[str, str],
    chars: str = GATE_CHARS,
    misrender_margin: float = 0.10,
    low_agreement_z: float = 2.5,
) -> list[Finding]:
    """Run the identity gate across every merchant's font directory."""
    normalized = {
        name: load_normalized_merchant(d, chars) for name, d in font_dirs.items()
    }
    return audit_normalized(
        normalized,
        chars=chars,
        misrender_margin=misrender_margin,
        low_agreement_z=low_agreement_z,
    )


def audit_normalized(
    normalized: dict[str, dict[int, np.ndarray]],
    chars: str = GATE_CHARS,
    misrender_margin: float = 0.10,
    low_agreement_z: float = 2.5,
) -> list[Finding]:
    """Identity gate over pre-normalized glyph dicts (pure; unit-testable).

    Returns findings sorted worst-first. ``misrender_margin`` is how much
    better the wrong character must match before flagging;
    ``low_agreement_z`` is the merchant-relative robust z-score cutoff.
    """
    findings: list[Finding] = []

    # Pre-compute fleet consensus per char per excluded merchant lazily.
    consensus_cache: dict[tuple[str, str], Optional[np.ndarray]] = {}
    holes_cache: dict[tuple[str, str], int] = {}

    def consensus(char: str, exclude: str) -> Optional[np.ndarray]:
        key = (char, exclude)
        if key not in consensus_cache:
            consensus_cache[key] = fleet_consensus(normalized, char, exclude)
        return consensus_cache[key]

    def consensus_holes(char: str, exclude: str) -> Optional[int]:
        key = (char, exclude)
        if key not in holes_cache:
            cons = consensus(char, exclude)
            holes_cache[key] = -1 if cons is None else hole_count(cons)
        return None if holes_cache[key] < 0 else holes_cache[key]

    for merchant, glyphs in sorted(normalized.items()):
        # -- per-merchant same-char agreement baseline ---------------------
        same_scores: dict[str, float] = {}
        for ch in chars:
            cp = ord(ch)
            if cp not in glyphs:
                continue
            cons = consensus(ch, merchant)
            if cons is None:
                continue
            same_scores[ch] = glyph_iou(glyphs[cp], cons)
        if not same_scores:
            _append_missing(findings, normalized, merchant, glyphs, chars)
            continue
        med = median(same_scores.values())
        mad = median(abs(v - med) for v in same_scores.values()) or 1e-6

        for ch, own in sorted(same_scores.items()):
            # -- MISRENDER: does it look like a different char? ------------
            own_holes = hole_count(glyphs[ord(ch)])
            best_other, best_other_iou = None, -1.0
            for other in chars:
                # Same char, or its case partner: shape normalization erases
                # scale, so u/U, o/O, w/W are inherently near-identical — a
                # case "confusion" carries no signal.
                if other == ch or other.lower() == ch.lower():
                    continue
                cons_o = consensus(other, merchant)
                if cons_o is None:
                    continue
                # Topology gate: a glyph only "reads as" another char if its
                # hole count matches (a straight-sided narrow 'A' overlaps
                # fleet-'H' in shape space, but its counter — 1 hole vs 0 —
                # says it is still an A; a degenerate bar-'A' has 0 holes and
                # genuinely reads as 'l').
                if consensus_holes(other, merchant) != own_holes:
                    continue
                iou = glyph_iou(glyphs[ord(ch)], cons_o)
                if iou > best_other_iou:
                    best_other, best_other_iou = other, iou
            if best_other is not None and best_other_iou > own + misrender_margin:
                findings.append(
                    Finding(
                        merchant,
                        ch,
                        "MISRENDER",
                        f"matches fleet {best_other!r} (IoU {best_other_iou:.2f}) "
                        f"better than fleet {ch!r} ({own:.2f})",
                        best_other_iou - own,
                    )
                )
                continue  # misrender subsumes low-agreement

            # -- LOW_AGREEMENT: far below the merchant's own baseline ------
            z = (med - own) / mad
            if z > low_agreement_z:
                findings.append(
                    Finding(
                        merchant,
                        ch,
                        "LOW_AGREEMENT",
                        f"same-char IoU {own:.2f} vs merchant median "
                        f"{med:.2f} (robust z {z:.1f})",
                        z,
                    )
                )

        # -- MISSING: fleet prints it, this merchant doesn't ---------------
        _append_missing(findings, normalized, merchant, glyphs, chars)

    findings.sort(key=lambda f: (f.kind != "MISRENDER", -f.score))
    return findings
