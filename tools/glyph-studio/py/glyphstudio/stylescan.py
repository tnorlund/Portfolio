"""Per-line style measurement over real Sprouts receipts.

For one receipt: load the raw scan + per-letter OCR boxes, then measure, per
visual line: cap height (px), per-letter ink density and stroke width
(run-length based, jitter-free), an underline probe under the line, and a
SECTION attribution (storefront/address/payment/items/section_header/
summary/total_line/survey/footer/barcode). Tiers (normal/bold/large) are
assigned relative to the receipt's own body median so scanner exposure
cancels out.

Usage:
  python -m glyphstudio.stylescan <image_id> <receipt_id> <out.json>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from statistics import median

import numpy as np

_WORKTREE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
)
for _pkg in (
    "receipt_agent",
    "receipt_dynamo",
    "receipt_upload",
    "synthesis_loop",
):
    _p = os.path.join(_WORKTREE, _pkg)
    if _p not in sys.path:
        sys.path.insert(0, _p)

SECTION_TOKENS = {
    "PRODUCE",
    "DAIRY",
    "MEAT",
    "GROCERY",
    "BULK",
    "DELI",
    "BAKERY",
    "FROZEN",
    "SEAFOOD",
    "VITAMINS",
    "BEER",
    "WINE",
    "BODY",
    "HOUSEHOLD",
}
_RULES = [
    ("store_hours", re.compile(r"Store Hours|MON-SUN|7AM-10PM", re.I)),
    (
        "address",
        re.compile(
            r"(BLVD|AVE\b|STREET|\bRD\b|WESTLAKE|,\s*CA\s+\d{5}|\(\d{3}\)\s*\d{3})",
            re.I,
        ),
    ),
    ("total_line", re.compile(r"^Total:", re.I)),
    (
        "summary",
        re.compile(
            r"BALANCE DUE|CHANGE\b|CREDIT\b|SUBTOTAL|^TAX\b|DEBIT\s*$", re.I
        ),
    ),
    (
        "payment",
        re.compile(
            r"AUTH|AID:|TVR:|TSI:|ARC:|IAD:|TC:|MID:|TID:|SEQ|Entry Method|APPROVED|CARD\s*#|Cntctless|MASTERCARD|US DEBIT|PURCHASE|Issuer|Verified|X{6,}",
            re.I,
        ),
    ),
    (
        "survey",
        re.compile(r"survey|feedback|WIN\b|Winners|gift card|Go to", re.I),
    ),
    (
        "footer",
        re.compile(
            r"Cashier|POS:|Transaction|Save money|weekly ad|sprouts\.com|original receipt|returns|receipt\.|Limits apply",
            re.I,
        ),
    ),
]
_BARCODE_RE = re.compile(r"^\d{10,}$")


_COSTCO_RULES = [
    (
        "self_checkout",
        re.compile(r"SELF-CHECKOUT|THANK YOU|Please Come Again", re.I),
    ),
    (
        "member",
        re.compile(r"^Member|^\d{12}$|Bottom of Basket|BOB Count", re.I),
    ),
    ("warehouse_header", re.compile(r"WHOLESALE|Costco|#\s?\d{3,4}", re.I)),
    ("items_sold", re.compile(r"ITEMS SOLD|TOTAL NUMBER", re.I)),
    ("total_line", re.compile(r"^\**\s*TOTAL\b", re.I)),
    ("summary", re.compile(r"SUBTOTAL|^TAX\b|^\s*\d+\s*%\s*TAX", re.I)),
    ("savings", re.compile(r"INSTANT SAVINGS|/\d+$", re.I)),
    (
        "payment",
        re.compile(
            r"AID:|Seq#|APPROVED|XXXX|CHIP|Visa|Mastercard|EFT|AMOUNT:|Purchase|"
            r"AUTH|CASH|CHANGE",
            re.I,
        ),
    ),
    ("footer", re.compile(r"OP#|Name:|Whse:|Trm:|Trn:|thank you", re.I)),
]
_VONS_RULES = [
    ("store_header", re.compile(r"VONS|Safeway|Store\s?#|Main Street", re.I)),
    (
        "savings",
        re.compile(
            r"SAVINGS|Club Savings|YOU PAY|Price You Pay|Member Savings", re.I
        ),
    ),
    (
        "section_header",
        re.compile(
            r"^(GROCERY|PRODUCE|MEAT|SEAFOOD|DELI|BAKERY|DAIRY|FROZEN|LIQUOR|GEN MERCHANDISE|REFRIG/FROZEN)\b",
            re.I,
        ),
    ),
    ("total_line", re.compile(r"^\**\s*BALANCE\b|^TOTAL\b", re.I)),
    ("summary", re.compile(r"SUBTOTAL|^TAX\b|CHANGE\b|CASH\b|CREDIT\b", re.I)),
    (
        "payment",
        re.compile(
            r"REF:|AUTH:|APPROVED|XXXX|DEBIT|VISA|MASTERCARD|EFT|PAYMENT AMOUNT",
            re.I,
        ),
    ),
    ("points", re.compile(r"POINTS|REWARDS|GAS REWARD|fuel", re.I)),
    (
        "footer",
        re.compile(
            r"Thank You|vons\.com|Your Cashier|survey|TWICE THE DIFFERENCE",
            re.I,
        ),
    ),
]
_TJ_RULES = [
    (
        "store_header",
        re.compile(
            r"TRADER JOE'?S|Store\s?#\s?\d|OPEN 8:00AM|THANK YOU FOR SHOPPING|traderjoes\.com",
            re.I,
        ),
    ),
    (
        "address",
        re.compile(
            r"(BLVD|AVE\b|STREET|\bRD\b|Parkway|Henderson|,\s*(CA|NV)\s+\d{5}|^\d{5}$|\d{3}[- ]\d{3}[- ]?\d{4})",
            re.I,
        ),
    ),
    ("qty_line", re.compile(r"^\d+\s*@\s*\$\d")),
    ("total_line", re.compile(r"TOTAL PURCHASE|Balance to pay", re.I)),
    ("summary", re.compile(r"Items in Transaction|^TAX\b|SUBTOTAL", re.I)),
    (
        "payment",
        re.compile(
            r"SALE TRANSACTION|PAYMENT CARD|US DEBIT|VISA|MASTERCARD|Auth Code|"
            r"TID:|TVR|Cardholder|PIN Verified|\*{4,}|CUSTOMER COPY|TRANS\.|TILL",
            re.I,
        ),
    ),
    (
        "footer",
        re.compile(r"Please retain|records|DATE\b|STORE\b|TIME\b", re.I),
    ),
]
_CVS_RULES = [
    ("store_header", re.compile(r"CVS\s*pharmacy|•CVS", re.I)),
    ("reg_line", re.compile(r"REG#\d+|TRN#\d+|CSHR#|STR#", re.I)),
    ("extracare", re.compile(r"ExtraCare|ExtraBucks|Reward|Earn\b", re.I)),
    (
        "pharmacy",
        re.compile(
            r"RX\s?#|vaccin|pharmacist|prescription|shingles|Tdap|RSV|insurance",
            re.I,
        ),
    ),
    ("fsa", re.compile(r"FSA|FLEXIBLE SPENDING|Eligible Total", re.I)),
    ("total_line", re.compile(r"^\s*TOTAL\b|^CHARGE\b", re.I)),
    ("summary", re.compile(r"SUBTOTAL|^TAX\b|\d+\.\d{2}N\s*$", re.I)),
    (
        "payment",
        re.compile(
            r"AID:|TVR|TSI|CVM:|TC:|REF#|MASTERCARD|VISA|DEBIT|PIN VERIFIED|"
            r"TRAN TYPE|\*{4,}|CHIP|TERMINAL",
            re.I,
        ),
    ),
    (
        "footer",
        re.compile(
            r"THANK YOU|Return Policy|Returns\b|with receipt|State law|Scan the QR|"
            r"CVS\.\s?COM|Helped by|Subject to",
            re.I,
        ),
    ),
]
_INNOUT_RULES = [
    (
        "store_header",
        re.compile(r"IN-N-OUT|WESTLAKE|VILLAGE|^\s*\d{3}\s*$", re.I),
    ),
    (
        "transaction",
        re.compile(r"Cashier|ORDERTAKER|Check\s*:|TRANS\s*#|Ticket|Station", re.I),
    ),
    ("note", re.compile(r"^NOTE\b|^tes$", re.I)),
    (
        "total_line",
        re.compile(r"Amount Due|AUTH\s+AMT", re.I),
    ),
    (
        "summary",
        re.compile(r"DRIVE-?Take Out|^TAX\b|Tender\b|Change\b", re.I),
    ),
    (
        "payment",
        re.compile(
            r"CHARGE\s+DETAIL|Card Type|Account:|Capture:|Contactless|PIN:|"
            r"Auth Code|Auth Ref|AID:|Trans\s*#|MasterCard|VISA|\*{4,}",
            re.I,
        ),
    ),
    (
        "footer",
        re.compile(r"THANK YOU|Questions/Comments|Call\s+800|^\d{4}-\d{2}-\d{2}\b", re.I),
    ),
]
_TARGET_RULES = [
    (
        "store_header",
        re.compile(
            r"TARGET|Westlake Village|Las Vegas|Simi Valley|Woodland Hills|"
            r"Henderson|Grand Canyon|Russell Ranch|Tierra Rejada|Ventura Blvd|"
            r"\d{3}[- ]\d{3}[- ]?\d{4}",
            re.I,
        ),
    ),
    (
        "section_header",
        re.compile(
            r"^(GROCERY|HEALTH AND BEAUTY|HOME|KITCHEN|APPAREL|ELECTRONICS|"
            r"LAUNDRY CLEANING AND CLOSET|PATIO & OUTDOOR DECOR|ENTERTAINMENT-"
            r"ELECTRONICS)\b",
            re.I,
        ),
    ),
    ("total_line", re.compile(r"^\s*TOTAL\b|DEBIT TOTAL PAYMENT", re.I)),
    ("summary", re.compile(r"SUBTOTAL|^T\s*=|^TAX\b|CA TAX|NV TAX", re.I)),
    (
        "payment",
        re.compile(
            r"AID:|AUTH CODE|MASTERCARD|VISA|DEBIT|CARD ENTRY|APPROVED|"
            r"\*{4,}|RETURN|Target Circle Card",
            re.I,
        ),
    ),
    (
        "footer",
        re.compile(
            r"RETURN POLICY|WHEN YOU RETURN|Target Circle|TARGET\.COM|"
            r"Guest Copy|Survey",
            re.I,
        ),
    ),
]


_MERCHANT_RULES = {
    "sprouts": _RULES,
    "costco": _COSTCO_RULES,
    "vons": _VONS_RULES,
    "traderjoes": _TJ_RULES,
    "cvs": _CVS_RULES,
    "innout": _INNOUT_RULES,
                   "target": _TARGET_RULES,
}


def _classify(text: str, has_price: bool, merchant: str = "sprouts") -> str:
    compact = text.strip()
    if _BARCODE_RE.match(compact.replace(" ", "")):
        return "barcode_caption"
    up = compact.upper().strip(":")
    if merchant == "sprouts" and up in SECTION_TOKENS:
        return "section_header"
    for name, rx in _MERCHANT_RULES.get(merchant, _RULES):
        if rx.search(compact):
            return name
    if has_price:
        return "item"
    if re.fullmatch(r"[*\-=_ ]{6,}", compact):
        return "separator"
    return "other"


def _sauvola(gray: np.ndarray) -> np.ndarray:
    from glyph_segment import auto_polarity, sauvola_mask

    crop, _was_reverse = auto_polarity(gray)
    return sauvola_mask(crop)


def _run_widths(mask: np.ndarray) -> list[int]:
    out = []
    for row in mask:
        padded = np.concatenate([[0], row.view(np.uint8), [0]])
        starts = np.where(np.diff(padded) == 1)[0]
        ends = np.where(np.diff(padded) == -1)[0]
        out.extend(
            int(e - s) for s, e in zip(starts, ends) if 1 <= e - s <= 20
        )
    return out


def measure(image_id: str, receipt_id: int, merchant: str = "sprouts") -> dict:
    from receipt_line_scorecard import _load_words_and_real

    from receipt_dynamo.data.dynamo_client import DynamoClient

    real, words = _load_words_and_real(
        "Sprouts Farmers Market", image_id, receipt_id
    )
    gray = np.asarray(real.convert("L"))
    H, W = gray.shape

    client = DynamoClient(
        os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22")
    )
    details = client.get_image_details(image_id)
    letters = [
        l
        for l in details.receipt_letters
        if str(l.receipt_id) == str(receipt_id)
    ]

    def box_px(obj):
        tl, br = obj.top_left, obj.bottom_right
        x0 = min(tl["x"], br["x"]) * W
        x1 = max(tl["x"], br["x"]) * W
        y0 = (1 - max(tl["y"], br["y"])) * H
        y1 = (1 - min(tl["y"], br["y"])) * H
        return x0, y0, x1, y1

    # group words into visual lines by y-center
    ws = []
    for w in words:
        x0, y0, x1, y1 = w["bbox"]
        left = min(x0, x1) / 1000 * W
        right = max(x0, x1) / 1000 * W
        top = (1 - max(y0, y1) / 1000) * H
        bottom = (1 - min(y0, y1) / 1000) * H
        ws.append(
            {
                "text": w["text"],
                "line_id": w.get("line_id"),
                "l": left,
                "r": right,
                "t": top,
                "b": bottom,
                "cy": (top + bottom) / 2,
                "h": bottom - top,
            }
        )
    ws.sort(key=lambda w: w["cy"])
    lines: list[list[dict]] = []
    for w in ws:
        if (
            lines
            and abs(w["cy"] - median(x["cy"] for x in lines[-1]))
            < w["h"] * 0.6
        ):
            lines[-1].append(w)
        else:
            lines.append([w])

    # letters indexed by (line_id, rough position) -> use line_id from words
    letters_by_line: dict[int, list] = {}
    for l in letters:
        letters_by_line.setdefault(int(l.line_id), []).append(l)

    price_re = re.compile(r"\d+\.\d{2}")
    out_lines = []
    for line in lines:
        line.sort(key=lambda w: w["l"])
        text = " ".join(w["text"] for w in line)
        has_price = bool(price_re.search(line[-1]["text"]))
        section = _classify(text, has_price, merchant)
        lt = min(w["t"] for w in line)
        lb = max(w["b"] for w in line)
        ll = min(w["l"] for w in line)
        lr = max(w["r"] for w in line)

        # Reverse-video detection (Costco's TOTAL/date boxes): the line band
        # is mostly DARK. Normal text bands run ~10-25% ink.
        by0, by1 = max(0, int(lt)), min(H, int(lb) + 1)
        bx0, bx1 = max(0, int(ll)), min(W, int(lr) + 1)
        reverse_video = 0
        if by1 - by0 > 3 and bx1 - bx0 > 3:
            band = gray[by0:by1, bx0:bx1]
            paper = float(np.percentile(gray, 85))
            reverse_video = int(float((band < paper - 40).mean()) > 0.55)

        # per-letter measurements for this line
        dens, strokes, caps, chars = [], [], [], []
        for w in line:
            for l in letters_by_line.get(
                int(w["line_id"]) if w["line_id"] is not None else -1, []
            ):
                x0, y0, x1, y1 = box_px(l)
                if not (y0 >= lt - 5 and y1 <= lb + 5):
                    continue
                xi0, yi0 = max(0, int(x0)), max(0, int(y0))
                xi1, yi1 = min(W, int(x1) + 1), min(H, int(y1) + 1)
                if xi1 - xi0 < 2 or yi1 - yi0 < 2:
                    continue
                crop = gray[yi0:yi1, xi0:xi1]
                mask = _sauvola(crop)
                if mask.sum() < 4:
                    continue
                ch = str(l.text or "")[:1]
                dens.append(float(mask.mean()))
                runs = _run_widths(mask)
                if runs:
                    strokes.append(float(np.mean(runs)))
                if ch.isupper() or ch.isdigit():
                    caps.append(float(yi1 - yi0))
                chars.append(
                    {
                        "ch": ch,
                        "density": round(float(mask.mean()), 4),
                        "stroke": (
                            round(float(np.mean(runs)), 2) if runs else None
                        ),
                        "h": int(yi1 - yi0),
                    }
                )

        # Underline probe: a wide horizontal ink run near the line bottom.
        # Underlines often sit INSIDE the OCR box (boxes include them), so the
        # band straddles the bottom edge. Paper level from a high percentile of
        # the band region (a mean collapses when most of a row IS the rule).
        underline = False
        line_h = lb - lt
        yb0 = max(0, int(lb - 0.30 * line_h))
        yb1 = min(H, int(lb + 0.50 * line_h))
        xb0, xb1 = max(0, int(ll) - 3), min(W, int(lr) + 3)
        if yb1 - yb0 >= 2 and xb1 - xb0 > 20:
            region = gray[yb0:yb1, xb0:xb1].astype(float)
            paper = float(np.percentile(region, 90))
            thresh = max(0.0, min(230.0, paper - 25.0))
            width_need = 0.55 * (xb1 - xb0)
            for row in region:
                ink = row < thresh
                padded = np.concatenate([[0], ink.view(np.uint8), [0]])
                starts = np.where(np.diff(padded) == 1)[0]
                ends = np.where(np.diff(padded) == -1)[0]
                if any(e - s >= width_need for s, e in zip(starts, ends)):
                    underline = True
                    break

        out_lines.append(
            {
                "reverse_video": reverse_video,
                "text": text[:60],
                "section": section,
                "cap_px": round(median(caps), 1) if caps else None,
                "density_med": round(median(dens), 4) if dens else None,
                "stroke_med": round(median(strokes), 2) if strokes else None,
                "underline": underline,
                "n_letters": len(chars),
                "letters": chars,
            }
        )

    # receipt-relative tiers: body = median over item/other/footer lines
    body_caps = [
        l["cap_px"]
        for l in out_lines
        if l["section"] in ("item", "other", "footer", "survey")
        and l["cap_px"]
    ]
    body_strokes = [
        l["stroke_med"]
        for l in out_lines
        if l["section"] in ("item", "other", "footer", "survey")
        and l["stroke_med"]
    ]
    body_cap = median(body_caps) if body_caps else None
    body_stroke = median(body_strokes) if body_strokes else None
    for l in out_lines:
        tier = "normal"
        if body_cap and l["cap_px"] and l["cap_px"] >= 1.45 * body_cap:
            tier = "large"
        elif (
            body_stroke
            and l["stroke_med"]
            and l["stroke_med"] >= 1.30 * body_stroke
        ):
            tier = "bold"
        l["tier"] = tier

    return {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "image_size": [W, H],
        "body_cap_px": body_cap,
        "body_stroke_px": body_stroke,
        "lines": out_lines,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("image_id")
    ap.add_argument("receipt_id", type=int)
    ap.add_argument("out")
    ap.add_argument("--merchant", default="sprouts")
    args = ap.parse_args(argv)
    result = measure(args.image_id, args.receipt_id, merchant=args.merchant)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1)
    n_ul = sum(1 for l in result["lines"] if l["underline"])
    tiers = {}
    for l in result["lines"]:
        tiers[l["tier"]] = tiers.get(l["tier"], 0) + 1
    print(
        f"{args.image_id}#{args.receipt_id}: {len(result['lines'])} lines, "
        f"tiers={tiers}, underlined={n_ul}, "
        f"reversed={sum(1 for l in result['lines'] if l.get('reverse_video'))}"
        f" -> {args.out}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
