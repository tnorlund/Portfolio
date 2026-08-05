"""A per-unit RATE row is never a line item.

The decoder already knows that an amount qualified by a unit is a rate
rather than an extended price -- ``QTY_AT_RE`` reads "1.23 lb @ 4.99/lb"
and the three-decimal rule keeps fuel's "$5.299/Gal" out of a band's
amounts entirely. The spelled-out form had no reader, so Yogurtland's
weight line reached the decode as a $0.67 item:

    Weight: 21.5 oz 8 $0.67 per oz      <- the "8" is OCR of "@"

That put prod 37f2d81f at ``near`` (15.07) against a printed 14.40 that
its one real item, "1 MED 14.40", hits exactly.

The amount count is the load-bearing half of the guard. A deli row prints
BOTH the rate and the total ("GROUND BEEF $4.99 per lb   7.48"), and there
the 7.48 is a real line total. Only a row whose single amount IS the rate
is an annotation -- which is what lets this ship without a merchant list.

PROVENANCE, so this does not read as a tuned constant: a scan of all
1,421 receipts across dev + prod (2026-08-05) found exactly ONE instance
of this row family -- prod 37f2d81f. The unit spellings below beyond that
row, and the deli-row gate, are extrapolation from n=1. Re-measure with
the corpus drift check rather than by eye::

    python3.12 scripts/backfill_receipt_line_items.py --check \\
        --table ReceiptsTable-d7ff76a

It is read-only (prod-safe) and prints every receipt whose STORED
reconciliation_status disagrees with a live recompute, with sums and
baselines. A second instance of this family shows up there as a drift
row before anyone goes looking; RATE_ROWS is where to add it.
"""

from __future__ import annotations

import pytest

from receipt_upload.line_items.geometry import (
    PER_UNIT_RATE_RE,
    is_unit_rate_row,
)

# The motivating row, plus the unit spellings receipts actually print.
RATE_ROWS = [
    ("Weight: 21.5 oz 8 $0.67 per oz", 1),
    ("$0.67 per oz", 1),
    ("2.99 per lb", 1),
    ("1.49 PER EACH", 1),
    ("$3.20 per kg", 1),
    ("4.99 Per Gal", 1),
]

# Rows that must stay items even though they carry "per <unit>", because
# they also carry an extended total.
RATE_PLUS_TOTAL_ROWS = [
    ("GROUND BEEF $4.99 per lb 7.48", 2),
    ("BANANAS 0.59 per lb 1.77", 2),
]

# "PER" inside a longer word. The corpus sells "PEPPER BELL GREEN EACH",
# and a substring scan would eat it -- the same failure shape as "OFF"
# inside COFFEE.
SUBSTRING_TRAPS = [
    "PEPPER BELL GREEN EACH",
    "PEPPERONI PIZZA",
    "PAPER TOWELS PER PACK",  # "per pack" is not a unit spelling here
    "SUPERIOR OZ BOTTLE",
    "OPERA CAKE EACH",
]


@pytest.mark.parametrize("text,n_amounts", RATE_ROWS)
def test_rate_rows_are_recognized(text: str, n_amounts: int) -> None:
    assert is_unit_rate_row(text, n_amounts), text


@pytest.mark.parametrize("text,n_amounts", RATE_PLUS_TOTAL_ROWS)
def test_a_rate_printed_beside_a_total_stays_an_item(
    text: str, n_amounts: int
) -> None:
    assert not is_unit_rate_row(text, n_amounts), text


@pytest.mark.parametrize("text", SUBSTRING_TRAPS)
def test_per_inside_a_longer_word_does_not_match(text: str) -> None:
    assert not PER_UNIT_RATE_RE.search(text), text
    assert not is_unit_rate_row(text, 1), text


def test_a_row_with_no_amount_is_not_a_rate_row() -> None:
    """Zero amounts is still <= 1, so the text anchor must do real work."""
    assert not is_unit_rate_row("ORGANIC BANANAS", 0)
    assert not is_unit_rate_row("", 0)


def test_the_yogurtland_receipt_decodes_to_its_printed_total() -> None:
    """End to end on the row geometry that motivated the guard.

    Two bands: the real item and the weight annotation below it. Before
    the guard the decode summed 15.07 against a printed 14.40; after, the
    annotation is OUTSIDE and the single real item reconciles exactly.
    """
    from receipt_upload.line_items.geometry import (
        extract_items,
        reconcile_detailed,
    )

    def w(line_id, word_id, text, x, y):
        return {
            "line_id": line_id,
            "word_id": word_id,
            "text": text,
            "x": x,
            "y_mid": y,
            "h": 0.02,
        }

    words = [
        w(17, 1, "1", 0.10, 0.50),
        w(17, 2, "MED", 0.20, 0.50),
        w(19, 1, "14.40", 0.80, 0.50),
        w(20, 1, "Weight:", 0.10, 0.44),
        w(20, 2, "21.5", 0.25, 0.44),
        w(20, 3, "oz", 0.33, 0.44),
        w(20, 4, "8", 0.40, 0.44),
        w(20, 5, "$0.67", 0.48, 0.44),
        w(20, 6, "per", 0.58, 0.44),
        w(20, 7, "oz", 0.64, 0.44),
    ]
    items, _ = extract_items(words, {17, 19, 20})
    recon = reconcile_detailed(items, {"subtotal": 14.40})

    assert [i["price"] for i in items] == [14.40]
    assert recon.status == "match"
