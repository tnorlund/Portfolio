#!/usr/bin/env python3
"""Prototype parser for Uber / Uber Eats receipt emails (sender domain uber.com).

Usage: python3 parse_uber.py <path.eml> [more.eml ...]
Emits one JSON object per input file (to stdout, or use --outdir DIR to write
<stem>.json files).

Email types handled:
  - "Your <day> <time> trip with Uber"        -> ride receipt / trip summary
  - "Your <day> <time> order with Uber Eats"  -> Eats order receipt
  - "Uber One payment confirmation"           -> subscription charge

Known format drift:
  - 2017-era rides: "Your Fare ... CHARGED $X Personal •••• 1234" layout.
  - 2020-2023 rides: "Total $X ... Amount Charged <method> - 1234" or
    "Payments <method> <date> $X" layout (payment info present).
  - 2024+ rides: email is explicitly "not a payment receipt" (trip summary);
    no payment method / card last4 present.
  - Eats orders: total + card last4 present, but NO itemization, subtotal,
    tax or tip (full receipt lives behind a link). Currency can be non-USD
    (e.g. CRC while traveling).
"""
import email
import email.policy
import html as html_mod
import json
import os
import re
import sys
from datetime import datetime
from html.parser import HTMLParser

INDEX_JSONL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "data", "index.jsonl")

BLOCK_TAGS = {"p", "div", "tr", "td", "th", "table", "br", "li", "h1", "h2",
              "h3", "h4", "ul", "ol"}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script"):
            self._skip += 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("style", "script") and self._skip:
            self._skip -= 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)

    def text(self):
        t = "".join(self.parts)
        t = t.replace("\xa0", " ")
        t = re.sub(r"[ \t]+", " ", t)
        t = re.sub(r" ?\n ?", "\n", t)
        t = re.sub(r"\n{2,}", "\n", t)
        return t.strip()


def html_to_text(h):
    p = _TextExtractor()
    p.feed(h)
    return p.text()


MONEY = r"(?:(?P<cur>\$|USD|CRC|EUR|€|£|MXN|CAD)\s?)(?P<amt>[\d][\d.,]*)"


def _to_float(amt, currency):
    amt = amt.strip().rstrip(".")
    if currency == "CRC" and amt.count(",") and amt.count("."):
        amt = amt.replace(",", "")  # CRC 20,844.90 style
    else:
        amt = amt.replace(",", "")
    try:
        return float(amt)
    except ValueError:
        return None


def _currency_name(sym):
    return {"$": "USD", "€": "EUR", "£": "GBP"}.get(sym, sym)


def find_money(text, label_pattern):
    """Find first money amount following a label regex. Returns (amount, currency)."""
    m = re.search(label_pattern + r"[^\d$€£A-Z]{0,20}" + MONEY, text,
                  re.I)
    if not m:
        return None, None
    return _to_float(m.group("amt"), m.group("cur")), _currency_name(m.group("cur"))


def parse_body_date(text, fallback_header):
    m = re.search(r"\b(January|February|March|April|May|June|July|August|"
                  r"September|October|November|December) (\d{1,2}), (\d{4})",
                  text)
    if m:
        try:
            return datetime.strptime(
                f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y"
            ).date().isoformat()
        except ValueError:
            pass
    if fallback_header:
        try:
            return email.utils.parsedate_to_datetime(
                fallback_header).date().isoformat()
        except Exception:
            pass
    return None


CARD_PAT = re.compile(
    r"(?P<method>Apple Pay|Apple Card|Google Pay|Visa|Mastercard|MasterCard|"
    r"MC|Amex|American Express|Discover|Personal|Business)"
    r"[^\d\n]{0,25}?(?:•+ ?|[-–] ?|\*+ ?)(?P<last4>\d{4})")


def find_payment(text):
    m = CARD_PAT.search(text)
    if m:
        method = m.group("method")
        ptype = "card"
        if "Apple Pay" in method or "Google Pay" in method:
            ptype = "wallet"
        return {"type": ptype, "card_last4": m.group("last4"),
                "method": method}
    if re.search(r"Payments\s*\n?Voucher", text, re.I) or \
       re.search(r"Voucher:", text):
        return {"type": "voucher", "card_last4": None, "method": "Voucher"}
    return None


def classify(subject):
    s = (subject or "").lower()
    if "trip with uber" in s:
        return "ride"
    if "order with uber eats" in s:
        return "eats"
    if "uber one payment" in s:
        return "uber_one"
    return "unknown"


RIDE_FEE_SKIP = re.compile(
    r"^(Total|Subtotal|Trip fare|Trip Fare|Amount Charged|CHARGED|"
    r"Payments|Your Fare)\b", re.I)


def parse_ride(text):
    out = {}
    total, cur = find_money(text, r"\bTotal\b")
    if total is None:
        total, cur = find_money(text, r"\bCHARGED\b")
    out["grand_total"] = total
    out["currency"] = cur
    sub, _ = find_money(text, r"\bSubtotal\b")
    out["subtotal"] = sub
    tip, _ = find_money(text, r"\bTip(?:\s+amount)?\b")
    out["tip"] = tip
    tax, _ = find_money(text, r"\b(?:Sales )?Tax\b")
    out["tax"] = tax
    # ride description: e.g. "UberX\n7.64 miles | 12 min"
    m = re.search(r"(UberX|UberXL|Comfort|Uber Green|Black SUV|Black|Pool|"
                  r"UberPool|Express Pool|uberX|WAV|Connect)\s*\n?"
                  r"([\d.]+ (?:miles|kilometers) \| [\dh ]+min)?", text)
    desc = None
    if m:
        desc = m.group(1)
        if m.group(2):
            desc += " " + m.group(2)
    items = []
    if desc:
        items.append({"description": "Ride: " + desc, "quantity": 1,
                      "unit_price": None, "total": total})
    out["items"] = items
    out["merchant_name"] = "Uber"
    return out


def parse_eats(text):
    out = {}
    total, cur = find_money(text, r"\bTotal\b")
    out["grand_total"] = total
    out["currency"] = cur
    out["subtotal"] = None   # not present in Eats emails
    out["tax"] = None
    out["tip"] = None
    m = re.search(r"Here's your receipt for (.+?)\.(?:\s|$)", text)
    restaurant = m.group(1).strip() if m else None
    if not restaurant:
        m = re.search(r"You ordered from (.+)", text)
        restaurant = m.group(1).strip() if m else None
    items = []
    if restaurant:
        items.append({"description": f"Uber Eats order from {restaurant}",
                      "quantity": 1, "unit_price": None, "total": total})
    out["items"] = items
    out["merchant_name"] = "Uber Eats" + (f" ({restaurant})" if restaurant else "")
    return out


def parse_uber_one(text):
    out = {}
    total, cur = find_money(text, r"Total charged")
    out["grand_total"] = total
    out["currency"] = cur
    out["subtotal"] = None
    out["tax"] = None
    out["tip"] = None
    out["items"] = [{"description": "Uber One membership (monthly)",
                     "quantity": 1, "unit_price": None, "total": total}]
    out["merchant_name"] = "Uber One"
    return out


def load_raw_ref(message_id):
    if not message_id or not os.path.exists(INDEX_JSONL):
        return None
    try:
        with open(INDEX_JSONL) as f:
            for line in f:
                if message_id in line:
                    r = json.loads(line)
                    if (r.get("message_id") or "").strip("<> ") == message_id:
                        return {"mbox_file": r["mbox_file"],
                                "byte_offset": r["byte_offset"]}
    except OSError:
        return None
    return None


def parse_eml(path):
    with open(path, "rb") as f:
        raw = f.read()
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    subject = msg.get("Subject", "")
    message_id = (msg.get("Message-Id") or "").strip("<> ")
    from_addr = email.utils.parseaddr(msg.get("From", ""))[1]
    sender_domain = from_addr.split("@")[-1] if "@" in from_addr else None

    # get HTML (or plain) body
    body_html = None
    body_plain = None
    for part in msg.walk():
        ct = part.get_content_type()
        if ct == "text/html" and body_html is None:
            body_html = part.get_content()
        elif ct == "text/plain" and body_plain is None:
            body_plain = part.get_content()
    text = html_to_text(body_html) if body_html else (body_plain or "")
    text = html_mod.unescape(text)

    kind = classify(subject)
    if kind == "ride":
        fields = parse_ride(text)
    elif kind == "eats":
        fields = parse_eats(text)
    elif kind == "uber_one":
        fields = parse_uber_one(text)
    else:
        fields = {"grand_total": None, "subtotal": None, "tax": None,
                  "tip": None, "items": [], "merchant_name": "Uber",
                  "currency": None}

    order_id = None
    m = re.search(r"\bxid([0-9a-f-]{20,})", text)
    if m:
        order_id = m.group(1)

    payment = find_payment(text)
    # 2024+ ride "trip summary" emails explicitly carry no payment info
    is_trip_summary = "This is not a payment receipt" in text

    result = {
        "source": "email",
        "message_id": message_id,
        "sender_domain": sender_domain,
        "merchant_name": fields["merchant_name"],
        "date": parse_body_date(text, msg.get("Date")),
        "order_id": order_id,
        "grand_total": fields["grand_total"],
        "subtotal": fields["subtotal"],
        "tax": fields["tax"],
        "tip": fields["tip"],
        "item_count": len(fields["items"]),
        "items": fields["items"],
        "payment_method": ({"type": payment["type"],
                            "card_last4": payment["card_last4"]}
                           if payment else
                           {"type": None, "card_last4": None}),
        "currency": fields["currency"],
        "raw_ref": load_raw_ref(message_id),
        # extra context (non-schema, prefixed with _)
        "_email_kind": kind,
        "_trip_summary_no_payment": is_trip_summary,
    }
    return result


def main(argv):
    outdir = None
    args = []
    it = iter(argv)
    for a in it:
        if a == "--outdir":
            outdir = next(it)
        else:
            args.append(a)
    if not args:
        print("usage: parse_uber.py [--outdir DIR] file.eml [...]",
              file=sys.stderr)
        return 1
    for p in args:
        res = parse_eml(p)
        js = json.dumps(res, indent=2, ensure_ascii=False)
        if outdir:
            os.makedirs(outdir, exist_ok=True)
            stem = os.path.splitext(os.path.basename(p))[0]
            with open(os.path.join(outdir, stem + ".json"), "w") as f:
                f.write(js + "\n")
            print(f"wrote {stem}.json")
        else:
            print(js)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
