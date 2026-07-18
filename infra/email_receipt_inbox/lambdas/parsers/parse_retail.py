#!/usr/bin/env python3
"""Prototype parser for retail order-confirmation emails.

Brands: Target, Best Buy, eBay, Starbucks (card reloads).
Usage: parse_retail.py <path.eml> [more.eml ...]
Emits one JSON object per file (to stdout, or use --outdir to write .json files).

Schema (aligned with receipts-online summaries):
{ source, message_id, sender_domain, merchant_name, date, order_id,
  grand_total, subtotal, tax, tip, item_count, items[], payment_method{type,card_last4},
  currency, raw_ref{mbox_file, byte_offset} }
"""
import json
import os
import re
import sys
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from html import unescape

MONEY = r"\$?\s?(-?\d{1,3}(?:,\d{3})*\.\d{2})"


def _money(s):
    m = re.search(MONEY, s)
    return float(m.group(1).replace(",", "")) if m else None


def eml_to_lines(path):
    with open(path, "rb") as f:
        msg = BytesParser(policy=policy.default).parse(f)
    html_part = txt_part = None
    for p in msg.walk():
        ct = p.get_content_type()
        if ct == "text/html" and html_part is None:
            html_part = p
        elif ct == "text/plain" and txt_part is None:
            txt_part = p
    if html_part is not None:
        h = html_part.get_content()
        h = re.sub(r"(?is)<(style|script).*?</\1>", " ", h)
        h = re.sub(r"(?i)<br\s*/?>", "\n", h)
        h = re.sub(r"(?i)</(td|tr|p|div|table|h\d|li|span)>", "\n", h)
        t = re.sub(r"<[^>]+>", " ", h)
        t = unescape(t)
    elif txt_part is not None:
        t = txt_part.get_content()
    else:
        t = ""
    t = re.sub(r"[ \t\xa0‌͏​]+", " ", t)
    lines = [ln.strip() for ln in t.split("\n")]
    return msg, [ln for ln in lines if ln]


def base_record(msg, path):
    from_addr = str(msg.get("From", ""))
    m = re.search(r"@([\w.-]+)", from_addr)
    domain = m.group(1).lower() if m else None
    date = None
    if msg.get("Date"):
        try:
            date = parsedate_to_datetime(msg["Date"]).isoformat()
        except Exception:
            pass
    return {
        "source": "email",
        "message_id": msg.get("Message-ID"),
        "sender_domain": domain,
        "merchant_name": None,
        "date": date,
        "order_id": None,
        "grand_total": None,
        "subtotal": None,
        "tax": None,
        "tip": None,
        "item_count": None,
        "items": [],
        "payment_method": {"type": None, "card_last4": None},
        "currency": "USD",
        "raw_ref": {"mbox_file": None, "byte_offset": None, "eml_path": os.path.abspath(path)},
    }


def _next_money(lines, i, lookahead=3):
    """Value on the same line as a label, or within the next few lines."""
    for j in range(i, min(len(lines), i + 1 + lookahead)):
        v = _money(lines[j] if j > i else lines[i])
        if v is not None:
            return v
    return None


# ---------------------------------------------------------------- Target
def parse_target(rec, lines, subject):
    rec["merchant_name"] = "Target"
    for src in [subject] + lines[:6]:
        m = re.search(r"Order\s*#:?\s*(\d{8,})", src or "")
        if m:
            rec["order_id"] = m.group(1)
            break
    items = []
    for i, ln in enumerate(lines):
        m = re.match(r"Qty:\s*(\d+)", ln)
        if m and i > 0:
            qty = int(m.group(1))
            unit = _money(lines[i + 1]) if i + 1 < len(lines) and "/ ea" in lines[i + 1] else None
            desc = lines[i - 1]
            if desc and not re.match(r"(Qty|Order|Processing|Delivers)", desc):
                items.append({
                    "description": desc,
                    "quantity": qty,
                    "unit_price": unit,
                    "total": round(qty * unit, 2) if unit is not None else None,
                })
    rec["items"] = items
    for i, ln in enumerate(lines):
        if re.match(r"Subtotal", ln):
            rec["subtotal"] = _next_money(lines, i)
        elif re.match(r"Estimated Taxes?$", ln):
            rec["tax"] = _next_money(lines, i)
        elif re.match(r"(Total|Order total)$", ln):
            rec["grand_total"] = _next_money(lines, i)
    rec["item_count"] = sum(it["quantity"] or 1 for it in items) or None
    return rec


# ---------------------------------------------------------------- Best Buy
def parse_bestbuy(rec, lines, subject):
    rec["merchant_name"] = "Best Buy"
    for src in [subject] + lines:
        m = re.search(r"(BBY01-\d{9,})", src or "")
        if m:
            rec["order_id"] = m.group(1)
            break
    # Items: "<name>" / "Model: X" / "SKU: N" / Qty / [Price] / <qty> / $<price>
    items = []
    for i, ln in enumerate(lines):
        m = re.match(r"SKU:\s*(\d+)", ln)
        if not m:
            continue
        # name is 1-2 lines back (skip a Model: line)
        name = None
        for j in (i - 1, i - 2):
            if j >= 0 and not lines[j].startswith("Model:"):
                name = lines[j]
                break
        qty = price = None
        for j in range(i + 1, min(len(lines), i + 6)):
            if lines[j].isdigit() and qty is None:
                qty = int(lines[j])
            elif _money(lines[j]) is not None and price is None:
                price = _money(lines[j])
            if qty is not None and price is not None:
                break
        items.append({
            "description": name,
            "quantity": qty or 1,
            "unit_price": price,
            "total": round((qty or 1) * price, 2) if price is not None else None,
        })
    rec["items"] = items
    for i, ln in enumerate(lines):
        if re.match(r"Subtotal:?$", ln):
            rec["subtotal"] = _next_money(lines, i)
        elif re.match(r"Tax:?\*?$", ln):
            rec["tax"] = _next_money(lines, i)
        elif re.match(r"Order Total:?\*?$", ln):
            rec["grand_total"] = _next_money(lines, i)
    rec["item_count"] = sum(it["quantity"] or 1 for it in items) or None
    return rec


# ---------------------------------------------------------------- eBay
def parse_ebay(rec, lines, subject):
    rec["merchant_name"] = "eBay"
    for i, ln in enumerate(lines):
        m = re.search(r"Order number\s*:?\s*([\d-]{8,})?$", ln)
        if m:
            oid = m.group(1) or (lines[i + 1] if i + 1 < len(lines) else "")
            if re.match(r"^[\d-]{8,}$", oid.strip()):
                rec["order_id"] = oid.strip()
                break
    # Items: a title line followed (within 2 lines) by "Price:"/"Total :" label.
    items = []
    for i, ln in enumerate(lines):
        if re.match(r"(Price|Total)\s*:", ln):
            val = _next_money(lines, i, lookahead=1)
            # walk back over boilerplate to a plausible title
            j = i - 1
            while j >= 0 and re.match(r"(eBay Money Back|Money Back Guarantee)", lines[j]):
                j -= 1
            if j >= 0 and val is not None:
                title = lines[j]
                if len(title) > 8 and not _money(title) and not re.match(
                        r"(Order|Your|View|Browse|We|Seller|Estimated|ETA|Thanks)", title):
                    # qty pattern: "Price (2 x $9.97)"
                    qm = re.match(r"Price \((\d+) x \$([\d.]+)\)", ln)
                    qty = int(qm.group(1)) if qm else 1
                    unit = float(qm.group(2)) if qm else val
                    if not any(it["description"] == title for it in items):
                        items.append({"description": title, "quantity": qty,
                                      "unit_price": unit, "total": val})
    rec["items"] = items
    # Totals block after "Order total:"
    try:
        start = next(i for i, ln in enumerate(lines) if re.match(r"Order total\s*:", ln))
    except StopIteration:
        start = None
    if start is not None:
        for i in range(start, min(len(lines), start + 14)):
            ln = lines[i]
            if re.match(r"(Subtotal|Price)\b", ln):
                rec["subtotal"] = _next_money(lines, i, 1)
            elif re.match(r"Sales tax", ln):
                rec["tax"] = _next_money(lines, i, 1)
            elif re.match(r"Total charged to", ln):
                rec["grand_total"] = _next_money(lines, i, 3)
    if rec["grand_total"] is None and items:
        rec["grand_total"] = items[0]["total"]
    m = re.search(r"x\s?-(\d{4})", "\n".join(lines))
    if m:
        rec["payment_method"] = {"type": "card", "card_last4": m.group(1)}
    rec["item_count"] = sum(it["quantity"] or 1 for it in items) or None
    return rec


# ---------------------------------------------------------------- Starbucks
def parse_starbucks(rec, lines, subject):
    rec["merchant_name"] = "Starbucks"
    text = "\n".join(lines)
    m = re.search(r"Order Number:\s*([A-Z0-9]{10,})", text)
    if m:
        rec["order_id"] = m.group(1)
    m = re.search(r"Received:\s*\w+,\s*(\w+ \d{1,2}, \d{4})", text)
    if m:
        try:
            from datetime import datetime
            rec["date"] = datetime.strptime(m.group(1), "%B %d, %Y").date().isoformat()
        except ValueError:
            pass
    def grab(label):
        m = re.search(label + r":?\s*(?:USD\s*)?\n?\s*(?:USD\s*)?(\d[\d,]*\.\d{2})", text)
        return float(m.group(1).replace(",", "")) if m else None
    rec["subtotal"] = grab(r"(?:Merchandise )?Subtotal")
    rec["tax"] = grab(r"(?:Estimated )?Tax")
    rec["grand_total"] = grab(r"Total(?: Charge)?")
    if rec["grand_total"] is None:
        rec["grand_total"] = rec["subtotal"]
    amt = rec["grand_total"] or rec["subtotal"]
    rec["items"] = [{"description": "Starbucks Card Reload", "quantity": 1,
                     "unit_price": amt, "total": amt}]
    rec["item_count"] = 1
    return rec


BRANDS = [
    ("target.com", parse_target),
    ("bestbuy.com", parse_bestbuy),
    ("ebay.com", parse_ebay),
    ("starbucks.com", parse_starbucks),
]


def parse(path):
    msg, lines = eml_to_lines(path)
    rec = base_record(msg, path)
    subject = str(msg.get("Subject", ""))
    domain = rec["sender_domain"] or ""
    for suffix, fn in BRANDS:
        if domain == suffix or domain.endswith("." + suffix):
            return fn(rec, lines, subject)
    rec["merchant_name"] = domain
    return rec


def main():
    args = sys.argv[1:]
    outdir = None
    if "--outdir" in args:
        i = args.index("--outdir")
        outdir = args[i + 1]
        args = args[:i] + args[i + 2:]
        os.makedirs(outdir, exist_ok=True)
    for p in args:
        rec = parse(p)
        js = json.dumps(rec, indent=2, ensure_ascii=False)
        if outdir:
            out = os.path.join(outdir, os.path.splitext(os.path.basename(p))[0] + ".json")
            with open(out, "w") as f:
                f.write(js + "\n")
            print(out)
        else:
            print(js)


if __name__ == "__main__":
    main()
