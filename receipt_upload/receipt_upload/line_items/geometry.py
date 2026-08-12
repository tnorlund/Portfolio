"""Geometric line-item extraction core.

Relocated verbatim from ``scripts/extract_line_items.py`` so the logic is
importable by ingest code and, critically, COVERED BY CI: the script's tests
lived in ``scripts/tests/``, which the repository-tests job never collects
(``find scripts -maxdepth 1``), so 12 passing tests were invisible to every
pull request. The script now imports from here; do not fork the logic back.

Pure functions only -- no boto3, no argparse, no I/O. The single runtime
dependency is ``receipt_dynamo.amounts`` for word-level price parsing.

Input word dicts carry: line_id, word_id, text, x, y_mid, h
(y_mid = bounding_box y + height/2, normalized page coordinates).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import combinations
from typing import Any, Optional

from receipt_dynamo.amounts import (
    looks_like_receipt_amount,
    parse_receipt_amount,
)

# Kept for stray-line detection only; item price parsing is word-level via
# receipt_dynamo.amounts (rejects "(7.00g)", 3-decimal fuel unit prices,
# date-like decimals, and handles all negative accounting forms).
PRICE_RE = re.compile(r"\$?(\d{1,4}(?:,\d{3})?\.\d{2})(-?)")
# "2 @ 3.99", "1.23 lb @ 4.99/lb", "18.871 @ $5.299/Gal"
QTY_AT_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:lb|1b|kg|oz|gal)?\s*@\s*\$?(\d+(?:\.\d{2,3}))"
    r"(?:\s*/\s*\w+)?",
    re.IGNORECASE,
)
# Grocery multiples deal: "4 FOR 1.00", "2 @ 2 FOR 3.00" (leading qty
# optional; deal is M-for-X so unit = X/M, qty = leading qty else M)
QTY_FOR_RE = re.compile(
    r"(?:(\d{1,2})\s*@\s*)?(\d{1,2})\s+FOR\s+\$?(\d+\.\d{2})",
    re.IGNORECASE,
)
# OCR reads "@" as "g" in "4 @ $1.79"; the explicit $ keeps this from
# matching real gram weights
QTY_AT_OCR_RE = re.compile(r"(\d+(?:\.\d+)?)\s*g\s*\$(\d+\.\d{2,3})")
# Leading "1x" / "2X" multiplier
QTY_MULT_RE = re.compile(r"^(\d{1,2})[xX]$")
# Settlement lines are never items even when a broken ITEMS section
# includes them (BALANCE DUE / Balance to pay / CREDIT / CHANGE ...).
# OCR sometimes scrambles word order ("17.98 DUE BALANCE") or prefixes an
# item count ("[1 item] Sub Total 16.00"); both forms are covered here.
SETTLEMENT_RE = re.compile(
    r"^\W*(?:ITEMS?\W+)?"
    r"(?:BALANCE(?:\s+DUE|\s+TO\s+PAY)?|DUE\s+BALANCE"
    r"|(?:AMOUNT|TOTAL)\s+(?:DUE|TO\s+PAY)|TO\s+PAY|CREDIT|(?:AUTH\s+)?DEBIT"
    r"|CHANGE(?:\s+DUE)?|CASH(?:\s+BACK)?|TENDER(?:ED)?"
    r"|SUB\W{0,2}T(?:OTA|T)L|TOTAL|(?:SALES\s+)?TAX)\W*$",
    re.IGNORECASE,
)
# BRANDED settlement rows. SETTLEMENT_RE above requires the row be ONLY
# the tender word, so every branded form slips through and decodes as a
# phantom item: prod carried 12 such receipts ("Visa", "MASTERCARD",
# "MasterCard 1394 (Swipe)", "Visa ...3931", "xXXX5061 MASTERCARD",
# "Visa Tendered: Trans *: 9 Batch#:", Trader Joe's "Local Cash",
# "Payment (Cash):"), and Trader Joe's "Visa Debit $37.51" is what
# surfaced it.
#
# Recognized by CLOSED VOCABULARY rather than by loosening SETTLEMENT_RE's
# anchors, because that is what keeps real food safe: a settlement row is
# a row whose every word is payment vocabulary. "33965 PORK TENDER" and
# "CHICKEN TENDER" carry a tender word but also a word this vocabulary
# does not contain, so they stay items -- which a prefix/suffix-tolerant
# regex around TENDER could never guarantee.
#
# At least one ANCHOR (card brand or tender word) must be present; the
# remaining words must all be AFFIXes. Affixes alone are never enough, so
# "GIFT CARD" and "CARD HOLDER" stay items.
TENDER_ANCHOR_TOKENS = frozenset(
    {
        # card brands / networks
        "visa",
        "mastercard",
        "master",  # "MASTER CARD" printed as two words
        "amex",
        "discover",
        "diners",
        "jcb",
        "unionpay",
        "maestro",
        "interac",
        "eftpos",
        # tender vocabulary (mirrors SETTLEMENT_RE's settlement words)
        "cash",
        "change",
        "credit",
        "debit",
        "tender",
        "tendered",
        # "Paid with card (8644): 32.30" (Stanley of New Orleans, prod
        # 916d7955) states the settlement without naming a network or a
        # tender: every word is payment vocabulary but NONE was an anchor,
        # so the row decoded as a 32.30 item and put the receipt at
        # mismatch (61.55 vs a printed 29.25 its two real items hit
        # exactly). Promoted from affix, which is the maintenance path
        # test_branded_tender_rows documents for exactly this case.
        "paid",
    }
)
# Words that may surround an anchor: card-entry modes, transaction-id
# labels, and the payment-process nouns receipts print beside a tender.
# Deliberately excludes anything that reads as a product word.
PAYMENT_AFFIX_TOKENS = frozenset(
    {
        "acct",
        "account",
        "aid",
        "appr",
        "approved",
        "auth",
        "authorization",
        "batch",
        "card",
        "cardholder",
        "cards",
        "chip",
        "contactless",
        "ending",
        "emv",
        "entry",
        "express",  # "AMERICAN EXPRESS"
        "american",
        "for",  # "MASTERCARD ...8644 for 32.30"
        "insert",
        "inserted",
        "keyed",
        "local",  # Trader Joe's prints its cash tender as "Local Cash"
        "manual",
        "mid",
        "mobile",
        "no",
        "num",
        "number",
        # "paid" is an ANCHOR, not an affix -- see TENDER_ANCHOR_TOKENS.
        "pay",
        "payment",
        "payments",
        "pin",
        "purchase",
        "read",
        "ref",
        "reference",
        "sale",
        "seq",
        "swipe",
        "swiped",
        "tap",
        "tapped",
        "term",
        "terminal",
        "tid",
        "trans",
        "transaction",
        "type",
        "us",
        "usd",
        "verified",
        "with",  # "Paid with card (8644)"
    }
)
# A run of masking characters left behind once digits are stripped
# ("xXXX5061" -> "xXXX", "****1454" -> ""); never a word.
_MASK_TOKEN_RE = re.compile(r"^x+$", re.IGNORECASE)


def is_settlement_row(bare: str) -> bool:
    """Whether de-amounted row text reads as a settlement/tender row.

    ``bare`` is the row text with amounts already stripped (the same
    ``re.sub(r"\\$?\\d[\\d.,]*", " ", text)`` both call sites apply), so
    "Visa Debit $37.51" arrives as "Visa Debit".
    """
    if SETTLEMENT_RE.match(bare):
        return True
    tokens = re.findall(r"[A-Za-z]+", bare)
    if not tokens:
        return False
    lowered = [t.lower() for t in tokens]
    if not any(t in TENDER_ANCHOR_TOKENS for t in lowered):
        return False
    return all(
        t in TENDER_ANCHOR_TOKENS
        or t in PAYMENT_AFFIX_TOKENS
        or _MASK_TOKEN_RE.match(t)
        for t in lowered
    )


# SETTLEMENT_RE minus its SUB TOTAL / TOTAL / TAX alternatives. The two
# regexes are kept SEPARATE rather than composed because SETTLEMENT_RE is
# ported to Swift verbatim and any restructuring of it is a parity risk;
# the tender alternatives below are a literal copy of that prefix.
#
# The split exists because the two vocabularies have OPPOSITE section
# semantics. A bare TOTAL / SUB TOTAL / TAX row is legitimately printed
# INSIDE the items block by some merchants (In-N-Out, Trader Joe's), so
# the section assigner must leave those rows where the decoder put them.
# A TENDER row -- what the customer paid with, and the change handed back
# -- is never part of the items block on any receipt in the corpus.
_TENDER_ONLY_RE = re.compile(
    r"^\W*(?:ITEMS?\W+)?"
    r"(?:BALANCE(?:\s+DUE|\s+TO\s+PAY)?|DUE\s+BALANCE"
    r"|(?:AMOUNT|TOTAL)\s+(?:DUE|TO\s+PAY)|TO\s+PAY|CREDIT|(?:AUTH\s+)?DEBIT"
    r"|CHANGE(?:\s+DUE)?|CASH(?:\s+BACK)?|TENDER(?:ED)?)\W*$",
    re.IGNORECASE,
)


def is_tender_row(bare: str) -> bool:
    """Whether de-amounted row text names a TENDER (not a total or tax).

    Same closed-vocabulary contract as :func:`is_settlement_row` -- the
    row must carry a tender anchor and nothing but anchors and payment
    affixes -- with the summary words (SUB TOTAL / TOTAL / TAX) removed,
    so "Sub Total" is NOT a tender row while "Cash", "Change Due" and
    "Visa Debit" are.

    ROW-LEVEL BY CONTRACT. Pass the whole visual row's text, never a
    single OCR line's. The corpus sweep found dev Costco's pork
    tenderloin split by OCR across two lines: the line "33965 19.51
    TENDER" de-amounts to "TENDER" and returns True on its own, while
    the ROW it belongs to de-amounts to "PORK TENDER" -- and "pork" is
    neither anchor nor affix, so the row correctly returns False. A
    line-level call would forbid that row from ITEMS and kill a real
    item's decode. (The measured false-positive rate at row level is
    zero across 6,694 ITEMS rows in dev and prod.)
    """
    if _TENDER_ONLY_RE.match(bare):
        return True
    tokens = re.findall(r"[A-Za-z]+", bare)
    if not tokens:
        return False
    lowered = [t.lower() for t in tokens]
    if not any(t in TENDER_ANCHOR_TOKENS for t in lowered):
        return False
    return all(
        t in TENDER_ANCHOR_TOKENS
        or t in PAYMENT_AFFIX_TOKENS
        or _MASK_TOKEN_RE.match(t)
        for t in lowered
    )


# --- Column-header vocabulary --------------------------------------------
# The words a receipt prints ABOVE its item table ("Item Qty Price Total",
# "Description Qty Amount", "Unit Price") and the price-qualifier headings
# it prints beside a markdown ("ORIGINAL PRICE", "Reguler Price"). None of
# them is a product; all of them are printed on a row that also carries an
# amount, so the decoder sees a priced row and names an item after it.
#
# Same shape as the tender vocabulary above, and for the same reason: a
# header row is a row whose EVERY word is header vocabulary. A closed,
# whole-token vocabulary is what keeps real food safe here -- a receipt can
# and does sell an item whose name contains a header word ("HOUSE REGULAR
# COFFEE CUP", "THE ORIGINAL MINI KABOB WRAP", "BAG SALE PAPER EA",
# "LEMON EACH", "CUSTOM ITEM", "PRICE MATCH"), and each of those carries a
# word this vocabulary does not contain, so each stays an item.
#
# Whole-token matching is not a style preference. A substring scan for the
# OCR misread "UTY" matches inside "BEAUTY", and this corpus really does
# contain "BEAUTY SECRETS NOVELTY TWEEZERS GARDEN" -- the same failure that
# made a substring scan for "OFF" read COFFEE / TOFFEE as a discount.
COLUMN_HEADER_ANCHOR_TOKENS = frozenset(
    {
        "amount",
        "amounts",
        "descrip",  # OCR truncation of a narrow header column
        "description",
        "descriptions",
        "item",
        "items",
        "price",
        "prices",
        "pricing",
        "qty",
        "quantity",
        "subtotal",
        "tax",
        "total",
        "totals",
        "unit",
        "units",
        "uty",  # Vision reads "QTY" as "UTY" on small type
    }
)
# Words that only ever QUALIFY a header anchor. Affixes alone are never
# enough, so a bare "EACH" or "EA" -- which is unit vocabulary a real item
# ends in -- never makes a row a header.
COLUMN_HEADER_AFFIX_TOKENS = frozenset(
    {
        "ea",
        "each",
        "ext",
        "extended",
        "list",
        "net",
        "orig",
        "original",
        "per",
        "regul",  # OCR: "REGUL R PRICE"
        "regular",
        "reguler",  # OCR: "Reguler Price"
        "retail",
        "sale",
        "your",
    }
)
# Restriction/disclaimer footer legalese. Deliberately ONE family: the
# corpus's other footer-shaped names are real charged rows ("BOTTLE
# RETURN") or real printed annotations the decoder must keep ("MAX REFUND
# VALUE", 40 occurrences), so REFUND / RETURN / VALUE are NOT vocabulary
# here. "RESTRICT" is: no receipt in dev or prod sells anything whose name
# contains it, and every occurrence is the tail of "...tobacco or alcohol.
# Restrictions apply."
RESTRICTION_NOTE_RE = re.compile(
    r"\bRESTRICT(?:S|ED|ION|IONS)?\b", re.IGNORECASE
)


def is_column_header_row(name: str) -> bool:
    """Whether a decoded item name is a column header or footer legalese.

    ``name`` is the decoder's name for an item, NOT raw row text: amounts
    are already stripped out by ``parse_band``, so "Unit Price 25.00"
    arrives as "Unit Price".

    Two closed rules, both anchored:

    * at least one COLUMN_HEADER_ANCHOR token is present AND every
      multi-letter token is an anchor or an affix -- the ``is_settlement_row``
      shape;
    * or the name carries restriction-footer vocabulary.

    Single-letter tokens are ignored ("REGUL R PRICE" keeps its "R"), which
    cannot widen the rule: an anchor is still required, and one letter
    carries no product identity.
    """
    text = name or ""
    if RESTRICTION_NOTE_RE.search(text):
        return True
    tokens = [t.lower() for t in re.findall(r"[A-Za-z]{2,}", text)]
    if not tokens:
        return False
    if not any(t in COLUMN_HEADER_ANCHOR_TOKENS for t in tokens):
        return False
    return all(
        t in COLUMN_HEADER_ANCHOR_TOKENS or t in COLUMN_HEADER_AFFIX_TOKENS
        for t in tokens
    )


# Price-comparison metadata ("SALE 2 @ $1.89, WAS: $3.59 each"): the WAS
# amount is not a line price and the real item price is on its own band
WAS_PRICE_RE = re.compile(r"\b(?:WAS|REG)\b[:.]?\s*\$?\d", re.IGNORECASE)
# BOGO annotation echo: "PENNE RIGATE PAST Sale Price 1.99" restates the
# post-discount unit price already carried by the discount line; counting
# it double-counts. Target prints the same echo as "Regular Price $22.99"
# under each discounted item (mode D in the failure-mode report: dropping
# that one band closes the delta exactly), and Nordstrom Rack prints it as
# "Comparable Value 59.95". CVS prints it as "ORIGINAL PRICE 49.99" above
# the coupon line that discounts it, which is the same annotation under a
# third name -- and it is the only reason CVS 5a7b884a decoded a $49.99
# phantom next to its real $49.89 item. Exact phrases only — "BAG SALE
# PAPER EA" is a real item.
SALE_PRICE_RE = re.compile(
    r"\b(?:(?:SALE|REG(?:ULAR)?\.?|ORIGINAL)\s+PRICE|COMPARABLE\s+VALUE)\b",
    re.IGNORECASE,
)
# An amount qualified "per <unit>" is a RATE, not an extended price. The
# decoder already knows this shape in its punctuated forms -- QTY_AT_RE
# reads "1.23 lb @ 4.99/lb" and the 3-decimal rule keeps fuel's
# "$5.299/Gal" out of `amounts` -- but the spelled-out "per oz" had no
# reader, so Yogurtland's weight line ("Weight: 21.5 oz 8 $0.67 per oz",
# the "8" being OCR of "@") decoded as a $0.67 item and put the receipt at
# `near` (15.07 against a printed 14.40 its one real item hits exactly).
#
# Whole-token "PER" only: a substring scan matches inside PEPPER, and the
# corpus sells "PEPPER BELL GREEN EACH".
PER_UNIT_RATE_RE = re.compile(
    r"\bPER\s+(?:OZ|LB|LBS|KG|G|GAL|EA|EACH|CT|PK|ITEM|UNIT)\b",
    re.IGNORECASE,
)


def is_unit_rate_row(text: str, n_amounts: int) -> bool:
    """Whether a row states a per-unit rate and NO extended total.

    The amount count is the load-bearing half. A deli row really does
    print both -- "GROUND BEEF $4.99 per lb   7.48" -- and there the 7.48
    is a genuine line total that must survive. Only a row whose single
    amount IS the rate is an annotation, which is what makes this safe
    without a merchant list.
    """
    return n_amounts <= 1 and bool(PER_UNIT_RATE_RE.search(text or ""))


# Non-product annotations that carry an amount but are never items:
# tip-suggestion footers ("22% Tip = 4.40", "15% = 10.73", "18%: (Tip
# Total 9.27)") and transaction-count notes ("Items in Transaction: 5").
# The %-sign / exact-phrase anchors keep product names ("6% FAT MLK",
# "STEAK TIPS") out of reach.
NON_PRODUCT_NOTE_RE = re.compile(
    r"\d{1,3}\s*%\s*[:=]|%\s*TIP\b|\bTIP\s+TOTAL\b"
    r"|\bITEMS?\s+IN\s+TRANSACTION\b",
    re.IGNORECASE,
)
# Standalone leading quantity: "2 BURRITO ..." only when integer < 100.
# parse_band applies this shape inline (word-level, so it can record which
# word the quantity came from); the compiled pattern is kept exported
# because the Swift port mirrors the module's regex surface.
LEAD_QTY_RE = re.compile(r"^(\d{1,2})\s+(?=[A-Za-z])")
# --- OCR-tolerant quantity expression -------------------------------------
# The regexes above require clean glyphs: a literal "@" (or the single "g"
# misread QTY_AT_OCR_RE covers) and a literal "$". Vision does much worse
# than that on the small type receipts print quantity lines in -- Trader
# Joe's "6 @ $0.49" came back as the three words "6", "8", "S0.49", so the
# whole band parsed to None and 6 lemons at 0.49 were lost even though
# 6 x 0.49 == the item's printed 2.94 exactly.
#
# These two patterns are deliberately LOOSE. They never decide anything on
# their own: every pair they propose is accepted only when
# quantity x unit_price equals the item's own price to the cent
# (``quantity_candidates`` -> ``accept_quantity_pair``). The arithmetic is
# the arbiter, so the parser can afford to be generous with glyphs.
#
# Money token with an OCR-mangled currency sign: "$" read as "S"/"s"/"5",
# or simply absent. Two or three decimals (fuel/unit prices print three).
NOISY_MONEY_RE = re.compile(r"^[Ss5$]?(\d{1,4}(?:,\d{3})*\.\d{2,3})$")
# A lone token sitting between quantity and unit price that is the "@"
# glyph, however OCR read it ("8", "g", "a", "e", "0"/"O", "&", "©"), or a
# unit word an "N EA @ X" layout prints there. "FOR" is excluded on
# purpose: "4 FOR 1.00" is a deal price, not a unit price, and QTY_FOR_RE
# already carries its different arithmetic.
QTY_SEPARATOR_RE = re.compile(
    r"^(?:[@8gGaAeE0oO&©®*x×X]|EA|EACH|LB|KG|OZ|CT|PK|QTY)$",
    re.IGNORECASE,
)
# Cent-exact tolerance for accepting a quantity pair. Both sides are
# printed money, so this is float-representation slack, not a band.
QTY_CENT_TOLERANCE = 0.005
# --- Taxability flags fused onto the amount they annotate ----------------
# Registers print a taxability code after the price -- CVS "7.32N", Home
# Depot "0.38N", Dollar Tree "1.25T" -- and whether OCR returns it as its
# own word ("7.32" "N") or fused into one ("7.32N") is a property of the
# glyph spacing, not of the receipt. Both are the same printed thing, so
# one rule covers both: the old ``\s+`` form could only strip the split
# case, and the fused case fell through to ``looks_like_receipt_amount``,
# which rejects "7.32N" outright. CVS never prints a currency symbol, so
# every CVS line price was invisible to the decoder (11 prod / 11 dev
# receipts decoded ZERO items with a perfectly good ITEMS section).
#
# The flag alphabet stays the one this module already declares, and is
# NOT widened to a bare [A-Z]: the trailing letter is not always a flag.
# "BERNZOMATIC MAP-PRO FUEL 14.10Z" is a 14.1-OUNCE product size and
# "Diamonds 0.95g" is a weight; both read as money under [A-Z] and
# neither is money. Restricting to [TFNOAB] and anchoring on a whole
# money-shaped token is what keeps SKUs, store numbers and loyalty ids
# ("4006N", "123456F") out -- they carry no 2-decimal fraction, so they
# fail the amount test even after the flag comes off.
#
# The digit lookbehind keeps the suffix pattern from firing on ordinary
# words that happen to end in a flag letter ("CAT", "SKIN OFF").
TAX_FLAG_RE = re.compile(r"(?<=\d)\s*[TFNOAB]X?$")
# A whole token that is an amount carrying such a flag, capturing the
# amount. strip_tax_flag reads group 1 rather than substituting, so the
# Swift port is the same two operations in the same order.
FLAGGED_AMOUNT_RE = re.compile(r"^(\$?-?\d[\d.,]*\d)\s*[TFNOAB]X?$")

# Right-most amount-word x is the price column; "in column" = within this.
# Same gate decode_band_blocks uses for META echo absorption.
PRICE_COLUMN_X_TOL = 0.15
# Two column prices are distinct visual rows when their y-gap is at least
# this fraction of the shorter price-word's height. Tight enough that
# same-row Price/You-Pay pairs (Vons, y-ratio ~0.04) stay one band; loose
# enough that glued product rows (Sprouts clover/tomatoes ~0.26, Costco
# warehouse pairs ~0.30) split.
PRICE_COLUMN_Y_SEP = 0.25


def strip_tax_flag(token: str) -> str:
    """Return ``token`` without a taxability flag fused onto its amount.

    Shape-gated: anything that is not a money token plus a flag comes back
    unchanged, so this can be applied to every word of a band without
    risking a product name.
    """
    match = FLAGGED_AMOUNT_RE.fullmatch((token or "").strip())
    return match.group(1) if match else (token or "")


def is_line_price_word(word: dict) -> bool:
    """Whether a word is a two-decimal line price (not a weight/SKU).

    Shared by ``band_words`` (price-column split) and ``parse_band`` so the
    column test and the parse cannot disagree on what an amount is.
    """
    text = word.get("text") or ""
    if text.endswith(")") and "(" not in text:
        return False
    text = strip_tax_flag(text)
    if not (
        looks_like_receipt_amount(text)
        and re.search(r"\d[.,]\d{2}(?!\d)", text)
    ):
        return False
    return parse_receipt_amount(text) is not None


DISCOUNT_WORDS = ("SAVED", "SAVING", "OFF", "COUPON", "DISCOUNT", "PROMO")
# Discount markers, matched as WORDS. The tuple above was tested with a
# plain substring scan, which read "OFF" inside COFFEE / TOFFEE / Office
# and flagged 15 real prod items as discounts -- and discounts are
# excluded from reconciliation, so those receipts could never balance.
#
# "OFF" additionally needs a numeric qualifier ("30% OFF", "$2 OFF",
# "BOGO 50% OFF"): bare "OFF" is product vocabulary, not a discount --
# Trader Joe's "SALMON FILLET SKIN OFF" and "EASY OFF" oven cleaner are
# items. Every corpus row that is genuinely a markdown carries the
# percent/amount, so nothing real is lost.
# The plural/derived endings the old substring scan matched for free
# ("MEMBER SAVINGS", "PROMOTION") are kept explicitly.
DISCOUNT_WORD_RE = re.compile(
    r"\b(?:SAVED|SAVINGS?|COUPONS?|DISCOUNTS?|PROMO(?:TION)?S?)\b"
    r"|[\d%]\s*%?\s*OFF\b",
    re.IGNORECASE,
)
# META band looks like SKU/qty metadata (safe to drop as a price echo)
SKU_LIKE_RE = re.compile(r"\d{4,}")
NON_ITEM_SECTIONS = {
    "SUMMARY",
    "TOTAL_LINE",
    "PAYMENT",
    "TRANSACTION_INFO",
    "SURVEY",
    "FOOTER",
    "BARCODE",
    "STOREFRONT",
    "ADDRESS",
}


def estimate_skew(words: list[dict]) -> float:
    """Residual baseline slope (dy/dx) of the receipt, from the words alone.

    Some warped crops keep a degree or so of rotation, and the stored
    ``angle_degrees`` is 0.0 on exactly those receipts, so it cannot be used
    to correct for it. Each OCR line is a horizontal run of text, so the
    drift of its own words against x measures the residual slope directly.

    Only lines spanning a meaningful width vote (a two-word line covering 2%
    of the receipt gives a slope dominated by glyph noise), and the median
    rejects the outliers that price-column-only or wrapped lines produce.
    """
    by_line: dict[int, list[tuple[float, float]]] = {}
    for w in words:
        by_line.setdefault(w["line_id"], []).append((w["x"], w["y_mid"]))
    slopes: list[float] = []
    for pts in by_line.values():
        if len(pts) < 2:
            continue
        pts.sort()
        dx = pts[-1][0] - pts[0][0]
        if dx > 0.15:
            slopes.append((pts[-1][1] - pts[0][1]) / dx)
    if not slopes:
        return 0.0
    slopes.sort()
    return slopes[len(slopes) // 2]


def band_words(words: list[dict]) -> list[list[dict]]:
    """Cluster words into visual bands by y-center gaps.

    Banding runs on a de-skewed y so that a product name on the left and its
    price on the right land in the same band. Without this, ~1.3 degrees of
    residual skew moves the price column a full row out of alignment across
    the receipt's width, and every item silently pairs with the price of its
    neighbour -- measured at 0% name accuracy on Trader Joe's receipts while
    recall stayed at 100%, because the items were all found, just mislabeled.

    Tight row pitch can still glue two product rows into one band (two
    left-side names + two right-column prices). Those are split after
    clustering, using the price column -- never by inventing a missing
    amount, and never by merchant vocabulary.
    """
    if not words:
        return []
    med_h = sorted(w["h"] for w in words)[len(words) // 2] or 0.01

    # Deskew unconditionally. The former activation guard (skip when drift
    # was below the band gap) was a tuned constant; the low-drift receipt it
    # protected is now handled structurally by the name-assignment stage in
    # extract_items, which pairs name rows to priced rows by need and
    # distance instead of depending on band membership alone.
    slope = estimate_skew(words)

    def y_flat(w: dict) -> float:
        return w["y_mid"] - slope * w["x"]

    ws = sorted(words, key=y_flat)
    bands: list[list[dict]] = [[ws[0]]]
    for w in ws[1:]:
        # Anchor the gap test to the band's FIRST word, not its last:
        # single-linkage lets slow y-drift on skewed receipts chain many
        # rows into one band, silently merging items.
        if y_flat(w) - y_flat(bands[-1][0]) < med_h * 0.6:
            bands[-1].append(w)
        else:
            bands.append([w])
    for band in bands:
        band.sort(key=lambda w: w["x"])
    amt_xs = [w["x"] for w in words if is_line_price_word(w)]
    zone_col_x = max(amt_xs) if amt_xs else None
    return split_overmerged_price_bands(bands, zone_col_x)


def split_overmerged_price_bands(
    bands: list[list[dict]], zone_col_x: Optional[float]
) -> list[list[dict]]:
    """Split a band that carries 2+ y-separated right-column prices.

    ``band_words`` joins name-left with price-right; on tight row pitch
    that also glues two product rows into one band, and ``parse_band``
    then emits a single item. Split only when each resulting row has its
    own printed column price -- never mint an amount OCR did not see.
    Same-row two-price layouts (Price / You Pay) stay one band.
    """
    if zone_col_x is None:
        return bands
    out: list[list[dict]] = []
    for band in bands:
        out.extend(_split_one_price_column_band(band, zone_col_x))
    return out


def _split_one_price_column_band(
    band: list[dict], zone_col_x: float
) -> list[list[dict]]:
    col_prices = [
        w
        for w in band
        if is_line_price_word(w)
        and abs(w["x"] - zone_col_x) < PRICE_COLUMN_X_TOL
    ]
    if len(col_prices) < 2:
        return [band]
    col_prices = sorted(col_prices, key=lambda w: w["y_mid"])
    clusters: list[list[dict]] = [[col_prices[0]]]
    for price in col_prices[1:]:
        prev = clusters[-1][-1]
        min_h = min(price["h"] or 0.01, prev["h"] or 0.01)
        if price["y_mid"] - prev["y_mid"] < min_h * PRICE_COLUMN_Y_SEP:
            clusters[-1].append(price)
        else:
            clusters.append([price])
    if len(clusters) < 2:
        return [band]
    anchors = [sum(w["y_mid"] for w in c) / len(c) for c in clusters]
    groups: list[list[dict]] = [[] for _ in clusters]
    for word in band:
        nearest = min(
            range(len(anchors)), key=lambda i: abs(anchors[i] - word["y_mid"])
        )
        groups[nearest].append(word)
    parts = [g for g in groups if g]
    for part in parts:
        part.sort(key=lambda w: w["x"])
    parts.sort(key=lambda p: sum(w["y_mid"] for w in p) / len(p))
    return parts


def _word_ref(w: dict) -> dict:
    return {
        "line_id": w["line_id"],
        "word_id": w["word_id"],
        "text": w["text"],
        "x": w["x"],
    }


def parse_band(band: list[dict]) -> Optional[dict[str, Any]]:
    """Parse one visual band (x-sorted word dicts) into a line-item dict.

    Word-level price detection via receipt_dynamo.amounts: 3-decimal fuel
    unit prices, "(7.00g)" weights, and date-like decimals are never
    prices; leading/trailing/parenthesized minus all parse negative.
    Returns None when the band carries no price and no quantity form.
    """
    texts = [w["text"] for w in band]
    joined = " ".join(texts)

    # Char span of each word in the joined text, for regex-span -> word maps
    spans: list[tuple[int, int]] = []
    pos = 0
    for t in texts:
        spans.append((pos, pos + len(t)))
        pos += len(t) + 1

    def words_in_span(a: int, b: int) -> set[int]:
        return {i for i, (s, e) in enumerate(spans) if s < b and e > a}

    consumed: set[int] = set()

    # Word-level amounts (candidate prices). Beyond amounts' own gate,
    # require a 2-decimal fraction: bare thousands ("4,444" SKUs) and
    # one-decimal annotations ("$4,333.6" loyalty spend trackers) are
    # not line prices even though they parse as amounts.
    amounts: list[tuple[int, float]] = []
    for i, t in enumerate(texts):
        # A close-paren with no opening paren is not an accounting negative
        # but an OCR carcass of a quantity annotation ("(2 @0.00)" reads as
        # "(2" + "80.00)") — never a price. A fused taxability flag is
        # stripped before the amount test, the same rule
        # blocks._line_amounts applies when it decides band roles.
        # Without this the two disagreed: a CVS band was scored PRICE on
        # its "7.32N" and then parsed to None here, so the row produced
        # no item at all.
        if not is_line_price_word(band[i]):
            continue
        v = parse_receipt_amount(strip_tax_flag(t))
        if v is not None and abs(v) < 100000:
            amounts.append((i, v))

    # Quantity forms, joined-text (they straddle word boundaries).
    # The M-FOR-X deal runs first: QTY_AT_RE cannot match its "@ 2 FOR"
    # interior (no decimals after the @), but checking FOR first keeps
    # that invariant explicit.
    qty = unit_price = None
    qty_word_idxs: set[int] = set()
    m = QTY_FOR_RE.search(joined)
    if m:
        deal_n = float(m.group(2))
        qty = float(m.group(1)) if m.group(1) else deal_n
        unit_price = round(float(m.group(3)) / deal_n, 2)
        qty_word_idxs = words_in_span(m.start(), m.end())
    if qty is None:
        m = QTY_AT_RE.search(joined) or QTY_AT_OCR_RE.search(joined)
        if m:
            qty = float(m.group(1))
            unit_price = float(m.group(2))
            qty_word_idxs = words_in_span(m.start(), m.end())
    if qty is None:
        # bare "2 3.99" (qty + unit price, no @)
        m2 = re.fullmatch(
            r"(\d{1,2})\s+\$?(\d+\.\d{2})", joined.replace("$", "").strip()
        )
        if m2:
            qty = float(m2.group(1))
            unit_price = float(m2.group(2))
            qty_word_idxs = set(range(len(texts)))

    # Price = last amount word that isn't part of the qty expression's
    # unit price (fuel "$5.299/Gal" never appears in amounts at all).
    price = None
    price_idx = None
    for i, v in amounts:
        if qty is not None and i in qty_word_idxs and len(amounts) > 1:
            continue
        price = v
        price_idx = i
    if price is None and amounts:
        price_idx, price = amounts[-1]
    if price is None and qty is None:
        return None

    consumed |= qty_word_idxs
    if price_idx is not None:
        consumed.add(price_idx)

    upper = joined.upper()
    is_discount = (price is not None and price < 0) or bool(
        DISCOUNT_WORD_RE.search(upper)
    )

    # Name = words not consumed by price/qty, minus flags/currency tokens
    name_idxs: list[int] = []
    for i, t in enumerate(texts):
        if i in consumed:
            continue
        if looks_like_receipt_amount(strip_tax_flag(t)):
            continue  # an earlier price on the band is never name content
        if re.fullmatch(r"[TFNOAB]X?", t):
            continue  # taxability flag
        name_idxs.append(i)

    # Leading quantity forms consume their word. The consumed index is
    # recorded in qty_word_idxs so downstream consumers (word-level label
    # derivation) can point QUANTITY at the exact word the quantity came
    # from; name_idxs was already computed above, so widening the set here
    # cannot change the decoded name, price or quantity.
    if qty is None and name_idxs:
        first = texts[name_idxs[0]]
        m3 = QTY_MULT_RE.match(first)
        if m3:
            qty = float(m3.group(1))
            qty_word_idxs.add(name_idxs[0])
            name_idxs.pop(0)
        elif re.fullmatch(r"\d{1,2}", first) and any(
            re.search(r"[A-Za-z]{2,}", texts[i]) for i in name_idxs[1:]
        ):
            qty = float(first)
            qty_word_idxs.add(name_idxs[0])
            name_idxs.pop(0)

    # Trailing single-letter token: a taxability flag the fixed [TFNOAB]
    # filter above misses (Target "M", Arizona "V"), or a truncation
    # glyph OCR read as a letter -- Trader Joe's prints "SALMON FILLET
    # SKIN OFF (" on two identically-named rows and Vision read the
    # cut-off paren as "C" on one of them, so one product decoded under
    # two different names.
    #
    # Two empirical bounds keep real names intact, both measured over the
    # whole dev+prod corpus (2190 + 2515 stored items):
    #   * at least three preceding name words -- the only real item that
    #     ends in a bare letter is "094060194 UP VITAMIN C", which has
    #     two, so short names keep their letter;
    #   * never "S" -- it is the one letter that is more often a
    #     truncated plural than a flag ("ORG KOSHER DILL PICKLE S" is
    #     hand-labeled WITH it in the golden set).
    if len(name_idxs) >= 4 and re.fullmatch(
        r"[A-RT-Za-rt-z]", texts[name_idxs[-1]]
    ):
        name_idxs.pop()

    name = re.sub(r"\s{2,}", " ", " ".join(texts[i] for i in name_idxs)).strip(
        " @$-"
    )

    return {
        "name": name,
        "quantity": qty,
        "unit_price": unit_price,
        "price": price if price is not None else 0.0,
        "is_discount": is_discount,
        "raw_text": joined,
        "band": [_word_ref(band[i]) for i in range(len(band))],
        "name_word_ids": [
            {"line_id": band[i]["line_id"], "word_id": band[i]["word_id"]}
            for i in name_idxs
        ],
        "price_word_id": (
            {
                "line_id": band[price_idx]["line_id"],
                "word_id": band[price_idx]["word_id"],
            }
            if price_idx is not None
            else None
        ),
        "qty_word_ids": [
            {"line_id": band[i]["line_id"], "word_id": band[i]["word_id"]}
            for i in sorted(qty_word_idxs)
        ],
        "n_amounts": len(amounts),
    }


def quantity_candidates(band: list[dict]) -> list[dict[str, Any]]:
    """Every (quantity, unit_price) pair the band's glyphs could support.

    Tolerant on purpose -- this is the PARSING half of the contract, and
    it proposes rather than decides. A candidate is a whole-integer count
    token (2..99) standing immediately before a money token, optionally
    with one separator token between them ("@" however OCR read it, or a
    unit word). Nothing here is believed until ``accept_quantity_pair``
    checks the arithmetic against the item's own printed price.

    Returned dicts carry the two word references the pair came from, so a
    caller can point QUANTITY / UNIT_PRICE word labels at the exact words
    (the same provenance ``parse_band`` records in ``qty_word_ids``).

    Quantity 1 is excluded: it multiplies out trivially against any price
    and would "prove" itself on every single-unit row, which is precisely
    the coincidence the arithmetic gate exists to reject.
    """
    out: list[dict[str, Any]] = []
    n = len(band)
    for i, w in enumerate(band):
        if not re.fullmatch(r"\d{1,2}", w["text"] or ""):
            continue
        count = int(w["text"])
        if count < 2:
            continue
        # money token adjacent, or one separator glyph away
        for j in (i + 1, i + 2):
            if j >= n:
                break
            if j == i + 2 and not QTY_SEPARATOR_RE.match(band[i + 1]["text"]):
                break
            m = NOISY_MONEY_RE.match(band[j]["text"] or "")
            if not m:
                continue
            try:
                unit = float(m.group(1).replace(",", ""))
            except ValueError:
                continue
            if unit <= 0:
                continue
            out.append(
                {
                    "quantity": float(count),
                    "unit_price": unit,
                    "qty_word_ids": [
                        {
                            "line_id": band[k]["line_id"],
                            "word_id": band[k]["word_id"],
                        }
                        for k in (i, j)
                    ],
                }
            )
            break
    return out


def accept_quantity_pair(
    quantity: Optional[float], unit_price: Optional[float], price: Any
) -> bool:
    """Whether quantity x unit_price reproduces ``price`` to the cent.

    The ACCEPTANCE half of the contract, and the only thing that ever lets
    a quantity onto an item. Deliberately cent-exact rather than the 0.02
    band the META-absorption paths use: those paths also require a
    structural relationship (an adjacent unnamed price band), where this
    one is asked to believe glyphs that OCR already mangled once.
    """
    if quantity is None or unit_price is None:
        return False
    if not isinstance(price, (int, float)) or price <= 0:
        return False
    return abs(quantity * unit_price - price) <= QTY_CENT_TOLERANCE


def implied_unit_price(quantity: Any, price: Any) -> Optional[float]:
    """The unit price a known quantity implies, when it divides exactly.

    The leading-quantity forms ("2 BURRITO 9.98", "2X ...") record a count
    and nothing else, so half of every such row's arithmetic went unstored
    even though the receipt proves it. Returns None unless the division
    lands on a whole cent that multiplies back to the printed price.

    Two populations are refused on purpose:

    * quantity 1 (every restaurant "1 Cheese Fry 4.75" row, 218 of the
      233 dev items that carry a count but no unit price). Its unit price
      would equal the extended price by definition, and the word carrying
      it is the one already labelled LINE_TOTAL -- storing it would put
      two contradictory financial roles on one word for no information.
    * counts that are really name content ("12 Naked Wings" at 18.49,
      "3 CHEESE SPINACH & ARTIC" at 3.79). Neither divides into whole
      cents, so the arithmetic rejects them without needing to know they
      are false quantities -- which is the entire point of the gate.
    """
    if not isinstance(quantity, (int, float)) or quantity < 2:
        return None
    if not isinstance(price, (int, float)) or price <= 0:
        return None
    unit = round(price / quantity, 2)
    if unit <= 0 or not accept_quantity_pair(quantity, unit, price):
        return None
    return unit


# Tokens that don't count as product-name content (units, tax flags, SKU-ish)
UNIT_WORDS = {
    "EA",
    "LB",
    "KG",
    "OZ",
    "CT",
    "PK",
    "X",
    "C",
    "F",
    "T",
    "N",
    "O",
    "A",
    "B",
    "TX",
    "FS",
    "QTY",
    "EACH",
}


def _name_is_real(name: str) -> bool:
    tokens = re.findall(r"[A-Za-z]{2,}", name or "")
    real = [t for t in tokens if t.upper() not in UNIT_WORDS]
    return len("".join(real)) >= 3


def extract_items(
    words: list[dict],
    line_ids: set[int],
    summary: Optional[dict] = None,
) -> tuple[list[dict], bool]:
    """Extract items via the band-block decoder (Phase D integration).

    Delegates to receipt_upload.line_items.blocks.decode_band_blocks with
    the committed golden-trained priors. Gates at swap time: all 12
    semantic guarantees, golden floors LOO 85/57/86 with zero failures,
    corpus sweep match 418 vs 415. The banded implementation remains below
    as _extract_items_banded for the corpus diff harness.

    ``summary`` (optional {subtotal, tax, grand_total}) activates the
    non-product band filter: bands whose price merely restates a printed
    summary figure are dropped (see blocks.filter_summary_figure_items
    for the guards). Default None preserves the unfiltered decode for
    callers that have no summary.
    """
    from receipt_upload.line_items.blocks import (
        decode_band_blocks,
        load_default_priors,
    )

    items = decode_band_blocks(
        {"words": list(words), "items_line_ids": sorted(line_ids)},
        load_default_priors(),
        summary=summary,
    )
    return constrain_items_to_baseline(items, summary), False


def _extract_items_banded(
    words: list[dict], line_ids: set[int]
) -> tuple[list[dict], bool]:
    """Extract items from the section's words.

    Bands classify as ITEM (real name + price), NAME (name, no price), or
    META (price/qty but no real name: SKU echoes, weight lines, bare
    "2 3.99" qty lines). META bands never become items on their own —
    they attach quantity to an adjacent ITEM, pair with a preceding NAME
    band (stacked name-over-price layouts), or are dropped as price
    echoes when a neighbor already carries the same price.

    Returns (items, collapsed); collapsed flags degenerate banding
    (skewed geometry merged many prices into one band).
    """
    section_words = [w for w in words if w["line_id"] in line_ids]
    collapsed = False
    bands = []
    for band in band_words(section_words):
        text = " ".join(w["text"] for w in band)
        lids = sorted({w["line_id"] for w in band})
        y_mid = sum(w["y_mid"] for w in band) / len(band)
        parsed = parse_band(band)
        if parsed is not None and parsed["n_amounts"] >= 3 and len(text) > 80:
            collapsed = True
        if parsed is None:
            if _name_is_real(text):
                bands.append(
                    (
                        "NAME",
                        {
                            "name": text.strip(),
                            "line_ids": lids,
                            "y_mid": y_mid,
                            "band": [_word_ref(w) for w in band],
                            "name_word_ids": [
                                {
                                    "line_id": w["line_id"],
                                    "word_id": w["word_id"],
                                }
                                for w in band
                            ],
                        },
                    )
                )
            continue
        parsed["line_ids"] = lids
        parsed["y_mid"] = y_mid
        if _name_is_real(parsed["name"]) or parsed["is_discount"]:
            bands.append(("ITEM", parsed))
        else:
            bands.append(("META", parsed))

    # --- META resolution against adjacent ITEMs (qty transplant / echo
    # dedupe). Unchanged rules; runs before name assignment so a NAME band
    # never attaches to a META that is about to be absorbed by a neighbor.
    dropped: set[int] = set()
    for i, (kind, data) in enumerate(bands):
        if kind != "META":
            continue
        neighbors = [
            bands[j][1]
            for j in (i - 1, i + 1)
            if 0 <= j < len(bands) and bands[j][0] == "ITEM"
        ]
        qty, unit = data.get("quantity"), data.get("unit_price")
        for nb in neighbors:
            # qty metadata explains the neighbor's price -> attach
            if (
                qty is not None
                and unit is not None
                and abs(qty * unit - nb["price"]) <= 0.02
            ):
                nb["quantity"], nb["unit_price"] = qty, unit
                nb.setdefault("extra_bands", []).append(
                    {
                        "band": data["band"],
                        "qty_word_ids": data["qty_word_ids"],
                        "price_word_id": data["price_word_id"],
                    }
                )
                dropped.add(i)
                break
            # Same price -> a price echo of the neighbor, but ONLY when
            # the META text actually looks like SKU/qty metadata. Two
            # genuinely distinct same-priced items (one with a garbled
            # name) must both survive.
            if abs(data["price"]) == abs(nb["price"]) and (
                SKU_LIKE_RE.search(data["raw_text"]) or qty is not None
            ):
                if qty is not None and nb["quantity"] is None:
                    nb["quantity"], nb["unit_price"] = qty, unit
                nb.setdefault("extra_bands", []).append(
                    {
                        "band": data["band"],
                        "qty_word_ids": data["qty_word_ids"],
                        "price_word_id": data["price_word_id"],
                    }
                )
                dropped.add(i)
                break

    # --- Name assignment. A NAME band may attach ONLY to an adjacent
    # priced band that NEEDS a name (no real alpha name of its own) --
    # anchors that already carry a real name never receive attachments,
    # matching the old pending_name behaviour where an ITEM discarded any
    # pending name. Rules, in order:
    #   1. neither adjacent priced band needs a name -> the NAME band is
    #      dropped (department headers, wrapped notes);
    #   2. exactly one needs -> attach there;
    #   3. both need -> a single receipt-wide orientation (all-prev or
    #      all-next) chosen to minimize priced bands left without a real
    #      name (the structural stranded-ends criterion), then by total
    #      attach distance. Per-name float comparisons are NOT used here:
    #      uniform row pitch makes them coin flips.
    # This subsumes both stacked directions (name-above and name-below)
    # without a per-receipt direction vote, which the corpus analysis
    # showed cannot work (Home Depot needs both in one receipt).
    priced = [
        i
        for i, (k, _) in enumerate(bands)
        if k in ("ITEM", "META") and i not in dropped
    ]
    name_idxs = [i for i, (k, _) in enumerate(bands) if k == "NAME"]

    def _needs_name(idx: int) -> bool:
        # An anchor needs a name when its own row does not carry a
        # human-readable one. Beyond "no real alpha at all" (META), a row
        # whose alpha survives only as a single compressed code after SKU
        # stripping ("764666103221 15/8CRDWSC5#" -> "CRDWSC") is a SKU
        # line, not a product name: real names keep >= 2 alpha words.
        # This is a structural property of the text, not a tuned constant.
        own = bands[idx][1].get("name") or ""
        if not _name_is_real(own):
            return True
        stripped = re.sub(r"\d{4,}", " ", own)
        tokens = [
            t
            for t in re.findall(r"[A-Za-z]{2,}", stripped)
            if t.upper() not in UNIT_WORDS
        ]
        return len(tokens) < 2

    forced: dict[int, int] = {}
    ambiguous: list[tuple[int, int, int, float, float]] = []
    for n in name_idxs:
        prev_p = max((p for p in priced if p < n), default=None)
        next_p = min((p for p in priced if p > n), default=None)
        prev_ok = prev_p is not None and _needs_name(prev_p)
        next_ok = next_p is not None and _needs_name(next_p)
        if not prev_ok and not next_ok:
            continue  # rule 1: nobody needs this name
        if prev_ok != next_ok:
            forced[n] = prev_p if prev_ok else next_p
            continue
        d_prev = abs(bands[n][1]["y_mid"] - bands[prev_p][1]["y_mid"])
        d_next = abs(bands[next_p][1]["y_mid"] - bands[n][1]["y_mid"])
        ambiguous.append((n, prev_p, next_p, d_prev, d_next))

    best_assign: dict[int, int] = dict(forced)
    if ambiguous:
        best_key = None
        for orientation in ("prev", "next"):
            trial = dict(forced)
            total_d = 0.0
            for n, prev_p, next_p, d_prev, d_next in ambiguous:
                if orientation == "prev":
                    trial[n] = prev_p
                    total_d += d_prev
                else:
                    trial[n] = next_p
                    total_d += d_next
            named = set(trial.values())
            unnamed = sum(
                1 for p in priced if _needs_name(p) and p not in named
            )
            key = (unnamed, total_d, orientation)
            if best_key is None or key < best_key:
                best_key = key
                best_assign = trial

    attached_names: dict[int, list[dict]] = {}
    for n, p in best_assign.items():
        attached_names.setdefault(p, []).append(bands[n][1])

    # --- Emit items in band order, merging attached names.
    items: list[dict] = []
    for i in priced:
        kind, data = bands[i]
        names = attached_names.get(i, [])
        if names:
            # Only needing anchors receive names, so the attached
            # description IS the product name (golden truth uses the
            # human-readable line, not the SKU line).
            data["name"] = " ".join(nm["name"] for nm in names).strip()
            data["line_ids"] = sorted(
                set(data["line_ids"])
                | {lid for nm in names for lid in nm["line_ids"]}
            )
            data["name_band"] = names[0]["band"]
            data["name_word_ids"] = [
                ref for nm in names for ref in nm["name_word_ids"]
            ]
            data["stacked"] = True
        if kind == "ITEM":
            if data["price"] == 0 and data["quantity"] is None:
                continue
            items.append(data)
            continue
        # surviving META
        if data["price"] == 0 and not names:
            # unnamed zero band is noise; a named $0.00 item (free/comped
            # line with its price in the price column) is kept via pairing
            continue
        if not names:
            # No name anywhere (SKU-only or garbled OCR). Keep the price —
            # dropping it hides real spend — but flag the name quality.
            data["name_quality"] = "low"
        items.append(data)
    return items, collapsed


@dataclass
class ReconcileResult:
    """Full reconciliation verdict.

    ``status`` keeps the existing four-value vocabulary (match / near /
    mismatch / no-baseline) -- golden floors, the stream stage and the
    ReceiptLineItem entity all depend on it. The extra fields are purely
    additive diagnostics:

    ``baseline_source``: which printed figure the items were compared
    against -- "subtotal" or "grand_total_minus_tax".

    ``baseline_figures_agreeing``: graded confidence of a match/near
    verdict = how many printed summary figures (subtotal, tax,
    grand_total) participate in an arithmetic story consistent with the
    item sum. 3 = subtotal, tax and grand_total all printed and
    subtotal + tax ~= grand_total (reconcile's match band on
    grand_total); 2 = two figures corroborate (subtotal ~= grand_total
    with no printed tax, or a grand_total - tax baseline with a printed
    tax); 1 = only a single printed figure existed to check against, or
    the cross-figure identity failed. None when status is mismatch or
    no-baseline.
    """

    status: str
    item_sum: Optional[float]
    baseline: Optional[float]
    baseline_source: Optional[str] = None
    baseline_figures_agreeing: Optional[int] = None


# Absolute plausibility ceiling for any printed summary figure. SKU /
# barcode strings occasionally OCR-parse as money (a 24-char SKU tail
# became a $2.0B subtotal); no honest receipt here approaches this.
MAX_PLAUSIBLE_BASELINE = 50_000.0


def _classify_against(item_sum: float, baseline: float) -> str:
    diff = abs(item_sum - baseline)
    if diff <= max(0.02, baseline * 0.01):
        return "match"
    if diff <= max(1.0, baseline * 0.10):
        return "near"
    return "mismatch"


def _baseline_implausible(item_sum: float, baseline: float) -> bool:
    """A printed baseline no honest receipt produces.

    Mirrors the failure-modes audit's B-baseline-ocr-broken rule: the
    extracted items exceed triple the baseline, i.e. OCR dropped digits
    from the printed figure. Deliberately one-directional -- a baseline
    far ABOVE the item sum is severe under-extraction (zone gap, zero
    items), which is the extractor's fault and must stay a hard
    mismatch; only a baseline the extracted evidence overwhelms is the
    baseline's fault.
    """
    return item_sum > 3 * baseline


def reconcile_detailed(
    items: list[dict], summary: Optional[dict]
) -> ReconcileResult:
    """Compare extracted item sum against the printed summary figures.

    Baseline selection: subtotal when printed (and positive); else
    grand_total - tax (tax defaulting to 0 when absent). Neither figure
    -> no-baseline.

    Baseline sanity: a would-be mismatch against a broken baseline
    (subtotal > grand_total, or implausible per
    ``_baseline_implausible``) is reclassified no-baseline instead of
    blaming the extractor -- unless grand_total - tax rescues it to a
    clean match/near. Receipts whose baseline is sane keep exactly the
    pre-existing match/near/mismatch semantics, and an already
    match/near verdict is never rerouted.
    """
    if summary is None:
        return ReconcileResult("no-baseline", None, None)

    def _f(key):
        v = summary.get(key)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    subtotal, grand, tax = _f("subtotal"), _f("grand_total"), _f("tax")
    # Figure hygiene: a zero/negative printed figure is no figure at
    # all (a $0.00 subtotal must not shadow a real grand_total), and
    # neither is an impossible one -- Zen Leaf 5b1ea5d7 r1 OCR-parsed
    # the SKU "1A4040300003CF2000271909" as a $2,000,271,909.00
    # subtotal. No receipt in this corpus is remotely near $50k.
    if subtotal is not None and not 0 < subtotal <= MAX_PLAUSIBLE_BASELINE:
        subtotal = None
    if grand is not None and not 0 < grand <= MAX_PLAUSIBLE_BASELINE:
        grand = None
    fallback = None
    if grand is not None:
        fallback = round(grand - (tax or 0.0), 2)
        if fallback <= 0:
            fallback = None

    if subtotal is not None:
        baseline, source = subtotal, "subtotal"
    elif fallback is not None:
        baseline, source = fallback, "grand_total_minus_tax"
    else:
        return ReconcileResult("no-baseline", None, None)

    item_sum = round(sum(i["price"] for i in items), 2)
    status = _classify_against(item_sum, baseline)

    if status == "mismatch":
        if source == "subtotal":
            insane = (
                grand is not None and subtotal > grand + 0.01
            ) or _baseline_implausible(item_sum, subtotal)
            if insane:
                if (
                    fallback is not None
                    and abs(fallback - subtotal) > 0.005
                    and not _baseline_implausible(item_sum, fallback)
                    and _classify_against(item_sum, fallback) != "mismatch"
                ):
                    baseline, source = fallback, "grand_total_minus_tax"
                    status = _classify_against(item_sum, fallback)
                else:
                    return ReconcileResult("no-baseline", None, None)
        elif _baseline_implausible(item_sum, baseline):
            return ReconcileResult("no-baseline", None, None)

    grade = None
    if status in ("match", "near"):
        grade = 1
        if source == "subtotal":
            if grand is not None and abs(
                round(subtotal + (tax or 0.0), 2) - grand
            ) <= max(0.02, grand * 0.01):
                grade = 3 if tax is not None else 2
        elif tax is not None:
            # items ~= grand_total - printed tax: grand and tax both
            # corroborate; no printed subtotal so 3 is unreachable.
            grade = 2
    return ReconcileResult(status, item_sum, baseline, source, grade)


def reconcile(
    items: list[dict], summary: Optional[dict]
) -> tuple[str, Optional[float], Optional[float]]:
    """Compare extracted item sum against summary subtotal/grand_total.

    Tuple-compatible wrapper over :func:`reconcile_detailed`; existing
    callers unpack (status, item_sum, baseline).
    """
    r = reconcile_detailed(items, summary)
    return r.status, r.item_sum, r.baseline


# PROVEN policy constant (user-decided 2026-08-03): exact-to-the-cent
# means a difference strictly under half a cent.
PROVEN_CENT_TOLERANCE = 0.005


def is_proven(
    recon_status: Optional[str],
    printed_total: Optional[float],
    bank_amount: Optional[float],
) -> bool:
    """PROVEN = exact-to-the-cent on BOTH truth-chain hops.

    Policy constant (user-decided 2026-08-03): a receipt is proven only
    when hop 1 (extracted items -> printed figures) reconciles as a
    full ``match`` — ``near`` NEVER counts, however small the band —
    AND hop 2 (printed total -> bank ledger amount) agrees to the cent
    (|printed - bank| < $0.005).

    The strictness exists because tolerance bands admit false accepts:
    the 14-receipt vision pilot found a receipt (1828b9ba) whose
    printed tax was 0.97 but whose stored figure read 1.07 — both
    arithmetics "close" inside the bands, and only the cent-exact bank
    hop (or the image) can see the dime. Anything not exactly right is
    not proven; it is at best "near", and near is a review queue, not
    a proof.

    Missing or non-numeric figures on either hop fail closed.
    """
    if recon_status != "match":
        return False
    if printed_total is None or bank_amount is None:
        return False
    try:
        printed = float(printed_total)
        bank = float(bank_amount)
    except (TypeError, ValueError):
        return False
    # Round the difference to the mill first: 21.075 - 21.07 computes
    # to 0.004999... in binary floats, and a half-cent gap must NOT
    # slip under the strict < 0.005 policy line on representation
    # noise alone.
    return round(abs(printed - bank), 3) < PROVEN_CENT_TOLERANCE


# Reconciliation rank for the ITEMS-boundary repair guard.  no-baseline is
# deliberately absent: an extension cannot be arithmetic-verified when
# either side has no comparable baseline.
_BOUNDARY_RECON_RANK = {"match": 0, "near": 1, "mismatch": 2}
_MAX_CONSTRAINT_DROPS = 3
# Cap unnamed candidates, not just drop-count. Exhaustive 1..3-subsets of
# an uncapped pool is O(d^3) reconciles per receipt; a long ITEMS zone of
# numeric bands (or PR2 splitting one band into many) would blow the
# Lambda budget. Later bands are preferred — totals sit below items.
_MAX_CONSTRAINT_CANDIDATES = 8


def _priced_for_reconcile(
    items: list[dict], include_discounts: bool
) -> list[dict]:
    """Items that participate in the printed-total sum."""

    if include_discounts:
        return list(items)
    return [item for item in items if not item.get("is_discount")]


def _recon_eval(result: ReconcileResult) -> dict[str, Any]:
    """Shape ``items_boundary_extension_guard`` consumes."""

    delta = (
        round(result.item_sum - result.baseline, 2)
        if result.item_sum is not None and result.baseline is not None
        else None
    )
    return {"status": result.status, "delta": delta}


def reconcile_extracted_items(
    items: list[dict], summary: Optional[dict]
) -> ReconcileResult:
    """Reconcile decoded items against the printed baseline.

    The historical sum skipped ``is_discount`` rows, which over-counts
    BOGO / void lines that already sit in ITEMS (the discount is the
    arithmetic). Including discounts is accepted only when it strictly
    shrinks ``|delta|`` and improves status — the same guard as an
    ITEMS-boundary repair. A receipt that already matches without
    discounts is left alone.
    """

    excluded = reconcile_detailed(_priced_for_reconcile(items, False), summary)
    if excluded.status == "match" or summary is None:
        return excluded
    included = reconcile_detailed(_priced_for_reconcile(items, True), summary)
    if included.item_sum == excluded.item_sum:
        return excluded
    verified, _ = items_boundary_extension_guard(
        _recon_eval(excluded), _recon_eval(included)
    )
    return included if verified else excluded


def constrain_items_to_baseline(
    items: list[dict], summary: Optional[dict]
) -> list[dict]:
    """Drop priced bands iff the printed total then reconciles better.

    No merchant vocabulary: unnamed non-discount bands are candidates
    (a named product coincidentally priced at the gap must survive), and
    a drop ships only when ``items_boundary_extension_guard`` accepts
    it. Discounts are never dropped; ``reconcile_extracted_items``
    decides whether they count. Already-matching receipts are untouched.
    """

    if not items or summary is None:
        return items
    baseline = reconcile_extracted_items(items, summary)
    if baseline.status == "match":
        return items

    before = _recon_eval(baseline)
    winners: list[tuple] = []

    def _consider(drop: tuple[int, ...]) -> None:
        remaining = [item for i, item in enumerate(items) if i not in drop]
        if not remaining:
            return
        after = reconcile_extracted_items(remaining, summary)
        verified, _ = items_boundary_extension_guard(
            before, _recon_eval(after)
        )
        if not verified:
            return
        winners.append(
            (
                _BOUNDARY_RECON_RANK.get(after.status, 9),
                (
                    abs(after.item_sum - after.baseline)
                    if after.item_sum is not None
                    and after.baseline is not None
                    else 99.0
                ),
                len(drop),
                # Prefer later bands at every position, not just max(drop).
                # Otherwise (0, 7) sorts before (6, 7) and an early real
                # unnamed item can lose to a later phantom.
                tuple(-i for i in sorted(drop, reverse=True)),
                drop,
                after,
            )
        )

    droppable = [
        i
        for i, item in enumerate(items)
        if not item.get("is_discount")
        and not _name_is_real(str(item.get("name") or ""))
    ]
    if len(droppable) > _MAX_CONSTRAINT_CANDIDATES:
        droppable = droppable[-_MAX_CONSTRAINT_CANDIDATES:]
    max_k = min(_MAX_CONSTRAINT_DROPS, len(droppable))
    for k in range(1, max_k + 1):
        for combo in combinations(droppable, k):
            _consider(combo)
    if not winners:
        return items
    winners.sort()
    return [item for i, item in enumerate(items) if i not in winners[0][4]]


def evaluate_items_zone(
    words: list[dict], summary: Optional[dict], line_ids: set[int]
) -> dict[str, Any]:
    """Decode and reconcile one proposed ITEMS zone.

    This is shared by the MCP repair tool and the automatic ingest repair so
    the verifier cannot drift.  The printed total is a constraint: discount
    lines in ITEMS count when they close the delta, and extra priced bands
    drop when dropping them improves reconciliation.
    """

    items, collapsed = extract_items(words, set(line_ids), summary=summary)
    result = reconcile_extracted_items(items, summary)
    delta = (
        round(result.item_sum - result.baseline, 2)
        if result.item_sum is not None and result.baseline is not None
        else None
    )
    return {
        "status": result.status,
        "items_sum": result.item_sum,
        "baseline": result.baseline,
        "delta": delta,
        "n_items": len(items),
        "collapsed_banding": collapsed,
    }


def items_boundary_extension_guard(
    before: dict[str, Any], after: dict[str, Any]
) -> tuple[bool, Optional[str]]:
    """Apply the exact reconciliation guard for an ITEMS extension.

    Acceptance requires both a strictly smaller absolute delta and a better
    reconciliation status: mismatch -> near/match or near -> match.
    """

    if before.get("status") == "match":
        return False, (
            "Current ITEMS zone already reconciles (match); nothing to "
            "repair."
        )
    if (
        before.get("status") not in _BOUNDARY_RECON_RANK
        or after.get("status") not in _BOUNDARY_RECON_RANK
        or before.get("delta") is None
        or after.get("delta") is None
    ):
        return False, (
            "Cannot verify the extension: reconciliation did not produce "
            "comparable deltas for both zones."
        )

    shrinks = abs(after["delta"]) < abs(before["delta"])
    improves = (
        _BOUNDARY_RECON_RANK[after["status"]]
        < _BOUNDARY_RECON_RANK[before["status"]]
    )
    if not (shrinks and improves):
        return False, (
            "Arithmetic guard failed: extension must strictly shrink "
            f"|delta| (before {before['delta']}, after {after['delta']}) "
            f"AND improve status (before {before['status']!r}, after "
            f"{after['status']!r})."
        )
    return True, None


def _entity_field(entity: Any, name: str, default: Any = None) -> Any:
    if isinstance(entity, dict):
        return entity.get(name, default)
    return getattr(entity, name, default)


def _is_non_product_row(row_words: list[dict]) -> bool:
    """Whether the decoder structurally recognizes a settlement/note row."""

    for band in band_words(row_words):
        text = " ".join(str(word.get("text") or "") for word in band)
        bare = re.sub(r"\$?\d[\d.,]*", " ", text).strip()
        if (
            is_settlement_row(bare)
            or WAS_PRICE_RE.search(text)
            or SALE_PRICE_RE.search(text)
            or NON_PRODUCT_NOTE_RE.search(text)
        ):
            return True
    return False


def _is_priced_product_row(row_words: list[dict]) -> bool:
    """Whether one visual row is safe to consider as a boundary item."""

    if _is_non_product_row(row_words):
        return False
    has_price = False
    for band in band_words(row_words):
        parsed = parse_band(band)
        if parsed is not None and parsed.get("price") not in (None, 0):
            has_price = True
    return has_price


def propose_items_boundary_extension(
    words: list[dict],
    summary: Optional[dict],
    current_line_ids: set[int],
    sections: list[Any],
    rows: list[Any],
    current_row_ids: Optional[list[int]] = None,
) -> Optional[dict[str, Any]]:
    """Return the best reconciliation-verified adjacent-row extension.

    Only whole, unclaimed, priced ReceiptRows in gaps inside the ITEMS span or
    adjacent to either edge are candidates.  Edge candidates are contiguous
    prefixes of the unclaimed zone (neutral barcode/SKU rows may separate
    printed product rows); claimed or settlement rows terminate the scan.
    Among verified proposals, prefer the best status, then the smallest
    absolute delta, then the smallest boundary change.
    """

    current = {int(line_id) for line_id in current_line_ids}
    if not words or not current or not rows:
        return None

    other_claimed: set[int] = set()
    for section in sections:
        if str(_entity_field(section, "section_type", "")).upper() == "ITEMS":
            continue
        other_claimed.update(
            int(line_id)
            for line_id in (_entity_field(section, "line_ids", []) or [])
        )

    words_by_line: dict[int, list[dict]] = {}
    for word in words:
        try:
            line_id = int(word["line_id"])
        except (KeyError, TypeError, ValueError):
            continue
        words_by_line.setdefault(line_id, []).append(word)

    visual_rows: list[dict[str, Any]] = []
    for row in rows:
        line_ids = {
            int(line_id)
            for line_id in (_entity_field(row, "line_ids", []) or [])
        }
        row_words = [
            word
            for line_id in line_ids
            for word in words_by_line.get(line_id, [])
        ]
        if not line_ids or not row_words:
            continue
        y_min = _entity_field(row, "y_min")
        if y_min is None:
            y_min = min(float(word.get("y_mid", 0.0)) for word in row_words)
        visual_rows.append(
            {
                "row_id": int(_entity_field(row, "row_id")),
                "line_ids": line_ids,
                "words": row_words,
                "y_min": float(y_min),
            }
        )
    visual_rows.sort(key=lambda row: (row["y_min"], row["row_id"]))

    item_indexes = [
        index
        for index, row in enumerate(visual_rows)
        if row["line_ids"] & current
    ]
    if not item_indexes:
        return None

    def adjacent_chain(indexes: range) -> list[dict[str, Any]]:
        chain = []
        for index in indexes:
            row = visual_rows[index]
            if row["line_ids"] & (current | other_claimed):
                break
            if _is_non_product_row(row["words"]):
                break
            if _is_priced_product_row(row["words"]):
                chain.append(row)
        return chain

    first, last = min(item_indexes), max(item_indexes)
    interior = [
        row
        for row in visual_rows[first : last + 1]
        if not row["line_ids"] & (current | other_claimed)
        and _is_priced_product_row(row["words"])
    ]
    above = adjacent_chain(range(first - 1, -1, -1))
    below = adjacent_chain(range(last + 1, len(visual_rows)))
    if not interior and not above and not below:
        return None

    before = evaluate_items_zone(words, summary, current)
    proposals = []

    def record_proposal(added_rows: list[dict[str, Any]]) -> None:
        added_line_ids = {
            line_id for row in added_rows for line_id in row["line_ids"]
        }
        proposed_line_ids = current | added_line_ids
        after = evaluate_items_zone(words, summary, proposed_line_ids)
        verified, _ = items_boundary_extension_guard(before, after)
        if not verified:
            return
        proposal = {
            "line_ids": sorted(proposed_line_ids),
            "added_line_ids": sorted(added_line_ids),
            "added_row_ids": sorted(row["row_id"] for row in added_rows),
            "row_ids": (
                sorted(
                    {int(row_id) for row_id in current_row_ids}
                    | {row["row_id"] for row in added_rows}
                )
                if current_row_ids is not None
                else None
            ),
            "before": before,
            "after": after,
        }
        signature = tuple(proposal["added_line_ids"])
        if not any(
            tuple(existing["added_line_ids"]) == signature
            for existing in proposals
        ):
            proposals.append(proposal)

    # Whole-zone proposals preserve every priced row inside the current span
    # and grow outward only as contiguous edge prefixes.
    for above_count in range(len(above) + 1):
        for below_count in range(len(below) + 1):
            if not interior and above_count == 0 and below_count == 0:
                continue
            record_proposal(
                interior + above[:above_count] + below[:below_count]
            )

    # Some OCR sections have several independent internal holes.  Follow the
    # arithmetic downhill one row at a time, but do not persist an
    # intermediate mismatch: only record states that pass the original
    # strict status-and-delta guard.  Edge availability remains prefix-based,
    # so the search cannot jump over a nearer priced row.
    selected: list[dict[str, Any]] = []
    remaining_interior = list(interior)
    above_count = below_count = 0
    current_evaluation = before
    while current_evaluation.get("delta") is not None:
        available = list(remaining_interior)
        if above_count < len(above):
            available.append(above[above_count])
        if below_count < len(below):
            available.append(below[below_count])
        downhill = []
        for row in available:
            candidate_rows = selected + [row]
            candidate_line_ids = current | {
                line_id
                for candidate in candidate_rows
                for line_id in candidate["line_ids"]
            }
            evaluation = evaluate_items_zone(
                words, summary, candidate_line_ids
            )
            if evaluation.get("delta") is not None and abs(
                evaluation["delta"]
            ) < abs(current_evaluation["delta"]):
                downhill.append((evaluation, row))
        if not downhill:
            break
        current_evaluation, chosen = min(
            downhill,
            key=lambda option: (
                abs(option[0]["delta"]),
                _BOUNDARY_RECON_RANK.get(option[0]["status"], 99),
                option[1]["row_id"],
            ),
        )
        selected.append(chosen)
        if chosen in remaining_interior:
            remaining_interior.remove(chosen)
        elif above_count < len(above) and chosen is above[above_count]:
            above_count += 1
        elif below_count < len(below) and chosen is below[below_count]:
            below_count += 1
        record_proposal(selected)
        if current_evaluation["status"] == "match":
            break
    if not proposals:
        return None
    return min(
        proposals,
        key=lambda proposal: (
            _BOUNDARY_RECON_RANK[proposal["after"]["status"]],
            abs(proposal["after"]["delta"]),
            len(proposal["added_line_ids"]),
            proposal["added_line_ids"],
        ),
    )


__all__ = [
    "COLUMN_HEADER_AFFIX_TOKENS",
    "COLUMN_HEADER_ANCHOR_TOKENS",
    "DISCOUNT_WORDS",
    "DISCOUNT_WORD_RE",
    "FLAGGED_AMOUNT_RE",
    "LEAD_QTY_RE",
    "NOISY_MONEY_RE",
    "NON_ITEM_SECTIONS",
    "PER_UNIT_RATE_RE",
    "PRICE_RE",
    "PROVEN_CENT_TOLERANCE",
    "QTY_AT_RE",
    "RESTRICTION_NOTE_RE",
    "QTY_CENT_TOLERANCE",
    "QTY_MULT_RE",
    "QTY_SEPARATOR_RE",
    "ReconcileResult",
    "SKU_LIKE_RE",
    "TAX_FLAG_RE",
    "UNIT_WORDS",
    "accept_quantity_pair",
    "band_words",
    "estimate_skew",
    "constrain_items_to_baseline",
    "evaluate_items_zone",
    "extract_items",
    "implied_unit_price",
    "is_column_header_row",
    "is_proven",
    "is_settlement_row",
    "is_unit_rate_row",
    "items_boundary_extension_guard",
    "parse_band",
    "propose_items_boundary_extension",
    "quantity_candidates",
    "reconcile",
    "reconcile_detailed",
    "reconcile_extracted_items",
    "strip_tax_flag",
]
