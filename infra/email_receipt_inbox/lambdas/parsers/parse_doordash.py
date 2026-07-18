#!/usr/bin/env python3
"""Prototype parser for DoorDash receipt emails (no-reply@doordash.com).

Usage: parse_doordash.py FILE.eml [--mbox-file NAME --byte-offset N]

Emits JSON on stdout matching the receipts-online email schema:
{ source, message_id, sender_domain, merchant_name, date, order_id,
  grand_total, subtotal, tax, tip, item_count, items[], payment_method,
  currency, raw_ref }

Format eras observed (2017-2026):
  A) 2018-2019  "Order Confirmation": items + per-item prices, merchant+total
     inline ("<Merchant> $22.39 Paid with Apple Pay"); NO subtotal/tax/tip.
  B) 2020-2022  "Order Confirmation"/"we got your order": full breakdown
     (items, Subtotal, Taxes, Delivery Fee, Service Fee, Tip, Discounts,
     Total Charged). Richest era.
  C) 2023-2026  "Order Confirmation": itemization REMOVED. Only merchant,
     "Estimated Total"/"Total Charged" and payment type. Items live behind
     a "View Full Receipt" web link.
  D) 2024-2026  "Final receipt" (grocery/convenience only): full itemization
     incl. substitutions, Subtotal, Tax, fees, Dasher tip, Final total charged.
"""
import json
import re
import sys
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

MONEY = r"-?\$([\d,]+\.\d{2})"


class _TextExtractor(HTMLParser):
    """Flatten HTML to text lines, one per block-ish element."""

    BLOCK = {"tr", "br", "p", "div", "td", "li", "h1", "h2", "h3", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.chunks = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script"):
            self._skip += 1
        if tag in self.BLOCK:
            self.chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in ("style", "script"):
            self._skip = max(0, self._skip - 1)
        if tag in self.BLOCK:
            self.chunks.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.chunks.append(data)


def html_to_lines(html):
    p = _TextExtractor()
    p.feed(html)
    text = "".join(p.chunks)
    # kill zero-width / soft-hyphen / nbsp preheader junk
    text = re.sub(r"[​‌‍­͏﻿]", "", text)
    text = text.replace("\xa0", " ")
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.split("\n")]
    return [ln for ln in lines if ln]


def money(s):
    m = re.search(MONEY, s)
    if not m:
        return None
    v = float(m.group(1).replace(",", ""))
    return -v if s.lstrip().startswith("-") else v


def find_labeled_amount(lines, labels):
    """Label on one line, $amount on same or next line."""
    for i, ln in enumerate(lines):
        low = ln.lower()
        for lab in labels:
            if low == lab or low.startswith(lab):
                v = money(ln)
                if v is None and i + 1 < len(lines):
                    v = money(lines[i + 1])
                if v is not None:
                    return v
    return None


def parse_items(lines):
    """Item blocks: '<N>x' (qty alone or with description), description /
    bullet-modifier lines, then a $price line.  Substitution sections in
    'Final receipt' emails list the original under 'Substituted' and the
    replacement under 'Substituted with:'; only the replacement is kept."""
    items = []
    i = 0
    in_adjusted = False
    take_next_sub = True  # within adjusted section: original vs replacement
    end_markers = ("subtotal", "estimated total", "total charged", "final total")
    while i < len(lines):
        ln = lines[i]
        low = ln.lower()
        if low.startswith("items that were adjusted"):
            in_adjusted = True
        elif low.startswith("items you ordered"):
            in_adjusted = False
        elif low.startswith("substituted with"):
            take_next_sub = True
        elif low == "substituted" or low.startswith("substituted ("):
            take_next_sub = False
        elif any(low.startswith(m) for m in end_markers) and money(ln) is None:
            # breakdown section begins; stop item scan only for the totals
            pass
        m = re.match(r"^(\d+)\s*x\b\s*(.*)$", ln, re.I)
        if m:
            qty = int(m.group(1))
            desc = m.group(2).strip()
            price = money(desc)
            if price is not None:
                desc = re.sub(MONEY, "", desc).strip()
            j = i + 1
            # gather description / modifiers until a price line
            while j < len(lines) and price is None and j - i < 15:
                nxt = lines[j]
                if money(nxt) is not None and re.fullmatch(r"-?\$[\d,]+\.\d{2}", nxt):
                    price = money(nxt)
                    j += 1
                    break
                if nxt.startswith(("•", "-", "*")):
                    j += 1
                    continue
                if re.match(r"^(\d+)\s*x\b", nxt) or nxt.lower().startswith(
                    ("subtotal", "estimated total", "total charged")
                ):
                    break
                if not desc:
                    desc = nxt
                j += 1
            if desc and price is not None:
                keep = take_next_sub if in_adjusted else True
                if keep:
                    items.append(
                        {
                            "description": desc,
                            "quantity": qty,
                            "unit_price": round(price / qty, 2) if qty else None,
                            "total": price,
                        }
                    )
            i = j
            continue
        i += 1
    return items


def parse_eml(path, mbox_file=None, byte_offset=None):
    with open(path, "rb") as f:
        msg = BytesParser(policy=policy.default).parse(f)

    subject = msg.get("Subject", "") or ""
    body = msg.get_body(preferencelist=("html", "plain"))
    content = body.get_content() if body else ""
    if body is not None and body.get_content_type() == "text/html":
        lines = html_to_lines(content)
    else:
        lines = [re.sub(r"\s+", " ", ln).strip() for ln in content.split("\n")]
        lines = [ln for ln in lines if ln]

    # --- merchant ---
    merchant = None
    m = re.search(
        r"(?:Order Confirmation for .+? from|Final receipt for .+? from|"
        r"Details of your .*?delivery from)\s+(.+)$",
        subject,
    )
    if m:
        merchant = m.group(1).strip().rstrip(".")
    if not merchant:
        for i, ln in enumerate(lines):
            if re.match(r"^Total: \$[\d,]+\.\d{2}$", ln) and i > 0:
                merchant = lines[i - 1]
                break
    if not merchant:  # era A: merchant line, then "$22.39 Paid with ..." line
        for i, ln in enumerate(lines):
            m = re.match(r"^(?:(.+?) )?\$[\d,]+\.\d{2} Paid with", ln)
            if m:
                merchant = (m.group(1) or (lines[i - 1] if i > 0 else "")).strip() or None
                break

    # --- totals ---
    grand_total = find_labeled_amount(
        lines, ["final total charged", "total charged", "estimated total"]
    )
    if grand_total is None:
        for ln in lines:
            m = re.match(r"^Total: (\$[\d,]+\.\d{2})$", ln)
            if m:
                grand_total = money(m.group(1))
                break
    if grand_total is None:  # mid-2022: "Total:" label, amount on next line
        grand_total = find_labeled_amount(lines, ["total:", "total"])
    if grand_total is None:  # era A: "[<Merchant> ]$22.39 Paid with ..."
        for ln in lines:
            m = re.match(r"^(?:.+? )?(\$[\d,]+\.\d{2}) Paid with", ln)
            if m:
                grand_total = money(m.group(1))
                break

    subtotal = find_labeled_amount(lines, ["subtotal"])
    tax = find_labeled_amount(lines, ["taxes", "tax"])
    tip = find_labeled_amount(lines, ["dasher tip", "tip"])

    # --- items ---
    items = parse_items(lines)

    # --- payment ---
    pay_type = None
    full = " ".join(lines)
    for ln in lines:
        mm = re.search(r"Paid with\s+(.{2,50})", ln)
        if mm:
            pay_type = mm.group(1)
            pay_type = re.sub(r"\s*and/or\s*credits.*$", "", pay_type)
            pay_type = re.sub(r"\s*\$[\d,]+\.\d{2}.*$", "", pay_type)
            pay_type = re.sub(r"\s*-\s*For:.*$", "", pay_type).strip()
            # era A puts "<Merchant> $X Paid with Apple Pay - For: ..." on one line
            break
    last4 = None
    m = re.search(r"(?:ending in|\*{2,}|x{4})\s*(\d{4})", full, re.I)
    if m:
        last4 = m.group(1)

    # --- order id (only present in some eras' deep links) ---
    order_id = None
    m = re.search(r"orders?/([0-9a-f]{8}-[0-9a-f-]{27,})", content)
    if m:
        order_id = m.group(1)

    # --- date ---
    date_iso = None
    try:
        date_iso = parsedate_to_datetime(msg["Date"]).isoformat()
    except Exception:
        pass

    return {
        "source": "email",
        "message_id": msg.get("Message-ID", "").strip() or None,
        "sender_domain": (msg.get("From", "").split("@")[-1].strip("> ") or None),
        "merchant_name": merchant,
        "date": date_iso,
        "order_id": order_id,
        "grand_total": grand_total,
        "subtotal": subtotal,
        "tax": tax,
        "tip": tip,
        "item_count": len(items) if items else None,
        "items": items,
        "payment_method": {"type": pay_type, "card_last4": last4},
        "currency": "USD",
        "raw_ref": {"mbox_file": mbox_file, "byte_offset": byte_offset},
    }


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)
    path = args[0]
    mbox_file = byte_offset = None
    if "--mbox-file" in args:
        mbox_file = args[args.index("--mbox-file") + 1]
    if "--byte-offset" in args:
        byte_offset = int(args[args.index("--byte-offset") + 1])
    print(json.dumps(parse_eml(path, mbox_file, byte_offset), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
