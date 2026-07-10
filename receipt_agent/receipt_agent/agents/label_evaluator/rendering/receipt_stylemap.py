"""Per-section style rules measured from real receipts (Glyph Studio fleet).

A stylemap (``tools/glyph-studio/fonts/<merchant>/stylemap.json``, published
into ``$BITMATRIX_DIR`` like the glyph atlases) carries per-section values
measured across a merchant's real scans: size scale, weight tier, and
underline prevalence. The renderer consumes it per ROW: classify the row's
text into a section, then apply that section's style. Merchants without a
stylemap are untouched.

Sprouts findings this encodes (173 receipts, adversarially verified):
section headers are the receipt's only underline signal (~41% of lines);
BALANCE DUE alone prints bold + taller; the payment block is condensed and
barcode captions are smaller/lighter; everything else is single-weight.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping

SECTION_TOKENS = {
    "PRODUCE",
    "DAIRY",
    "MEAT",
    "GROCERY",
    "BULK",
    "DELI",
    "BAKERY",
    "FROZEN",
    "SEAFOOD",
    "VITAMINS",
    "BEER",
    "WINE",
    "BODY",
    "HOUSEHOLD",
    "HEALTH AND BEAUTY",
    "HOME",
    "KITCHEN",
    "APPAREL",
    "ELECTRONICS",
    "ENTERTAINMENT-ELECTRONICS",
    "LAUNDRY CLEANING AND CLOSET",
    "PATIO & OUTDOOR DECOR",
}
_INNOUT_RULES: list[tuple[str, re.Pattern]] = [
    (
        "store_header",
        re.compile(r"IN-N-OUT", re.I),
    ),
    (
        "transaction",
        re.compile(
            r"Cashier|ORDERTAKER|Check\s*:|TRANS\s*#|Ticket|Station",
            re.I,
        ),
    ),
    ("note", re.compile(r"^NOTE\b|^tes$", re.I)),
    ("total_line", re.compile(r"Amount Due|AUTH\s+AMT", re.I)),
    (
        "summary",
        re.compile(r"DRIVE-?Take Out|^TAX\b|Tender\b|Change\b", re.I),
    ),
    (
        "payment",
        re.compile(
            r"CHARGE\s+DETAIL|Card Type|Account:|Capture:|Contactless|PIN:|"
            r"Auth Code|Auth Ref|AID:|Trans\s*#|MasterCard|VISA|\*{4,}",
            re.I,
        ),
    ),
    (
        "footer",
        re.compile(
            r"THANK YOU|Questions/Comments|Call\s+800|^\d{4}-\d{2}-\d{2}\b",
            re.I,
        ),
    ),
]
_RULES: list[tuple[str, re.Pattern]] = [
    ("balance_due", re.compile(r"^BALANCE DUE", re.I)),
    ("store_header", re.compile(r"^WF$|^Thousand Oaks CA$", re.I)),
    ("store_hours", re.compile(r"Store Hours|MON-SUN|7AM-10PM", re.I)),
    (
        "address",
        re.compile(
            r"(BLVD|BLYD|BIVD|WESTLAKE|AVE\b|STREET|\bRD\b|,\s*CA\s+\d{5}|"
            r"\(\d{3}\)\s*\d{3}|\b\d{1}-\d{3}-\d{3}-\d{4}\b)",
            re.I,
        ),
    ),
    ("policy", re.compile(r"FEFO|returns|refunds|exchanges", re.I)),
    (
        "transaction",
        re.compile(
            r"Ticket\s?#|Station:|Sales Rep|User:|" r"^\d{1,2}/\d{1,2}/\d{4}",
            re.I,
        ),
    ),
    (
        "item_header",
        re.compile(r"^(Item|Description)\b|Qty|Uty|Price\s+Total", re.I),
    ),
    ("total_line", re.compile(r"^\s*(Subtotal|Total)\b(?!\s*Tax)", re.I)),
    (
        "summary",
        re.compile(
            r"CHANGE\b|CREDIT\b|^TAX\b|DEBIT\s*$|Total Tax|"
            r"quantity purchased|items purchased",
            re.I,
        ),
    ),
    (
        "payment",
        re.compile(
            r"AUTH|AID:|TVR:|TSI:|ARC:|IAD:|TC:|MID:|TID:|SEQ|Entry Method|"
            r"APPROVED|CARD\s*#|Cntctless|MASTERCARD|US DEBIT|PURCHASE|Issuer|"
            r"Verified|X{6,}",
            re.I,
        ),
    ),
    (
        "survey",
        re.compile(r"survey|feedback|WIN\b|Winners|gift card|Go to", re.I),
    ),
    (
        "footer",
        re.compile(
            r"Cashier|POS:|Transaction|Save money|weekly ad|sprouts\.com|"
            r"original receipt|returns|Limits apply|CAREERS|Forkies|"
            r"Nourish Better Lives|Visit Link|Scan QR|pages/careers|"
            r"wildforkfoods|gettingmymail",
            re.I,
        ),
    ),
]
_BARCODE_RE = re.compile(r"^\d{10,}$")
_MERCHANT_RULES = {
    "innout": _INNOUT_RULES,
}


def _merchant_key(stylemap: Mapping[str, Any] | None) -> str | None:
    source = (stylemap or {}).get("source") or {}
    raw = str(source.get("merchant") or "").lower()
    if raw in _MERCHANT_RULES:
        return raw
    return None


def classify_row(text: str, merchant: str | None = None) -> str:
    compact = text.strip()
    if _BARCODE_RE.match(compact.replace(" ", "")):
        return "barcode_caption"
    if compact.upper().strip(":") in SECTION_TOKENS:
        return "section_header"
    rules = _MERCHANT_RULES.get((merchant or "").lower(), _RULES)
    for name, rx in rules:
        if rx.search(compact):
            return name
    return "other"


def normalize_face_key(text: str) -> str:
    """Canonical row key shared by the renderer and the M4 face selector.

    Uppercase, whitespace-collapsed, truncated to 60 chars (stylescan stores
    measured line text truncated to 60). Both sides MUST use this exact
    function or measured rows silently miss and fall back to text rules.
    """
    return " ".join(str(text).upper().split())[:60]


def measured_row_style(
    row_faces: Mapping[str, Any],
    row_text: str,
) -> dict[str, Any] | None:
    """Resolve one row's MEASURED style from an M4 ``row_faces`` map.

    ``row_faces`` maps :func:`normalize_face_key` keys to entries built by
    ``glyphstudio.face_select`` from stylescan measurements of the real
    receipt being mimicked: ``{"face": "regular"|"heavy", "scale": float,
    "underline": bool}``. Returns the same shape as :func:`row_style`
    (``{"scale", "bold", "underline"}``) or ``None`` when the row has no
    measurement (caller falls back to the stylemap text rules).
    """
    entry = row_faces.get(normalize_face_key(row_text))
    if entry is None:
        return None
    return {
        "scale": float(entry.get("scale", 1.0)),
        "bold": str(entry.get("face", "regular")).lower() == "heavy",
        "underline": bool(entry.get("underline", False)),
    }


def row_style(
    stylemap: Mapping[str, Any] | None,
    row_text: str,
    seed: str = "",
) -> dict[str, Any]:
    """Resolve one row's style: {"scale", "bold", "underline"}.

    Underline is sampled deterministically at the section's measured rate
    (hash of row text + seed), so renders are reproducible while the fleet
    still shows the real-world mix (Sprouts headers: ~41%).
    """
    style = {"scale": 1.0, "bold": False, "underline": False}
    if not stylemap:
        return style
    sections = stylemap.get("sections") or {}
    section = classify_row(row_text, merchant=_merchant_key(stylemap))
    rule = sections.get(section)
    if not rule:
        return style
    style["scale"] = float(rule.get("sizeScale", 1.0))
    style["bold"] = rule.get("weight") == "bold"
    ul = rule.get("underline", False)
    if ul is True:
        style["underline"] = True
    elif ul == "sometimes":
        rate = float(rule.get("underlineRate", 0.5))
        digest = hashlib.sha1(f"{seed}|{row_text}".encode()).digest()
        style["underline"] = (digest[0] * 256 + digest[1]) / 65535.0 < rate
    return style
