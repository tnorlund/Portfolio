#!/usr/bin/env python3
"""Parse a Chase transaction-alert .eml into the receipts-online summary schema.

IMPORTANT CONTEXT: chase.com emails are NOT receipts. They are account alerts
(debit-card transaction over the user's $125 alert threshold, outbound
transfers, Zelle received, external transfers, ATM withdrawals). They carry
merchant/counterparty descriptor + amount + timestamp + account last4, but
never items/subtotal/tax/tip. They are useful as a RECONCILIATION SIGNAL
(match against Chase CSV rows or against real merchant receipts), not as
receipt sources.

Usage: parse_chase-alerts.py path/to/message.eml [more.eml ...]
Emits one JSON object per file to stdout (and optionally writes alongside).

If a sibling file <name>.eml.ref.json exists with {mbox_file, byte_offset},
it is used to fill raw_ref; otherwise raw_ref points at the .eml path.
"""

import html as htmlmod
import json
import os
import re
import sys
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime

MONTHS = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}


def load_body_text(msg):
    """Prefer HTML (stripped) over plain text; return whitespace-normalized text."""
    html_body, text_body = None, None
    for part in msg.walk():
        ct = part.get_content_type()
        try:
            if ct == "text/html" and html_body is None:
                html_body = part.get_content()
            elif ct == "text/plain" and text_body is None:
                text_body = part.get_content()
        except Exception:
            continue
    raw = html_body or text_body or ""
    if html_body:
        raw = re.sub(r"<(style|script)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
        raw = re.sub(r"<!--.*?-->", " ", raw, flags=re.S)
        raw = re.sub(r"<[^>]+>", " ", raw)
        raw = htmlmod.unescape(raw)
    return re.sub(r"\s+", " ", raw).strip()


def parse_amount(s):
    return float(s.replace(",", ""))


def parse_alert_datetime(text, fallback_dt):
    """Parse 'Jun 10, 2026 at 5:47 PM ET' or '06/16/2017 9:32:16 PM EDT'."""
    m = re.search(r"([A-Z][a-z]{2}) (\d{1,2}), (\d{4})(?: at \d{1,2}:\d{2} [AP]M)?", text)
    if m and m.group(1) in MONTHS:
        return "%s-%02d-%02d" % (m.group(3), MONTHS[m.group(1)], int(m.group(2)))
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", text)
    if m:
        return "%s-%s-%s" % (m.group(3), m.group(1), m.group(2))
    return fallback_dt.date().isoformat() if fallback_dt else None


# Each pattern -> (alert_type, payment_type, merchant_group, amount_group, sign)
# sign: 'debit' = money out (spend), 'credit' = money in.
PATTERNS = [
    # New-format (2019+) debit card alert
    (re.compile(r"debit card transaction of \$([\d,]+\.\d{2}) with (.+?) Account ending"),
     "debit_card_transaction", "debit_card", 2, 1, "debit"),
    # Old-format (pre-2019) debit card alert
    (re.compile(r"A \$([\d,]+\.\d{2}) debit card transaction to (.+?) on \d{2}/\d{2}/\d{4}"),
     "debit_card_transaction", "debit_card", 2, 1, "debit"),
    # Outbound transfer / bill pay ("You sent $X to RECIPIENT")
    (re.compile(r"You sent \$([\d,]+\.\d{2}) to (.+?) Account ending"),
     "outbound_transfer", "bank_transfer", 2, 1, "debit"),
    # External transfer (old plaintext format)
    (re.compile(r"A \$([\d,]+\.\d{2}) external transfer to (.+?) on "),
     "external_transfer", "bank_transfer", 2, 1, "debit"),
    # Zelle received, new format
    (re.compile(r"(?:Zelle ?\S* payment )?(.+?) sent you money Here are the details: Amount \$([\d,]+\.\d{2})"),
     "zelle_received", "zelle", 1, 2, "credit"),
    # Zelle/QuickPay received, old format
    (re.compile(r"(.+?) sent you money through Chase QuickPay.*?Amount: \$([\d,]+\.\d{2})"),
     "zelle_received", "zelle", 1, 2, "credit"),
    # ATM withdrawal
    (re.compile(r"A \$([\d,]+\.\d{2}) ATM withdrawal on "),
     "atm_withdrawal", "atm", None, 1, "debit"),
]


def parse(eml_path):
    with open(eml_path, "rb") as f:
        raw = f.read()
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    text = load_body_text(msg)

    hdr_dt = None
    if msg["Date"]:
        try:
            hdr_dt = parsedate_to_datetime(msg["Date"])
        except Exception:
            hdr_dt = None

    alert_type = payment_type = merchant = None
    amount = None
    sign = None
    for rx, atype, ptype, mg, ag, sgn in PATTERNS:
        m = rx.search(text)
        if m:
            alert_type = atype
            payment_type = ptype
            amount = parse_amount(m.group(ag))
            if mg is not None:
                merchant = re.sub(r"\s+", " ", m.group(mg)).strip()
                # strip preview-text junk that can precede the Zelle sender name
                merchant = re.sub(r"^.*inbox preview\.?\s*", "", merchant)
                merchant = re.sub(r"^Zelle \S* payment\s*", "", merchant)
            sign = sgn
            break

    # account last4: "(...1234)" / "(…1234)" / "account ending in 1234"
    last4 = None
    m = re.search(r"[Aa]ccount ending in [^0-9]{0,4}(\d{4})", text) or \
        re.search(r"\(\W*(\d{4})\)", msg["Subject"] or "")
    if m:
        last4 = m.group(1)

    date_iso = parse_alert_datetime(text, hdr_dt)

    # Zelle transaction number doubles as an order/reference id
    order_id = None
    m = re.search(r"Transaction number (\d+)", text)
    if m:
        order_id = m.group(1)

    out = {
        "source": "email",
        "message_id": (msg["Message-ID"] or "").strip() or None,
        "sender_domain": (msg["From"] or "").split("@")[-1].rstrip(">").lower() or None,
        "merchant_name": merchant,
        "date": date_iso,
        "order_id": order_id,
        "grand_total": amount,
        "subtotal": None,
        "tax": None,
        "tip": None,
        "item_count": 0,
        "items": [],
        "payment_method": {"type": payment_type, "card_last4": last4},
        "currency": "USD" if amount is not None else None,
        "raw_ref": None,
        # Extension fields (not in the paper-receipt schema, but essential for
        # using these as reconciliation signals rather than receipts):
        "alert_type": alert_type,          # debit_card_transaction | outbound_transfer | ...
        "direction": sign,                 # debit (money out) | credit (money in)
        "is_receipt": False,               # these are alerts, never receipts
    }

    ref_path = eml_path + ".ref.json"
    if os.path.exists(ref_path):
        with open(ref_path) as f:
            ref = json.load(f)
        out["raw_ref"] = {"mbox_file": ref.get("mbox_file"),
                          "byte_offset": ref.get("byte_offset")}
    else:
        out["raw_ref"] = {"mbox_file": os.path.abspath(eml_path), "byte_offset": 0}
    return out


def main():
    paths = sys.argv[1:]
    if not paths:
        print("usage: parse_chase-alerts.py file.eml [...]", file=sys.stderr)
        sys.exit(2)
    for p in paths:
        result = parse(p)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
