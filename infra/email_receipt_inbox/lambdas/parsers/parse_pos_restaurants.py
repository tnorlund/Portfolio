#!/usr/bin/env python3
"""Prototype parser for POS restaurant e-receipts (Toast, Square, SpotOn, Clover).

Usage: python3 parse_pos-restaurants.py file.eml [file2.eml ...]
Emits one JSON object per input file (to stdout, or use --out DIR).

Providers handled:
  toasttab.com  - "Online Order Receipt", "Order & Pay Receipt", "Tell us how we did" review receipts
  squareup.com  - "Receipt from X" (2015-2026), "Your order from X" pickup confirmations
  spoton.com    - "Pickup Order Confirmation" receipts
  clover.com    - stub receipts (merchant/date/total only; itemization is behind a web link)
"""
import argparse
import json
import os
import re
import sys
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

MONEY = re.compile(r"^[\$£€]\s?(-?\d[\d,]*\.\d{2})$")
MONEY_ANY = re.compile(r"[\$£€]\s?(-?\d[\d,]*\.\d{2})")
CARD_BRANDS = r"(Visa|Master[Cc]ard|MASTERCARD|Amex|American Express|Discover|JCB|Diners|Union ?Pay|Interac)"
CARD_INLINE = re.compile(CARD_BRANDS + r"[^\d]{0,30}(\d{4})\b")
CARD_BRAND_ONLY = re.compile(r"^" + CARD_BRANDS + r"$")
CARD_MASKED = re.compile(r"^[xX*•]{4,}\s?(\d{4})$")


class TextLines(HTMLParser):
    """Flatten HTML into a list of visible text lines (one per block-ish element)."""

    BLOCKS = {"br", "tr", "p", "div", "td", "li", "h1", "h2", "h3", "h4", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines, self.cur, self.skip = [], [], 0

    def _flush(self):
        t = " ".join("".join(self.cur).split())
        if t:
            self.lines.append(t)
        self.cur = []

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script", "head", "title"):
            self.skip += 1
        if tag in self.BLOCKS:
            self._flush()

    def handle_endtag(self, tag):
        if tag in ("style", "script", "head", "title"):
            self.skip = max(0, self.skip - 1)
        if tag in self.BLOCKS:
            self._flush()

    def handle_data(self, data):
        if not self.skip:
            self.cur.append(data)


def html_to_lines(html_text):
    p = TextLines()
    p.feed(html_text)
    p._flush()
    out = []
    for l in p.lines:
        if not out or out[-1] != l:
            out.append(l)
    return out


def load_email(path):
    with open(path, "rb") as f:
        msg = BytesParser(policy=policy.default).parse(f)
    html_body, plain_body = None, None
    for part in msg.walk():
        ct = part.get_content_type()
        try:
            if ct == "text/html" and html_body is None:
                html_body = part.get_content()
            elif ct == "text/plain" and plain_body is None:
                plain_body = part.get_content()
        except Exception:
            pass
    if html_body:
        lines = html_to_lines(html_body)
    elif plain_body:
        lines = [" ".join(l.split()) for l in plain_body.splitlines() if l.strip()]
    else:
        lines = []
    return msg, lines


def money(s):
    m = MONEY_ANY.search(s or "")
    return float(m.group(1).replace(",", "")) if m else None


def currency_of(lines):
    joined = " ".join(lines)
    if "£" in joined:
        return "GBP"
    if "€" in joined:
        return "EUR"
    return "USD"


def find_amount_after(lines, label_re, start=0, window=2):
    """Return amount on a line matching label_re (inline) or within `window` following lines."""
    rx = re.compile(label_re, re.I)
    for i in range(start, len(lines)):
        if rx.search(lines[i]):
            amt = money(lines[i])
            if amt is not None and not rx.fullmatch(lines[i]):
                pass  # label and amount inline
            if amt is not None:
                return amt, i
            for j in range(i + 1, min(i + 1 + window, len(lines))):
                amt = money(lines[j])
                if amt is not None:
                    return amt, j
    return None, None


def extract_card(lines):
    for i, l in enumerate(lines):
        m = CARD_INLINE.search(l)
        if m:
            return m.group(1).title().replace("Mastercard", "Mastercard"), m.group(2)
        m = CARD_BRAND_ONLY.match(l)
        if m and i + 1 < len(lines):
            m2 = CARD_MASKED.match(lines[i + 1])
            if m2:
                return m.group(1).title(), m2.group(1)
    return None, None


# ---------------------------------------------------------------- Toast
def parse_toast(msg, lines, subject):
    out = {}
    m = re.search(r"Receipt for \$?[\d.,]* at (.+?)\s*$", subject)
    if not m:
        m = re.search(r"review your receipt for Order #\d+ at (.+?)(?: - .*)?$", subject)
    if not m:
        m = re.search(r"Receipt for Order #\d+ at (.+?)(?: - .*)?$", subject)
    if m:
        out["merchant_name"] = m.group(1).strip()
    else:
        # first line is usually "Merchant - phone"
        for l in lines[:5]:
            mm = re.match(r"^(.*?)\s*-\s*[\d()\- .]{7,}$", l)
            if mm:
                out["merchant_name"] = mm.group(1).strip()
                break

    m2 = next((re.search(r"Check #(\w+)", l) for l in lines if re.search(r"Check #\w+", l)), None)
    if m2:
        out["order_id"] = "Check #" + m2.group(1)

    # items: between "Ordered:" (or "How was your visit?") and "Subtotal"
    try:
        end = next(i for i, l in enumerate(lines) if re.fullmatch(r"Subtotal", l, re.I))
    except StopIteration:
        end = None
    items = []
    if end is not None:
        start = 0
        for i, l in enumerate(lines[:end]):
            if re.fullmatch(r"Ordered:", l) or "How was your visit" in l:
                start = i + 1
        i = start
        pending = None  # (name, qty)
        skip_rx = re.compile(
            r"^(Due:|Ordered:|Server:|Table \d+|Check #|How was your visit|The restaurant tracks|"
            r"\d{1,2}/\d{1,2}/\d{2,4}.*[AP]M|Pick up|Take ?Out|Delivery|Dine In|.*\(Online\)$)",
            re.I,
        )
        while i < end:
            l = lines[i]
            if MONEY.match(l):
                if pending:
                    name, qty = pending
                    price = money(l)
                    items.append(
                        {
                            "description": name,
                            "quantity": qty,
                            "unit_price": round(price / qty, 2) if qty else price,
                            "total": price,
                        }
                    )
                    pending = None
            elif not skip_rx.match(l) and l != out.get("merchant_name"):
                qm = re.match(r"^(\d+)\s+(.+)$", l)
                if qm:
                    pending = (qm.group(2), int(qm.group(1)))
                else:
                    # modifier line (e.g. "$Grilled Chicken", "Avocado")
                    pending = (l.lstrip("$"), 1)
            i += 1

    out["items"] = items
    out["subtotal"], _ = find_amount_after(lines, r"^Subtotal$")
    out["tax"], _ = find_amount_after(lines, r"^Tax(es)?$")
    out["tip"], _ = find_amount_after(lines, r"^Tip$")
    out["grand_total"], _ = find_amount_after(lines, r"^Total$")
    return out


# ---------------------------------------------------------------- Square
def parse_square(msg, lines, subject):
    out = {}
    m = re.search(r"Receipt from (.+)$", subject) or re.search(r"Your order from (.+)$", subject)
    if m:
        out["merchant_name"] = m.group(1).strip()
    else:
        for l in lines:
            mm = re.search(r"Receipt for \$[\d.,]+ at (.+?) on ", l)
            if mm:
                out["merchant_name"] = mm.group(1)
                break

    # receipt code (#FGcX) and/or Order #
    order_id = None
    for l in lines:
        mm = re.search(r"Order #:?\s*(\d+)", l)
        if mm:
            order_id = "Order #" + mm.group(1)
            break
    if not order_id:
        for l in lines:
            if re.fullmatch(r"#\w{4,6}", l):
                order_id = l
                break
    out["order_id"] = order_id

    # items: (name [× N]) followed by $price, before Total/Subtotal footer
    stop_rx = re.compile(
        r"^(Purchase Subtotal|Subtotal|Total|Tip|Tax|Sales Tax|Shop Online|Receipt Settings)$", re.I
    )
    tax_name_rx = re.compile(r"\(\d+(\.\d+)?%\)")
    items = []
    pending = None
    for l in lines:
        if stop_rx.match(l) and items:
            break
        if MONEY.match(l):
            if pending:
                name = pending
                qty = 1
                qm = re.search(r"^(.*?)\s*[×x]\s*(\d+)$", name)
                if qm:
                    name, qty = qm.group(1).strip(), int(qm.group(2))
                price = money(l)
                items.append(
                    {
                        "description": name,
                        "quantity": qty,
                        "unit_price": round(price / qty, 2) if qty else price,
                        "total": price,
                    }
                )
                pending = None
        else:
            junk = (
                stop_rx.match(l)
                or tax_name_rx.search(l)
                or re.search(r"Square Receipt|automatically sends|How was your experience|Order confirmed|"
                             r"Thank you|Estimated Pickup|Pickup location|Pickup instructions|Order status|"
                             r"Receipt for \$|reply to this email|leave feedback", l, re.I)
                or l == out.get("merchant_name")
            )
            pending = None if junk else l
    out["items"] = items

    out["subtotal"], _ = find_amount_after(lines, r"^(Purchase )?Subtotal$")
    tax, _ = find_amount_after(lines, r"^(Sales )?Tax(es)?$")
    if tax is None:
        # modern Square labels tax as e.g. "California (7.25%)"
        for i, l in enumerate(lines):
            if tax_name_rx.search(l) and "CC" not in l:
                tax = money(l)
                if tax is None and i + 1 < len(lines):
                    tax = money(lines[i + 1])
                if tax is not None:
                    break
    out["tax"] = tax
    out["tip"], _ = find_amount_after(lines, r"^Tip$")
    out["grand_total"], _ = find_amount_after(lines, r"^Total$")
    if out["grand_total"] is None:
        for l in lines:
            mm = re.search(r"Receipt for \$([\d.,]+) at ", l)
            if mm:
                out["grand_total"] = float(mm.group(1).replace(",", ""))
                break
    return out


# ---------------------------------------------------------------- SpotOn
def parse_spoton(msg, lines, subject):
    out = {}
    m = re.search(r"from (.+?)(?:\.|$)", subject)
    for l in lines:
        mm = re.search(r"order from (.+?)\. Check out", l)
        if mm:
            out["merchant_name"] = mm.group(1).strip()
            break
    if "merchant_name" not in out and m:
        out["merchant_name"] = m.group(1).strip()

    for l in lines:
        mm = re.search(r"Order #([\w-]+)", l)
        if mm:
            out["order_id"] = "Order #" + mm.group(1)
            break

    # items: "Nx" line, then name, then $price; modifier lines in between are noted
    items = []
    try:
        start = next(i for i, l in enumerate(lines) if l == "Order Details")
    except StopIteration:
        start = 0
    qty = None
    name = None
    for l in lines[start:]:
        if re.match(r"^(Item total|Sales Tax|Total Charged)", l, re.I):
            break
        qm = re.fullmatch(r"(\d+)x", l)
        if qm:
            qty, name = int(qm.group(1)), None
            continue
        if qty is not None:
            if MONEY.match(l):
                if name:
                    price = money(l)
                    items.append(
                        {
                            "description": name,
                            "quantity": qty,
                            "unit_price": round(price / qty, 2),
                            "total": price,
                        }
                    )
                qty, name = None, None
            elif name is None:
                name = l
            # extra lines after price & before next "Nx" are modifiers; ignored
    out["items"] = items
    out["subtotal"], _ = find_amount_after(lines, r"^Item total$")
    out["tax"], _ = find_amount_after(lines, r"^Sales Tax$")
    out["tip"], _ = find_amount_after(lines, r"^Tip$")
    out["grand_total"], _ = find_amount_after(lines, r"^Total Charged$")
    return out


# ---------------------------------------------------------------- Clover
def parse_clover(msg, lines, subject):
    out = {"items": [], "subtotal": None, "tax": None, "tip": None}
    m = re.search(r"Your receipt from (.+)$", subject)
    if m:
        out["merchant_name"] = m.group(1).strip()
    for l in lines:
        if MONEY.match(l):
            out["grand_total"] = money(l)
            break
    out["order_id"] = None
    return out


# ---------------------------------------------------------------- driver
def parse_eml(path, raw_ref=None):
    msg, lines = load_email(path)
    from_addr = str(msg.get("From", ""))
    dm = re.search(r"@([\w.\-]+)", from_addr)
    sender_domain = dm.group(1).lower() if dm else None
    subject = str(msg.get("Subject", "")).replace("\n", " ").strip()

    root = ".".join(sender_domain.split(".")[-2:]) if sender_domain else ""
    if root == "toasttab.com":
        parsed = parse_toast(msg, lines, subject)
    elif root in ("squareup.com", "square.com"):
        parsed = parse_square(msg, lines, subject)
    elif root == "spoton.com":
        parsed = parse_spoton(msg, lines, subject)
    elif root == "clover.com":
        parsed = parse_clover(msg, lines, subject)
    else:
        parsed = {"items": []}

    try:
        date_iso = parsedate_to_datetime(msg["Date"]).isoformat()
    except Exception:
        date_iso = None

    brand, last4 = extract_card(lines)
    items = parsed.get("items") or []
    result = {
        "source": "email",
        "message_id": str(msg.get("Message-ID", "")).strip() or None,
        "sender_domain": sender_domain,
        "merchant_name": parsed.get("merchant_name"),
        "date": date_iso,
        "order_id": parsed.get("order_id"),
        "grand_total": parsed.get("grand_total"),
        "subtotal": parsed.get("subtotal"),
        "tax": parsed.get("tax"),
        "tip": parsed.get("tip"),
        "item_count": len(items) or None,
        "items": items,
        "payment_method": {"type": brand.lower() if brand else ("card" if last4 else None), "card_last4": last4},
        "currency": currency_of(lines),
        "raw_ref": raw_ref or {"mbox_file": None, "byte_offset": None},
    }
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("eml", nargs="+")
    ap.add_argument("--out", help="directory for per-file .json outputs")
    args = ap.parse_args()
    for p in args.eml:
        ref = None
        sidecar = p + ".ref.json"
        if os.path.exists(sidecar):
            with open(sidecar) as f:
                rr = json.load(f)
            ref = {"mbox_file": rr.get("mbox_file"), "byte_offset": rr.get("byte_offset")}
        res = parse_eml(p, raw_ref=ref)
        text = json.dumps(res, indent=2, ensure_ascii=False)
        if args.out:
            os.makedirs(args.out, exist_ok=True)
            name = os.path.splitext(os.path.basename(p))[0] + ".json"
            with open(os.path.join(args.out, name), "w") as f:
                f.write(text + "\n")
            print(f"wrote {name}", file=sys.stderr)
        else:
            print(text)


if __name__ == "__main__":
    main()
