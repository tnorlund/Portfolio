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
import re
from collections import Counter, defaultdict
from decimal import Decimal


def _money(text) -> Decimal | None:
    cleaned = re.sub(r"[^0-9.]", "", str(text or ""))
    if not cleaned or cleaned == ".":
        return None
    try:
        return Decimal(cleaned)
    except Exception:
        return None


# Same totals block, so subtotal/grand should sit within this normalized-y window.
_TOTALS_BLOCK_Y_WINDOW = 0.20
_FOOTER_MARGIN = 0.005

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


def _localize_totals_block(labels, words):
    """Per receipt, return (footer_boundary_y, confidence).

    The footer boundary is the y of the real grand total; an item-region label
    BELOW it is footer noise. We only trust the boundary when the totals block
    is CONFIRMED, because a single mislabeled GRAND_TOTAL high on the receipt
    would otherwise flag every real item below it as footer noise:

      reconciled         : subtotal + tax == grand (strongest)
      subtotal_confirmed : subtotal sits in the same y-window as grand
      weak               : grand total only (no subtotal/tax to confirm) ->
                           NOT used to flag footer items (precision over recall)
    """
    block: dict[tuple[str, str], dict[str, list]] = defaultdict(
        lambda: defaultdict(list)
    )
    for wl in labels:
        if str(wl.get("validation_status") or "").upper() != "VALID":
            continue
        label = wl.get("label")
        if label not in ("SUBTOTAL", "TAX", "GRAND_TOTAL"):
            continue
        word = words.get(
            (
                str(wl.get("image_id")),
                str(wl.get("receipt_id")),
                str(wl.get("line_id")),
                str(wl.get("word_id")),
            )
        )
        if word and _y(word) is not None:
            r = (str(wl["image_id"]), str(wl["receipt_id"]))
            block[r][label].append((_y(word), _money(word.get("text"))))

    out: dict[tuple[str, str], tuple[float | None, str]] = {}
    for r, a in block.items():
        grands = a.get("GRAND_TOTAL") or []
        subs = a.get("SUBTOTAL") or []
        taxes = a.get("TAX") or []
        if not grands:
            out[r] = (None, "none")
            continue
        bottom_grand = min(gy for gy, _ in grands)  # real total is the lowest one
        confidence = "weak"
        anchor = bottom_grand
        # reconciled: some grand == some subtotal + some tax
        for gy, gv in grands:
            for _, sv in subs:
                for _, tv in taxes:
                    if (
                        None not in (gv, sv, tv)
                        and abs((sv + tv) - gv) <= Decimal("0.02")
                    ):
                        confidence, anchor = "reconciled", gy
        if confidence == "weak" and subs:
            # subtotal clustered with a grand total confirms the block
            for sy, _ in subs:
                for gy, _ in grands:
                    if abs(sy - gy) <= _TOTALS_BLOCK_Y_WINDOW:
                        confidence = "subtotal_confirmed"
                        anchor = min(anchor, gy)
        out[r] = (anchor, confidence)
    return out


def _places_context(payload) -> dict:
    """Merchant location context from receipt_places (Google Places = the
    internet + the receipt's location): real website + address, used to confirm
    that a digit token labeled WEBSITE is wrong and to give the reviewer the
    tax jurisdiction."""
    places = payload.get("receipt_places") or []
    by_key: dict[tuple[str, str], dict] = {}
    fallback: dict = {}
    for p in places:
        if not isinstance(p, dict):
            continue
        ctx = {
            "website": p.get("website"),
            "address": p.get("formatted_address") or p.get("short_address"),
            "category": p.get("merchant_category"),
        }
        image_id = str(p.get("image_id") or "")
        receipt_id = str(p.get("receipt_id") or "")
        by_key[(image_id, receipt_id)] = ctx
        if image_id and not fallback:
            fallback = ctx
    return {"by_key": by_key, "fallback": fallback}


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
    totals = _localize_totals_block(labels, words)
    places = _places_context(payload)

    def ctx_for(image_id, receipt_id):
        return (
            places["by_key"].get((image_id, receipt_id))
            or places["by_key"].get((image_id, ""))
            or places["fallback"]
        )

    auto_safe: list[dict] = []
    review_payment: list[dict] = []
    skipped_unconfirmed_footer = 0
    for wl in labels:
        if str(wl.get("validation_status") or "").upper() != "VALID":
            continue
        word = words.get(
            (
                str(wl.get("image_id")),
                str(wl.get("receipt_id")),
                str(wl.get("line_id")),
                str(wl.get("word_id")),
            )
        )
        if not word:
            continue
        text = str(word.get("text") or "")
        label = str(wl.get("label"))
        upper = text.upper().strip(".,")
        r = (str(wl["image_id"]), str(wl["receipt_id"]))

        rec = {
            "image_id": str(wl["image_id"]),
            "receipt_id": int(wl["receipt_id"]),
            "line_id": int(wl["line_id"]),
            "word_id": int(wl["word_id"]),
            "label": label,
            "text": text,
        }
        if label == "WEBSITE" and text.replace(".", "").isdigit():
            site = (ctx_for(r[0], r[1]) or {}).get("website")
            auto_safe.append(
                {**rec, "pattern": "store_number_as_website", "real_website": site}
            )
        elif label in ITEM_LABELS:
            anchor, confidence = totals.get(r, (None, "none"))
            if anchor is None or _y(word) is None:
                continue
            if _y(word) >= anchor - _FOOTER_MARGIN:
                continue  # above the total -> a real item, not footer
            if confidence in ("reconciled", "subtotal_confirmed"):
                auto_safe.append(
                    {**rec, "pattern": "item_label_in_footer", "localization": confidence}
                )
            else:
                # totals block could not be confirmed -> do NOT flag (a
                # mislocalized total would otherwise destroy real item labels).
                skipped_unconfirmed_footer += 1
        elif label == "PAYMENT_METHOD":
            if upper in KEEP_PAYMENT or upper.startswith("XXXX"):
                continue
            if upper in FLIP_PAYMENT or upper in REVIEW_PAYMENT or text.replace("X", "").isdigit():
                # merchant-specific (CARD/CREDIT can be a real tender) -> review
                review_payment.append({**rec, "pattern": "nonpayment_as_payment"})

    return {
        "merchant": merchant,
        "merchant_location": places["fallback"],
        "auto_safe": auto_safe,
        "review_payment": review_payment,
        "summary": {
            "auto_safe": len(auto_safe),
            "auto_safe_by_pattern": dict(Counter(f["pattern"] for f in auto_safe)),
            "review_payment": len(review_payment),
            "skipped_unconfirmed_footer": skipped_unconfirmed_footer,
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
            f"{result['merchant']}: auto_safe={s['auto_safe']} {s['auto_safe_by_pattern']}, "
            f"review_payment={s['review_payment']}, "
            f"skipped_unconfirmed_footer={s['skipped_unconfirmed_footer']} -> {args.out}"
        )
    else:
        print(text)


if __name__ == "__main__":
    main()
