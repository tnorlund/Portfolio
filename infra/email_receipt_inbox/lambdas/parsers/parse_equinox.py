#!/usr/bin/env python3
"""Prototype parser for Equinox receipt emails (equinox.com).

Handles:
  A. Personal Training Purchase Confirmation (noreply@equinox.com):
     "ORDER DETAILS Order Number: N Purchase Description: X
      Order/Billing Total: 220.00 Payment Details: MASTERCARD8644"
     No subtotal/tax breakdown; total has no '$'. merchant "Equinox".
  B. The Shop at Equinox order confirmation (concierge@equinox.com):
     "Order #EQXSHOP37579 ... Order summary <item> × N \n $price ...
      Subtotal $x Shipping $x Taxes $x Total $x USD".
     merchant "Equinox (The Shop)".

Everything else on equinox.com (session cancel/reschedule/confirm, membership
notes, shipping notices, marketing) is not a receipt -> grand_total=None.

Usage: parse_equinox.py file.eml [file2.eml ...]  -> JSON per file to stdout
"""
import email
import email.policy
import json
import re
import sys
from datetime import datetime
from html import unescape

MONEY = r"\$?([0-9][0-9,]*\.[0-9]{2})"
# brand glued to last4, e.g. MASTERCARD8644, MasterCard5454, VISA1234
CARD_GLUED = re.compile(
    r"(VISA|MASTERCARD|AMEX|AMERICAN EXPRESS|DISCOVER|DINERS|JCB)\s*([0-9]{4})",
    re.I)
BRAND_NORM = {
    "visa": "Visa", "mastercard": "MasterCard", "amex": "American Express",
    "american express": "American Express", "discover": "Discover",
    "diners": "Diners Club", "jcb": "JCB",
}


def render(part):
    if part is None:
        return ""
    html = part.get_content()
    if part.get_content_type() == "text/plain":
        txt = html
    else:
        txt = re.sub(r"(?is)<(style|script)[^>]*>.*?</\1\s*>", " ", html)
        txt = re.sub(r"(?s)<!--.*?-->", " ", txt)
        txt = re.sub(r"(?i)<br\s*/?>|</(td|tr|div|p|table|li)\s*>", "\n", txt)
        txt = re.sub(r"<[^>]+>", " ", txt)
        txt = unescape(txt)
    txt = re.sub(r"[ \t\xa0]+", " ", txt)
    txt = re.sub(r"\s*\n\s*", "\n", txt).strip()
    return txt


def eml_text(path):
    """Return (msg, text). Equinox PT emails are HTML-only (empty plain part);
    Shop emails carry a good plain part. Pick whichever renders more content."""
    with open(path, "rb") as f:
        msg = email.message_from_bytes(f.read(), policy=email.policy.default)
    plain = render(msg.get_body(preferencelist=("plain",)))
    htmlt = render(msg.get_body(preferencelist=("html",)))
    txt = plain if len(plain) >= len(htmlt) else htmlt
    return msg, txt


def flat(txt):
    return re.sub(r"\s+", " ", txt)


def money(m):
    return float(m.replace(",", "")) if m else None


def header_date_iso(msg):
    try:
        return msg["date"].datetime.date().isoformat()
    except Exception:
        return None


def parse_payment(s):
    m = CARD_GLUED.search(s)
    if not m:
        return None
    brand = BRAND_NORM.get(m.group(1).lower(), m.group(1).title())
    return {"type": "card", "card_brand": brand, "card_last4": m.group(2)}


def parse_pt(s, out):
    """Personal Training purchase confirmation."""
    out["merchant_name"] = "Equinox"
    out["receipt_kind"] = "personal_training"
    m = re.search(r"Order Number:?\s*([A-Za-z0-9]+)", s)
    out["order_id"] = m.group(1) if m else None
    m = re.search(r"Purchase Description:?\s*(.+?)\s*(?:Order/Billing Total|Payment Details|$)", s)
    desc = flat(m.group(1)).strip() if m else None
    m = re.search(r"Order/Billing Total:?\s*" + MONEY, s)
    total = money(m.group(1)) if m else None
    out["grand_total"] = total
    # PT receipts carry no subtotal/tax breakdown.
    out["subtotal"] = total
    out["tax"] = None
    m = re.search(r"Payment Details:?\s*(.+)", s)
    if m:
        out["payment_method"] = parse_payment(m.group(1))
    if desc and total is not None:
        out["items"] = [{"description": desc[:120], "quantity": 1,
                         "unit_price": total, "total": total}]
    out["currency"] = "USD"
    return out


def parse_shop(txt, s, out):
    """The Shop at Equinox order confirmation. Totals read from flattened `s`;
    line items read from `txt` (newlines separate '<name> × N' from '$price')."""
    out["merchant_name"] = "Equinox (The Shop)"
    out["receipt_kind"] = "shop_order"
    m = re.search(r"Order #\s*([A-Z0-9]+)", s)
    out["order_id"] = m.group(1) if m else None
    m = re.search(r"Subtotal\s*\n?\s*" + MONEY, s)
    out["subtotal"] = money(m.group(1)) if m else None
    m = re.search(r"Tax(?:es)?\s*\n?\s*" + MONEY, s)
    out["tax"] = money(m.group(1)) if m else None
    m = re.search(r"Total\s*\n?\s*" + MONEY + r"\s*USD", s)
    if not m:
        m = re.search(r"\bTotal\s*\n?\s*" + MONEY, s)
    out["grand_total"] = money(m.group(1)) if m else None
    # items: "<NAME> × N" on one line, "$price" on the next.
    items = []
    for im in re.finditer(r"([^\n]+?)\s*[×xX]\s*(\d+)\s*\n\s*" + MONEY, txt):
        name = flat(im.group(1)).strip(" -|")
        qty = int(im.group(2))
        line_total = money(im.group(3))
        unit = round(line_total / qty, 2) if qty else line_total
        items.append({"description": name[:120], "quantity": qty,
                      "unit_price": unit, "total": line_total})
    out["items"] = items
    out["currency"] = "USD"
    return out


def parse(path):
    msg, txt = eml_text(path)
    s = flat(txt)
    subj = (msg["subject"] or "").strip()
    from_addr = str(msg["from"] or "")
    dom_m = re.search(r"@([\w.-]+)", from_addr)
    out = {
        "source": "email",
        "message_id": str(msg["message-id"] or "").strip(),
        "sender_domain": dom_m.group(1).lower() if dom_m else None,
        "merchant_name": "Equinox",
        "date": header_date_iso(msg),
        "order_id": None,
        "grand_total": None,
        "subtotal": None,
        "tax": None,
        "tip": None,
        "item_count": None,
        "items": [],
        "payment_method": None,
        "currency": "USD" if "$" in s else None,
        "raw_ref": {"mbox_file": None, "byte_offset": None},
        "email_subject": subj,
        "receipt_kind": None,
    }

    is_reply = re.match(r"(?i)^\s*(re|fw|fwd):", subj)
    if not is_reply and re.search(r"Order/Billing Total|PERSONAL TRAINING\s*PURCHASE CONFIRMATION",
                                  txt, re.I) and re.search(r"Order Number", s, re.I):
        parse_pt(s, out)
    elif not is_reply and re.search(r"Order #\s*EQXSHOP", s) and re.search(r"Order summary|Subtotal", s, re.I):
        parse_shop(txt, s, out)
    else:
        out["receipt_kind"] = "non_receipt"

    out["item_count"] = len(out["items"]) if out["receipt_kind"] not in (None, "non_receipt") else None
    return out


def main():
    for p in sys.argv[1:]:
        print(json.dumps(parse(p), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
