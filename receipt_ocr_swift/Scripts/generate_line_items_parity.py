#!/usr/bin/env python3
"""Generate the Swift line-item decoder parity expectations.

Runs the CANONICAL Python decoder over the canonical golden OCR fixture
(``receipt_upload/tests/fixtures/line_items_golden_ocr.json``) and dumps the
values the Swift port must reproduce exactly.

The original port (#1313) committed the expectations without committing the
generator, so the fixture silently froze while Python gained the non-product
band filter (#1320), the printed-total fallback (#1321), the three-figure
reconciliation baseline (#1324) and the zone-gap boundary rules (#1329) --
"33/33 parity" then proved agreement with a Python that no longer existed.
This script is the single source of the expectations, and
``receipt_upload/tests/test_swift_line_item_parity_fixture.py`` regenerates
and diffs it on every CI run so the snapshot can never freeze again.

Per receipt the expectations carry:

``items``
    ``extract_items(words, items_line_ids)`` -- the unfiltered decode, the
    same call the Python golden regression gate makes.
``summary`` / ``items_with_summary``
    the decode WITH a summary, which activates
    ``blocks.filter_summary_figure_items`` (#1320). The summary is built
    from the hand-labeled ``printed_subtotal`` / ``printed_total`` in
    ``line_items_golden.json``.
``reconcile``
    ``geometry.reconcile_detailed`` over the non-discount filtered items:
    status plus the #1324 ``baseline_source`` / ``baseline_figures_agreeing``
    grade.
``printed``
    ``find_printed_grand_total`` / ``find_printed_subtotal`` (#1321) over
    the fixture's words, which pins the shared summary-keyword vocabulary
    (including ``TENDER_KEYWORD_RE``).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Optional

from receipt_dynamo.entities.receipt_summary import (
    find_printed_grand_total,
    find_printed_subtotal,
)
from receipt_upload.line_items.geometry import (
    extract_items,
    propose_items_boundary_extension,
    reconcile_detailed,
)

SCRIPT_DIR = Path(__file__).resolve().parent
PACKAGE_DIR = SCRIPT_DIR.parent
REPO_ROOT = PACKAGE_DIR.parent

DEFAULT_OCR = (
    REPO_ROOT / "receipt_upload/tests/fixtures/line_items_golden_ocr.json"
)
DEFAULT_GOLDEN = (
    REPO_ROOT / "receipt_upload/tests/fixtures/line_items_golden.json"
)
FIXTURES_DIR = PACKAGE_DIR / "Tests/ReceiptOCRCoreTests/Fixtures"
DEFAULT_OUTPUT = FIXTURES_DIR / "line_items_parity_expected.json"
GUARD_OUTPUT = FIXTURES_DIR / "line_items_guard_parity_expected.json"
SWIFT_OCR_COPY = FIXTURES_DIR / "line_items_golden_ocr.json"


class FixtureWord:
    """Minimal ReceiptWord facade for the printed-total fallback.

    ``find_printed_*`` reads ``text`` / ``line_id`` / ``word_id`` and a
    normalized ``bounding_box``; the compact fixture schema stores the
    y-CENTER and height, so the box is reconstructed as (y_mid - h/2, h).
    Width is unused by the anchor logic and is left at zero.
    """

    def __init__(self, raw: dict) -> None:
        self.line_id = int(raw["line_id"])
        self.word_id = int(raw["word_id"])
        self.text = str(raw["text"])
        self.bounding_box = {
            "x": float(raw["x"]),
            "y": float(raw["y_mid"]) - float(raw["h"]) / 2.0,
            "width": 0.0,
            "height": float(raw["h"]),
        }


def _money(text: Any) -> Optional[float]:
    if text is None:
        return None
    cleaned = str(text).replace("$", "").replace(",", "").strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def build_summary(golden: Optional[dict]) -> Optional[dict]:
    """Summary dict from the hand-labeled golden truth, or None.

    The golden truth records ``printed_subtotal`` / ``printed_total`` but no
    printed tax, so ``tax`` is always None -- exactly the shape ingest passes
    when a receipt has a subtotal and a total but no isolated tax label.
    """
    if not golden:
        return None
    subtotal = _money(golden.get("printed_subtotal"))
    grand = _money(golden.get("printed_total"))
    if subtotal is None and grand is None:
        return None
    return {"subtotal": subtotal, "tax": None, "grand_total": grand}


def dump_item(item: dict) -> dict:
    return {
        "name": item.get("name"),
        "price": item.get("price"),
        "quantity": item.get("quantity"),
        "unit_price": item.get("unit_price"),
        "is_discount": bool(item.get("is_discount")),
        "name_quality": item.get("name_quality"),
        "line_ids": list(item.get("line_ids") or []),
    }


def build_expectations(ocr_fixture: dict, golden_fixture: dict) -> list[dict]:
    golden_by_key = {
        (r["image_id"], r["receipt_id"]): r for r in golden_fixture["receipts"]
    }
    expected: list[dict] = []
    for receipt in ocr_fixture["receipts"]:
        words = receipt["words"]
        zone = set(receipt["items_line_ids"])
        golden = golden_by_key.get(
            (receipt["image_id"], receipt["receipt_id"])
        )
        summary = build_summary(golden)

        items, _ = extract_items(words, zone)
        filtered, _ = extract_items(words, zone, summary=summary)
        result = reconcile_detailed(
            [item for item in filtered if not item.get("is_discount")],
            summary,
        )
        facade = [FixtureWord(raw) for raw in words]

        expected.append(
            {
                "image_id": receipt["image_id"],
                "receipt_id": receipt["receipt_id"],
                "merchant": receipt.get("merchant"),
                "items": [dump_item(i) for i in items],
                "summary": summary,
                "items_with_summary": [dump_item(i) for i in filtered],
                "reconcile": {
                    "status": result.status,
                    "item_sum": result.item_sum,
                    "baseline": result.baseline,
                    "baseline_source": result.baseline_source,
                    "baseline_figures_agreeing": (
                        result.baseline_figures_agreeing
                    ),
                },
                "printed": {
                    "grand_total": find_printed_grand_total(facade),
                    "subtotal": find_printed_subtotal(facade),
                },
                "boundary": build_boundary_case(receipt, summary),
            }
        )
    return expected


# ---------------------------------------------------------------------------
# Synthetic guard bands.
#
# The 35-receipt golden set does NOT exercise the #1320 non-product guards --
# verified by re-running the whole fixture with the pre-#1320 regexes, which
# reproduced it byte for byte. So the golden parity alone can never catch a
# stale SETTLEMENT_RE / SALE_PRICE_RE / NON_PRODUCT_NOTE_RE in the Swift port
# (and did not: the port shipped with all three frozen).
#
# These synthetic two-row layouts, lifted from the Python unit tests, are
# decoded by the same real decoder and put the guards under the same
# regenerate-and-diff gate as the golden receipts. Each case is a real
# product row plus one band that must NOT become an item.
# ---------------------------------------------------------------------------

GUARD_BANDS: list[tuple[str, list[str]]] = [
    # #1320 SALE_PRICE_RE: BOGO / pre-discount annotation echoes
    ("sale_price_echo", ["Sale", "Price", "1.99"]),
    ("target_regular_price_echo", ["Regular", "Price", "$22.99"]),
    ("nordstrom_comparable_value", ["Comparable", "Value", "59.95"]),
    ("reg_price_abbreviated", ["Reg.", "Price", "8.49"]),
    # #1320 NON_PRODUCT_NOTE_RE: tip footers and transaction-count notes
    ("tip_percent_equals", ["22%", "Tip", "=", "4.40"]),
    ("bare_percent_equals", ["15%", "=", "10.73"]),
    ("tip_total_parenthesized", ["18%:", "(Tip", "Total", "9.27"]),
    ("items_in_transaction", ["Items", "in", "Transaction:", "5", "5.49"]),
    # ... and the product names the anchors must NOT reach
    ("percent_in_product_name", ["JRG", "A2/A2", "6%", "FAT", "MLK", "7.99"]),
    ("steak_tips_is_a_product", ["STEAK", "TIPS", "12.99"]),
    ("bag_sale_paper_is_a_product", ["BAG", "SALE", "PAPER", "EA", "0.10"]),
    # #1320 SETTLEMENT_RE: scrambled / prefixed / AUTH forms
    ("settlement_scrambled_due_balance", ["17.98", "DUE", "BALANCE"]),
    (
        "settlement_item_prefixed_subtotal",
        ["[1", "item]", "Sub", "Total", "16.00"],
    ),
    ("settlement_auth_debit", ["2014.98", "AUTH", "DEBIT", "$20.47"]),
    ("settlement_amount_due", ["Amount", "Due", "31.20"]),
    ("settlement_total_to_pay", ["Total", "To", "Pay", "9.99"]),
    ("settlement_subttl_ocr_variant", ["SUB-TTL", "42.00"]),
    ("settlement_change_due", ["CHANGE", "DUE", "0.00"]),
    # BRANDED settlement rows: SETTLEMENT_RE requires the row be ONLY the
    # tender word, so these 12 prod forms decoded as phantom items until
    # is_settlement_row's closed vocabulary caught them.
    ("settlement_visa_debit", ["Visa", "Debit", "$37.51"]),
    ("settlement_bare_visa", ["Visa", "13.01"]),
    ("settlement_bare_mastercard", ["MASTERCARD", "32.30"]),
    (
        "settlement_mastercard_swipe",
        ["MasterCard", "1394", "(Swipe)", "20.00"],
    ),
    ("settlement_visa_masked", ["Visa", "...3931", "22.52"]),
    ("settlement_masked_mastercard", ["xXXX5061", "MASTERCARD", "42.14"]),
    (
        "settlement_visa_tendered_batch",
        ["Visa", "Tendered:", "Trans", "*:", "9", "Batch#:", "9.65"],
    ),
    ("settlement_local_cash", ["Local", "Cash", "10.44"]),
    (
        "settlement_mastercard_for_amount",
        ["MASTERCARD", "...8644", "for", "32.30"],
    ),
    ("settlement_payment_cash", ["Payment", "(Cash):", "$40.00"]),
    # ... and the real food the vocabulary must NOT reach
    ("pork_tender_is_a_product", ["33965", "PORK", "TENDER", "19.51"]),
    ("chicken_tender_is_a_product", ["CHICKEN", "TENDER", "15.00"]),
    ("gift_card_is_a_product", ["VISA", "GIFT", "CARD", "25.00"]),
    # DISCOUNT_WORD_RE: "OFF" inside COFFEE / TOFFEE / Office flagged real
    # items as discounts, which excluded them from reconciliation.
    ("coffee_is_not_a_discount", ["ORG", "BIRCHWOOD", "COFFEE", "15.99"]),
    ("toffee_is_not_a_discount", ["TOFFEE", "ICE", "CREAM", "BAR", "6.99"]),
    (
        "skin_off_is_not_a_discount",
        ["SALMON", "FILLET", "SKIN", "OFF", "9.74"],
    ),
    ("percent_off_is_a_discount", ["20%", "OFF", "ORG", "PRODU", "-0.31"]),
    # Trailing single-letter name token (flag or truncation glyph); "S" is
    # excluded because it is more often a truncated plural.
    (
        "trailing_flag_letter_trimmed",
        ["FRUITLANDS", "GOSE", "6", "PK", "I", "8.99"],
    ),
    ("trailing_s_is_kept", ["ORG", "KOSHER", "DILL", "PICKLE", "S", "3.99"]),
    ("short_name_keeps_its_letter", ["UP", "VITAMIN", "C", "7.49"]),
    # WAS_PRICE_RE (already ported at #1313; pinned so it cannot regress)
    ("was_price_comparison", ["SALE", "2", "@", "$1.89,", "WAS:", "$3.59"]),
]

GUARD_ANCHOR = ["REAL", "WIDGET", "3.00"]


def synth_row(line_id: int, y: float, tokens: list[str]) -> list[dict]:
    """One synthetic visual row, laid out left to right.

    Identical geometry to the ``row`` helper in
    ``receipt_upload/tests/test_line_item_geometry.py`` so the vectors stay
    comparable with the hand-written Python unit tests.
    """
    return [
        {
            "line_id": line_id,
            "word_id": index + 1,
            "text": token,
            "x": 0.1 + 0.2 * index,
            "y_mid": y,
            "h": 0.02,
        }
        for index, token in enumerate(tokens)
    ]


def build_guard_cases() -> list[dict]:
    cases = []
    for name, tokens in GUARD_BANDS:
        words = synth_row(1, 0.30, GUARD_ANCHOR) + synth_row(2, 0.25, tokens)
        items, _ = extract_items(words, {1, 2})
        cases.append(
            {
                "case": name,
                "words": words,
                "items_line_ids": [1, 2],
                "items": [dump_item(i) for i in items],
            }
        )
    return cases


# ---------------------------------------------------------------------------
# Boundary-extension vectors (#1329).
#
# The golden fixture stores only ITEMS-zone words, so a zone shrunk by its
# outermost line is the only way to give the extension search somewhere to
# grow back into. Rows are one-per-OCR-line (the same facade the section
# parity generator uses), which keeps both sides deterministic.
# ---------------------------------------------------------------------------


def build_boundary_case(receipt: dict, summary: Optional[dict]) -> dict:
    words = receipt["words"]
    zone = sorted(set(receipt["items_line_ids"]))
    line_ids = sorted({int(w["line_id"]) for w in words})
    rows = [
        {
            "row_id": index,
            "line_ids": [line_id],
            "y_min": min(
                float(w["y_mid"]) - float(w["h"]) / 2.0
                for w in words
                if int(w["line_id"]) == line_id
            ),
        }
        for index, line_id in enumerate(line_ids)
    ]
    # Shrink the zone by its outermost line so the search has a candidate.
    shrunk = set(zone[1:-1]) if len(zone) >= 4 else set(zone)
    proposal = propose_items_boundary_extension(
        words,
        summary,
        shrunk,
        sections=[],
        rows=rows,
        current_row_ids=None,
    )
    return {
        "image_id": receipt["image_id"],
        "receipt_id": receipt["receipt_id"],
        "current_line_ids": sorted(shrunk),
        "rows": rows,
        "summary": summary,
        "proposal": (
            None
            if proposal is None
            else {
                "line_ids": proposal["line_ids"],
                "added_line_ids": proposal["added_line_ids"],
                "added_row_ids": proposal["added_row_ids"],
                "before": proposal["before"],
                "after": proposal["after"],
            }
        ),
    }


def render(expected: Any) -> str:
    """Serialize exactly as the committed fixture is written."""
    return json.dumps(expected, indent=1, sort_keys=False) + "\n"


def generate(ocr_path: Path, golden_path: Path) -> dict[Path, str]:
    """Every committed artifact this generator owns: path -> exact bytes."""
    ocr_fixture = json.loads(ocr_path.read_text(encoding="utf-8"))
    golden_fixture = json.loads(golden_path.read_text(encoding="utf-8"))
    return {
        DEFAULT_OUTPUT: render(
            build_expectations(ocr_fixture, golden_fixture)
        ),
        GUARD_OUTPUT: render(build_guard_cases()),
        # The Swift package needs its own copy of the golden OCR words, and a
        # copy is exactly the thing that silently goes stale. Regenerating it
        # here makes the drift check own it too.
        SWIFT_OCR_COPY: ocr_path.read_text(encoding="utf-8"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ocr", type=Path, default=DEFAULT_OCR)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write; exit 1 when any committed file is stale",
    )
    args = parser.parse_args()

    artifacts = generate(args.ocr, args.golden)

    if args.check:
        stale = [
            path
            for path, payload in artifacts.items()
            if not path.exists() or path.read_text(encoding="utf-8") != payload
        ]
        if stale:
            raise SystemExit(
                "STALE Swift parity expectations (the Python decoder moved "
                "and the committed snapshot did not):\n  "
                + "\n  ".join(str(p) for p in stale)
                + "\nRegenerate with `python receipt_ocr_swift/Scripts/"
                "generate_line_items_parity.py`."
            )
        print(f"fresh: {len(artifacts)} artifacts match the Python decoder")
        return

    for path, payload in artifacts.items():
        path.write_text(payload, encoding="utf-8")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
