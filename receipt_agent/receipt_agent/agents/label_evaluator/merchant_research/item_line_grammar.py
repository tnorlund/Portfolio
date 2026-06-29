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

# A trailing tax flag is a 1-2 char alphabetic token right of the price
# (Amazon F/T, Costco A, occasional NF). A *leading* margin flag (Costco's
# tax-exempt 'E' in the far-left column) is restricted to a single letter to
# avoid mistaking stray 2-char OCR fragments in the margin for a flag.
_FLAG_RE = re.compile(r"^[A-Za-z]{1,2}$")
_LEADING_FLAG_RE = re.compile(r"^[A-Za-z]$")
# Markdown / "sale now" marker tokens (OCR is noisy: NOW / now / nOW ...).
_MARKER_RE = re.compile(r"^n[o0]w$", re.IGNORECASE)
# Truncation markers OCR emits for clipped names ("..." or a clipped "..").
_TRUNC_RE = re.compile(r"\.{2,}$")
# Sub-line signature: a SALE ... WAS ... row beneath a discounted item.
_SUBLINE_RE = re.compile(r"\bSALE\b.*\bWAS\b", re.IGNORECASE)

_PRICE_TOKEN_RE = re.compile(r"\$?\d+\.\d{2}")
_INT_TOKEN_RE = re.compile(r"\b\d+\b")


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------


@dataclass
class TaxFlag:
    """Tax flag specification for an item row.

    Some merchants print one trailing flag (Amazon: F/T right of the price);
    others use a dual scheme (Costco: 'A' trailing taxable rows, 'E' at the
    far-left margin for tax-exempt rows), in which case ``position`` is
    ``"mixed"``.
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
    price_right_xs: list[float] = []
    flag_xs: list[float] = []
    trailing_counter: Counter = Counter()
    leading_counter: Counter = Counter()
    flag_same_line = 0
    flag_trailing_rows = 0  # rows with a flag right of the price
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

        # Tax flag(s): a 1-2 char alpha token. Two placements are possible and
        # may co-exist on the same receipt (Costco): a TRAILING flag just right
        # of the price, and a LEADING flag in the far-left margin (left of the
        # product name -- the numeric item number sits between and is ignored
        # since it is not alphabetic). At most one of each per row (nearest).
        if price_word is not None:
            pr_edge = _right_edge_x(price_word) or 0.0
            name_left = nstart if nstart is not None else 0.0
            trailing = None  # nearest alpha token right of the price
            leading = None  # nearest alpha token left of the product name
            for w in row_words:
                if w is price_word:
                    continue
                txt = str(w.get("text") or "").strip()
                if not _FLAG_RE.match(txt) or _MARKER_RE.match(txt):
                    continue
                lx = _left_x(w)
                if lx is None:
                    continue
                if lx >= pr_edge - 1e-6:  # right of the price -> trailing
                    if trailing is None or lx < (_left_x(trailing) or 1.0):
                        trailing = w
                elif lx < name_left and _LEADING_FLAG_RE.match(txt):
                    # far-left margin, single letter only
                    if leading is None or lx > (_left_x(leading) or -1.0):
                        leading = w

            if trailing is not None:
                flag_trailing_rows += 1
                trailing_counter[str(trailing.get("text") or "").upper()] += 1
                fx = _left_x(trailing)
                if fx is not None:
                    flag_xs.append(fx)
                if trailing.get("line_id") == price_word.get("line_id"):
                    flag_same_line += 1
            if leading is not None:
                leading_counter[str(leading.get("text") or "").upper()] += 1

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

    # tax flag. Trailing flags are trusted even when rare (they sit in the
    # established price-flag column). Leading margin flags are only accepted if
    # they recur on a meaningful share of rows -- a lone far-left letter is OCR
    # noise, not a tax code -- and one-off leading chars are pruned.
    leading_min = max(3, int(round(0.08 * item_rows))) if item_rows else 3
    leading_rows = sum(leading_counter.values())
    if leading_rows >= leading_min:
        accepted_leading = Counter(
            {c: n for c, n in leading_counter.items() if n > 1}
        )
    else:
        accepted_leading = Counter()
    flag_leading_rows = sum(accepted_leading.values())

    combined = trailing_counter + accepted_leading
    if combined:
        chars = [c for c, _ in combined.most_common()]
        if flag_trailing_rows and flag_leading_rows:
            position = "mixed"
        elif flag_trailing_rows:
            position = "trailing"
        else:
            position = "leading"
        tmpl.tax_flag = TaxFlag(
            present=True,
            chars=chars,
            char_counts=dict(combined),
            position=position,
            trailing_same_line=(
                flag_trailing_rows > 0
                and flag_same_line >= (flag_trailing_rows / 2.0)
            ),
            count=flag_trailing_rows + flag_leading_rows,
            trailing_count=flag_trailing_rows,
            leading_count=flag_leading_rows,
        )

    # markdown marker
    if marker_rows:
        token = marker_counter.most_common(1)[0][0]
        tmpl.markdown_marker = MarkdownMarker(
            present=True, token=token, count=marker_rows
        )

    # sub-line
    if subline_examples:
        template = subline_templates.most_common(1)[0][0]
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
        flag_x=round(_median(flag_xs), 4) if flag_xs else -1.0,
    )

    tmpl.confidence = _confidence(tmpl)
    return tmpl


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
