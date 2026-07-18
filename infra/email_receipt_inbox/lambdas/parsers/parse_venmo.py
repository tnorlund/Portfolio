#!/usr/bin/env python3
"""Prototype parser for Venmo notification emails -> receipt-schema JSON.

Handles three format eras:
  A. Classic P2P template (2016 - early 2025):
     "You paid NAME" / "NAME paid you" / "You charged NAME" + note +
     "Transfer Date and Amount: Mon DD, YYYY TZ . - $X.XX" + "Payment ID: N"
  B. New P2P template (mid 2025+):
     hero card "You paid NAME $ X . XX" + note + "Transaction details"
     table (Date / Transaction ID / Payment Method / Sent from).
  C. Merchant purchase receipts ("Receipt from DoorDash - $X.XX"):
     Venmo used as checkout wallet at a merchant; body has merchant name,
     purchase datetime (no year in old ones), funding source, total.

Emits JSON aligned with the receipts-online summary shape.
Usage: parse_venmo.py file.eml [file2.eml ...]  (prints one JSON per file)
"""
import sys, os, re, json, email, email.policy
from datetime import datetime
from html.parser import HTMLParser

BLOCK_TAGS = {"p", "div", "tr", "td", "th", "table", "br", "li", "h1", "h2",
              "h3", "h4", "center", "blockquote"}


class TextExtractor(HTMLParser):
    """Flatten HTML to text, newline at block boundaries, skip style/script."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.chunks = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script", "head", "title"):
            self._skip += 1
        elif tag in BLOCK_TAGS:
            self.chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in ("style", "script", "head", "title") and self._skip:
            self._skip -= 1
        elif tag in BLOCK_TAGS:
            self.chunks.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.chunks.append(data)

    def text(self):
        raw = "".join(self.chunks)
        raw = raw.replace("\xa0", " ").replace(" ", " ")
        lines = [re.sub(r"\s+", " ", ln).strip() for ln in raw.split("\n")]
        return [ln for ln in lines if ln]


def get_html_text(msg):
    html_body = plain_body = None
    for part in msg.walk():
        ct = part.get_content_type()
        if ct == "text/html" and html_body is None:
            html_body = part.get_content()
        elif ct == "text/plain" and plain_body is None:
            plain_body = part.get_content()
    if html_body:
        ex = TextExtractor()
        ex.feed(html_body)
        return ex.text()
    if plain_body:
        return [ln.strip() for ln in plain_body.splitlines() if ln.strip()]
    return []


AMT = r"\$\s?([\d,]+(?:\s?\.\s?\d{2})?)"


def money(s):
    if s is None:
        return None
    return float(re.sub(r"[,\s]", "", s))


def parse_date_us(s, fallback_year=None):
    """'Jun 30, 2025' or 'Nov 12, 2016' -> ISO date."""
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(s.strip(), fmt).date().isoformat()
        except ValueError:
            pass
    return None


def classify(subject):
    sl = (subject or "").lower()
    if sl.startswith("receipt from"):
        return "merchant_purchase"
    if re.match(r"you paid .+ \$", sl):
        return "p2p_sent"
    if re.search(r" paid you \$", sl):
        return "p2p_received"
    if re.search(r"you completed .+ charge request", sl):
        return "p2p_sent"          # you paid a request someone sent you
    if re.search(r"completed your .+ charge request", sl):
        return "p2p_received"      # they paid your request
    if "you sent money on imessage" in sl:
        return "p2p_sent"
    if "accepted your" in sl and "payment on imessage" in sl:
        return "p2p_sent"
    return None


def extract_payment_method(text):
    """Return (type, last4) from funding-source sentences."""
    m = re.search(r"(?:account|checking|savings)\s+ending in\s+\D*(\d{4})",
                  text, re.I)
    if m:
        return "bank_account", m.group(1)
    m = re.search(r"card\s+ending in\s+\D*(\d{4})", text, re.I)
    if m:
        return "card", m.group(1)
    if re.search(r"charged to your (?:debit|credit) card", text, re.I):
        return "card", None
    if re.search(r"venmo balance", text, re.I):
        return "venmo_balance", None
    if re.search(r"bank transfer|personal (checking|savings)", text, re.I):
        return "bank_account", None
    return None, None


def parse_eml(path):
    with open(path, "rb") as f:
        msg = email.message_from_binary_file(f, policy=email.policy.default)
    subject = str(msg.get("subject", "")).strip()
    kind = classify(subject)
    lines = get_html_text(msg)
    text = " ".join(lines)
    hdr_date = None
    if msg["date"]:
        try:
            hdr_date = email.utils.parsedate_to_datetime(str(msg["date"]))
        except Exception:
            pass

    out = {
        "source": "email",
        "message_id": str(msg.get("message-id", "")).strip() or None,
        "sender_domain": "venmo.com",
        "merchant_name": None,
        "date": hdr_date.date().isoformat() if hdr_date else None,
        "order_id": None,
        "grand_total": None,
        "subtotal": None,
        "tax": None,
        "tip": None,
        "item_count": 0,
        "items": [],
        "payment_method": {"type": None, "card_last4": None},
        "currency": "USD",
        "raw_ref": {"mbox_file": None, "byte_offset": None},
        # extras beyond base schema (P2P semantics)
        "transaction_kind": kind,          # p2p_sent / p2p_received / merchant_purchase
        "direction": None,                 # outflow / inflow
        "counterparty": None,
        "note": None,
    }
    if kind is None:
        out["error"] = "not a transaction email (marketing/security/statement)"
        return out

    # ---- counterparty + amount from subject ----
    m = (re.match(r"You paid (.+?) \$([\d,]+\.?\d*)", subject)
         or re.match(r"(.+?) paid you \$([\d,]+\.?\d*)", subject)
         or re.match(r"You completed (.+?)['’]s \$([\d,]+\.?\d*) charge",
                     subject)
         or re.match(r"(.+?) completed your \$([\d,]+\.?\d*) charge", subject)
         or re.match(r"(.+?) accepted your \$([\d,]+\.?\d*) payment", subject))
    if m:
        out["counterparty"] = m.group(1).strip()
        out["grand_total"] = money(m.group(2))
    m = re.match(r"Receipt from (.+?) - \$([\d,]+\.?\d*)", subject)
    if m:
        out["merchant_name"] = m.group(1).strip()
        out["grand_total"] = money(m.group(2))

    if kind == "merchant_purchase":
        # date like "Tuesday, October 28 at 09:00PM PDT" (no year) -> header yr
        m = re.search(r"on \w+day, (\w+ \d{1,2}) at", text)
        if m and hdr_date:
            try:
                d = datetime.strptime(
                    f"{m.group(1)}, {hdr_date.year}", "%B %d, %Y").date()
                # purchase may be shortly before email; year boundary guard
                if hdr_date and d > hdr_date.date():
                    d = d.replace(year=d.year - 1)
                out["date"] = d.isoformat()
            except ValueError:
                pass
        if out["grand_total"] is None:
            m = re.search(r"Total(?: to merchant)?:?\s*" + AMT, text)
            if not m:
                m = re.search(r"-\s?" + AMT, text)
            if m:
                out["grand_total"] = money(m.group(1))
    else:
        # ---- classic template ----
        m = re.search(r"Transfer Date and Amount:\s*(\w{3} \d{2}, \d{4})", text)
        if m:
            out["date"] = parse_date_us(m.group(1)) or out["date"]
            am = re.search(
                r"Transfer Date and Amount:.{0,40}?([+-])\s?" + AMT, text)
            if am:
                if out["grand_total"] is None:
                    out["grand_total"] = money(am.group(2))
                out["direction"] = "outflow" if am.group(1) == "-" else "inflow"
        # ---- new 2025 template ----
        m = re.search(r"Transaction details\s+Date\s+(\w{3} \d{1,2}, \d{4})",
                      text)
        if m:
            out["date"] = parse_date_us(m.group(1)) or out["date"]
        if out["grand_total"] is None:
            m = re.search(AMT, text)
            if m:
                out["grand_total"] = money(m.group(1))

    if out["direction"] is None:
        out["direction"] = ("inflow" if kind == "p2p_received" else "outflow")

    # ---- payment / transaction id ----
    m = re.search(r"(?:Payment ID|Transaction ID)\D{0,5}(\d{6,})", text)
    if m:
        out["order_id"] = m.group(1)

    # ---- funding source ----
    ptype, last4 = extract_payment_method(text)
    if kind == "p2p_received":
        ptype, last4 = "venmo_balance", None  # credited to balance
    out["payment_method"] = {"type": ptype, "card_last4": last4}

    # ---- note (memo) ----
    note = extract_note(lines, subject, kind, out)
    out["note"] = note

    # P2P merchant_name: use counterparty so reconciliation has a name
    if out["merchant_name"] is None and out["counterparty"]:
        out["merchant_name"] = out["counterparty"]

    # single pseudo-item from the note
    if note or out["grand_total"] is not None:
        desc = note or subject
        out["items"] = [{"description": desc, "quantity": 1,
                         "unit_price": out["grand_total"],
                         "total": out["grand_total"]}]
        out["item_count"] = 1
    return out


def extract_note(lines, subject, kind, out):
    """The memo is the line(s) just before the date/details marker.

    Classic template rendering:
        You / paid / Claire Rhodes / <note> / Transfer Date and Amount: ...
    New (2025+) template rendering:
        You paid Kyle Covelli / $ / 55 / . / 00 / <note> / See transaction
    """
    if kind == "merchant_purchase":
        return None
    end_idx = None
    for i, ln in enumerate(lines):
        if re.match(r"^(Transfer Date and Amount|See transaction)", ln):
            end_idx = i
            break
    if end_idx is None:
        return None
    cp = out.get("counterparty") or ""
    stop = {"you", "paid", "charged", "sent money on imessage",
            "payment not accepted yet", cp.lower(), subject.lower(),
            "- venmo", "venmo"}
    note_parts = []
    for ln in reversed(lines[max(0, end_idx - 4):end_idx]):
        low = ln.lower()
        if low in stop or low.startswith(("you paid", "you charged")) \
                or re.search(r"paid you", low) or re.search(r"charged", low):
            break
        # amount fragments: '$', '55', '.', '00', '- $15.00'
        if re.fullmatch(r"[+\-·]?\s?\$?\s?[\d,.\s]*", ln):
            break
        note_parts.append(ln)
    note = " ".join(reversed(note_parts)).strip()
    note = re.sub(r"\$\s?[\d,]+\s?\.\s?\d{2}", "", note).strip()
    return note or None


def main():
    for path in sys.argv[1:]:
        result = parse_eml(path)
        print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
