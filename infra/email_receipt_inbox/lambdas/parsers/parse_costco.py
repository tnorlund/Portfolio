#!/usr/bin/env python3
"""Prototype parser for Costco sender-group emails (online.costco.com,
digital.costco.com, costco.com, trx.costco.com, costco.com.mx).

Reality of this group (measured on Tyler's mailbox, 2019-2026):
  - ~99% of the 2,256 messages are marketing (Treasure Hunt, savings books).
  - There are NO Costco.com online-order confirmations and NO US warehouse
    e-receipts in the mailbox (not enrolled; Costco-via-Instacart orders come
    from instacart.com and belong to that group).
  - The only monetary receipt is the Costco Mexico gas e-ticket
    ("Su Ticket Costo Gas") which carries a PDF of the pump receipt.
  - Membership renewal thank-yous / auto-renew confirmations carry NO amounts;
    they are emitted as skeleton records (nulls) so the membership charge can
    still be date-anchored for Chase reconciliation.

Usage: parse_costco.py FILE.eml [FILE2.eml ...]  -> JSON per file on stdout
"""
import email
import email.policy
import json
import re
import sys
from datetime import datetime
from html.parser import HTMLParser

try:
    from pypdf import PdfReader  # present on this machine; used for gas PDFs
except ImportError:  # pragma: no cover
    PdfReader = None

MARKETING_PAT = re.compile(
    r"treasure hunt|savings|hot buys|deals|warehouse insider|shop |sale|"
    r"member-only|holiday|last chance|starts today|ends today|preview",
    re.I,
)


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.chunks = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("style", "script"):
            self._skip = max(0, self._skip - 1)

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.chunks.append(data.strip())


def _body_text(msg):
    body = msg.get_body(preferencelist=("html", "plain"))
    if body is None:
        return ""
    content = body.get_content()
    if body.get_content_type() == "text/html":
        ex = _TextExtractor()
        ex.feed(content)
        return "\n".join(ex.chunks)
    return content


def _base_record(msg, path):
    date = None
    if msg["Date"]:
        try:
            date = email.utils.parsedate_to_datetime(msg["Date"]).isoformat()
        except (TypeError, ValueError):
            pass
    from_addr = email.utils.parseaddr(msg.get("From", ""))[1]
    return {
        "source": "email",
        "message_id": (msg.get("Message-ID") or "").strip("<> "),
        "sender_domain": from_addr.rsplit("@", 1)[-1].lower() if "@" in from_addr else None,
        "merchant_name": "Costco",
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
        "raw_ref": {"mbox_file": None, "byte_offset": None, "eml_path": path},
    }


def _money(s):
    return float(s.replace(",", "").replace("$", ""))


def _parse_gas_pdf(msg, rec):
    """Costco Mexico gas e-ticket: full receipt lives in a PDF attachment."""
    pdf_bytes = None
    for part in msg.iter_attachments():
        if part.get_content_type() == "application/pdf":
            pdf_bytes = part.get_content()
            break
    if pdf_bytes is None or PdfReader is None:
        rec["_note"] = "gas ticket PDF missing or pypdf unavailable"
        return rec
    import io
    import os

    reader = PdfReader(io.BytesIO(pdf_bytes))
    text = "\n".join(p.extract_text() or "" for p in reader.pages)
    rec["merchant_name"] = "Costco Gas"
    rec["currency"] = "MXN"

    if not text.strip():
        # Measured case: the PDF has no text layer, just a PNG scan of the
        # pump ticket. Extract the image and hand it to the existing paper-
        # receipt OCR pipeline (LayoutLM) instead of parsing here.
        img_paths = []
        for i, page in enumerate(reader.pages):
            for img in getattr(page, "images", []):
                out = os.path.splitext(rec["raw_ref"]["eml_path"])[0] + f"_p{i}_{img.name}"
                with open(out, "wb") as fh:
                    fh.write(img.data)
                img_paths.append(out)
        rec["_note"] = "image-only PDF; route extracted image(s) through OCR pipeline"
        rec["needs_ocr"] = True
        rec["ocr_image_paths"] = img_paths
        return rec

    m = re.search(r"FOLIO\s+(\d+)", text)
    if m:
        rec["order_id"] = m.group(1)
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})\s+(\d{2}:\d{2}:\d{2})", text)
    if m:  # DD/MM/YYYY on the printed ticket
        dd, mm, yyyy, hms = m.groups()
        rec["date"] = f"{yyyy}-{mm}-{dd}T{hms}"

    prod = re.search(r"PRODUCTO\s*:\s*(.+)", text)
    qty = re.search(r"CANTIDAD\s*:\s*([\d.,]+)\s*L", text)
    price = re.search(r"PRECIO\s*:\s*([\d.,]+)", text)
    monto = re.search(r"MONTO\s*:\s*([\d.,]+)", text)
    if prod:
        item = {
            "description": prod.group(1).strip() + " (gasoline, liters)",
            "quantity": _money(qty.group(1)) if qty else None,
            "unit_price": _money(price.group(1)) if price else None,
            "total": _money(monto.group(1)) if monto else None,
        }
        rec["items"] = [item]
        rec["item_count"] = 1

    m = re.search(r"SUBTOTAL\s*:\s*([\d.,]+)", text)
    if m:
        rec["subtotal"] = _money(m.group(1))
    m = re.search(r"IVA\s*:\s*([\d.,]+)", text)
    if m:
        rec["tax"] = _money(m.group(1))
    m = re.search(r"TOTAL\s*:\s*\$?\s*([\d.,]+)", text)
    if m:
        rec["grand_total"] = _money(m.group(1))
    m = re.search(r"TARJETA\s*:\s*\d+\*+(\d{4})", text)
    if m:
        rec["payment_method"] = {"type": "card", "card_last4": m.group(1)}
    return rec


def parse(path):
    with open(path, "rb") as fh:
        msg = email.message_from_binary_file(fh, policy=email.policy.default)
    subject = msg.get("Subject", "") or ""
    rec = _base_record(msg, path)

    # 1. Costco Mexico gas e-ticket (the only true monetary receipt).
    if re.search(r"ticket\s+costo\s+gas", subject, re.I):
        return _parse_gas_pdf(msg, rec)

    # 2. Membership lifecycle: renewal/new-member confirmations. No amounts in
    #    the email; emit skeleton so the charge date can anchor reconciliation.
    if re.search(r"thank you for being a costco member|welcome to costco", subject, re.I):
        rec["merchant_name"] = "Costco Membership"
        rec["_note"] = "membership confirmation; email contains no amount"
        return rec
    if re.search(r"auto renew", subject, re.I):
        rec["merchant_name"] = "Costco Membership"
        rec["_note"] = "auto-renew enrollment notice; no amount, future charge"
        return rec

    # 3. Known non-receipt transactional notices.
    if re.search(r"card transfer confirmation|password|account has been verified|"
                 r"recall notice|e-waste", subject, re.I):
        return {"skip": True, "reason": "transactional-non-receipt", "subject": subject}

    # 4. Marketing.
    body = _body_text(msg)[:4000]
    if MARKETING_PAT.search(subject) or MARKETING_PAT.search(body):
        return {"skip": True, "reason": "marketing", "subject": subject}

    return {"skip": True, "reason": "unrecognized", "subject": subject}


if __name__ == "__main__":
    for p in sys.argv[1:]:
        print(json.dumps(parse(p), indent=2, ensure_ascii=False))
