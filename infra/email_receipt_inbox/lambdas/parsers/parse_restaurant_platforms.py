#!/usr/bin/env python3
"""Prototype parser for restaurant online-ordering platform e-receipts.

Usage: python3 parse_restaurant_platforms.py file.eml [file2.eml ...]
Emits one JSON object per input file (to stdout, or use --out DIR).

Emits the receipts-online email schema:
{ source, message_id, sender_domain, merchant_name, platform, date, order_id,
  grand_total, subtotal, tax, tip, item_count, items[], payment_method,
  currency, raw_ref }

`merchant_name` is the RESTAURANT (or, for SCE, the utility); `platform` names
the online-ordering service the confirmation came through.

Platforms handled (dispatch by sender_domain):
  chownow.com        - "Order Receipt" itemized confirmations ("Thanks for your
                       order (#...)"), plus pickup-ready notifications and
                       refunds (no itemization, refund total is negative).
  dylish.com         - "[<Restaurant> #<id>] Your order is confirmed/ready/sent"
                       itemized; price line is FOLLOWED by an "xN" qty line.
  oftendining.com    - "Order Confirmation: OD-<id>" itemized; name/$price pairs,
                       no per-item qty, "Sub Total"/"<region> Tax"/"Total".
  scewebservices.com - Southern California Edison (a UTILITY, grouped here by
                       domain). "Payment Confirmation" (Amount Paid) and
                       "<Month> SCE Bill is Ready to View" (Amount Due). Other
                       subjects (app-login, service-request, verify-email) are
                       not receipts and yield no total.
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
BARE_DECIMAL = re.compile(r"^(-?\d[\d,]*\.\d{2})$")


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
        # strip zero-width / soft-hyphen preheader junk
        l = re.sub(r"[​‌‍­͏﻿]", "", l).strip()
        if l and (not out or out[-1] != l):
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


def from_display_name(msg):
    raw = str(msg.get("From", ""))
    m = re.match(r'\s*"?([^"<]+?)"?\s*<', raw)
    if m:
        return m.group(1).strip()
    return None


def find_amount_after(lines, label_re, start=0, window=2):
    """Amount on a line matching label_re (inline) or within `window` following lines."""
    rx = re.compile(label_re, re.I)
    for i in range(start, len(lines)):
        if rx.search(lines[i]):
            amt = money(lines[i])
            if amt is not None:
                return amt, i
            for j in range(i + 1, min(i + 1 + window, len(lines))):
                amt = money(lines[j])
                if amt is not None:
                    return amt, j
    return None, None


# ---------------------------------------------------------------- ChowNow
def parse_chownow(msg, lines, subject):
    out = {"platform": "ChowNow", "items": []}

    # merchant: From display name is the restaurant (except platform-sent refunds)
    disp = from_display_name(msg)
    merchant = disp if disp and disp.lower() != "chownow" else None
    if not merchant:
        m = re.search(r"Order refund from (.+?)\s*\(#", subject)
        if m:
            merchant = m.group(1).strip()
    if not merchant:
        for l in lines:
            m = re.search(r"submitted to (.+?)\.", l) or re.search(r"refunded by (.+?)\.", l)
            if m:
                merchant = m.group(1).strip()
                break
    out["merchant_name"] = merchant

    # order id
    order_id = None
    m = re.search(r"\(#(\d+)\)", subject) or re.search(r"(?:ORDER #|order #)(\d+)", " ".join(lines))
    if m:
        order_id = m.group(1)
    out["order_id"] = order_id

    is_refund = "refund" in subject.lower() or any("Refund Amount" in l for l in lines)

    # totals
    out["subtotal"], _ = find_amount_after(lines, r"^Sub-?total:?$")
    out["tax"], _ = find_amount_after(lines, r"^Taxes?:?$")
    out["tip"], _ = find_amount_after(lines, r"^Tip:?$")
    if is_refund:
        amt, _ = find_amount_after(lines, r"^Refund Amount$")
        out["grand_total"] = -amt if amt is not None else None
    else:
        out["grand_total"], _ = find_amount_after(lines, r"^Total:?$")

    # items: between order-details header and Sub-total; blocks delimited by $price
    try:
        start = next(i for i, l in enumerate(lines)
                     if re.search(r"Details for order|Order Receipt", l))
    except StopIteration:
        start = 0
    try:
        end = next(i for i, l in enumerate(lines) if re.match(r"^Sub-?total", l, re.I))
    except StopIteration:
        end = len(lines)
    block, qty = [], 1
    for l in lines[start + 1:end]:
        if re.fullmatch(r"\d+", l):  # standalone qty line precedes the item name
            qty = int(l)
            block = []  # discard intro / "Details for order" text before the item
            continue
        if MONEY.match(l):
            price = money(l)
            names = [b for b in block if b.lower() not in ("additions", "add-ons", "options")]
            desc = " ".join(names).strip() or (block[0] if block else None)
            if desc:
                out["items"].append({
                    "description": desc, "quantity": qty,
                    "unit_price": round(price / qty, 2) if qty else price, "total": price,
                })
            block, qty = [], 1
        else:
            block.append(l)
    return out


# ---------------------------------------------------------------- Dylish
def parse_dylish(msg, lines, subject):
    out = {"platform": "Dylish", "items": []}
    m = re.match(r"^\[(.+?)\s+#[\w-]+\]", subject)
    out["merchant_name"] = m.group(1).strip() if m else None

    order_id = None
    m = re.search(r"#([\w-]+)\]", subject)
    if m:
        order_id = m.group(1)
    else:
        for l in lines:
            mm = re.search(r"ORDER#\s*([\w-]+)", l)
            if mm:
                order_id = mm.group(1)
                break
    out["order_id"] = order_id

    out["subtotal"], si = find_amount_after(lines, r"^Subtotal$")
    out["tax"], _ = find_amount_after(lines, r"^Tax( & Fees)?$")
    out["tip"], _ = find_amount_after(lines, r"^(Restaurant )?Tip$")
    out["grand_total"], _ = find_amount_after(lines, r"^Total$")

    # items: name [+ modifier lines], $price, then "xN".  Region ends at Subtotal.
    try:
        start = next(i for i, l in enumerate(lines) if re.fullmatch(r"Pickup|Delivery", l, re.I))
    except StopIteration:
        start = 0
    end = si if si is not None else len(lines)
    block = []
    i = start + 1
    while i < end:
        l = lines[i]
        if MONEY.match(l):
            price = money(l)
            qty = 1
            if i + 1 < len(lines):
                qm = re.fullmatch(r"x\s*(\d+)", lines[i + 1], re.I)
                if qm:
                    qty = int(qm.group(1))
                    i += 1
            desc = block[0] if block else None
            if desc:
                out["items"].append({
                    "description": desc, "quantity": qty,
                    "unit_price": round(price / qty, 2) if qty else price, "total": price,
                })
            block = []
        else:
            block.append(l)
        i += 1
    return out


# ---------------------------------------------------------------- OftenDining
def parse_oftendining(msg, lines, subject):
    out = {"platform": "OftenDining", "items": []}
    order_id = None
    m = re.search(r"Order Confirmation:\s*(OD-\d+)", subject)
    if m:
        order_id = m.group(1)
    out["order_id"] = order_id

    # merchant: line following the "Order Confirmation: OD-..." body line
    merchant = None
    for i, l in enumerate(lines):
        if re.match(r"Order Confirmation:\s*OD-\d+", l) and i + 1 < len(lines):
            merchant = lines[i + 1].strip()
            break
    out["merchant_name"] = merchant

    out["subtotal"], si = find_amount_after(lines, r"^Sub ?Total$")
    tax, _ = find_amount_after(lines, r"Tax$")
    out["tax"] = tax
    out["tip"], _ = find_amount_after(lines, r"^Tip$")
    out["grand_total"], _ = find_amount_after(lines, r"^Total$")

    # items: name then $price pairs, between order-detail block and "Sub Total"
    try:
        start = next(i for i, l in enumerate(lines) if re.match(r"Pickup Time:|Ordered On:", l))
    except StopIteration:
        start = 0
    end = si if si is not None else len(lines)
    pending = None
    for l in lines[start:end]:
        if re.match(r"Pickup Time:|Ordered On:|Type:|Store Invoice", l):
            continue
        if MONEY.match(l):
            if pending:
                price = money(l)
                out["items"].append({
                    "description": pending, "quantity": 1,
                    "unit_price": price, "total": price,
                })
                pending = None
        elif re.search(r"[A-Za-z]", l) and not re.match(r"^\d", l):
            pending = l
    return out


# ---------------------------------------------------------------- SCE (utility)
def parse_sce(msg, lines, subject):
    out = {"platform": "SCE", "merchant_name": "Southern California Edison",
           "items": [], "subtotal": None, "tax": None, "tip": None,
           "order_id": None, "grand_total": None}
    joined = " ".join(lines)
    low = subject.lower()

    if "payment" in low or any("Amount Paid" in l for l in lines):
        # newer layout: "Amount Paid" label then "$26.62" on a following line
        amt, _ = find_amount_after(lines, r"^Amount Paid$", window=2)
        if amt is None:
            # old column layout: first bare decimal after the headers
            for l in lines:
                if BARE_DECIMAL.match(l):
                    amt = float(l.replace(",", ""))
                    break
        out["grand_total"] = amt
        # confirmation number: labelled, else a 12-digit value beginning with 3
        conf = None
        for i, l in enumerate(lines):
            if l == "Confirmation Number":
                for j in range(i + 1, min(i + 4, len(lines))):
                    if re.fullmatch(r"\d{9,}", lines[j]):
                        conf = lines[j]
                        break
            if conf:
                break
        if not conf:
            conf = next((l for l in lines if re.fullmatch(r"3\d{11}", l)), None)
        out["order_id"] = conf
    elif "bill" in low or any("Amount Due" in l for l in lines):
        amt, _ = find_amount_after(lines, r"^Amount Due$", window=5)
        if amt is None:
            amt = next((money(l) for l in lines if MONEY_ANY.search(l)), None)
        out["grand_total"] = amt

    m = re.search(r"(?:X{4,}|\*{4,})(\d{4})\b", joined)
    last4 = m.group(1) if m else None
    ptype = "bank account" if re.search(r"Checking Account|Bank Account", joined, re.I) else None
    out["payment_method"] = {"type": ptype, "card_last4": last4} if (ptype or last4) else None
    return out


# ---------------------------------------------------------------- driver
def _card_from_lines(lines):
    joined = " ".join(lines)
    m = re.search(r"(MasterCard|Master[Cc]ard|Visa|Amex|American Express|Discover)"
                  r"[^\d]{0,20}(?:\*{2,}|ending in|x{4})?\s*(\d{4})\b", joined)
    if m:
        return m.group(1).replace("Master card", "Mastercard"), m.group(2)
    m = re.search(r"(Saved Credit Card|Credit Card|Nintendo eShop Funds|Apple Pay)", joined)
    if m:
        return m.group(1), None
    return None, None


DISPATCH = [
    ("chownow", parse_chownow),
    ("dylish", parse_dylish),
    ("oftendining", parse_oftendining),
    ("scewebservices", parse_sce),
]


def parse(path, raw_ref=None):
    msg, lines = load_email(path)
    from_addr = str(msg.get("From", ""))
    dm = re.search(r"@([\w.\-]+)", from_addr)
    sender_domain = dm.group(1).lower() if dm else None
    subject = str(msg.get("Subject", "")).replace("\n", " ").strip()

    parsed, handler = {"items": []}, None
    dom = sender_domain or ""
    for key, fn in DISPATCH:
        if key in dom:
            handler = fn
            break
    if handler:
        parsed = handler(msg, lines, subject)

    try:
        date_iso = parsedate_to_datetime(msg["Date"]).isoformat()
    except Exception:
        date_iso = None

    # payment: use handler's if present, else sniff card
    pm = parsed.get("payment_method", "unset")
    if pm == "unset":
        brand, last4 = _card_from_lines(lines)
        pm = {"type": brand, "card_last4": last4} if (brand or last4) else None

    items = parsed.get("items") or []
    currency = "GBP" if "£" in "".join(lines) else "EUR" if "€" in "".join(lines) else "USD"
    return {
        "source": "email",
        "message_id": str(msg.get("Message-ID", "")).strip() or None,
        "sender_domain": sender_domain,
        "merchant_name": parsed.get("merchant_name"),
        "platform": parsed.get("platform"),
        "date": date_iso,
        "order_id": parsed.get("order_id"),
        "grand_total": parsed.get("grand_total"),
        "subtotal": parsed.get("subtotal"),
        "tax": parsed.get("tax"),
        "tip": parsed.get("tip"),
        "item_count": len(items) or None,
        "items": items,
        "payment_method": pm,
        "currency": currency,
        "raw_ref": raw_ref or {"mbox_file": None, "byte_offset": None},
    }


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
        res = parse(p, raw_ref=ref)
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
