#!/usr/bin/env python3
"""Prototype parser for Amazon order-confirmation emails -> receipt JSON.

Handles the format eras observed in the mailbox:
  A) 2015-2019 "Your Amazon.com order of \"X\"...": plaintext has
     Total Before Tax / Estimated Tax / Order Total. One (truncated) item title.
  B) 2020-2023 "Your Amazon.com order #...": de-itemized. Order # + Order Total only.
  C) 2023-2024 "order of \"X\"" revival: truncated title + "Qty : N" + Order Total.
  D) 2025-2026 "Ordered: ...": HTML has item titles (truncated), Quantity,
     unit price + line total (split "$"/"29"/"95" cells), Grand Total / Total.
     Plaintext hides all amounts, so HTML is required.
  E) Amazon Fresh in-store receipt / "Thanks for shopping at Amazon":
     Sub Total / Tax / Purchase Total / Total Items (no item lines).
  F) Whole Foods "order has been received": only Payment Authorization (estimate).

No Amazon order email carries card last4 or true payment-method detail.

Usage: parse_amazon.py file.eml [file2.eml ...]   (prints one JSON per file)
"""
import sys, os, re, json, html as htmllib
import email
from email import policy

MONEY = r"\$?\s*([\d,]+\.\d{2})"
ZWJUNK = "​‌‍⁦⁧⁨⁩﻿­͏   ⠀"
ORDER_ID = re.compile(r"\b((?:\d{3}|D01)-\d{7}-\d{7})\b")

BOILER = (
    "your orders", "your account", "buy again", "view or edit order",
    "view or manage order", "order #", "ordered", "shipped", "out for delivery",
    "delivered", "details", "order confirmation", "hello", "thanks for your order",
    "thank you for shopping", "we hope to see you again", "amazon.com",
    "ship to:", "arriving", "sold by", "questions?",
)
MARKETING_HEADERS = (
    "top picks for you", "keep shopping for", "deals for you",
    "customers also bought", "customers who bought", "also bought",
    "recommended based on", "related to items",
)


def _decode_amount(s):
    return float(s.replace(",", ""))


def get_bodies(msg):
    plain, html = "", ""
    for part in msg.walk():
        ct = part.get_content_type()
        if ct not in ("text/plain", "text/html"):
            continue
        try:
            content = part.get_content()
        except Exception:
            payload = part.get_payload(decode=True) or b""
            content = payload.decode("utf-8", "replace")
        if ct == "text/plain" and not plain:
            plain = content
        elif ct == "text/html" and not html:
            html = content
    return plain, html


def html_to_lines(h):
    h = re.sub(r"<(style|script).*?</\1>", " ", h, flags=re.S | re.I)
    h = re.sub(r"<[^>]+>", "\n", h)
    h = htmllib.unescape(h)
    h = h.translate({ord(c): " " for c in ZWJUNK})
    lines = [re.sub(r"\s+", " ", l).strip() for l in h.split("\n")]
    return [l for l in lines if l]


def find_total(text, labels):
    """Find 'Label: $x.xx' (same line) in plaintext."""
    for lab in labels:
        m = re.search(re.escape(lab) + r"\s*:?\s*" + MONEY, text, re.I)
        if m:
            return _decode_amount(m.group(1))
    return None


def find_total_lines(lines, labels):
    """Find label line, amount on same or one of next 2 lines (HTML layout)."""
    for i, l in enumerate(lines):
        for lab in labels:
            if l.lower().startswith(lab.lower()):
                m = re.search(MONEY, l)
                if m:
                    return _decode_amount(m.group(1))
                for j in (i + 1, i + 2):
                    if j < len(lines):
                        m = re.fullmatch(MONEY, lines[j])
                        if m:
                            return _decode_amount(m.group(1))
    return None


def collect_prices_after(lines, i, limit=10):
    """Collect decimal amounts following index i. Handles split '$'/'29'/'95'
    cells and inline '$29.95' lines. Stops at a non-price, non-'$' line."""
    prices, j = [], i + 1
    while j < len(lines) and j <= i + limit:
        l = lines[j]
        m = re.fullmatch(MONEY, l)
        if m:
            prices.append(_decode_amount(m.group(1)))
            j += 1
            continue
        if l == "$" and j + 2 < len(lines) and lines[j + 1].isdigit() and lines[j + 2].isdigit():
            prices.append(float(f"{int(lines[j+1].replace(',',''))}.{int(lines[j+2]):02d}")
                          if "," not in lines[j + 1]
                          else float(lines[j + 1].replace(",", "") + "." + lines[j + 2]))
            j += 3
            continue
        if l == "$" or l.isdigit():
            j += 1
            continue
        break
    return prices


def is_boiler(l):
    ll = l.lower()
    return any(ll.startswith(b) for b in BOILER) or ORDER_ID.fullmatch(l.strip("‫‬ ")) \
        or re.fullmatch(r"\d+", l)


def parse_items_new_era(lines):
    """Era D: title line precedes 'Quantity: N'; two prices follow (unit, line total)."""
    items = []
    # cut off marketing tail
    cut = len(lines)
    for i, l in enumerate(lines):
        if any(l.lower().startswith(h) for h in MARKETING_HEADERS):
            cut = i
            break
    lines = lines[:cut]
    for i, l in enumerate(lines):
        m = re.fullmatch(r"(?:Quantity|Qty)\s*:\s*(\d+)", l, re.I)
        if not m:
            continue
        qty = int(m.group(1))
        title = None
        for j in range(i - 1, max(i - 4, -1), -1):
            if not is_boiler(lines[j]) and not re.fullmatch(MONEY, lines[j]) and lines[j] != "$":
                title = lines[j]
                break
        prices = collect_prices_after(lines, i)
        unit = prices[0] if prices else None
        line_total = prices[1] if len(prices) > 1 else (unit * qty if unit is not None else None)
        items.append({"description": title, "quantity": qty,
                      "unit_price": unit, "total": line_total})
    return items


def parse(path):
    with open(path, "rb") as f:
        msg = email.message_from_binary_file(f, policy=policy.default)
    subject = str(msg.get("Subject", "") or "")
    plain, html = get_bodies(msg)
    lines = html_to_lines(html) if html else []
    all_text = plain + "\n" + "\n".join(lines)

    out = {
        "source": "email",
        "message_id": str(msg.get("Message-ID", "") or "").strip("<> "),
        "sender_domain": "amazon.com",
        "merchant_name": "Amazon.com",
        "date": None, "order_id": None,
        "grand_total": None, "subtotal": None, "tax": None, "tip": None,
        "item_count": None, "items": [],
        "payment_method": {"type": None, "card_last4": None},
        "currency": "USD",
        "raw_ref": {"mbox_file": None, "byte_offset": None},
    }

    try:
        d = msg.get("Date")
        if d:
            out["date"] = email.utils.parsedate_to_datetime(str(d)).date().isoformat()
    except Exception:
        pass

    m = ORDER_ID.search(all_text)
    if m:
        out["order_id"] = m.group(1)

    sub_l = subject.lower()
    if "whole foods" in sub_l or "whole foods" in all_text.lower()[:2000]:
        out["merchant_name"] = "Whole Foods Market"
    elif "fresh" in sub_l or "shopping at amazon fresh" in all_text.lower():
        out["merchant_name"] = "Amazon Fresh"

    # ---- totals (plaintext first: eras A/B/C/E carry values there) ----
    out["subtotal"] = find_total(plain, ["Total Before Tax", "Sub Total", "Subtotal", "Item Subtotal"])
    out["tax"] = find_total(plain, ["Estimated Tax", "Tax"])
    out["grand_total"] = find_total(plain, ["Order Total", "Purchase Total", "Grand Total", "Order total"])
    if out["grand_total"] is None:
        out["grand_total"] = find_total(plain, ["Payment Authorization"])  # WF estimate
    # HTML fallback (era D hides plaintext values)
    if out["grand_total"] is None and lines:
        out["grand_total"] = find_total_lines(lines, ["Grand Total", "Order Total", "Purchase Total", "Total"])
    if out["subtotal"] is None and lines:
        out["subtotal"] = find_total_lines(lines, ["Total Before Tax", "Sub Total", "Subtotal"])
    if out["tax"] is None and lines:
        out["tax"] = find_total_lines(lines, ["Estimated Tax", "Tax:"])

    # ---- items ----
    items = parse_items_new_era(lines) if lines else []
    if not items:
        # era A/C: one truncated title from subject or body
        title, qty = None, 1
        m = re.search(r'order of (\d+ x )?"(.+?)"', subject)
        if m:
            title = m.group(2)
            if m.group(1):
                qty = int(m.group(1).split()[0])
        else:
            m = re.search(r'Ordered:\s*(\d+)?\s*"(.+?)"', subject)
            if m:
                title = m.group(2)
                qty = int(m.group(1)) if m.group(1) else 1
            else:
                m = re.search(r'You ordered\s+"(.+?)"', plain)
                if m:
                    title = m.group(1)
        if title:
            items = [{"description": title, "quantity": qty,
                      "unit_price": None, "total": None}]
        # subject may note additional hidden items: 'and N more item(s)'
        m = re.search(r"and\s+.?(\d+).?\s+more item", subject)
        if m:
            for _ in range(int(m.group(1))):
                items.append({"description": None, "quantity": 1,
                              "unit_price": None, "total": None})

    out["items"] = items
    if items:
        out["item_count"] = sum(i.get("quantity") or 1 for i in items)
    else:
        m = re.search(r"Total Items\s*:?\s*(\d+)", all_text)
        if m:
            out["item_count"] = int(m.group(1))

    return out


def main():
    for path in sys.argv[1:]:
        rec = parse(path)
        print(json.dumps(rec, indent=2))


if __name__ == "__main__":
    main()
