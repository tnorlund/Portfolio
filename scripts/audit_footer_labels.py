#!/usr/bin/env python3.12
"""Pattern-driven footer/header label-noise auditor for one merchant.

Finds VALID-status ReceiptWordLabel records that are systematic validation
MISSES — the patterns observed across Home Depot that recur on every merchant:

  auto_safe (store_number_as_website):
      a pure-digit token labeled WEBSITE (store/register number, never a URL).
      Position-independent and unambiguous -> safe to flip in a batch.

  review_footer (item_label_in_footer):
      an item-region label (PRODUCT_NAME / UNIT_PRICE / QUANTITY / LINE_TOTAL /
      ITEM_TOTAL / ITEM_NAME) on a line BELOW the grand-total block. CANDIDATE
      ONLY — depends on correctly locating the grand total, which varies by
      merchant. On dense grocery receipts (e.g. Sprouts) a mislocalized total
      flags REAL products; an agent must verify per merchant before flipping.

  review_payment (nonpayment_as_payment + ambiguous):
      GIFT / AUTH / CARD / numeric-fragment / CREDIT labeled PAYMENT_METHOD.
      Merchant-specific (CARD/CREDIT can be a real tender elsewhere) -> review.

Only `auto_safe` should be flipped without per-receipt review. The loader
already drops INVALID labels, so flipping VALID->INVALID removes them from
training AND the real validation set without any masking.

Usage:
  python3.12 scripts/audit_footer_labels.py \
      --merchant-file ./grouped/the_home_depot.json \
      --out ./flips/the_home_depot.flips.json

Output JSON: {"merchant", "high_confidence": [...], "needs_review": [...]} where
each item is {image_id, receipt_id, line_id, word_id, label, text, pattern}.
Apply the high_confidence flips with the receipt-tools update_word_label MCP
(VALID -> INVALID); eyeball needs_review before deciding.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter

ITEM_LABELS = {
    "PRODUCT_NAME",
    "ITEM_NAME",
    "UNIT_PRICE",
    "QUANTITY",
    "LINE_TOTAL",
    "ITEM_TOTAL",
}
# PAYMENT_METHOD tokens that legitimately describe HOW a purchase was paid —
# never auto-flip these.
KEEP_PAYMENT = {"CHIP", "CONTACTLESS", "CASH", "DEBIT", "VISA", "MASTERCARD", "AMEX"}
# Clearly-wrong PAYMENT_METHOD tokens (promo/survey/auth), safe to flip.
FLIP_PAYMENT = {"GIFT", "AUTH", "CARD"}
# Ambiguous — emit for review, do not auto-flip.
REVIEW_PAYMENT = {"CREDIT"}


def _y(word):
    return (word.get("bounding_box") or {}).get("y")


def _grand_total_y(labels, words):
    gt: dict[tuple[str, str], float] = {}
    for wl in labels:
        if wl.get("label") != "GRAND_TOTAL":
            continue
        if str(wl.get("validation_status") or "").upper() != "VALID":
            continue
        key = (
            str(wl.get("image_id")),
            str(wl.get("receipt_id")),
            str(wl.get("line_id")),
            str(wl.get("word_id")),
        )
        word = words.get(key)
        if word and _y(word) is not None:
            r = (str(wl["image_id"]), str(wl["receipt_id"]))
            gt[r] = max(gt.get(r, 0.0), _y(word))
    return gt


def audit(payload: dict) -> dict:
    merchant = payload.get("merchant_name") or "unknown"
    words = {
        (
            str(w.get("image_id")),
            str(w.get("receipt_id")),
            str(w.get("line_id")),
            str(w.get("word_id")),
        ): w
        for w in (payload.get("receipt_words") or [])
    }
    labels = payload.get("receipt_word_labels") or []
    gt = _grand_total_y(labels, words)

    auto_safe: list[dict] = []
    review_footer: list[dict] = []
    review_payment: list[dict] = []
    for wl in labels:
        if str(wl.get("validation_status") or "").upper() != "VALID":
            continue
        key = (
            str(wl.get("image_id")),
            str(wl.get("receipt_id")),
            str(wl.get("line_id")),
            str(wl.get("word_id")),
        )
        word = words.get(key)
        if not word:
            continue
        text = str(word.get("text") or "")
        label = str(wl.get("label"))
        upper = text.upper().strip(".,")
        r = (str(wl["image_id"]), str(wl["receipt_id"]))
        below = r in gt and _y(word) is not None and _y(word) < gt[r]

        rec = {
            "image_id": str(wl["image_id"]),
            "receipt_id": int(wl["receipt_id"]),
            "line_id": int(wl["line_id"]),
            "word_id": int(wl["word_id"]),
            "label": label,
            "text": text,
        }
        if label == "WEBSITE" and text.replace(".", "").isdigit():
            auto_safe.append({**rec, "pattern": "store_number_as_website"})
        elif label in ITEM_LABELS and below:
            # CANDIDATE ONLY — high false-positive risk on dense receipts.
            review_footer.append({**rec, "pattern": "item_label_in_footer"})
        elif label == "PAYMENT_METHOD":
            if upper in KEEP_PAYMENT or upper.startswith("XXXX"):
                continue
            if upper in FLIP_PAYMENT or text.replace("X", "").isdigit():
                review_payment.append({**rec, "pattern": "nonpayment_as_payment"})
            elif upper in REVIEW_PAYMENT:
                review_payment.append({**rec, "pattern": "payment_method_ambiguous"})

    return {
        "merchant": merchant,
        "auto_safe": auto_safe,
        "review_footer": review_footer,
        "review_payment": review_payment,
        "summary": {
            "auto_safe": len(auto_safe),
            "review_footer": len(review_footer),
            "review_payment": len(review_payment),
        },
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--merchant-file", required=True, help="Grouped export JSON for one merchant")
    ap.add_argument("--out", help="Write flips JSON here (default: stdout)")
    args = ap.parse_args()
    with open(args.merchant_file) as fh:
        payload = json.load(fh)
    result = audit(payload)
    text = json.dumps(result, indent=1)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(text)
        s = result["summary"]
        print(
            f"{result['merchant']}: auto_safe={s['auto_safe']} (store#->WEBSITE), "
            f"review_footer={s['review_footer']} (verify total localization), "
            f"review_payment={s['review_payment']} -> {args.out}"
        )
    else:
        print(text)


if __name__ == "__main__":
    main()
