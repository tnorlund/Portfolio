#!/usr/bin/env python3
"""Prototype parser for recurring-service e-receipts (utilities, SaaS, eShop).

Usage: python3 parse_services.py file.eml [file2.eml ...]
Emits one JSON object per input file (to stdout, or use --out DIR).

Emits the receipts-online email schema:
{ source, message_id, sender_domain, merchant_name, date, order_id,
  grand_total, subtotal, tax, tip, item_count, items[], payment_method,
  currency, raw_ref }

Domains handled (dispatch by sender_domain):
  socalgas.com          - Southern California Gas. "Bill Ready Notification"
                          (Total Amount Due / Balance), "One-Time Payment
                          Confirmation" and "Automatic Monthly Payment is
                          scheduled" (Payment Amount + Confirmation Number).
  digitalocean.com      - "Your <Month> invoice is available" and "Thanks for
                          your payment" (Amount paid). Dunning emails ("We
                          couldn't process your payment", "Failed to process
                          card") are NOT receipts and yield no total.
  stripe.com            - Stripe-powered merchant receipts ("Your receipt from
                          <Merchant> #...", "Your <Merchant> receipt [#...]").
                          merchant_name is the actual merchant, not Stripe.
                          "$X payment to <M> was unsuccessful" are not receipts.
  accounts.nintendo.com - plain-text eShop receipts: "Confirmation of Digital
                          Purchase" (Purchased Item + Total) and "Funds Added
                          to Your Account" (Total). merchant_name = "Nintendo".
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


class TextLines(HTMLParser):
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
    # Nintendo (and some SCE) are text/plain only; prefer plain there.
    if plain_body and not html_body:
        lines = [" ".join(l.split()) for l in plain_body.splitlines() if l.strip()]
    elif html_body:
        lines = html_to_lines(html_body)
    else:
        lines = []
    return msg, lines


def money(s):
    m = MONEY_ANY.search(s or "")
    return float(m.group(1).replace(",", "")) if m else None


def from_display_name(msg):
    raw = str(msg.get("From", ""))
    m = re.match(r'\s*"?([^"<]+?)"?\s*<', raw)
    return m.group(1).strip() if m else None


def find_amount_after(lines, label_re, start=0, window=2):
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


# ---------------------------------------------------------------- SoCalGas
def parse_socalgas(msg, lines, subject):
    out = {"merchant_name": "Southern California Gas Company", "items": [],
           "subtotal": None, "tax": None, "tip": None, "order_id": None,
           "grand_total": None, "payment_method": None}
    low = subject.lower()

    if "payment" in low or any("Payment Amount" in l for l in lines):
        amt, _ = find_amount_after(lines, r"^Payment Amount:?$", window=2)
        out["grand_total"] = amt
        for i, l in enumerate(lines):
            if re.match(r"Confirmation Number", l, re.I):
                m = re.search(r"(\d{5,})", l)
                if m:
                    out["order_id"] = m.group(1)
                elif i + 1 < len(lines) and re.fullmatch(r"\d{5,}", lines[i + 1]):
                    out["order_id"] = lines[i + 1]
                break
        # bank account name (e.g. "Chase") — no card number in these
        for i, l in enumerate(lines):
            if re.match(r"Bank Account:?$", l, re.I) and i + 1 < len(lines):
                out["payment_method"] = {"type": "bank account (%s)" % lines[i + 1], "card_last4": None}
                break
    else:  # bill-ready notification
        amt, _ = find_amount_after(lines, r"^Total Amount Due", window=2)
        if amt is None:
            # credit-balance notices: "Total Balance" / "Balance:" then "$X Credit"
            amt, _ = find_amount_after(lines, r"^(Total )?Balance", window=2)
        out["grand_total"] = amt
    return out


# ---------------------------------------------------------------- DigitalOcean
def parse_digitalocean(msg, lines, subject):
    out = {"merchant_name": "DigitalOcean", "items": [], "subtotal": None,
           "tax": None, "tip": None, "order_id": None, "grand_total": None,
           "payment_method": None}
    amt, _ = find_amount_after(lines, r"^Amount paid$", window=2)
    out["grand_total"] = amt
    if amt is not None:  # only a real receipt carries a subtotal
        sub, _ = find_amount_after(lines, r"^Usage charges", window=2)
        out["subtotal"] = sub
    m = re.search(r"Your (\w+ \d{4}) invoice", subject)
    if m:
        out["order_id"] = m.group(1)  # e.g. "June 2022"
    return out


# ---------------------------------------------------------------- Stripe
def parse_stripe(msg, lines, subject):
    out = {"items": [], "subtotal": None, "tax": None, "tip": None,
           "order_id": None, "grand_total": None}

    # merchant: subject "Your receipt from <M> #.." or "Your <M> receipt [#..]"
    merchant = None
    m = re.search(r"Your receipt from (.+?)\s*[#\[]", subject)
    if not m:
        m = re.search(r"Your (.+?) receipt\b", subject)
    if m:
        merchant = m.group(1).strip()
    if not merchant:
        disp = from_display_name(msg)
        merchant = disp if disp and disp.lower() != "stripe" else None
    if not merchant:
        for l in lines[:4]:
            if re.search(r"[A-Za-z]", l) and "receipt" not in l.lower():
                merchant = l.strip()
                break
    out["merchant_name"] = merchant

    # order/receipt number
    m = re.search(r"#\s?(\d{4}-\d{4})", subject)
    if not m:
        for i, l in enumerate(lines):
            if l == "Receipt number" and i + 1 < len(lines):
                m = re.match(r"(\S+)", lines[i + 1])
                break
    out["order_id"] = m.group(1) if m else None

    # totals
    amt, _ = find_amount_after(lines, r"^Amount paid$", window=2)
    if amt is None:
        amt, _ = find_amount_after(lines, r"^Total$", window=2)
    if amt is None:
        # "$7.00 Paid August 17, 2021" hero line
        for l in lines:
            if re.search(r"\bPaid\b", l) and MONEY_ANY.search(l):
                amt = money(l)
                break
    out["grand_total"] = amt

    # payment method: "Payment method" then "- 8644", or inline "•••• 8644"
    last4 = None
    m = re.search(r"(?:••••|\*{2,}|ending in|-)\s?(\d{4})\b", " ".join(lines))
    if m:
        last4 = m.group(1)
    out["payment_method"] = {"type": "card", "card_last4": last4} if last4 else None

    # best-effort single line item: the description line above "Qty N"
    for i, l in enumerate(lines):
        if re.match(r"Qty\s*\d+", l):
            desc = lines[i - 1] if i > 0 else None
            price = None
            for j in range(i, min(i + 3, len(lines))):
                if MONEY.match(lines[j]):
                    price = money(lines[j])
                    break
            if desc and price is not None and not desc.lower().startswith("receipt"):
                qm = re.search(r"Qty\s*(\d+)", l)
                q = int(qm.group(1)) if qm else 1
                out["items"].append({"description": desc, "quantity": q,
                                     "unit_price": round(price / q, 2) if q else price,
                                     "total": price})
            break
    return out


# ---------------------------------------------------------------- Nintendo
def parse_nintendo(msg, lines, subject):
    out = {"merchant_name": "Nintendo", "items": [], "subtotal": None,
           "tax": None, "tip": None, "order_id": None, "grand_total": None}
    text = "\n".join(lines)

    m = re.search(r"Transaction ID:\s*(\w+)", text)
    out["order_id"] = m.group(1) if m else None

    m = re.search(r"^Total:\s*" + MONEY_ANY.pattern, text, re.M)
    out["grand_total"] = float(m.group(1).replace(",", "")) if m else None

    # payment method
    ptype = None
    m = re.search(r"Payment Method:\s*(.+)", text)
    if m:
        ptype = m.group(1).strip()
    elif re.search(r"PayPal:", text):
        ptype = "PayPal"
    last4 = None
    m = re.search(r"(?:ending in|\*{2,})\s?(\d{4})\b", text)
    if m:
        last4 = m.group(1)
    out["payment_method"] = {"type": ptype, "card_last4": last4} if (ptype or last4) else None

    # item: purchased item (digital purchase) or funds-add
    m = re.search(r"Purchased Item:\s*(.+)", text)
    desc = m.group(1).strip() if m else None
    if desc:
        mm = re.search(r"Purchased Membership:\s*(.+)", text)
        if mm:
            desc = mm.group(1).strip()
        up = re.search(r"Unit Price:\s*" + MONEY_ANY.pattern, text)
        price = float(up.group(1).replace(",", "")) if up else out["grand_total"]
        out["items"].append({"description": desc, "quantity": 1,
                             "unit_price": price, "total": price})
    elif "Funds Added" in subject or "adding funds" in text:
        price = out["grand_total"]
        out["items"].append({"description": "Funds added to Nintendo Account",
                             "quantity": 1, "unit_price": price, "total": price})
    return out


DISPATCH = [
    ("socalgas", parse_socalgas),
    ("digitalocean", parse_digitalocean),
    ("stripe", parse_stripe),
    ("nintendo", parse_nintendo),
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

    items = parsed.get("items") or []
    joined = "".join(lines)
    currency = "GBP" if "£" in joined else "EUR" if "€" in joined else "USD"
    return {
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
        "payment_method": parsed.get("payment_method"),
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
