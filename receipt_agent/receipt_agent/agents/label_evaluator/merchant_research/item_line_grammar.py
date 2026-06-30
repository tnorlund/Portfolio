"""Item-line GRAMMAR extractor.

A *pure* extractor that learns a single merchant's item-line grammar from real
labeled receipts. Given a merchant's export (the per-merchant JSON produced by
the synth-batch tooling, keys: ``receipts``, ``receipt_lines``,
``receipt_words``, ``receipt_word_labels``, ``receipt_places``), it derives an
:class:`ItemLineTemplate` describing how that merchant prints item rows:

* ``tax_flag``   -- is there a trailing tax flag (F/T/S/A/E ...) next to the
  price, and where does it sit relative to the price?
* ``markdown_marker`` -- is there a ``now`` / ``NOW`` sale marker left of the
  price on discounted rows?
* ``sub_line``   -- do items carry a second OCR line such as
  ``SALE 1@ $x, WAS: $y each``? captured as a templated pattern.
* ``name_truncation`` -- are long product names cut off with ``...`` and at
  roughly what character width?
* ``columns``    -- normalized x for the product-name start, the price right
  edge, and the flag, computed as label-x medians from the labeled words
  (mirrors :mod:`...merchant_research.structure`'s ``_right_edge_x`` /
  ``statistics.median`` approach). Non-positive => unmeasured.
* ``sample_count`` / ``receipt_count`` / ``confidence``.

This module **only reads** export JSON. There is no network access and no
mutation of any existing artifact. Wiring the template into synthesis is a
later round; this is a standalone, deterministic function over receipt data.

CLI::

    python -m receipt_agent.agents.label_evaluator.merchant_research.item_line_grammar amazon_fresh

prints the derived :class:`ItemLineTemplate` as JSON. The export directory
defaults to ``~/synth-batch/exports`` and can be overridden with the
``SYNTH_BATCH_DIR`` environment variable.
"""

from __future__ import annotations

import dataclasses
import json
import math
import os
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

# Labels (normalized upper) whose words name a purchased product.
NAME_LABELS = {"PRODUCT_NAME", "ITEM_NAME"}
# Labels whose word is the line/extended price of an item row.
PRICE_LABELS = {"LINE_TOTAL", "ITEM_TOTAL"}

# Two words belong to the same visual item *row* when their vertical centers are
# within this normalized-y distance. Item rows for the merchants we have are
# >= ~0.014 apart, and a price/flag/markdown token sits within ~0.002 of its
# name, so this cleanly groups a row without bleeding into the next item or the
# (slightly lower) WAS/SALE sub-line.
ROW_Y_TOLERANCE = 0.010

# Tax-flag detection -------------------------------------------------------
#
# A per-line tax flag is a short alphabetic code printed next to an item's
# price. It can sit on either side of the price: *trailing* (right of the
# price -- Amazon F/T, Costco A) or *leading* (left of the price -- Costco's
# tax-exempt 'E' in the far-left margin, Target's NF/T column just left of the
# price). The previous heuristic admitted any 1-2 char alpha token in the
# price-flag column, which let SKU/OCR fragments through (Home Depot 'WD'/'AB',
# Sprouts 'TT'/'I', Target 'NE'). We now gate candidates on three things:
#
#   (a) the token sits in a *consistent* flag column (x-clustered across rows),
#   (b) the token is a known tax-flag code (small curated alphabet below), and
#   (c) the column -- and each char within it -- recurs above a frequency floor.
#
# FLAG_ALPHABET is the small, generic set of US grocery/retail tax codes (NOT
# per-merchant): single-letter taxable/exempt/food markers plus the well-known
# two-letter "non-food" codes. Anything outside it (WD, AB, TT, I, NE, <A>) is
# treated as SKU text or OCR noise and rejected.
FLAG_ALPHABET = {
    "A",  # taxable (Costco) / category A
    "B",  # bottle deposit / category B
    "E",  # tax-exempt (Costco)
    "F",  # food / SNAP-eligible (non-taxable)
    "N",  # non-taxable
    "P",  # taxable category P
    "S",  # taxable / sale
    "T",  # taxable
    "X",  # taxable
    "NF",  # non-food (taxable) -- Target et al.
    "NT",  # non-taxable
}
# Within an accepted flag column, a code must appear on at least this fraction
# of the column's occurrences to be kept. Scaling by the column (not the row
# count) is deliberate: flags are sparse on some merchants (Sprouts prints a
# code on ~28 of 838 item rows), so a row-relative floor would erase a real but
# sparse column. A column-relative floor instead drops in-column one-offs while
# a dominant flag's lone minority partner survives -- Amazon's single 'T' among
# 12 'F' clears ceil(0.05 * 13) = 1, but a stray code in a 28-deep column needs
# ceil(0.05 * 28) = 2 and is dropped.
FLAG_FREQ_FLOOR = 0.05
# A flag column must recur on at least this many rows to be trusted as a real
# column rather than a one-/two-off coincidence. Kept small so a merchant that
# only ever taxes a couple of items still registers its column.
FLAG_MIN_COLUMN_ROWS = 2
# Two flag occurrences belong to the same column when their left-x are within
# this normalized distance. Flag columns across our merchants are >> this apart
# from the name column, and a column's own jitter is well under it.
FLAG_COLUMN_TOL = 0.04
_EPS = 1e-6
# Markdown / "sale now" marker tokens. OCR is noisy: the leading 'n' is also
# read as 'h' (NOW -> HOW) and the 'o' as '0', so we admit the h/0 variants and
# canonicalize the survivor to "now" (see _canonicalize_marker_token). Gating to
# a product row's pre-price slot keeps a stray "HOW" (e.g. survey copy) out.
_MARKER_RE = re.compile(r"^[hn][o0]w$", re.IGNORECASE)
# Truncation markers OCR emits for clipped names ("..." or a clipped "..").
_TRUNC_RE = re.compile(r"\.{2,}$")
# Sub-line signature: a SALE ... WAS ... row beneath a discounted item. OCR
# reads the leading "SALE" as SAVE/SALES/SAVES, so admit those variants; the
# captured template is canonicalized to "SALE 1@ ..." (_canonicalize_subline_template).
_SUBLINE_RE = re.compile(r"\bsa[lv]es?\b.*\bwas\b", re.IGNORECASE)

_PRICE_TOKEN_RE = re.compile(r"\$?\d+\.\d{2}")
_INT_TOKEN_RE = re.compile(r"\b\d+\b")

# ---------------------------------------------------------------------------
# Canonical clean forms for the two known receipt phrases this extractor reads
# VERBATIM off real receipts.
#
# The markdown marker and the sale sub-line template are lifted straight from a
# real receipt's OCR tokens, so they carry that OCR's noise: the "now" marker is
# read as NOW / Now / HOW / N0W, and the "SALE 1@ ..." sub-line drifts to
# SAVE / SALES and loses the "1@" quantity form. Downstream these tokens render
# as supervision-neutral ('O') decorative copy that is NOT corrected by the
# synthesis text-clean pass (and in fact gets mis-"repaired" by it, e.g.
# now->how, SALE->SALES, because "now"/"sale" are edit-distance 1 from common
# receipt-vocabulary words). They are KNOWN, fixed phrases, so we snap the
# extracted text to its canonical clean form HERE, at the source -- a generic
# phrase canonicalization (a tiny variant->canon map / fuzzy match), not
# per-merchant hardcoding. A merchant that genuinely prints neither phrase keeps
# them absent (the present=False default below).
_CANONICAL_MARKER_TOKEN = "now"
# OCR/casing variants of the single "now" sale marker (now/NOW/Now/HOW/N0W/H0W).
_MARKER_CANON_RE = re.compile(r"^[hn][o0]w$", re.IGNORECASE)

_CANONICAL_SUBLINE_TEMPLATE = "SALE {n}@ {price}, WAS: {price} each"
# The sub-line's leading sale keyword as OCR variants: SALE / SAVE / SALES / SAVES.
_SUBLINE_SALE_RE = re.compile(r"\bsa[lv]es?\b", re.IGNORECASE)
_WAS_RE = re.compile(r"\bwas\b", re.IGNORECASE)


def _canonicalize_marker_token(token: str | None) -> str | None:
    """Snap an extracted markdown-marker token to the canonical clean ``now``.

    The marker is the single known receipt word "now"; OCR reads it as NOW / Now /
    HOW / N0W. Map any such variant to lowercase ``now`` and leave a genuinely
    different token untouched so a real, non-"now" marker survives unchanged.
    """
    if not token:
        return token
    return (
        _CANONICAL_MARKER_TOKEN
        if _MARKER_CANON_RE.match(token.strip())
        else token
    )


def _canonicalize_subline_template(template: str | None) -> str | None:
    """Snap an extracted sale sub-line template to the canonical clean phrase.

    Real receipts print ``SALE 1@ $x, WAS: $y each``; the verbatim extraction
    drifts (SALE->SAVE/SALES OCR misread, dropped "@" after the quantity, loose
    spacing). When the extracted template still carries a SALE/SAVE...WAS shape --
    the same signature the sub-line is detected on -- we snap it to the canonical
    form; otherwise (no recognizable shape) we leave it untouched.
    """
    if not template:
        return template
    if _SUBLINE_SALE_RE.search(template) and _WAS_RE.search(template):
        return _CANONICAL_SUBLINE_TEMPLATE
    return template


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------


@dataclass
class TaxFlag:
    """Tax flag specification for an item row.

    The flag can sit on either side of the price. ``position`` is:

    * ``"trailing"`` -- right of the price (Amazon: F/T).
    * ``"leading"``  -- left of the price, whether in the far-left margin
      (Costco's tax-exempt 'E') or a dedicated pre-price column (Target's
      NF/T column just left of the price).
    * ``"mixed"``    -- both occur (Costco: 'A' trailing taxable rows + 'E'
      leading exempt rows).
    """

    present: bool = False
    chars: list[str] = field(default_factory=list)  # distinct, freq-sorted
    char_counts: dict[str, int] = field(default_factory=dict)
    position: str = "none"  # "trailing" | "leading" | "mixed" | "none"
    trailing_same_line: bool = False  # trailing flag shares the price's OCR line
    count: int = 0  # rows where any flag was found
    trailing_count: int = 0
    leading_count: int = 0


@dataclass
class MarkdownMarker:
    """A 'now'/sale marker printed left of the price on discounted rows."""

    present: bool = False
    token: str | None = None  # canonical token, e.g. "NOW"
    count: int = 0


@dataclass
class SubLine:
    """A secondary OCR line beneath the item (SALE 1@ $x, WAS: $y each)."""

    present: bool = False
    template: str | None = None  # numbers/prices replaced with placeholders
    example: str | None = None  # a verbatim instance
    count: int = 0


@dataclass
class NameTruncation:
    """Long-name truncation behavior."""

    present: bool = False
    marker: str = "..."
    max_char_width: int = 0  # approx visible chars before the cut
    truncated_fraction: float = 0.0  # share of item rows truncated


@dataclass
class Columns:
    """Normalized-x medians for the row's columns (non-positive = unmeasured)."""

    name_start_x: float = -1.0  # left edge of the product name
    price_right_x: float = -1.0  # right edge of the price
    flag_x: float = -1.0  # left edge of the tax flag


@dataclass
class ItemLineTemplate:
    """Derived item-line grammar for one merchant."""

    merchant: str
    sample_count: int = 0  # item rows analyzed
    receipt_count: int = 0
    tax_flag: TaxFlag = field(default_factory=TaxFlag)
    markdown_marker: MarkdownMarker = field(default_factory=MarkdownMarker)
    sub_line: SubLine = field(default_factory=SubLine)
    name_truncation: NameTruncation = field(default_factory=NameTruncation)
    columns: Columns = field(default_factory=Columns)
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


# ---------------------------------------------------------------------------
# Geometry helpers (mirror merchant_research.structure)
# ---------------------------------------------------------------------------


def _left_x(word: dict[str, Any]) -> float | None:
    bb = word.get("bounding_box") or {}
    x = bb.get("x")
    if x is not None:
        return float(x)
    tl = word.get("top_left") or {}
    return tl.get("x")


def _right_edge_x(word: dict[str, Any]) -> float | None:
    bb = word.get("bounding_box") or {}
    x = bb.get("x")
    w = bb.get("width")
    if x is None or w is None:
        tr = word.get("top_right") or {}
        return tr.get("x")
    return float(x) + float(w)


def _center_y(word: dict[str, Any]) -> float | None:
    bb = word.get("bounding_box") or {}
    y = bb.get("y")
    h = bb.get("height")
    if y is None or h is None:
        tl = word.get("top_left") or {}
        return tl.get("y")
    return float(y) + float(h) / 2.0


def _median(values: Iterable[float]) -> float:
    vals = [v for v in values if v is not None]
    return statistics.median(vals) if vals else -1.0


# ---------------------------------------------------------------------------
# Core extraction
# ---------------------------------------------------------------------------


def _word_key(rec: dict[str, Any]) -> tuple:
    # Include image_id: an export bundles several receipts, each with its own
    # (receipt_id, line_id, word_id) namespace.
    return (
        rec.get("image_id"),
        rec.get("receipt_id"),
        rec.get("line_id"),
        rec.get("word_id"),
    )


def _line_key(rec: dict[str, Any]) -> tuple:
    return (rec.get("image_id"), rec.get("receipt_id"), rec.get("line_id"))


def _templatize(text: str) -> str:
    """Replace concrete prices/quantities with placeholders for a stable template."""
    out = _PRICE_TOKEN_RE.sub("{price}", text)
    out = _INT_TOKEN_RE.sub("{n}", out)
    return re.sub(r"\s+", " ", out).strip()


def extract_item_line_template(
    merchant_receipts: dict[str, Any],
) -> ItemLineTemplate:
    """Derive an :class:`ItemLineTemplate` from one merchant's labeled export.

    ``merchant_receipts`` is the export dict (keys ``receipt_words``,
    ``receipt_word_labels``, optionally ``receipt_lines`` / ``merchant_name`` /
    ``receipts``). Pure: reads only the passed-in data.
    """
    merchant = str(merchant_receipts.get("merchant_name") or "").strip()
    words: Sequence[dict[str, Any]] = merchant_receipts.get("receipt_words") or []
    labels: Sequence[dict[str, Any]] = (
        merchant_receipts.get("receipt_word_labels") or []
    )
    lines: Sequence[dict[str, Any]] = merchant_receipts.get("receipt_lines") or []

    # word_key -> normalized label
    label_of: dict[tuple, str] = {}
    for lab in labels:
        name = str(lab.get("label") or "").strip().upper()
        if name:
            label_of[_word_key(lab)] = name

    # Index words by receipt (image_id, receipt_id) and by line.
    words_by_receipt: dict[tuple, list[dict]] = defaultdict(list)
    words_by_line: dict[tuple, list[dict]] = defaultdict(list)
    for w in words:
        words_by_receipt[(w.get("image_id"), w.get("receipt_id"))].append(w)
        words_by_line[_line_key(w)].append(w)

    receipt_ids = set(words_by_receipt.keys())

    # Line text lookup (for sub-line scanning).
    line_text: dict[tuple, str] = {}
    for ln in lines:
        line_text[_line_key(ln)] = str(ln.get("text") or "")

    # ------------------------------------------------------------------
    # Find product-name lines and build item rows by y-proximity.
    # ------------------------------------------------------------------
    name_line_keys: set[tuple] = set()
    for w in words:
        if label_of.get(_word_key(w)) in NAME_LABELS:
            name_line_keys.add(_line_key(w))

    # Accumulators.
    name_start_xs: list[float] = []
    name_word_xs: list[float] = []  # left-x of every name word (column-density guard)
    price_right_xs: list[float] = []
    # Flag candidates: every alphabet-matching token sitting on one side of the
    # price, tagged with its row, x, and whether it shares the price's OCR line.
    # Column clustering + the frequency floor are applied afterwards.
    left_flag_cands: list[_FlagCand] = []  # left of the price
    right_flag_cands: list[_FlagCand] = []  # right of the price
    marker_counter: Counter = Counter()
    marker_rows = 0
    truncated_names = 0
    truncated_widths: list[int] = []
    item_rows = 0

    for lk in name_line_keys:
        img, rid, _lid = lk
        row_words = _collect_row(lk, words_by_line, words_by_receipt)
        if not row_words:
            continue

        # Product-name words in this row (only on the anchor line).
        name_words = [
            w
            for w in words_by_line[lk]
            if label_of.get(_word_key(w)) in NAME_LABELS
        ]
        if not name_words:
            continue
        item_rows += 1

        # name start x (left edge of leftmost name word)
        nstart = min(
            (_left_x(w) for w in name_words if _left_x(w) is not None),
            default=None,
        )
        if nstart is not None:
            name_start_xs.append(nstart)
        name_word_xs.extend(
            x for x in (_left_x(w) for w in name_words) if x is not None
        )

        # Reconstruct the visible product name (sorted by x) for truncation.
        name_words_sorted = sorted(
            name_words, key=lambda w: (_left_x(w) or 0.0)
        )
        name_text = " ".join(str(w.get("text") or "") for w in name_words_sorted)
        if _TRUNC_RE.search(name_text.strip()):
            truncated_names += 1
            visible = _TRUNC_RE.sub("", name_text).strip()
            truncated_widths.append(len(visible))

        # Price = rightmost priced word in the row.
        price_words = [
            w for w in row_words if label_of.get(_word_key(w)) in PRICE_LABELS
        ]
        price_word = None
        if price_words:
            price_word = max(
                price_words, key=lambda w: (_right_edge_x(w) or 0.0)
            )
            pr = _right_edge_x(price_word)
            if pr is not None:
                price_right_xs.append(pr)

        # Markdown marker anywhere in the row, left of the price.
        price_left = _left_x(price_word) if price_word is not None else None
        found_marker = False
        for w in row_words:
            txt = str(w.get("text") or "").strip()
            if _MARKER_RE.match(txt):
                lx = _left_x(w)
                if price_left is None or (lx is not None and lx < price_left):
                    marker_counter[txt.upper()] += 1
                    found_marker = True
        if found_marker:
            marker_rows += 1

        # Tax flag(s): collect every known tax-code token (FLAG_ALPHABET) in
        # the row and bucket it by side of the price. A token is TRAILING when
        # it sits right of the price's right edge and LEADING when it sits fully
        # left of the price's left edge. We do NOT pick a single token per row
        # here -- the real flag column is recovered later by x-clustering, which
        # is robust to a stray name word that happens to match the alphabet (it
        # lands in the name column, not the flag column).
        if price_word is not None:
            pr_edge = _right_edge_x(price_word) or 0.0
            pr_left = _left_x(price_word)
            pr_left = pr_left if pr_left is not None else pr_edge
            same_line_id = price_word.get("line_id")
            for w in row_words:
                if w is price_word:
                    continue
                txt = str(w.get("text") or "").strip()
                if _MARKER_RE.match(txt) or txt.upper() not in FLAG_ALPHABET:
                    continue
                lx = _left_x(w)
                if lx is None:
                    continue
                same_line = w.get("line_id") == same_line_id
                if lx >= pr_edge - _EPS:  # right of the price -> trailing
                    right_flag_cands.append(
                        _FlagCand(lk, txt.upper(), lx, same_line)
                    )
                elif (_right_edge_x(w) or lx) <= pr_left + _EPS:  # left -> leading
                    left_flag_cands.append(
                        _FlagCand(lk, txt.upper(), lx, same_line)
                    )

    # ------------------------------------------------------------------
    # Sub-line: scan all line texts for the SALE ... WAS ... signature.
    # ------------------------------------------------------------------
    subline_examples: list[str] = []
    subline_templates: Counter = Counter()
    for lk, txt in line_text.items():
        if lk[0] is not None and (lk[0], lk[1]) not in receipt_ids:
            continue
        if _SUBLINE_RE.search(txt):
            subline_examples.append(txt.strip())
            subline_templates[_templatize(txt)] += 1
    # Fall back to reconstructing from words if receipt_lines were absent.
    if not subline_examples and not lines:
        for (img, rid), rwords in words_by_receipt.items():
            by_line: dict[tuple, list[dict]] = defaultdict(list)
            for w in rwords:
                by_line[_line_key(w)].append(w)
            for lk, lw in by_line.items():
                lw_sorted = sorted(lw, key=lambda w: (_left_x(w) or 0.0))
                txt = " ".join(str(w.get("text") or "") for w in lw_sorted)
                if _SUBLINE_RE.search(txt):
                    subline_examples.append(txt.strip())
                    subline_templates[_templatize(txt)] += 1

    # ------------------------------------------------------------------
    # Assemble result.
    # ------------------------------------------------------------------
    tmpl = ItemLineTemplate(merchant=merchant)
    tmpl.sample_count = item_rows
    tmpl.receipt_count = len(receipt_ids)

    # tax flag. Recover the consistent flag column on each side of the price by
    # x-clustering the candidates, then keep only columns/chars that clear the
    # frequency floor. This rejects SKU-like multi-char tokens and OCR noise
    # (they are not in FLAG_ALPHABET) and rare one-offs (they fall below the
    # floor), while preserving a dominant flag's lone minority partner.
    trailing = _choose_flag_column(right_flag_cands, name_word_xs)
    leading = _choose_flag_column(left_flag_cands, name_word_xs)

    if trailing is not None or leading is not None:
        combined: Counter = Counter()
        if trailing is not None:
            combined += trailing.char_counts
        if leading is not None:
            combined += leading.char_counts
        chars = [c for c, _ in combined.most_common()]
        trailing_count = trailing.row_count if trailing is not None else 0
        leading_count = leading.row_count if leading is not None else 0
        if trailing is not None and leading is not None:
            position = "mixed"
        elif trailing is not None:
            position = "trailing"
        else:
            position = "leading"
        tmpl.tax_flag = TaxFlag(
            present=True,
            chars=chars,
            char_counts=dict(combined),
            position=position,
            trailing_same_line=(
                trailing is not None
                and trailing.same_line_count >= (trailing.occurrences / 2.0)
            ),
            count=trailing_count + leading_count,
            trailing_count=trailing_count,
            leading_count=leading_count,
        )
        # flag_x: the trailing column if present, else the leading column.
        flag_col_x = trailing.col_x if trailing is not None else leading.col_x
    else:
        flag_col_x = -1.0

    # markdown marker. The chosen token is read verbatim off real receipts and
    # carries OCR noise (NOW/HOW casing/misreads), so snap it to canonical "now".
    if marker_rows:
        token = _canonicalize_marker_token(marker_counter.most_common(1)[0][0])
        tmpl.markdown_marker = MarkdownMarker(
            present=True, token=token, count=marker_rows
        )

    # sub-line. The chosen template is also verbatim OCR copy (SALE->SAVE/SALES,
    # dropped "1@" form), so snap it to the canonical "SALE 1@ ..., WAS: ... each".
    if subline_examples:
        template = _canonicalize_subline_template(
            subline_templates.most_common(1)[0][0]
        )
        tmpl.sub_line = SubLine(
            present=True,
            template=template,
            example=subline_examples[0],
            count=len(subline_examples),
        )

    # name truncation
    if truncated_names:
        tmpl.name_truncation = NameTruncation(
            present=True,
            marker="...",
            max_char_width=max(truncated_widths) if truncated_widths else 0,
            truncated_fraction=round(truncated_names / item_rows, 3)
            if item_rows
            else 0.0,
        )

    # columns
    tmpl.columns = Columns(
        name_start_x=round(_median(name_start_xs), 4),
        price_right_x=round(_median(price_right_xs), 4),
        flag_x=round(flag_col_x, 4) if flag_col_x > 0 else -1.0,
    )

    tmpl.confidence = _confidence(tmpl)
    return tmpl


@dataclass
class _FlagCand:
    """A single alphabet-matching token sitting on one side of a row's price."""

    row: tuple  # name-line key, used to count distinct rows
    char: str  # uppercased token
    x: float  # left-x, used for column clustering
    same_line: bool  # shares the price word's OCR line


@dataclass
class _FlagColumn:
    """The accepted flag column on one side of the price (post floor/filter)."""

    char_counts: Counter
    row_count: int  # distinct rows carrying a kept flag
    occurrences: int  # total kept flag occurrences
    same_line_count: int
    col_x: float  # median left-x of the column


def _choose_flag_column(
    cands: list[_FlagCand], name_word_xs: Sequence[float]
) -> _FlagColumn | None:
    """Recover a consistent tax-flag column from one side's candidates.

    Clusters candidates by x, picks the column that recurs on the most rows
    (tie-break: closest to the price), and enforces the floor at both the column
    level (a real column must recur on >= FLAG_MIN_COLUMN_ROWS rows) and the
    char level (a code must clear FLAG_FREQ_FLOOR of the column's occurrences).
    A final density guard rejects a "column" that is really product-name text:
    if name words within the column's x-band outnumber the flag occurrences, the
    band belongs to the name (Home Depot, whose broken labels scatter stray
    alphabet tokens through the description column). Returns ``None`` when
    nothing survives -- i.e. the side carries only stray/noise tokens.
    """
    if not cands:
        return None

    # Single-linkage cluster by x (columns are >> FLAG_COLUMN_TOL apart).
    cands_sorted = sorted(cands, key=lambda c: c.x)
    clusters: list[list[_FlagCand]] = [[cands_sorted[0]]]
    for c in cands_sorted[1:]:
        if c.x - clusters[-1][-1].x <= FLAG_COLUMN_TOL:
            clusters[-1].append(c)
        else:
            clusters.append([c])

    # A real flag column recurs on at least FLAG_MIN_COLUMN_ROWS rows.
    row_floor = FLAG_MIN_COLUMN_ROWS
    best: list[_FlagCand] | None = None
    best_key: tuple | None = None
    for cl in clusters:
        rows = {c.row for c in cl}
        if len(rows) < row_floor:
            continue
        # Prefer the column on the most rows; break ties toward the price.
        key = (len(rows), statistics.median([c.x for c in cl]))
        if best_key is None or key > best_key:
            best, best_key = cl, key
    if best is None:
        return None

    # Within the column, each code must clear the floor (drops rare one-offs).
    char_floor = max(1, math.ceil(FLAG_FREQ_FLOOR * len(best)))
    raw = Counter(c.char for c in best)
    kept_chars = {ch for ch, n in raw.items() if n >= char_floor}
    kept = [c for c in best if c.char in kept_chars]
    if not kept:
        return None

    # Density guard: if name words crowd this x-band more than the flags do, the
    # band is product-name text, not a flag column.
    col_x = statistics.median([c.x for c in kept])
    name_in_band = sum(
        1 for x in name_word_xs if abs(x - col_x) <= FLAG_COLUMN_TOL
    )
    if name_in_band >= len(kept):
        return None

    return _FlagColumn(
        char_counts=Counter(c.char for c in kept),
        row_count=len({c.row for c in kept}),
        occurrences=len(kept),
        same_line_count=sum(1 for c in kept if c.same_line),
        col_x=col_x,
    )


def _collect_row(
    name_line_key: tuple,
    words_by_line: dict[tuple, list[dict]],
    words_by_receipt: dict[tuple, list[dict]],
) -> list[dict]:
    """All words within ``ROW_Y_TOLERANCE`` of the name line's center y."""
    img, rid, _lid = name_line_key
    anchor = words_by_line.get(name_line_key) or []
    centers = [c for c in (_center_y(w) for w in anchor) if c is not None]
    if not centers:
        return []
    anchor_y = statistics.median(centers)
    row: list[dict] = []
    for w in words_by_receipt.get((img, rid), []):
        cy = _center_y(w)
        if cy is not None and abs(cy - anchor_y) <= ROW_Y_TOLERANCE:
            row.append(w)
    return row


def _confidence(tmpl: ItemLineTemplate) -> float:
    """Heuristic 0-1 confidence: grows with sample size, boosted by a measured
    price column (the structural anchor every other feature hangs off of)."""
    if tmpl.sample_count <= 0:
        return 0.0
    base = min(1.0, tmpl.sample_count / 20.0)
    if tmpl.columns.price_right_x <= 0:
        base *= 0.5
    return round(base, 2)


# ---------------------------------------------------------------------------
# Loading / CLI
# ---------------------------------------------------------------------------


def _exports_dir() -> str:
    return os.path.expanduser(
        os.environ.get("SYNTH_BATCH_DIR", "~/synth-batch/exports")
    )


def load_merchant_export(slug: str) -> dict[str, Any]:
    """Load a merchant export by slug from the synth-batch exports directory."""
    path = slug
    if not os.path.isfile(path):
        path = os.path.join(_exports_dir(), f"{slug}.json")
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Derive a merchant's item-line grammar from labeled receipts."
    )
    parser.add_argument(
        "slug",
        help="merchant slug (e.g. amazon_fresh) or path to an export JSON",
    )
    args = parser.parse_args(argv)

    export = load_merchant_export(args.slug)
    template = extract_item_line_template(export)
    print(template.to_json())
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
