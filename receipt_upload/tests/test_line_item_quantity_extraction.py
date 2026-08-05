"""Printed-quantity / unit-price capture on decoded line items.

The band-block decoder used to throw away quantity and unit price the
receipt had already proved. Trader Joe's dev receipt 2b630bec prints
"6 @ $0.49" under a 2.94 LEMON EACH row; Vision read it as the three
words "6", "8", "S0.49", so the band parsed to nothing, landed in
OUTSIDE, and the stored item carried neither field -- even though
6 x 0.49 == 2.94 exactly and the receipt's own "Items in Transaction: 10"
corroborates it (4 single items + 6 lemons).

The rule these tests pin: PARSING is tolerant of OCR damage, ACCEPTANCE
is not. A pair is believed only when quantity x unit_price reproduces the
item's own printed price to the cent.
"""

import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from receipt_upload.line_items.blocks import attach_printed_quantities
from receipt_upload.line_items.geometry import (
    accept_quantity_pair,
    extract_items,
    implied_unit_price,
    quantity_candidates,
)

_FIXTURES = Path(__file__).parent / "fixtures"


def W(line_id, word_id, text, x, y, h=0.02):
    return {
        "line_id": line_id,
        "word_id": word_id,
        "text": text,
        "x": x,
        "y_mid": y,
        "h": h,
    }


def row(line_id, y, *tokens):
    return [
        W(line_id, i + 1, t, 0.1 + 0.2 * i, y) for i, t in enumerate(tokens)
    ]


def items_for(words):
    items, _ = extract_items(words, {w["line_id"] for w in words})
    return items


def by_name(items, name):
    return next(i for i in items if i["name"] == name)


# --- the parser proposes -------------------------------------------------


def test_candidates_read_the_ocr_mangled_at_and_dollar():
    # "6 @ $0.49" as Vision actually returned it on 2b630bec
    cands = quantity_candidates(row(14, 0.2, "6", "8", "S0.49"))
    assert (cands[0]["quantity"], cands[0]["unit_price"]) == (6.0, 0.49)


def test_candidates_read_the_clean_form_and_the_adjacent_form():
    clean = quantity_candidates(row(1, 0.2, "2", "@", "3.49"))
    bare = quantity_candidates(row(1, 0.2, "2", "3.49"))
    assert clean[0]["unit_price"] == bare[0]["unit_price"] == 3.49
    assert clean[0]["quantity"] == bare[0]["quantity"] == 2.0


def test_candidates_point_at_the_two_words_that_prove_the_pair():
    cands = quantity_candidates(row(14, 0.2, "6", "8", "S0.49"))
    assert cands[0]["qty_word_ids"] == [
        {"line_id": 14, "word_id": 1},
        {"line_id": 14, "word_id": 3},
    ]


def test_candidates_ignore_quantity_one():
    # A count of 1 multiplies out against any price, so it can never be
    # evidence of anything.
    assert quantity_candidates(row(1, 0.2, "1", "@", "4.75")) == []


def test_candidates_need_the_count_before_the_money():
    # Two tokens with a word between them are not a quantity expression.
    assert quantity_candidates(row(1, 0.2, "3", "CHEESE", "3.79")) == []


# --- the arithmetic decides ----------------------------------------------


def test_acceptance_is_cent_exact():
    assert accept_quantity_pair(6, 0.49, 2.94)
    assert not accept_quantity_pair(6, 0.49, 2.95)
    assert not accept_quantity_pair(6, 0.49, None)
    assert not accept_quantity_pair(None, 0.49, 2.94)


def test_implied_unit_price_completes_a_lead_quantity_row():
    # "2 BURRITO ... 9.98" records a count and nothing else; the row's own
    # price supplies the rest.
    assert implied_unit_price(2, 9.98) == 4.99


def test_implied_unit_price_refuses_a_count_that_is_name_content():
    # Twin Peaks "12 Naked Wings" at 18.49 -- 18.49/12 is not whole cents,
    # so the false quantity never gains a unit price.
    assert implied_unit_price(12, 18.49) is None


def test_implied_unit_price_refuses_quantity_one():
    # Its unit price would be the extended price, on the word already
    # labelled LINE_TOTAL.
    assert implied_unit_price(1, 4.75) is None


# --- end to end through the decoder --------------------------------------


def test_outside_band_quantity_reaches_the_item():
    # The 2b630bec layout: name+price on one visual row, the mangled
    # quantity line on the row below carrying no parseable amount at all.
    words = row(13, 0.20, "LEMON", "EACH", "$2.94") + row(
        14, 0.10, "6", "8", "S0.49"
    )
    item = by_name(items_for(words), "LEMON EACH")
    assert (item["quantity"], item["unit_price"]) == (6.0, 0.49)
    assert item["price"] == 2.94


def test_quantity_span_follows_the_donor_band():
    words = row(13, 0.20, "LEMON", "EACH", "$2.94") + row(
        14, 0.10, "6", "8", "S0.49"
    )
    item = by_name(items_for(words), "LEMON EACH")
    assert item["qty_word_ids"] == [
        {"line_id": 14, "word_id": 1},
        {"line_id": 14, "word_id": 3},
    ]


def test_neighbour_quantity_that_does_not_multiply_out_is_refused():
    # Same layout, one cent off. Nothing is stored rather than something
    # plausible: the arithmetic is the only evidence there is.
    words = row(13, 0.20, "LEMON", "EACH", "$2.95") + row(
        14, 0.10, "6", "8", "S0.49"
    )
    item = by_name(items_for(words), "LEMON EACH")
    assert item["quantity"] is None
    assert item["unit_price"] is None


def test_lead_quantity_row_gains_its_unit_price():
    item = by_name(items_for(row(1, 0.2, "2", "BURRITO", "9.98")), "BURRITO")
    assert (item["quantity"], item["unit_price"]) == (2.0, 4.99)


def test_attachment_never_rewrites_an_existing_pair():
    items = [
        {
            "name": "X",
            "price": 2.94,
            "quantity": 3.0,
            "unit_price": 0.98,
            "band": row(1, 0.2, "6", "8", "S0.49"),
        }
    ]
    attach_printed_quantities(items)
    assert (items[0]["quantity"], items[0]["unit_price"]) == (3.0, 0.98)


def test_attachment_touches_nothing_but_the_quantity_fields():
    before = {
        "name": "LEMON EACH",
        "price": 2.94,
        "is_discount": False,
        "line_ids": [13, 24],
        "quantity": None,
        "unit_price": None,
        "band": row(14, 0.2, "6", "8", "S0.49"),
    }
    after = dict(before)
    attach_printed_quantities([after])
    assert after["quantity"] == 6.0 and after["unit_price"] == 0.49
    for key in ("name", "price", "is_discount", "line_ids"):
        assert after[key] == before[key]


# --- golden-set floor ----------------------------------------------------
#
# The hand-labeled golden set carries 84 true quantities across 224 items,
# which is ground truth the recall/name/precision floors never scored. The
# floors below are FLOORS in the same sense as the golden regression
# gate's: raise them when a change genuinely improves the decode, never
# lower them to go green.
#
# Measured 2026-08-05 over the hermetic OCR fixture:
#   quantity  41 correct / 0 wrong   (was 39 / 0)
#   unit price 27 correct / 0 wrong  (was 25 / 0)
# The zeros are the load-bearing part: the arithmetic gate means a wrong
# quantity should be impossible, not merely rare.
_QTY_CORRECT_FLOOR = 41
_UNIT_CORRECT_FLOOR = 27


def _money(v):
    if v is None:
        return None
    t = str(v).replace("$", "").replace(",", "").strip().lstrip("-")
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


def _norm(s: str) -> str:
    t = re.sub(r"\s+", " ", (s or "").strip().upper())
    t = re.sub(r"<[A-Z]>", " ", t)
    t = re.sub(r"\b\d{5,}\b", " ", t)
    t = re.sub(r"[^A-Z0-9 ]", " ", t)
    t = re.sub(r"\b\d+\b", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _golden_quantity_scores():
    """(qty_correct, qty_wrong, unit_correct, unit_wrong, wrong_detail).

    Pairing is the golden gate's price-first greedy match, so a truth item
    is scored against the predicted item the gate itself would call it.
    """
    golden = json.loads(
        (_FIXTURES / "line_items_golden.json").read_text(encoding="utf-8")
    )
    golden = golden["receipts"] if isinstance(golden, dict) else golden
    ocr = {
        (r["image_id"], r["receipt_id"]): r
        for r in json.loads(
            (_FIXTURES / "line_items_golden_ocr.json").read_text(
                encoding="utf-8"
            )
        )["receipts"]
    }
    tally = {"qty_ok": 0, "qty_bad": 0, "unit_ok": 0, "unit_bad": 0}
    detail = []
    for g in golden:
        o = ocr.get((g["image_id"], g["receipt_id"]))
        if o is None:
            continue
        pred, _ = extract_items(o["words"], set(o["items_line_ids"]))
        used: set[int] = set()
        for truth in g["true_items"]:
            tp = _money(truth.get("price"))
            best, best_score = None, 0.0
            for i, p in enumerate(pred):
                if i in used:
                    continue
                price_ok = tp is not None and _money(p.get("price")) == tp
                ta = set(_norm(truth.get("name", "")).split())
                tb = set(_norm(p.get("name", "")).split())
                sim = (
                    len(ta & tb) / max(len(ta), len(tb)) if ta and tb else 0.0
                )
                score = (2.0 if price_ok else 0.0) + sim
                if score > best_score:
                    best, best_score = i, score
            if best is None or best_score < 0.5:
                continue
            used.add(best)
            for field, key in (("quantity", "qty"), ("unit_price", "unit")):
                tv, pv = _money(truth.get(field)), _money(
                    pred[best].get(field)
                )
                if tv is None or pv is None:
                    continue
                if abs(float(tv) - float(pv)) < 0.005:
                    tally[f"{key}_ok"] += 1
                else:
                    tally[f"{key}_bad"] += 1
                    detail.append(
                        (
                            g.get("merchant"),
                            truth.get("name"),
                            field,
                            str(tv),
                            str(pv),
                        )
                    )
    return tally, detail


def test_golden_quantities_are_never_wrong():
    # The arithmetic gate's whole promise: a quantity is stored only when
    # it reproduces the printed price, so it cannot disagree with a hand
    # label that was read off the same row.
    tally, detail = _golden_quantity_scores()
    assert tally["qty_bad"] == 0, f"quantity disagreements: {detail}"
    assert tally["unit_bad"] == 0, f"unit price disagreements: {detail}"


def test_golden_quantity_coverage_floor():
    tally, _ = _golden_quantity_scores()
    assert tally["qty_ok"] >= _QTY_CORRECT_FLOOR, (
        f"quantity coverage regressed: {tally['qty_ok']} < "
        f"{_QTY_CORRECT_FLOOR}"
    )
    assert tally["unit_ok"] >= _UNIT_CORRECT_FLOOR, (
        f"unit price coverage regressed: {tally['unit_ok']} < "
        f"{_UNIT_CORRECT_FLOOR}"
    )
