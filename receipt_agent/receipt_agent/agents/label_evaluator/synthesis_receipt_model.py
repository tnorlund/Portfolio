"""synthesis_receipt_model.py -- the single pure CONTENT-reconciliation model.

A synthesized receipt is only realistic if its PRINTED numbers agree with each
other. The add/remove/replace/compose paths each recompute totals locally, which
historically left the candidate internally contradictory:

  * the printed item COUNT ("TOTAL NUMBER OF ITEMS SOLD = N", "Items Sold: N")
    stayed stale after an add/remove, so it disagreed with the visible product
    rows (e.g. ``8852`` items above two product lines);
  * the SUBTOTAL / TAX / GRAND_TOTAL cascade did not reconcile against the
    printed LINE_TOTAL rows (e.g. ``17.40 + 0.65 != 18.13`` or a stray, un-summed
    LINE_TOTAL leaking from a noisy base receipt).

This module is the ONE place that owns that arithmetic. It is deliberately a set
of *pure* functions over the flattened candidate -- ``(tokens, bboxes,
ner_tags)`` parallel arrays, exactly the representation produced by
``_flatten_lines`` / consumed by ``synthesis_reconcile.reconcile_candidate`` --
so a single fix is applied to every merchant and every operation.

Two public entry points:

  * ``reconcile_item_count(tokens, bboxes, ner_tags)`` -- rewrite every printed
    item-count line so its integer equals the number of printed product line
    items (one per ``LINE_TOTAL`` run). Returns the cleaned arrays; asserts the
    post-condition that every line it rewrote now matches.
  * ``validate_receipt_totals(tokens, bboxes, ner_tags)`` -- a non-mutating audit
    returning a ``ReconciliationReport``: whether the item count matches the line
    rows, whether ``subtotal + tax == grand_total``, and whether the subtotal
    equals the summed line totals (net of savings). The add/remove/compose paths
    use ``report.reconciles`` to drop a candidate whose totals still contradict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

# Money tolerance: one cent of rounding slack on each side.
_TOL = Decimal("0.02")

# Labels whose printed amount reduces the subtotal below the summed line totals
# (manufacturer/store coupons, member discounts, markdowns). Mirrors the label
# set the renderer/verifier emit; a token only counts as savings when it parses
# as a real 2-decimal money amount, so a stray ``COUPON``-tagged word ("Store",
# "Q1") never inflates the savings total.
_SAVINGS_LABELS = {
    "DISCOUNT",
    "COUPON",
    "SAVINGS",
    "MEMBER_SAVINGS",
    "MEMBER_SAVING",
    "INSTANT_SAVINGS",
    "MARKDOWN",
    "PROMOTION",
    "PROMO",
}

# Item-COUNT summary phrases, matched against a visual line's joined text with
# underscores normalized to spaces. Phrase-based (not a bare "number"/"count"
# keyword) so an item/transaction IDENTIFIER ("ITEM NUMBER 12345") is never
# mistaken for a counter. Byte-compatible with merchant_synthesis._ITEM_COUNT_PHRASES.
_ITEM_COUNT_PHRASES = (
    "items sold",
    "item sold",
    "number of items",
    "number of item",
    "total items",
    "total item",
    "item count",
    "count of items",
    "items purchased",
    "no. of items",
    "# of items",
    "qty sold",
)

_INT_RE = re.compile(r"^\d+$")
_MONEY_RE = re.compile(r"-?\d+\.\d{2}")


# --- money / label helpers ------------------------------------------------------
def _parse_money(value: object) -> Decimal | None:
    """Parse a printed money token to Decimal, or None when it is not money.

    Requires an explicit 2-decimal cents group so integers / ids / quantities
    (``3``, ``8852``) are never read as money amounts.
    """
    text = str(value or "").strip().upper()
    if not text:
        return None
    text = text.replace("USD$", "").replace("$", "").replace(",", "")
    text = text.rstrip("T")  # trailing taxable flag, e.g. "1.99T"
    match = _MONEY_RE.search(text)
    if not match:
        return None
    try:
        return Decimal(match.group(0))
    except InvalidOperation:
        return None


def _label(tag: object) -> str:
    tag = str(tag or "O")
    if tag == "O":
        return "O"
    return tag.split("-", 1)[-1]


def _entity_runs(tags: list[str]) -> list[tuple[str, int, int]]:
    """Contiguous BIO spans -> (label, start, end_inclusive).

    A same-label token that starts a fresh ``B-`` begins a NEW run, so two
    adjacent line totals are counted as two items (matches the verifier's run
    semantics in synthesis_reconcile._verifier_entity_runs).
    """
    runs: list[tuple[str, int, int]] = []
    i, n = 0, len(tags)
    while i < n:
        lab = _label(tags[i])
        if lab == "O":
            i += 1
            continue
        j = i
        while (
            j + 1 < n
            and _label(tags[j + 1]) == lab
            and not str(tags[j + 1]).startswith("B-")
        ):
            j += 1
        runs.append((lab, i, j))
        i = j + 1
    return runs


def _run_value(tokens: list[str], start: int, end: int) -> Decimal | None:
    return _parse_money(" ".join(str(tokens[k]) for k in range(start, end + 1)))


def _cy(bbox: object) -> float | None:
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        return (float(bbox[1]) + float(bbox[3])) / 2.0
    return None


# --- totals extraction ----------------------------------------------------------
@dataclass
class ReceiptTotals:
    """The reconcilable money/count fields parsed from a flattened candidate."""

    line_totals: list[Decimal] = field(default_factory=list)
    savings: list[Decimal] = field(default_factory=list)
    subtotal: Decimal | None = None
    tax: Decimal = Decimal("0.00")
    has_tax_label: bool = False
    grand_total: Decimal | None = None

    @property
    def line_item_count(self) -> int:
        return len(self.line_totals)

    @property
    def line_sum(self) -> Decimal:
        return sum(self.line_totals, Decimal("0.00"))

    @property
    def savings_sum(self) -> Decimal:
        return sum(self.savings, Decimal("0.00"))

    @property
    def effective_subtotal(self) -> Decimal | None:
        """Labeled SUBTOTAL, else reconstructed from grand_total - tax.

        Many real receipts (Vons/Sprouts exports) print TAX + GRAND_TOTAL but no
        SUBTOTAL line; deriving it lets the line-sum check still run.
        """
        if self.subtotal is not None:
            return self.subtotal
        if self.grand_total is not None:
            return self.grand_total - self.tax
        return None


def extract_totals(tokens: list[str], ner_tags: list[str]) -> ReceiptTotals:
    """Parse the printed LINE_TOTAL / SUBTOTAL / TAX / GRAND_TOTAL / savings fields."""
    totals = ReceiptTotals()
    for lab, start, end in _entity_runs(ner_tags):
        value = _run_value(tokens, start, end)
        if value is None:
            continue
        if lab == "LINE_TOTAL":
            totals.line_totals.append(value)
        elif lab == "SUBTOTAL":
            # First labeled subtotal wins (a footer may echo it).
            if totals.subtotal is None:
                totals.subtotal = value
        elif lab == "TAX":
            totals.tax += value
            totals.has_tax_label = True
        elif lab == "GRAND_TOTAL":
            if totals.grand_total is None:
                totals.grand_total = value
        elif lab in _SAVINGS_LABELS:
            totals.savings.append(value)
    return totals


# --- item-count reconciliation --------------------------------------------------
def _visual_line_indices(bboxes: list[object]) -> list[list[int]]:
    """Group token indices into top->bottom visual lines by y-center proximity."""
    centers: list[tuple[float, int]] = []
    for i, b in enumerate(bboxes):
        cy = _cy(b)
        if cy is not None:
            centers.append((cy, i))
    centers.sort(reverse=True)
    lines: list[list[int]] = []
    cur: list[int] = []
    last: float | None = None
    for cy, i in centers:
        if last is None or abs(cy - last) <= 8:
            cur.append(i)
        else:
            lines.append(cur)
            cur = [i]
        last = cy
    if cur:
        lines.append(cur)
    return lines


def _line_count_target_index(
    tokens: list[str], ordered: list[int]
) -> int | None:
    """Index (within the token array) of the item-count integer on this line.

    The line names an item count ("... ITEMS SOLD = 8852", "Items Sold: 3") and
    the count is the first standalone integer AFTER the phrase, ignoring a stray
    ``=`` / ``:`` separator. Register/terminal numbers that PRECEDE the phrase
    ("705 Items Sold: 3") are skipped because the scan starts after the phrase.
    """
    norm = [str(tokens[i]).lower().replace("_", " ").strip(": ") for i in ordered]
    line_text = " ".join(norm)
    if not any(phrase in line_text for phrase in _ITEM_COUNT_PHRASES):
        return None
    for pos in range(len(ordered)):
        for span in (1, 2, 3, 4):
            phrase = " ".join(norm[pos : pos + span]).strip()
            if phrase in _ITEM_COUNT_PHRASES:
                for k in range(pos + span, min(len(ordered), pos + span + 5)):
                    candidate = str(tokens[ordered[k]]).strip().strip(":")
                    if _INT_RE.match(candidate):
                        return ordered[k]
                return None
    return None


def reconcile_item_count(
    tokens: list[str],
    bboxes: list[list[int]],
    ner_tags: list[str],
) -> tuple[list[str], list[list[int]], list[str]]:
    """Rewrite every printed item-count line to the visible product-line count.

    The number of product line items is the number of ``LINE_TOTAL`` runs (one
    priced product row each). Each item-count summary line is rewritten so its
    integer equals that count, so a printed "items sold" can never disagree with
    the visible rows. No-op (count left untouched) when the receipt has zero
    labeled line totals -- we never stamp "0 items" on a receipt whose rows simply
    were not LINE_TOTAL-labeled.

    Asserts the post-condition: every line it rewrote now reads the line count.
    """
    tokens = list(tokens)
    totals = extract_totals(tokens, ner_tags)
    line_count = totals.line_item_count
    if line_count <= 0:
        return tokens, bboxes, ner_tags

    rewritten: list[int] = []
    for ordered in _visual_line_indices(bboxes):
        ordered = sorted(ordered, key=lambda i: (_cy(bboxes[i]) or 0, bboxes[i][0]))
        target = _line_count_target_index(tokens, ordered)
        if target is not None and str(tokens[target]).strip() != str(line_count):
            tokens[target] = str(line_count)
            rewritten.append(target)

    # Hard post-condition: nothing we touched is left stale.
    for target in rewritten:
        assert str(tokens[target]).strip() == str(line_count), (
            "item-count reconcile post-condition violated: "
            f"token[{target}]={tokens[target]!r} != {line_count}"
        )
    return tokens, bboxes, ner_tags


# --- totals validation ----------------------------------------------------------
@dataclass
class ReconciliationReport:
    """Audit of a candidate's internal CONTENT consistency."""

    reconciles: bool
    violations: list[str]
    line_item_count: int
    item_counts_printed: list[int]
    subtotal: str | None
    tax: str
    grand_total: str | None
    line_sum: str
    savings_sum: str

    def to_dict(self) -> dict[str, object]:
        return {
            "reconciles": self.reconciles,
            "violations": list(self.violations),
            "line_item_count": self.line_item_count,
            "item_counts_printed": list(self.item_counts_printed),
            "subtotal": self.subtotal,
            "tax": self.tax,
            "grand_total": self.grand_total,
            "line_sum": self.line_sum,
            "savings_sum": self.savings_sum,
        }


def _printed_item_counts(
    tokens: list[str], bboxes: list[list[int]]
) -> list[int]:
    counts: list[int] = []
    for ordered in _visual_line_indices(bboxes):
        ordered = sorted(ordered, key=lambda i: (_cy(bboxes[i]) or 0, bboxes[i][0]))
        target = _line_count_target_index(tokens, ordered)
        if target is not None:
            counts.append(int(str(tokens[target]).strip()))
    return counts


def validate_receipt_totals(
    tokens: list[str],
    bboxes: list[list[int]],
    ner_tags: list[str],
) -> ReconciliationReport:
    """Audit the printed item-count + totals cascade for internal consistency.

    Violations (any one => ``reconciles is False``):
      * ``item_count`` -- a printed item-count line disagrees with the number of
        product line items.
      * ``sub_tax_grand`` -- a labeled SUBTOTAL is present and
        ``|subtotal + tax - grand_total| > 0.02``.
      * ``sub_eq_lines`` -- the printed product rows sum to MORE than the
        subtotal accounts for: ``sum(line_totals) - savings > subtotal + 0.02``
        (using the labeled subtotal, else grand_total - tax). This is the
        contradiction the realism analysis flags -- a stray, un-summed LINE_TOTAL
        leaking from a noisy base, or a subtotal left stale after an edit. It is
        deliberately ONE-SIDED: a receipt that labels only some of its rows
        (``line_sum < subtotal``) is under-labeled, not contradictory, and the
        printed subtotal legitimately totals the unlabeled rows too.

    Checks only fire when the needed fields are printed, so a candidate that
    simply omits a field (e.g. an unlabeled base TAX) is not failed for a field
    it never claims.
    """
    totals = extract_totals(tokens, ner_tags)
    violations: list[str] = []

    printed_counts = _printed_item_counts(tokens, bboxes)
    if printed_counts and any(c != totals.line_item_count for c in printed_counts):
        violations.append("item_count")

    if totals.subtotal is not None and totals.grand_total is not None:
        if abs(totals.subtotal + totals.tax - totals.grand_total) > _TOL:
            violations.append("sub_tax_grand")

    eff_subtotal = totals.effective_subtotal
    if eff_subtotal is not None and totals.line_totals:
        net_sum = totals.line_sum - totals.savings_sum
        if net_sum - eff_subtotal > _TOL:
            violations.append("sub_eq_lines")

    return ReconciliationReport(
        reconciles=not violations,
        violations=violations,
        line_item_count=totals.line_item_count,
        item_counts_printed=printed_counts,
        subtotal=(str(totals.subtotal) if totals.subtotal is not None else None),
        tax=str(totals.tax),
        grand_total=(
            str(totals.grand_total) if totals.grand_total is not None else None
        ),
        line_sum=str(totals.line_sum),
        savings_sum=str(totals.savings_sum),
    )


def reconcile_and_validate(
    tokens: list[str],
    bboxes: list[list[int]],
    ner_tags: list[str],
) -> tuple[list[str], list[list[int]], list[str], ReconciliationReport]:
    """Reconcile the item count then audit the totals cascade.

    Returns the (possibly count-corrected) arrays plus the post-reconcile report.
    Item-count violations are repaired in place; totals violations are reported
    (the caller decides whether to drop the candidate).
    """
    tokens, bboxes, ner_tags = reconcile_item_count(tokens, bboxes, ner_tags)
    report = validate_receipt_totals(tokens, bboxes, ner_tags)
    # The reconcile pass repairs the count when it CAN (a rewritable count token
    # exists and there are line rows). When it can't (no rewritable count token,
    # zero line rows, an ambiguous count), the residual `item_count` violation is
    # left IN THE REPORT for the caller to drop the candidate -- never asserted,
    # because a hard assert here crashes the whole synthesis pipeline on a single
    # un-reconcilable receipt instead of just rejecting that one candidate.
    return tokens, bboxes, ner_tags, report
