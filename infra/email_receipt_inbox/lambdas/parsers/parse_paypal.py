#!/usr/bin/env python3
"""Prototype parser: PayPal payment-receipt emails -> receipts-online summary JSON.

Usage: python3 parse_paypal.py <path.eml> [more.eml ...]
Emits one JSON object per input (list if multiple) on stdout.

Handles PayPal receipt formats 2013-2026:
  A. 2013-2017 classic  "Receipt for Your Payment to X" (multipart text+html)
  B. 2018-2020 classic  same layout, html-only, item table Description/Unit price/Qty/Amount
  C. 2020-2025          "You have authorized a payment to X" (pre-auth receipt)
  D. 2024               "Receipt for your PayPal payment" (goods & services)
  E. 2023-2024          "You sent a $X USD payment" (friends & family, no items)
  F. 2025-2026          "Merchant: $X USD" / "You paid $X USD to Merchant" (new template)

Notes:
  - card_last4 is usually a BANK ACCOUNT last4 (e.g. Chase ••1234) or "PayPal Balance";
    still useful for Chase-CSV reconciliation.
  - tax/tip almost never present on PayPal receipts (merchant-side detail).
  - 2025+ template truncates long merchant names ("Private Internet Acc...").
"""
import json
import os
import re
import sys
from datetime import datetime
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser


# ---------------------------------------------------------------- html -> lines
class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script", "head", "title"):
            self.skip += 1
        elif tag in ("br", "tr", "p", "div", "li", "table"):
            self.out.append("\n")
        elif tag == "td":
            self.out.append(" | ")

    def handle_endtag(self, tag):
        if tag in ("style", "script", "head", "title"):
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_lines(html):
    p = _TextExtractor()
    p.feed(html)
    text = "".join(p.out)
    text = text.replace("\xa0", " ").replace("‌", "").replace("’", "'")
    lines = []
    for raw in text.split("\n"):
        line = re.sub(r"[ \t]+", " ", raw).strip()
        line = re.sub(r"^(\| ?)+", "", line)          # leading empty cells
        line = re.sub(r"( ?\| ?)+", " | ", line)       # collapse cell runs
        line = line.strip(" |").strip()
        if line:
            lines.append(line)
    return lines


AMT = r"\$\s?([\d,]+\.\d{2}|[\d,]+)"


def _money(s):
    try:
        return round(float(s.replace(",", "")), 2)
    except (ValueError, AttributeError):
        return None


def _parse_date(s):
    s = s.strip()
    fmts = ("%b %d, %Y %H:%M:%S", "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%b. %d, %Y")
    core = re.sub(r"\s+(P[SD]T|E[SD]T|C[SD]T|M[SD]T|GMT[+\-]?\d*|UTC)$", "", s)
    for f in fmts:
        try:
            return datetime.strptime(core, f).date().isoformat()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------- field pulls
def _find_merchant(subject, full, lines):
    subject = (subject or "").replace("\xa0", " ")
    m = re.match(r"Receipt for [Yy]our (?:PayPal )?[Pp]ayments? to (.+)", subject)
    if m:
        return m.group(1).strip()
    m = re.match(r"You have authorized a payment to (.+)", subject)
    if m:
        return m.group(1).strip()
    m = re.match(r"(.+?): \$[\d,\.]+ USD\s*$", subject)
    if m:
        return m.group(1).strip()
    pats = [
        r"You (?:sent a payment|authorized a payment) (?:of|for) \$[\d,\.]+ ?USD to ([^\n(]+)",
        r"You paid \$[\d,\.]+ ?USD to ([^\n(]+)",
        r"You sent \$[\d,\.]+ ?USD to ([^\n(]+?)\.?\n",
        r"You authorized a transaction to ([^\n.]+)\.",
    ]
    for p in pats:
        m = re.search(p, full)
        if m:
            return m.group(1).strip().rstrip(".")
    # "Merchant" block: name on next line
    for i, ln in enumerate(lines):
        if ln == "Merchant" and i + 1 < len(lines):
            return lines[i + 1].split(" | ")[0].strip()
    # goods&services P2P: "Payment to" / "RECIPIENT"
    for i, ln in enumerate(lines):
        m = re.search(r"(?:Payment to|RECIPIENT)$", ln) or re.search(r"\| (?:Payment to|RECIPIENT)$", ln)
        if m and i + 1 < len(lines):
            return lines[i + 1].split(" | ")[0].strip()
        m2 = re.search(r"(?:Payment to|RECIPIENT)\n", ln)
    return None


def _find_date(full, lines, msg):
    m = re.search(r"Transaction date\n([^\n|]+)", full)
    if m:
        d = _parse_date(m.group(1))
        if d:
            return d
    m = re.search(r"(?:^|\n)Date\n([A-Z][a-z]+ \d{1,2}, \d{4})", full)
    if m:
        d = _parse_date(m.group(1))
        if d:
            return d
    m = re.search(r"([A-Z][a-z]{2} \d{1,2}, \d{4} \d{2}:\d{2}:\d{2} [A-Z]{3,4})", full)
    if m:
        d = _parse_date(m.group(1))
        if d:
            return d
    if msg["date"]:
        try:
            return msg["date"].datetime.date().isoformat()
        except Exception:
            pass
    return None


def _find_amount_after(label_re, full):
    m = re.search(label_re + r"[^\n]*?\|\s*" + AMT, full)
    if not m:
        m = re.search(label_re + r"[^\$\n]*\n?\s*" + AMT, full)
    return _money(m.group(1)) if m else None


_HEADER_WORDS = {"description", "unit price", "qty", "amount", "subtotal", "total",
                 "payment", "shipping and handling", "merchant"}


def _clean_desc(s):
    s = (s or "").split(" | ")[-1].strip()
    if not s or s.startswith("$") or "Qty:" in s or s.lower() in _HEADER_WORDS:
        return None
    return s


def _find_items(lines):
    items = []
    # S1: one line "desc | $unit [USD] | qty | $amt [USD]"
    s1 = re.compile(
        r"^(?P<desc>.*?)\s*\|\s*\$(?P<unit>[\d,]+\.\d{2})(?: USD)?\s*\|\s*(?P<qty>\d+)\s*\|\s*\$(?P<amt>[\d,]+\.\d{2})(?: USD)?$"
    )
    # S2: line "$unit USD | qty | $amt USD" (desc on the previous line, or empty)
    s2 = re.compile(
        r"^\$(?P<unit>[\d,]+\.\d{2})(?: USD)?\s*\|\s*(?P<qty>\d+)\s*\|\s*\$(?P<amt>[\d,]+\.\d{2})(?: USD)?$"
    )
    for i, ln in enumerate(lines):
        m = s1.match(ln)
        desc = None
        if m:
            desc = _clean_desc(m.group("desc"))
        else:
            m = s2.match(ln)
            if m and i > 0:
                desc = _clean_desc(lines[i - 1])
        if m:
            items.append({
                "description": desc,
                "quantity": int(m.group("qty")),
                "unit_price": _money(m.group("unit")),
                "total": _money(m.group("amt")),
            })
    if items:
        return items
    # S3: cell-per-line "$unit USD" / "qty" / "$amt USD" triplets
    for i in range(len(lines) - 2):
        if (re.fullmatch(r"\$[\d,]+\.\d{2} USD", lines[i])
                and re.fullmatch(r"\d{1,3}", lines[i + 1])
                and re.fullmatch(r"\$[\d,]+\.\d{2} USD", lines[i + 2])):
            items.append({
                "description": _clean_desc(lines[i - 1]) if i > 0 else None,
                "quantity": int(lines[i + 1]),
                "unit_price": _money(lines[i].strip("$ USD").replace("USD", "")),
                "total": _money(lines[i + 2].replace("USD", "").strip("$ ")),
            })
    if items:
        return items
    # S4 (2025+): "desc" / "Qty: N" / "$amt" — either "Qty: N | $amt" or split lines
    for i, ln in enumerate(lines):
        m = re.fullmatch(r"Qty: (\d+)(?: \| \$([\d,]+\.\d{2}))?", ln)
        if not m:
            continue
        qty = int(m.group(1))
        total = _money(m.group(2)) if m.group(2) else None
        if total is None and i + 1 < len(lines):
            m2 = re.fullmatch(r"\$([\d,]+\.\d{2})", lines[i + 1])
            total = _money(m2.group(1)) if m2 else None
        desc = _clean_desc(lines[i - 1]) if i > 0 else None
        if total is not None:
            items.append({
                "description": desc,
                "quantity": qty,
                "unit_price": round(total / qty, 2) if qty else total,
                "total": total,
            })
    return items


def _find_payment_method(full, lines):
    ptype, last4 = None, None
    # last4 markers: "x-1234", "x- 1234", "••1234", "**1234", "ending in 1234"
    m = re.search(r"(?:x-\s?|••\s?|\*\*\s?|ending in )(\d{4})", full)
    if m:
        last4 = m.group(1)
    # type from funding-source context
    ctx = ""
    anchor = re.search(
        r"(Funding Sources Used[^\n]*|Paid with:|Payment method:|Paid [^\n]+ with)\n((?:[^\n]*\n){0,4})",
        full,
    )
    if anchor:
        ctx = anchor.group(0)
    hay = ctx or full
    if re.search(r"PayPal Balance", hay):
        ptype = "paypal_balance"
    if re.search(r"(?i)(visa|mastercard|amex|american express|discover|credit card|debit card)", hay):
        ptype = "card"
    elif re.search(r"(?i)(bank|checking|savings)", hay):
        ptype = "bank"
    elif ptype is None and last4:
        ptype = "unknown"
    if ptype is None and re.search(r"PayPal Balance", full):
        ptype = "paypal_balance"
    return ({"type": ptype, "card_last4": last4} if (ptype or last4) else None)


# ---------------------------------------------------------------- main parse
def parse_eml(path):
    with open(path, "rb") as f:
        msg = BytesParser(policy=policy.default).parse(f)

    body = msg.get_body(preferencelist=("html", "plain"))
    content = body.get_content() if body else ""
    if body and body.get_content_type() == "text/html":
        lines = html_to_lines(content)
    else:
        lines = [re.sub(r"\s+", " ", l).strip() for l in content.split("\n") if l.strip()]
    full = "\n".join(lines)

    subject = str(msg["subject"] or "").replace("\xa0", " ")
    from_addr = str(msg["from"] or "")
    dom = from_addr.split("@")[-1].strip(">").lower() if "@" in from_addr else None

    merchant = _find_merchant(subject, full, lines)
    date = _find_date(full, lines, msg)

    txn = re.search(r"Transaction ID:?\s*\n?\s*([0-9A-Z]{17})", full)
    order_id = txn.group(1) if txn else None
    inv = re.search(r"Invoice ID:?\s*\n?\s*([^\s|][^\n|]*)", full)
    invoice_id = inv.group(1).strip() if inv else None

    grand_total = _find_amount_after(r"\nTotal", full)
    if grand_total is None:
        grand_total = _find_amount_after(
            r"\n(?:Your total charge:?|You paid|Money sent|Amount you'll pay)", full)
    if grand_total is None:
        m = re.search(
            r"You (?:sent(?: a payment (?:of|for))?|paid|authorized a payment of) " + AMT, full)
        grand_total = _money(m.group(1)) if m else None
    subtotal = _find_amount_after(r"\nSubtotal", full)
    tax_m = re.search(r"\n((?:Sales )?Tax)[^\n]*?\|\s*" + AMT, full)
    tax = _money(tax_m.group(2)) if tax_m else None

    items = _find_items(lines)
    payment_method = _find_payment_method(full, lines)

    currency = None
    cm = re.search(r"\$[\d,\.]+ ?(USD|EUR|GBP|CAD|AUD|JPY)", full) or re.search(r"(USD|EUR|GBP)", subject)
    if cm:
        currency = cm.group(1)

    # raw_ref from optional sidecar written at extraction time
    raw_ref = {"mbox_file": None, "byte_offset": None}
    side = path + ".ref.json"
    if os.path.exists(side):
        with open(side) as f:
            ref = json.load(f)
        raw_ref = {"mbox_file": ref.get("mbox_file"), "byte_offset": ref.get("byte_offset")}

    return {
        "source": "email",
        "message_id": str(msg["message-id"] or "").strip("<> ") or None,
        "sender_domain": dom,
        "merchant_name": merchant,
        "date": date,
        "order_id": order_id,
        "invoice_id": invoice_id,   # extra: PayPal has both txn id and merchant invoice id
        "grand_total": grand_total,
        "subtotal": subtotal,
        "tax": tax,
        "tip": None,
        "item_count": len(items),
        "items": items,
        "payment_method": payment_method,
        "currency": currency,
        "raw_ref": raw_ref,
    }


if __name__ == "__main__":
    outs = [parse_eml(p) for p in sys.argv[1:]]
    print(json.dumps(outs[0] if len(outs) == 1 else outs, indent=2, ensure_ascii=False))
