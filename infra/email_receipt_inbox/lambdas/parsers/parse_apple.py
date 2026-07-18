#!/usr/bin/env python3
"""Prototype parser for Apple receipt emails (email.apple.com, apple.com,
applepay.apple.com, orders.apple.com).

Handles:
  A. App Store / subscription receipts, classic layout (~2013 - mid/late 2025):
     "Receipt APPLE ID ... BILLED TO ... DATE ... ORDER ID ... TOTAL $x"
  B. App Store / AppleCare receipts, new layout (late 2025+):
     "Receipt <Month D, YYYY> Order ID: X Document: N ... Subtotal $x
      Visa •••• 1234 (Apple Pay) $x"
  C. Apple Cash payment receipts (applepay.apple.com).
  D. Apple online-store order confirmations (orders.apple.com):
     Subtotal / Estimated Tax / Order Total, items with Qty.
  E. Apple retail-store receipts ("Your receipt from Apple <Store>"):
     body is a thank-you shell; the real receipt is a PDF attachment.
     Emits a stub record flagged needs_pdf.

Usage: parse_apple.py file.eml [file2.eml ...]  -> JSON per file to stdout
"""
import email
import email.policy
import json
import re
import sys
from html import unescape

MONEY = r"\$([0-9][0-9,]*\.[0-9]{2})"
CARD_BRANDS = r"(Visa|MasterCard|Mastercard|American Express|Amex|Discover|Apple Card)"


def eml_text(path):
    with open(path, "rb") as f:
        msg = email.message_from_bytes(f.read(), policy=email.policy.default)
    body = msg.get_body(preferencelist=("html", "plain"))
    html = body.get_content() if body else ""
    txt = re.sub(r"(?is)<(style|script)[^>]*>.*?</\1\s*>", " ", html)
    txt = re.sub(r"(?s)<!--.*?-->", " ", txt)
    txt = re.sub(r"(?i)<br\s*/?>|</(td|tr|div|p|table)\s*>", "\n", txt)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = unescape(txt)
    txt = re.sub(r"[ \t ]+", " ", txt)
    txt = re.sub(r"\s*\n\s*", "\n", txt).strip()
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


def body_date_iso(s):
    m = re.search(r"([A-Z][a-z]+ \d{1,2}, \d{4})", s)
    if not m:
        return None
    from datetime import datetime
    try:
        return datetime.strptime(m.group(1), "%B %d, %Y").date().isoformat()
    except ValueError:
        try:
            return datetime.strptime(m.group(1), "%b %d, %Y").date().isoformat()
        except ValueError:
            return None


def parse_payment(s):
    """Find card brand + last4 in text like 'Visa .... 8761' or
    'Visa •••• 1234 (Apple Pay)' or bare 'Apple Card'."""
    m = re.search(CARD_BRANDS + r"[ .•·․]*([0-9]{4})?", s)
    if not m:
        return None
    brand = m.group(1)
    last4 = m.group(2)
    ptype = "apple_card" if brand == "Apple Card" else "card"
    if "(Apple Pay)" in s[m.start():m.end() + 15]:
        ptype = "apple_pay"
    return {"type": ptype, "card_brand": brand, "card_last4": last4}


STORE_SECTIONS = ("App Store", "Apple TV", "Apple Music", "iTunes Store",
                  "Apple Books", "iCloud+", "iCloud", "Apple Arcade",
                  "Apple News+", "Apple Fitness+", "Apple One")
ITEM_NOISE = re.compile(
    r"(Write a Review \| Report a Problem|Write a Review|Report a Problem|"
    r"Renews [A-Z][a-z]+ \d{1,2}, \d{4}|Renews [A-Z][a-z]+ \d{1,2} \d{4}|"
    r"TYPE PURCHASED FROM PRICE|"
    r"\(Automatic Renewal\)|Tyler[’']s [A-Za-z ]+)")


def split_items(segment):
    """Split a text run into items: each item is text ending in a $price."""
    items = []
    for m in re.finditer(r"(.*?)" + MONEY + r"(?=\s|$)", segment, re.S):
        desc = flat(m.group(1)).strip(" |•-")
        price = money(m.group(2))
        desc = ITEM_NOISE.sub(" ", desc)
        for sect in STORE_SECTIONS:
            if desc.startswith(sect + " "):
                desc = desc[len(sect):].strip()
                break
            if desc == sect:
                desc = ""
        desc = re.sub(r"\s+", " ", desc).strip(" |,")
        if desc and price is not None:
            items.append({"description": desc[:120], "quantity": 1,
                          "unit_price": price, "total": price})
    return items


def parse_classic(s, out):
    """Classic layout: Receipt APPLE ID ... TOTAL $x. The HTML contains the
    whole receipt twice (desktop + mobile rendering); cut at the duplicate
    boundary, then take the LAST 'TOTAL $x' in that half (2016-era emails
    also show a TOTAL in the header block before the items)."""
    dup = list(re.finditer(r"Receipt APPLE (ID|ACCOUNT)", s))
    cut = dup[1].start() if len(dup) > 1 else None
    cr = re.search(r"Copyright ©", s)
    if cr and (cut is None or cr.start() < cut):
        cut = cr.start()
    first = s[:cut] if cut else s
    totals = list(re.finditer(r"TOTAL " + MONEY, first))
    out["grand_total"] = money(totals[-1].group(1)) if totals else None
    m2 = re.search(r"Subtotal " + MONEY, first)
    out["subtotal"] = money(m2.group(1)) if m2 else None
    m2 = re.search(r"Tax " + MONEY, first)
    out["tax"] = money(m2.group(1)) if m2 else None
    m2 = re.search(r"ORDER ID[: ]+([A-Z0-9]+)", first)
    out["order_id"] = m2.group(1) if m2 else None
    m2 = re.search(r"DATE ([A-Z][a-z]+ \d{1,2}, \d{4})", first)
    if m2:
        out["date"] = body_date_iso(m2.group(0)) or out["date"]
    m2 = re.search(r"BILLED TO (.{0,60})", first)
    if m2:
        out["payment_method"] = parse_payment(m2.group(1))
    # items live between DOCUMENT NO. <digits> and Subtotal/TOTAL
    m2 = re.search(r"DOCUMENT NO\.? ?(\d+)(.*?)(?:Subtotal|TOTAL)", first, re.S)
    if m2:
        out["items"] = split_items(m2.group(2))
    out["merchant_name"] = "Apple (App Store)"
    return out


def parse_new(s, out):
    """Late-2025+ layout: Receipt <date> Order ID: X ... Subtotal $x
    <Card> •••• 1234 (Apple Pay) $x"""
    m = re.search(r"Order ID:? ([A-Z0-9]+)", s)
    out["order_id"] = m.group(1) if m else None
    m = re.search(r"Receipt(?: & Renewal Notice)? ([A-Z][a-z]+ \d{1,2}, \d{4})", s)
    if m:
        out["date"] = body_date_iso(m.group(1)) or out["date"]
    m = re.search(r"Subtotal " + MONEY, s)
    out["subtotal"] = money(m.group(1)) if m else None
    m = re.search(r"Tax " + MONEY, s)
    out["tax"] = money(m.group(1)) if m else None
    # payment line: "<brand> •••• 1234 (Apple Pay) $x" -> grand total
    m = re.search(CARD_BRANDS + r"[ •.]*([0-9]{4})?( \(Apple Pay\))? " + MONEY, s)
    if m:
        out["payment_method"] = parse_payment(m.group(0))
        out["grand_total"] = money(m.group(4))
    if out["grand_total"] is None:
        out["grand_total"] = out["subtotal"]
    # items between "Apple Account: <email>" and "Billing and Payment"
    m = re.search(r"Apple Account:? \S+@\S+(.*?)Billing and Payment", s, re.S)
    if m:
        seg = re.sub(r"(Agreement/Policy number: \d+|Next Billing Date: [A-Z][a-z]+ \d{1,2}, \d{4}|"
                     r"Next Contract Renewal Date: [A-Z][a-z]+ \d{1,2}, \d{4}|"
                     r"Renews [A-Z][a-z]+ \d{1,2}, \d{4})", " ", m.group(1))
        out["items"] = split_items(seg)
    out["merchant_name"] = "Apple (App Store)"
    return out


def parse_apple_cash(s, out):
    out["merchant_name"] = "Apple Cash"
    m = re.search(r"Transaction ID:? (\w+)", s)
    out["order_id"] = m.group(1) if m else None
    m = re.search(r"(\d{2}/\d{2}/\d{2,4}) (.*?) " + MONEY + " " + MONEY + " " + MONEY, s)
    if m:
        from datetime import datetime
        for fmt in ("%m/%d/%y", "%m/%d/%Y"):
            try:
                out["date"] = datetime.strptime(m.group(1), fmt).date().isoformat()
                break
            except ValueError:
                pass
        desc, fee, amount, total = m.group(2), money(m.group(3)), money(m.group(4)), money(m.group(5))
        out["items"] = [{"description": flat(desc).strip()[:120], "quantity": 1,
                         "unit_price": amount, "total": amount}]
        out["subtotal"] = amount
        out["grand_total"] = total
        out["tax"] = None
    out["payment_method"] = {"type": "apple_cash", "card_brand": None, "card_last4": None}
    return out


def parse_order(s, out):
    out["merchant_name"] = "Apple Store (online)"
    m = re.search(r"Order Number:? (\w+)", s)
    out["order_id"] = m.group(1) if m else None
    m = re.search(r"Ordered on:? ([A-Z][a-z]+ \d{1,2}, \d{4})", s)
    if m:
        out["date"] = body_date_iso(m.group(1)) or out["date"]
    m = re.search(r"Subtotal " + MONEY, s)
    out["subtotal"] = money(m.group(1)) if m else None
    m = re.search(r"(?:Estimated )?Tax " + MONEY, s)
    out["tax"] = money(m.group(1)) if m else None
    m = re.search(r"Order Total " + MONEY, s)
    out["grand_total"] = money(m.group(1)) if m else None
    # items: "<name> $price Qty n $line_total"  or  "<name> ... $price"
    items = []
    for m in re.finditer(r"([A-Z][^\n$]{3,90}?) " + MONEY + r" Qty (\d+) " + MONEY, flat(s)):
        items.append({"description": m.group(1).strip()[:120],
                      "quantity": int(m.group(3)),
                      "unit_price": money(m.group(2)),
                      "total": money(m.group(4))})
    for m in re.finditer(r"(AppleCare\+[^$\n]{0,80}?)\.? " + MONEY, flat(s)):
        items.append({"description": flat(m.group(1)).strip()[:120], "quantity": 1,
                      "unit_price": money(m.group(2)), "total": money(m.group(2))})
    out["items"] = items
    m = re.search(r"Billing and Payment(.{0,400})", flat(s))
    if m:
        out["payment_method"] = parse_payment(m.group(1))
    return out


def parse_retail_stub(msg, s, out):
    subj = msg["subject"] or ""
    m = re.search(r"receipt from (Apple[^.]*)", subj)
    out["merchant_name"] = m.group(1).strip() if m else "Apple Store (retail)"
    for part in msg.walk():
        fn = part.get_filename() or ""
        if fn.lower().endswith(".pdf"):
            out["notes"] = f"receipt data in PDF attachment {fn}; body has no amounts"
            dm = re.search(r"(20\d{6})R(\d+)", fn)
            if dm:
                d = dm.group(1)
                out["date"] = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
                out["order_id"] = "R" + dm.group(2)
    out["needs_pdf"] = True
    return out


def parse(path):
    msg, txt = eml_text(path)
    s = flat(txt)
    subj = (msg["subject"] or "").replace(" ", " ")
    from_addr = str(msg["from"] or "")
    dom_m = re.search(r"@([\w.-]+)", from_addr)
    out = {
        "source": "email",
        "message_id": str(msg["message-id"] or "").strip(),
        "sender_domain": dom_m.group(1).lower() if dom_m else None,
        "merchant_name": "Apple",
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

    if re.search(r"Apple Cash Transaction Receipt", s):
        out["receipt_kind"] = "apple_cash_payment"
        parse_apple_cash(s, out)
    elif re.search(r"Order Number: W\d+", s) or re.search(r"Ordered on:", s):
        out["receipt_kind"] = "store_order"
        parse_order(s, out)
    elif re.search(r"receipt from Apple (?!\.)[A-Z]", subj) or (
            "Thank you for shopping at the Apple Store" in s and len(s) < 600):
        out["receipt_kind"] = "retail_store_pdf"
        parse_retail_stub(msg, s, out)
    elif re.search(r"APPLE (ID|ACCOUNT)", s) and re.search(r"ORDER ID", s):
        out["receipt_kind"] = "app_store_classic"
        parse_classic(s, out)
    elif re.search(r"Receipt(?: & Renewal Notice)? [A-Z][a-z]+ \d{1,2}, \d{4} Order ID:", s):
        out["receipt_kind"] = "app_store_new"
        parse_new(s, out)
    elif re.search(r"Subscription Confirmation", subj, re.I):
        out["receipt_kind"] = "subscription_terms_notice"
        out["notes"] = "offer-acceptance notice, no charge amount; not a receipt"
        m = re.search(r"App (.*?) Subscription", s)
        if m:
            out["items"] = [{"description": flat(m.group(1)).strip()[:120],
                             "quantity": 1, "unit_price": None, "total": None}]
    else:
        out["receipt_kind"] = "unknown"

    out["item_count"] = len(out["items"]) if out["items"] else (None if out["receipt_kind"] in ("retail_store_pdf", "unknown") else 0)
    return out


if __name__ == "__main__":
    for p in sys.argv[1:]:
        rec = parse(p)
        print(json.dumps(rec, indent=2, ensure_ascii=False))
