#!/usr/bin/env python3
"""Prototype parser for GitHub receipt emails (github.com, email.github.com).

CRITICAL: github.com is overwhelmingly notification traffic (PR/issue threads,
security alerts, billing *alerts*). Only "[GitHub] Payment Receipt ..." emails
are actual receipts with a charged total; everything else returns
grand_total=None (receipt_kind "non_receipt"). Note in particular that Tyler's
own repo is a receipt platform, so thousands of PR-notification subjects contain
the word "receipt" -- subject keywords alone are NOT sufficient.

Payment receipt layout (plain text, stable 2021-2026):
    GITHUB RECEIPT - PERSONAL SUBSCRIPTION - exampleuser
    GitHub Copilot Pro - year: $100.00 USD      <- item (may omit price)
    Feb 21, 2026 - Feb 20, 2027                  <- service period
    Tax: $0.00 USD                               <- (absent pre-2023)
    Total: $100.00 USD*
    Charged to: Visa (4*** **** **** 1234)
    Transaction ID: ch_...
    Date: 22 Feb 2026 11:28AM PST

merchant "GitHub".

Usage: parse_github.py file.eml [file2.eml ...]  -> JSON per file to stdout
"""
import email
import email.policy
import json
import re
import sys
from datetime import datetime
from html import unescape

MONEY = r"\$([0-9][0-9,]*\.[0-9]{2})"
CARD_BRANDS = r"(Visa|MasterCard|Mastercard|American Express|Amex|Discover|PayPal)"


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
    """GitHub receipts are text/plain; prefer it, fall back to html."""
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
    """'Charged to: Visa (4*** **** **** 1234)' -> brand + last4."""
    m = re.search(CARD_BRANDS + r"\s*\(?[0-9* ]*?([0-9]{4})\)?", s)
    if not m:
        m = re.search(CARD_BRANDS, s)
        if not m:
            return None
        brand = m.group(1)
        return {"type": "paypal" if brand == "PayPal" else "card",
                "card_brand": brand, "card_last4": None}
    brand = m.group(1).replace("Mastercard", "MasterCard")
    return {"type": "paypal" if brand == "PayPal" else "card",
            "card_brand": brand, "card_last4": m.group(2)}


def parse_receipt(txt, s, out):
    out["receipt_kind"] = "payment_receipt"
    m = re.search(r"Transaction ID:?\s*(\S+)", s)
    out["order_id"] = m.group(1) if m else None
    m = re.search(r"Tax:?\s*" + MONEY, s)
    out["tax"] = money(m.group(1)) if m else None
    m = re.search(r"Total:?\s*" + MONEY, s)
    total = money(m.group(1)) if m else None
    out["grand_total"] = total
    m = re.search(r"Charged to:?\s*(.+)", s)
    if m:
        out["payment_method"] = parse_payment(m.group(1))
    # body 'Date: 22 Feb 2026 11:28AM PST' is the charge date (more precise than
    # the mail header, which can differ by a day across timezones).
    m = re.search(r"\bDate:?\s*(\d{1,2} [A-Za-z]{3,9} \d{4})", s)
    if m:
        for fmt in ("%d %b %Y", "%d %B %Y"):
            try:
                out["date"] = datetime.strptime(m.group(1), fmt).date().isoformat()
                break
            except ValueError:
                pass
    # items: lines in the plain-text body between the RECEIPT header and Tax/Total.
    items = []
    seg = re.search(r"GITHUB RECEIPT[^\n]*\n(.*?)\n(?:Tax:|Total:)", txt, re.S | re.I)
    if seg:
        for line in seg.group(1).splitlines():
            line = line.strip()
            if not line:
                continue
            # skip pure service-period / date-range lines
            if re.match(r"^[A-Za-z]{3,9} \d{1,2}, \d{4}\s*-", line):
                continue
            if re.match(r"^For service through", line, re.I):
                continue
            pm = re.search(r"(.*?):?\s*" + MONEY + r"\s*USD\s*$", line)
            if pm and pm.group(1).strip():
                price = money(pm.group(2))
                items.append({"description": pm.group(1).strip()[:120],
                              "quantity": 1, "unit_price": price, "total": price})
            else:
                # item with no explicit price (older 'Pro yearly' / 'data packs')
                items.append({"description": line[:120], "quantity": 1,
                              "unit_price": None, "total": None})
    out["items"] = items
    if total is not None and out["tax"] is not None:
        out["subtotal"] = round(total - out["tax"], 2)
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
        "merchant_name": "GitHub",
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

    # A real receipt requires the plain-text 'GITHUB RECEIPT' block AND a Total.
    # Billing *alerts* ("Annual Billing Alert", "problem billing") and all
    # notification threads carry no total -> non_receipt.
    if re.search(r"GITHUB RECEIPT\b", txt, re.I) and re.search(r"\bTotal:?\s*" + MONEY, s):
        parse_receipt(txt, s, out)
    else:
        out["receipt_kind"] = "non_receipt"

    out["item_count"] = len(out["items"]) if out["receipt_kind"] == "payment_receipt" else None
    return out


def main():
    for p in sys.argv[1:]:
        print(json.dumps(parse(p), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
