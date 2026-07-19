"""Dry-run section-seed projection + coverage report (no Dynamo writes).

M0.3 of the font-intelligence epic. Combines the two seed sources defined in
:mod:`glyphstudio.sections` over a receipt's words —

  A. **word-label projection** — ``section_for_core_label`` on each word's
     validated ``CORE_LABELS``.
  B. **stylescan line rules** — group words into lines, run stylescan's rule
     engine (``_classify``) and fold the result to a canonical section.

— assigns every word a ``section_final`` (source A wins over B), and aggregates
per-merchant **coverage** (fraction of words that get any section) and
**cross-source agreement** (where both A and B fire, do they agree — a
ground-truth-free accuracy proxy). This is the M0 exit report.

The projection logic here is pure and unit-tested with synthetic words. The
CLI adapter (``seed_section_report.py``) loads real receipts and feeds them in.
Nothing in this module writes ``SECTION_*`` rows — that is the approval gate.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

from .sections import normalize_stylescan_section, section_for_core_label

# Canonical merchant name (ReceiptMetadata) -> stylescan rule slug. Convenience
# defaults for the nine calibrated merchants; the CLI can override.
MERCHANT_SLUGS: dict[str, str] = {
    "sprouts farmers market": "sprouts",
    "costco wholesale": "costco",
    "costco": "costco",
    "vons": "vons",
    "trader joe's": "traderjoes",
    "trader joes": "traderjoes",
    "cvs": "cvs",
    "cvs pharmacy": "cvs",
    "in-n-out burger": "innout",
    "in-n-out": "innout",
    "target": "target",
    "wild fork": "wildfork",
    "wild fork foods": "wildfork",
    "the home depot": "homedepot",
    "home depot": "homedepot",
}


def merchant_slug(merchant_name: str) -> Optional[str]:
    """Canonical merchant name -> stylescan slug, or None if unknown."""
    if not merchant_name:
        return None
    return MERCHANT_SLUGS.get(merchant_name.strip().lower())


def known_stylescan_slugs() -> frozenset[str]:
    """The slugs stylescan has rules for (keys of ``_MERCHANT_RULES``)."""
    from .stylescan import _MERCHANT_RULES

    return frozenset(_MERCHANT_RULES)


@dataclass(frozen=True)
class SeedWord:
    """The minimal per-word record the report needs (adapter builds these)."""

    line_id: int
    word_id: int
    text: str
    labels: tuple[str, ...] = ()  # validated CORE label names on this word


@dataclass
class WordSeed:
    """A word's projected section from each source and the resolved final."""

    line_id: int
    word_id: int
    text: str
    section_label: Optional[str] = None  # source A (word-label projection)
    section_style: Optional[str] = None  # source B (stylescan rules)
    section_final: Optional[str] = None
    source: Optional[str] = None  # "label" | "stylescan" | None


def word_label_section(
    labels: Sequence[str], include_medium: bool = False
) -> Optional[str]:
    """Highest-confidence canonical section projected from a word's labels.

    Prefers a ``high``-confidence label over a ``medium`` one. With
    ``include_medium=False`` (default) only ``high`` labels seed.
    """
    from .sections import core_label_confidence

    best: Optional[str] = None
    best_conf = ""
    for lbl in labels:
        section = section_for_core_label(lbl)
        if not section:
            continue
        conf = core_label_confidence(lbl) or ""
        if conf == "medium" and not include_medium:
            continue
        # high beats medium; first-seen wins within a tier
        if best is None or (conf == "high" and best_conf != "high"):
            best, best_conf = section, conf
    return best


def _line_text_and_price(words: Sequence[SeedWord]) -> tuple[str, bool]:
    ordered = sorted(words, key=lambda w: w.word_id)
    text = " ".join(w.text for w in ordered)
    # shared predicate with stylescan.measure: a line classifies identically
    # through every entry point (#1188 review F12)
    from .stylescan import line_has_price

    has_price = line_has_price(w.text for w in ordered)
    return text, has_price


def line_section(words: Sequence[SeedWord], merchant: str) -> Optional[str]:
    """Canonical section for a line of words via stylescan's rule engine.

    Raises ValueError for a slug stylescan has no rules for — otherwise
    ``_classify`` silently falls back to the Sprouts rules and returns
    plausible-but-wrong sections for the wrong merchant.
    """
    from .stylescan import _classify

    if merchant not in known_stylescan_slugs():
        raise ValueError(
            f"unknown stylescan slug {merchant!r}; "
            f"known: {sorted(known_stylescan_slugs())}"
        )
    text, has_price = _line_text_and_price(words)
    raw = _classify(text, has_price, merchant)
    return normalize_stylescan_section(raw)


def seed_receipt(
    words: Iterable[SeedWord],
    merchant: str,
    include_medium: bool = False,
) -> list[WordSeed]:
    """Project both seed sources over one receipt's words.

    ``merchant`` is the stylescan slug (e.g. ``"costco"``). Returns one
    :class:`WordSeed` per input word, source A (labels) winning over B
    (stylescan) for ``section_final``. Raises ValueError for an unknown slug.
    """
    if merchant not in known_stylescan_slugs():
        raise ValueError(
            f"unknown stylescan slug {merchant!r}; "
            f"known: {sorted(known_stylescan_slugs())}"
        )
    words = list(words)
    lines: dict[int, list[SeedWord]] = {}
    for w in words:
        lines.setdefault(w.line_id, []).append(w)
    line_sections = {
        lid: line_section(lws, merchant) for lid, lws in lines.items()
    }

    out: list[WordSeed] = []
    for w in words:
        a = word_label_section(w.labels, include_medium=include_medium)
        b = line_sections.get(w.line_id)
        if a is not None:
            final, source = a, "label"
        elif b is not None:
            final, source = b, "stylescan"
        else:
            final, source = None, None
        out.append(
            WordSeed(
                line_id=w.line_id,
                word_id=w.word_id,
                text=w.text,
                section_label=a,
                section_style=b,
                section_final=final,
                source=source,
            )
        )
    return out


@dataclass
class SeedReport:
    """Aggregate coverage/agreement over one or more receipts."""

    merchant: str = ""
    receipts: int = 0
    total_words: int = 0
    covered_words: int = 0
    per_section: Counter = field(default_factory=Counter)
    source_counts: Counter = field(default_factory=Counter)
    both_present: int = 0
    both_agree: int = 0
    disagreements: list[dict] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        return (
            self.covered_words / self.total_words if self.total_words else 0.0
        )

    @property
    def agreement(self) -> float:
        return (
            self.both_agree / self.both_present if self.both_present else 0.0
        )

    def add_receipt(
        self,
        seeds: Sequence[WordSeed],
        image_id: str = "",
        receipt_id: Optional[int] = None,
        disagreement_cap: int = 25,
    ) -> None:
        self.receipts += 1
        for s in seeds:
            self.total_words += 1
            if s.section_final is not None:
                self.covered_words += 1
                self.per_section[s.section_final] += 1
            if s.source:
                self.source_counts[s.source] += 1
            if s.section_label is not None and s.section_style is not None:
                self.both_present += 1
                if s.section_label == s.section_style:
                    self.both_agree += 1
                elif len(self.disagreements) < disagreement_cap:
                    self.disagreements.append(
                        {
                            "image_id": image_id,
                            "receipt_id": receipt_id,
                            "line_id": s.line_id,
                            "word_id": s.word_id,
                            "text": s.text,
                            "label_section": s.section_label,
                            "style_section": s.section_style,
                        }
                    )

    def to_dict(self) -> dict:
        return {
            "merchant": self.merchant,
            "receipts": self.receipts,
            "total_words": self.total_words,
            "covered_words": self.covered_words,
            "coverage": round(self.coverage, 4),
            "per_section": dict(self.per_section.most_common()),
            "source_counts": dict(self.source_counts),
            "cross_source": {
                "both_present": self.both_present,
                "both_agree": self.both_agree,
                "agreement": round(self.agreement, 4),
            },
            "disagreements_sample": self.disagreements,
        }


# --- aggregation to ReceiptSection specs (line-level persistence) ----------
#
# ReceiptSection is line-level: one row per (receipt, section) holding its
# line_ids. Words inherit their line's section, so we collapse the per-word
# WordSeeds to a single section + confidence per line, then group lines by
# section. Pure data (no Dynamo) — the write CLI turns these specs into rows.


@dataclass(frozen=True)
class LineSection:
    line_id: int
    section: str  # canonical section
    confidence: float


@dataclass(frozen=True)
class SectionSpec:
    """One ReceiptSection row's worth of data."""

    section_type: str  # SectionType value (canonical.upper())
    line_ids: tuple[int, ...]
    confidence: float


def _line_confidence(
    chosen: str,
    from_label: bool,
    label_votes: Sequence[str],
    style: Optional[str],
) -> float:
    """Confidence for a line's chosen section.

    Hand labels are ground truth, so a label-chosen line starts at 0.70 and is
    lifted by (a) internal label agreement and (b) stylescan corroboration —
    up to 1.0. A rule-only line (no labels, stylescan alone) is a heuristic:
    a flat 0.60. This ordering (label > rule) is deliberate; M1 QA recalibrates
    it from measured per-source precision.
    """
    if not from_label:
        return 0.60  # rule-only, unverified by any label
    frac = sum(1 for v in label_votes if v == chosen) / len(label_votes)
    conf = 0.70 + 0.15 * frac + (0.15 if style == chosen else 0.0)
    return round(min(1.0, conf), 4)


def aggregate_line_sections(
    seeds: Sequence[WordSeed],
    label_only: bool = False,
) -> list[LineSection]:
    """Collapse per-word seeds to one (section, confidence) per line.

    **Labels win the line** — consistent with the per-word ``seed_receipt``
    policy (source A over B). If any word on the line carries a label-derived
    section, the line takes the majority of those (hand-validated ground
    truth); otherwise it falls back to the line-level stylescan rule. A line
    with neither is dropped.

    ``label_only=True`` additionally withholds rule-only lines (no labels) —
    used for low-agreement merchants until their disagreements are reviewed.
    """
    by_line: dict[int, list[WordSeed]] = {}
    for s in seeds:
        by_line.setdefault(s.line_id, []).append(s)

    out: list[LineSection] = []
    for line_id, ws in sorted(by_line.items()):
        style = next(
            (w.section_style for w in ws if w.section_style is not None), None
        )
        label_votes = [
            w.section_label for w in ws if w.section_label is not None
        ]
        if label_votes:
            chosen = Counter(label_votes).most_common(1)[0][0]
            from_label = True
        elif style is not None:
            if label_only:
                continue  # withhold rule-only line
            chosen, from_label = style, False
        else:
            continue  # no section for this line
        out.append(
            LineSection(
                line_id=line_id,
                section=chosen,
                confidence=_line_confidence(
                    chosen, from_label, label_votes, style
                ),
            )
        )
    return out


def receipt_section_specs(
    seeds: Sequence[WordSeed], label_only: bool = False
) -> list[SectionSpec]:
    """Group a receipt's line sections into per-(receipt, section) specs.

    section_type is the ``SectionType`` value (canonical upper-cased);
    confidence is the mean of the section's member-line confidences.
    ``label_only`` withholds stylescan-only lines (see aggregate_line_sections).
    """
    lines = aggregate_line_sections(seeds, label_only=label_only)
    by_section: dict[str, list[LineSection]] = {}
    for ls in lines:
        by_section.setdefault(ls.section, []).append(ls)

    specs: list[SectionSpec] = []
    for section, members in sorted(by_section.items()):
        line_ids = tuple(sorted({m.line_id for m in members}))
        conf = round(sum(m.confidence for m in members) / len(members), 4)
        specs.append(
            SectionSpec(
                section_type=section.upper(),
                line_ids=line_ids,
                confidence=conf,
            )
        )
    return specs
