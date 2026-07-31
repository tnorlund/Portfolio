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
# Leading "1x" / "2X" multiplier
QTY_MULT_RE = re.compile(r"^(\d{1,2})[xX]$")
# Standalone leading quantity: "2 BURRITO ..." only when integer < 100
LEAD_QTY_RE = re.compile(r"^(\d{1,2})\s+(?=[A-Za-z])")
TAX_FLAG_RE = re.compile(r"\s+[TFNOAB]X?$")
DISCOUNT_WORDS = ("SAVED", "SAVING", "OFF", "COUPON", "DISCOUNT", "PROMO")
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
    return bands


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
        # "(2" + "80.00)") — never a price.
        if t.endswith(")") and "(" not in t:
            continue
        if looks_like_receipt_amount(t) and re.search(r"\d[.,]\d{2}(?!\d)", t):
            v = parse_receipt_amount(t)
            if v is not None and abs(v) < 100000:
                amounts.append((i, v))

    # Quantity forms, joined-text (they straddle word boundaries)
    qty = unit_price = None
    qty_word_idxs: set[int] = set()
    m = QTY_AT_RE.search(joined)
    if m:
        qty = float(m.group(1))
        unit_price = float(m.group(2))
        qty_word_idxs = words_in_span(m.start(), m.end())
    else:
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
    is_discount = (price is not None and price < 0) or any(
        w in upper for w in DISCOUNT_WORDS
    )

    # Name = words not consumed by price/qty, minus flags/currency tokens
    name_idxs: list[int] = []
    for i, t in enumerate(texts):
        if i in consumed:
            continue
        if looks_like_receipt_amount(t):
            continue  # an earlier price on the band is never name content
        if re.fullmatch(r"[TFNOAB]X?", t):
            continue  # taxability flag
        name_idxs.append(i)

    # Leading quantity forms consume their word
    if qty is None and name_idxs:
        first = texts[name_idxs[0]]
        m3 = QTY_MULT_RE.match(first)
        if m3:
            qty = float(m3.group(1))
            name_idxs.pop(0)
        elif re.fullmatch(r"\d{1,2}", first) and any(
            re.search(r"[A-Za-z]{2,}", texts[i]) for i in name_idxs[1:]
        ):
            qty = float(first)
            name_idxs.pop(0)

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
    words: list[dict], line_ids: set[int]
) -> tuple[list[dict], bool]:
    """Extract line items: band-block decoder over the ITEMS zone.

    Delegates to blocks.decode_band_blocks with the committed prior asset
    (block_role_priors_v1.json -- the section_order_priors pattern, so the
    Swift worker loads the same file). First design to clear all three
    instruments: golden leave-one-out floors (recall/name/precision),
    the 686-receipt corpus reconciliation sweep, and the 12-test semantic
    suite. The banded implementation remains as extract_items_banded: it
    is the decoder's internal row machinery and the sweep baseline.
    """
    # Lazy import: blocks imports this module at module scope.
    from receipt_upload.line_items import blocks as _blocks

    items = _blocks.decode_band_blocks(
        {
            "words": [w for w in words if w["line_id"] in line_ids],
            "items_line_ids": sorted(line_ids),
        },
        _blocks.load_default_priors(),
    )
    return items, False


def extract_items_banded(
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


def reconcile(
    items: list[dict], summary: Optional[dict]
) -> tuple[str, Optional[float], Optional[float]]:
    """Compare extracted item sum against summary subtotal/grand_total."""
    if summary is None:
        return "no-baseline", None, None

    def _f(key):
        v = summary.get(key)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    subtotal, grand, tax = _f("subtotal"), _f("grand_total"), _f("tax")
    baseline = subtotal
    if baseline is None and grand is not None:
        baseline = grand - (tax or 0.0)
    if baseline is None or baseline <= 0:
        return "no-baseline", None, None
    item_sum = round(sum(i["price"] for i in items), 2)
    diff = abs(item_sum - baseline)
    if diff <= max(0.02, baseline * 0.01):
        return "match", item_sum, baseline
    if diff <= max(1.0, baseline * 0.10):
        return "near", item_sum, baseline
    return "mismatch", item_sum, baseline


__all__ = [
    "DISCOUNT_WORDS",
    "LEAD_QTY_RE",
    "NON_ITEM_SECTIONS",
    "PRICE_RE",
    "QTY_AT_RE",
    "QTY_MULT_RE",
    "SKU_LIKE_RE",
    "TAX_FLAG_RE",
    "UNIT_WORDS",
    "band_words",
    "estimate_skew",
    "extract_items",
    "parse_band",
    "reconcile",
]
