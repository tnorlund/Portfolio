#!/usr/bin/env python3
"""Prototype parser for the travel-housing email group.

Domains: airbnb.com, hellolanding.com, tesla.com

Handles (with real dollar data in the email body):
  - Airbnb "Your receipt from Airbnb" (2022-2026, format stable)
  - Airbnb 2026 "Confirmed: ... here's your Airbnb receipt" trip confirmations
  - Tesla Shop order confirmations ("Your Tesla Shop Order is Confirmed")
  - Landing reservation receipts ("Your Landing reservation receipt")

Recognizes but cannot fully extract (no amounts in email body):
  - Tesla service invoices/estimates (amounts live behind a partner-site link)
  - Tesla Premium Connectivity subscription notices (no amount shown)
  - Tesla vehicle order agreements (amounts in PDF attachment; PDF parsing
    out of scope for this stdlib prototype -- attachment is noted in output)
  - Landing booking confirmations (no amounts)

Usage: python3 parse_travel-housing.py FILE.eml [--mbox-file X --byte-offset N]
Emits one JSON object on stdout matching the receipts-online summary shape.
"""

import argparse
import json
import re
import sys
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser

MONEY = r"-?\$[\d,]+(?:\.\d{2})?"


def money(s):
    if s is None:
        return None
    m = re.search(r"(-?)\$([\d,]+(?:\.\d{2})?)", s)
    if not m:
        return None
    v = float(m.group(2).replace(",", ""))
    return -v if m.group(1) else v


class TextExtract(HTMLParser):
    """HTML -> line-oriented text. Block-level tags become newlines."""

    BLOCK = {"br", "tr", "p", "div", "td", "th", "h1", "h2", "h3", "h4",
             "li", "table", "hr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.chunks = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script", "head", "title"):
            self.skip += 1
        if tag in self.BLOCK:
            self.chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in ("style", "script", "head", "title"):
            self.skip = max(0, self.skip - 1)

    def handle_data(self, d):
        if not self.skip:
            self.chunks.append(d)


def body_lines(msg):
    part = msg.get_body(preferencelist=("html",))
    if part is not None:
        te = TextExtract()
        te.feed(part.get_content())
        text = "".join(te.chunks)
    else:
        part = msg.get_body(preferencelist=("plain",))
        text = part.get_content() if part else ""
    # normalize: soft hyphens / nbsp / zero-width used as spacers in Airbnb 2026
    text = text.replace("­", "").replace(" ", " ").replace("​", "")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    return [ln for ln in lines if ln]


def base_record(msg, args):
    from_addr = parseaddr(msg.get("From", ""))[1]
    domain = from_addr.split("@")[-1].lower() if "@" in from_addr else None
    try:
        date = parsedate_to_datetime(msg.get("Date")).isoformat()
    except Exception:
        date = None
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
        "item_count": 0,
        "items": [],
        "payment_method": {"type": None, "card_last4": None},
        "currency": "USD",
        "raw_ref": {"mbox_file": args.mbox_file, "byte_offset": args.byte_offset},
    }


# ---------------------------------------------------------------- Airbnb

def parse_airbnb(rec, lines, subject):
    rec["merchant_name"] = "Airbnb"
    text = "\n".join(lines)

    m = re.search(r"Receipt ID:\s*(RC\w+)", text)
    if m:
        rec["order_id"] = m.group(1)
    else:
        m = re.search(r"Confirmation code:\s*(HM\w+)", text)
        if m:
            rec["order_id"] = m.group(1)

    # receipt-issued date on "Receipt ID: X · Month D, YYYY" line
    m = re.search(r"Receipt ID:\s*RC\w+\s*[·•-]\s*([A-Z][a-z]+ \d{1,2}, \d{4})", text)
    if m:
        try:
            from datetime import datetime
            rec["date"] = datetime.strptime(m.group(1), "%B %d, %Y").date().isoformat()
        except ValueError:
            pass

    # price breakdown: label line followed by money-only line, until Total
    try:
        i = next(k for k, ln in enumerate(lines)
                 if ln.lower().startswith("price breakdown"))
    except StopIteration:
        return rec
    items = []
    label = None
    j = i + 1
    while j < len(lines):
        ln = lines[j]
        tm = re.match(r"^Total \(([A-Z]{3})\)$", ln)
        if tm:
            rec["currency"] = tm.group(1)
            if j + 1 < len(lines):
                rec["grand_total"] = money(lines[j + 1])
            break
        if re.fullmatch(MONEY, ln):
            if label:
                amt = money(ln)
                qty, unit = 1, amt
                qm = re.match(r"^(%s) x (\d+) nights?$" % MONEY, label)
                if qm:
                    unit = money(qm.group(1))
                    qty = int(qm.group(2))
                items.append({"description": label, "quantity": qty,
                              "unit_price": unit, "total": amt})
                label = None
        else:
            label = ln
        j += 1

    # tax line -> tax field; everything is kept as items too
    for it in items:
        if re.search(r"tax", it["description"], re.I):
            rec["tax"] = it["total"]
    rec["items"] = items
    rec["item_count"] = len(items)
    if rec["grand_total"] is not None and rec["tax"] is not None:
        rec["subtotal"] = round(rec["grand_total"] - rec["tax"], 2)

    # payment block: heading "Payment" (or "Payment schedule" for installment
    # plans) then a method line within the next few lines
    for k, ln in enumerate(lines):
        if ln in ("Payment", "Payment schedule"):
            for meth in lines[k + 1:k + 5]:
                cm = re.match(r"^([A-Za-z ]+?)\s*[•·]{2,}\s*(\d{4})$", meth)
                if cm:
                    rec["payment_method"] = {"type": cm.group(1).strip().lower(),
                                             "card_last4": cm.group(2)}
                    break
                wm = re.search(r"apple pay|google pay|paypal|klarna", meth, re.I)
                if wm:
                    rec["payment_method"] = {"type": wm.group(0).lower(),
                                             "card_last4": None}
                    break
            break
    return rec


# ---------------------------------------------------------------- Tesla

def parse_tesla(rec, lines, subject, msg):
    rec["merchant_name"] = "Tesla"
    text = "\n".join(lines)

    m = re.search(r"Order\s+#?([A-Z0-9]{8,12})\s+is confirmed", text, re.I)
    if m:
        rec["order_id"] = m.group(1)
    else:
        m = re.search(r"\bRN\d{9}\b", subject + " " + text)
        if m:
            rec["order_id"] = m.group(0)

    # Shop order summary
    if "Order Summary" in text:
        try:
            i = next(k for k, ln in enumerate(lines) if ln == "Order Summary")
        except StopIteration:
            i = None
        if i is not None:
            items, pending = [], []
            j = i + 1
            while j < len(lines):
                ln = lines[j]
                if ln == "Subtotal":
                    break
                qm = re.match(r"^Quantity:\s*(\d+)$", ln)
                if qm:
                    pending.append(("qty", int(qm.group(1))))
                elif re.fullmatch(MONEY, ln):
                    desc = next((p[1] for p in pending if p[0] == "desc"), None)
                    qty = next((p[1] for p in pending if p[0] == "qty"), 1)
                    amt = money(ln)
                    if desc:
                        items.append({"description": desc, "quantity": qty,
                                      "unit_price": round(amt / qty, 2) if qty else amt,
                                      "total": amt})
                    pending = []
                else:
                    # description lines (may be multi-line, e.g. "Wall Connector"/"24' Cable")
                    if pending and pending[0][0] == "desc":
                        pending[0] = ("desc", pending[0][1] + " - " + ln)
                    else:
                        pending.insert(0, ("desc", ln))
                j += 1
            rec["items"] = items
            rec["item_count"] = len(items)
            for k in range(j, min(j + 12, len(lines))):
                if lines[k] == "Subtotal" and k + 1 < len(lines):
                    rec["subtotal"] = money(lines[k + 1])
                if lines[k] == "Tax" and k + 1 < len(lines):
                    rec["tax"] = money(lines[k + 1])
                if lines[k] == "Total" and k + 1 < len(lines):
                    rec["grand_total"] = money(lines[k + 1])

    # note non-extractable categories honestly
    if re.search(r"Service (Invoice|Estimate)", subject, re.I):
        rec["_note"] = ("Tesla service invoice/estimate: amounts are behind a "
                        "partner-site link, not in the email body")
    if re.search(r"Subscribed to", subject, re.I):
        rec["_note"] = "subscription notice; no amount present in email"
    pdfs = [p.get_filename() for p in msg.walk()
            if p.get_content_type() == "application/pdf"]
    if pdfs:
        rec["_note"] = f"amounts likely in PDF attachment(s): {pdfs}"
    return rec


# ---------------------------------------------------------------- Landing

def parse_landing(rec, lines, subject):
    rec["merchant_name"] = "Landing"
    text = "\n".join(lines)
    m = re.search(r"Property & Unit:\s*(.+)", text)
    if m:
        rec["order_id"] = m.group(1).strip()  # no booking number in receipt email
    try:
        i = next(k for k, ln in enumerate(lines) if ln == "Description")
    except StopIteration:
        return rec
    items = []
    label = None
    j = i + 1
    while j < len(lines):
        ln = lines[j]
        if ln.startswith("Total"):
            if j + 1 < len(lines):
                rec["grand_total"] = money(lines[j + 1]) or money(ln)
            else:
                rec["grand_total"] = money(ln)
            break
        if re.fullmatch(MONEY, ln):
            if label:
                items.append({"description": label, "quantity": 1,
                              "unit_price": money(ln), "total": money(ln)})
                label = None
        elif ln != "Amount":
            label = ln
        j += 1
    rec["items"] = items
    rec["item_count"] = len(items)
    # Landing receipts have no tax line; surcharge implies card payment
    if any("credit card surcharge" in it["description"].lower() for it in items):
        rec["payment_method"] = {"type": "credit card", "card_last4": None}
    if rec["grand_total"] is not None:
        rec["subtotal"] = rec["grand_total"]
    return rec


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("eml")
    ap.add_argument("--mbox-file", default=None)
    ap.add_argument("--byte-offset", type=int, default=None)
    args = ap.parse_args()

    with open(args.eml, "rb") as f:
        msg = BytesParser(policy=policy.default).parse(f)

    rec = base_record(msg, args)
    subject = msg.get("Subject", "") or ""
    lines = body_lines(msg)
    dom = rec["sender_domain"] or ""

    if "airbnb" in dom:
        rec = parse_airbnb(rec, lines, subject)
    elif "tesla" in dom:
        rec = parse_tesla(rec, lines, subject, msg)
    elif "landing" in dom or "hellolanding" in dom:
        rec = parse_landing(rec, lines, subject)
    else:
        rec["_note"] = f"unrecognized domain {dom}"

    json.dump(rec, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
