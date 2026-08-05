"""Item-block segmentation over the ITEMS zone (Phase D.6a).

Blocks are the section problem one level down: contiguous runs of OCR lines
forming one purchased item (SKU / description / qty / discount / annotation
members, exactly one price-carrier line). This module decodes at the OCR
LINE level, not the visual-band level, deliberately: the Home Depot failure
included a description line band-merging into a neighbouring SKU line, and
line-level decode sidesteps banding entirely.

Stage 1 (this commit): derive supervised block labels from the golden
fixtures. The hand-labeled ``line_ids`` on every golden item are free
segmentation labels; the item's ``price`` identifies which member line
carries it. These labels train the decoder's priors and evaluate it.

Roles:
  PRICE   -- the line carrying the item's extended total
  MEMBER  -- any other line inside an item's line_ids (SKU, description,
             qty, refund-note, ...)
  OUTSIDE -- zone lines belonging to no item (headers, stray annotations)
Discount items keep the same roles with ``is_discount`` carried on the
block, mirroring the golden truth.

Prior art: receipt_agent's item_line_grammar (feat/item-grammar-refine)
learns per-merchant item-row templates for synthesis from the same kind of
labels -- shape templatization and label-derived column medians. It never
segments; the concepts transfer, the code does not (it reads synth-batch
exports).
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from receipt_dynamo.amounts import (
    looks_like_receipt_amount,
    parse_receipt_amount,
)

__all__ = [
    "attach_printed_quantities",
    "derive_block_labels",
    "filter_summary_figure_items",
    "templatize",
]


def templatize(text: str) -> str:
    """Digit-collapsed shape of a line ("MAX REFUND VALUE $ #.#")."""
    t = re.sub(r"\s+", " ", (text or "").strip().upper())
    return re.sub(r"\d+", "#", t)


def _money(v: Any) -> float | None:
    if v is None:
        return None
    t = str(v).replace("$", "").replace(",", "").strip()
    neg = t.startswith("-") or (t.startswith("(") and t.endswith(")"))
    t = t.strip("()").lstrip("-")
    try:
        val = float(t)
    except ValueError:
        return None
    return -val if neg else val


def _line_amounts(words: list[dict]) -> list[float]:
    out = []
    for w in words:
        t = w["text"]
        if t.endswith(")") and "(" not in t:
            continue  # OCR carcass ("80.00)" from "@0.00)"), never a price
        # OCR fuses trailing taxability flags onto amounts ("0.38N",
        # "0.20N" on Home Depot fee lines); strip a single trailing letter
        # when what remains is an amount shape.
        if re.fullmatch(r"\$?\d[\d.,]*\d[A-Z]", t):
            t = t[:-1]
        if looks_like_receipt_amount(t) and re.search(r"\d[.,]\d{2}(?!\d)", t):
            v = parse_receipt_amount(t)
            if v is not None:
                out.append(v)
    return out


def derive_block_labels(golden_receipt: dict, ocr_receipt: dict) -> list[dict]:
    """Per-line role labels for one golden receipt.

    Returns one record per zone line: {line_id, text, template, role,
    block_index (or None), is_discount}. ``role`` is PRICE / MEMBER /
    OUTSIDE. A PRICE line is the member line whose parsed amounts include
    the item's price; when several member lines carry it (SKU-row echo
    layouts), the LAST in reading order wins, matching how receipts print
    the extended total after the metadata.
    """
    words_by_line: dict[int, list[dict]] = defaultdict(list)
    for w in ocr_receipt["words"]:
        words_by_line[w["line_id"]].append(w)
    for ws in words_by_line.values():
        ws.sort(key=lambda w: w["x"])
    # reading order: larger y_mid is higher on the receipt
    line_order = sorted(
        words_by_line,
        key=lambda lid: -(
            sum(w["y_mid"] for w in words_by_line[lid])
            / len(words_by_line[lid])
        ),
    )
    rank = {lid: i for i, lid in enumerate(line_order)}

    role: dict[int, str] = {}
    block_of: dict[int, int] = {}
    discount: dict[int, bool] = {}
    for b_idx, item in enumerate(golden_receipt["true_items"]):
        lids = [lid for lid in (item.get("line_ids") or []) if lid in rank]
        if not lids:
            continue
        target = _money(item.get("price"))
        carriers = [
            lid
            for lid in lids
            if target is not None
            and any(
                abs(a - target) < 0.005
                for a in _line_amounts(words_by_line[lid])
            )
        ]
        price_line = (
            max(carriers, key=lambda lid: rank[lid]) if carriers else None
        )
        for lid in lids:
            role[lid] = "PRICE" if lid == price_line else "MEMBER"
            block_of[lid] = b_idx
            discount[lid] = bool(item.get("is_discount"))

    zone = set(ocr_receipt["items_line_ids"])
    out = []
    for lid in line_order:
        if lid not in zone:
            continue
        text = " ".join(w["text"] for w in words_by_line[lid])
        out.append(
            {
                "line_id": lid,
                "text": text,
                "template": templatize(text),
                "role": role.get(lid, "OUTSIDE"),
                "block_index": block_of.get(lid),
                "is_discount": discount.get(lid, False),
            }
        )
    return out


def build_role_priors(labeled: list[list[dict]]) -> dict:
    """Template -> role frequency table from derived labels.

    ``labeled`` is a list of per-receipt outputs of derive_block_labels.
    Templates are digit-collapsed shapes, so "MAX REFUND VALUE $ #.#"
    learned on one receipt applies to every other receipt printing it.
    """
    from collections import Counter as _C

    table: dict[str, Any] = {}
    counts: dict[str, _C] = {}
    for rows in labeled:
        for r in rows:
            counts.setdefault(r["template"], _C())[r["role"]] += 1
    for tpl, c in counts.items():
        total = sum(c.values())
        role, n = c.most_common(1)[0]
        table[tpl] = {"role": role, "support": total, "purity": n / total}
    return table


def decode_blocks(ocr_receipt: dict, priors: dict) -> list[dict]:
    """Segment the ITEMS zone into item blocks and emit items.

    Role per line: the learned template table when it has seen the shape
    (purity >= 0.75, support >= 2), else structural fallback -- a line
    whose amounts reach the zone's right-most amount column is PRICE, an
    alpha-bearing line is MEMBER, else OUTSIDE. Blocks: each PRICE line
    seeds a block; MEMBER lines attach to the nearer adjacent PRICE line
    in reading order, with the receipt-wide orientation rule from
    extract_items reused for ties. The item's name prefers alpha-rich
    member text over SKU-heavy text; price is the PRICE line's rightmost
    amount.
    """
    words_by_line: dict[int, list[dict]] = defaultdict(list)
    for w in ocr_receipt["words"]:
        words_by_line[w["line_id"]].append(w)
    for ws in words_by_line.values():
        ws.sort(key=lambda w: w["x"])
    zone = set(ocr_receipt["items_line_ids"])
    line_order = sorted(
        (lid for lid in words_by_line if lid in zone),
        key=lambda lid: -(
            sum(w["y_mid"] for w in words_by_line[lid])
            / len(words_by_line[lid])
        ),
    )
    if not line_order:
        return []

    # zone price column = right-most x of any amount word, minus tolerance
    amt_x = []
    for lid in line_order:
        for w in words_by_line[lid]:
            t = w["text"]
            if re.fullmatch(r"\$?\d[\d.,]*\d[A-Z]", t):
                t = t[:-1]
            if looks_like_receipt_amount(t) and re.search(
                r"\d[.,]\d{2}(?!\d)", t
            ):
                amt_x.append(w["x"])
    col_x = max(amt_x) if amt_x else None

    lines = []
    for lid in line_order:
        ws = words_by_line[lid]
        text = " ".join(w["text"] for w in ws)
        tpl = templatize(text)
        amounts = _line_amounts(ws)
        prior = priors.get(tpl)
        if prior and prior["purity"] >= 0.75 and prior["support"] >= 2:
            role = prior["role"]
        elif (
            amounts
            and col_x is not None
            and any(
                abs(w["x"] - col_x) < 0.15 for w in ws if _line_amounts([w])
            )
        ):
            role = "PRICE"
        elif re.search(r"[A-Za-z]{3,}", text):
            role = "MEMBER"
        else:
            role = "MEMBER" if amounts else "OUTSIDE"
        lines.append(
            {
                "line_id": lid,
                "text": text,
                "template": tpl,
                "role": role,
                "amounts": amounts,
            }
        )

    price_idx = [i for i, l in enumerate(lines) if l["role"] == "PRICE"]
    if not price_idx:
        return []
    # attach members to the nearer adjacent PRICE line (index distance in
    # reading order; ties go up, matching how metadata precedes totals)
    blocks: dict[int, list[int]] = {p: [] for p in price_idx}
    for i, l in enumerate(lines):
        if l["role"] != "MEMBER":
            continue
        prev_p = max((p for p in price_idx if p < i), default=None)
        next_p = min((p for p in price_idx if p > i), default=None)
        if prev_p is None and next_p is None:
            continue
        if prev_p is None:
            blocks[next_p].append(i)
        elif next_p is None:
            blocks[prev_p].append(i)
        else:
            blocks[prev_p if (i - prev_p) <= (next_p - i) else next_p].append(
                i
            )

    # Hybrid emit (composition rule): the decoder decided GROUPING only.
    # Per-line parsing/naming is parse_band, the mature component -- v1
    # rebuilt naming and regressed every single-row format while fixing
    # the multi-row ones. Member text replaces the parsed name only when
    # the PRICE line's own name is missing or SKU-dominated.
    from receipt_upload.line_items.geometry import parse_band

    def _sku_dominated(name: str) -> bool:
        stripped = re.sub(r"\d{4,}", " ", name or "")
        toks = re.findall(r"[A-Za-z]{2,}", stripped)
        return len(toks) < 2

    items = []
    # Emission order matches the banded implementation (ascending y,
    # bottom of the receipt first): item order is a pinned guarantee --
    # backfill derives item_index from list position, and the echo-dedup
    # fixture asserts positional semantics.
    for p in reversed(price_idx):
        pl = lines[p]
        parsed = parse_band(list(words_by_line[pl["line_id"]]))
        if parsed is None or parsed.get("price") is None:
            continue
        member_ids = [lines[i]["line_id"] for i in blocks[p]]
        # qty metadata on a member line ("2@7.97") explains this price
        if parsed.get("quantity") is None:
            for i in blocks[p]:
                mp = parse_band(list(words_by_line[lines[i]["line_id"]]))
                if (
                    mp
                    and mp.get("quantity") is not None
                    and mp.get("unit_price") is not None
                    and abs(
                        mp["quantity"] * mp["unit_price"] - parsed["price"]
                    )
                    <= 0.02
                ):
                    parsed["quantity"] = mp["quantity"]
                    parsed["unit_price"] = mp["unit_price"]
                    break
        if _sku_dominated(parsed.get("name") or ""):
            cands = []
            for i in blocks[p]:
                t = lines[i]["text"]
                if _sku_dominated(t):
                    continue
                cands.append((len(t), t))
            if cands:
                parsed["name"] = max(cands)[1].strip()
        parsed["line_ids"] = sorted({pl["line_id"], *member_ids})
        items.append(parsed)
    return items


def merge_price_fragments(words: list[dict]) -> list[dict]:
    """Concatenate OCR-shattered price fragments within a band.

    Vision splits some prices into adjacent tokens -- "1." + "99" for 1.99
    (Costco), "5," + "90" (comma for period). Textract read the same
    pixels whole, which is a large part of its 90% vs our 85% recall.
    Merge an x-adjacent pair when the left token is digits ending in a
    separator and the right is exactly two digits, yielding a valid
    amount. Digit MISREADS (4.89 read as 1.89) are left alone: no
    geometry can recover a digit OCR never produced -- that class is the
    re-OCR trigger's job.
    """
    if len(words) < 2:
        return words
    ws = sorted(words, key=lambda w: w["x"])
    out: list[dict] = []
    i = 0
    while i < len(ws):
        w = ws[i]
        if i + 1 < len(ws):
            nxt = ws[i + 1]
            gap = nxt["x"] - w["x"]
            if (
                re.fullmatch(r"\$?\d{1,4}[.,]", w["text"])
                and re.fullmatch(r"\d{2}", nxt["text"])
                and 0 <= gap < 0.08
            ):
                merged = dict(w)
                merged["text"] = w["text"].replace(",", ".") + nxt["text"]
                out.append(merged)
                i += 2
                continue
        out.append(w)
        i += 1
    return out


def should_reocr_items_zone(
    items: list[dict], printed_subtotal: float | None
) -> bool:
    """Reconciliation-triggered re-OCR decision (pure; wiring is the
    pipeline's REGIONAL_REOCR job).

    Fires when the decoded items exist but do not sum to the receipt's own
    printed subtotal beyond the reconcile tolerance -- the signature of
    digit-level OCR misreads (4.89 -> 1.89) that no downstream logic can
    recover. Deliberately does NOT fire on empty zones (nothing to
    re-read) or on match/near (re-OCR spends time to fix nothing; most
    receipts are already right). Known limit, verified on Twin Peaks: some
    glyphs are unreadable at any resolution; the caller must cap attempts.
    """
    if not items or printed_subtotal is None or printed_subtotal <= 0:
        return False
    total = sum(
        i["price"] for i in items if isinstance(i.get("price"), (int, float))
    )
    diff = abs(round(total, 2) - printed_subtotal)
    return diff > max(1.0, printed_subtotal * 0.10)


def _zone_bands(ocr_receipt: dict) -> list[dict]:
    """Deskewed visual bands over the zone, as decode units.

    Bands restore same-visual-row joining (name left, price right as two
    OCR lines) that line-level decode measurably lost -- In-N-Out /
    The Stand / Smith's / Target names went to 0-25% on lines and their
    layouts never let name meet price in one parse_band call.
    """
    from receipt_upload.line_items.geometry import band_words

    zone = set(ocr_receipt["items_line_ids"])
    words = [w for w in ocr_receipt["words"] if w["line_id"] in zone]
    out = []
    for band in band_words(words):
        # merge_price_fragments is deliberately NOT applied here: run
        # unconditionally it manufactured a plausible-but-wrong 5.90 from
        # "5,"+"90" (truth 5.99 -- shattered AND digit-misread) and failed
        # Costco's precision floor. Fragment reconstruction is a REPAIR
        # action for the reconciliation-triggered path, alongside re-OCR.
        text = " ".join(w["text"] for w in band)
        out.append(
            {
                "words": band,
                "line_ids": sorted({w["line_id"] for w in band}),
                "text": text,
                "template": templatize(text),
                "amounts": _line_amounts(band),
                "y": sum(w["y_mid"] for w in band) / len(band),
            }
        )
    out.sort(key=lambda b: -b["y"])  # reading order
    return out


def derive_band_labels(golden_receipt: dict, ocr_receipt: dict) -> list[dict]:
    """Band-granularity roles lifted from the line-level derivation."""
    line_roles = {
        r["line_id"]: r
        for r in derive_block_labels(golden_receipt, ocr_receipt)
    }
    out = []
    for b in _zone_bands(ocr_receipt):
        roles = [line_roles.get(lid) for lid in b["line_ids"]]
        roles = [r for r in roles if r]
        if any(r["role"] == "PRICE" for r in roles):
            role, blk = "PRICE", next(
                r["block_index"] for r in roles if r["role"] == "PRICE"
            )
        elif any(r["role"] == "MEMBER" for r in roles):
            role = "MEMBER"
            blk = next(
                r["block_index"] for r in roles if r["role"] == "MEMBER"
            )
        else:
            role, blk = "OUTSIDE", None
        out.append(
            {
                **{k: b[k] for k in ("text", "template")},
                "role": role,
                "block_index": blk,
            }
        )
    return out


def filter_summary_figure_items(
    items: list[dict], summary: dict | None
) -> list[dict]:
    """Drop non-product bands whose price equals a printed summary figure.

    Mode A/F of the failure-mode analysis: receipts that print their
    subtotal / tax / grand total INSIDE the ITEMS zone (Mob Museum's bare
    "57.90" == grand total; In-N-Out's "DRIVE-Thru Eat Out 14.50" order
    line == subtotal) emit that figure as an item and double the sum.

    Guards, all load-bearing:
      * Runs only when the receipt does NOT already reconcile -- a
        matching receipt is never touched, so the filter cannot lose a
        currently-matching receipt.
      * n_items >= 2: at least 2 other non-discount items must survive
        every drop. Single-item receipts legitimately have item price ==
        total (Barnes & Noble / CVS / Target / LA County in the report).
      * An UNNAMED band (no real name) matching subtotal / tax /
        grand_total is dropped only when the drop strictly improves the
        reconciliation delta.
      * A NAMED band may match only subtotal / grand_total (a product
        coincidentally priced at the tax amount must survive) and is
        dropped only when the remaining items then reconcile to a match
        -- the self-verifying "apply, then re-reconcile" rule.
    """
    from receipt_upload.line_items.geometry import _name_is_real

    if not summary or not items:
        return items

    def _f(key: str) -> float | None:
        v = summary.get(key)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    subtotal, tax, grand = _f("subtotal"), _f("tax"), _f("grand_total")
    baseline = subtotal
    if baseline is None and grand is not None:
        baseline = grand - (tax or 0.0)
    if baseline is None or baseline <= 0:
        return items

    non_disc = [i for i in items if not i.get("is_discount")]
    cur = round(sum(i["price"] for i in non_disc), 2)
    tol = max(0.02, baseline * 0.01)
    if abs(cur - baseline) <= tol:
        return items  # already reconciles; never touch a match

    drop: set[int] = set()
    changed = True
    while changed:
        changed = False
        for idx, it in enumerate(items):
            if idx in drop or it.get("is_discount"):
                continue
            price = it.get("price")
            if not isinstance(price, (int, float)) or price <= 0:
                continue
            unnamed = not _name_is_real(it.get("name") or "")
            figures = [subtotal, grand] + ([tax] if unnamed else [])
            # 1% figure tolerance (same shape as reconcile's match band):
            # the printed figure itself carries OCR jitter -- Trader Joe's
            # 0e75127f prints "$16.41" for a summary total read as 16.47.
            if not any(
                f is not None and abs(price - f) <= max(0.02, f * 0.01)
                for f in figures
            ):
                continue
            if len(non_disc) - len(drop) - 1 < 2:
                continue  # at least 2 other items must survive
            new_diff = abs(round(cur - price, 2) - baseline)
            if unnamed:
                ok = new_diff < abs(cur - baseline) - 0.005
            else:
                ok = new_diff <= tol
            if ok:
                drop.add(idx)
                cur = round(cur - price, 2)
                changed = True
    if not drop:
        return items
    return [it for i, it in enumerate(items) if i not in drop]


def attach_printed_quantities(
    items: list[dict],
    donors_for: dict[int, list[list[dict]]] | None = None,
) -> list[dict]:
    """Populate quantity / unit_price where the printed band proves them.

    STRICTLY ADDITIVE. Runs after the decode is finished and writes only
    ``quantity``, ``unit_price`` and ``qty_word_ids``; names, prices,
    line_ids, discount flags and the item count are never touched, so the
    reconciliation this decode feeds cannot move. That is the whole reason
    it is a separate pass instead of another branch inside ``parse_band``:
    quantity words participate in ``parse_band``'s price and name
    selection, and widening the shapes it recognizes there would silently
    re-decode names and prices across the corpus.

    Three sources, all gated on the same arithmetic (see
    ``accept_quantity_pair`` -- quantity x unit_price must reproduce the
    item's own printed price to the cent):

    1. An item that already carries both is left alone.
    2. An item that carries a quantity but no unit price (every
       leading-quantity row: "2 BURRITO ... 9.98", "2X ...") gets the unit
       price its own price implies, when the division is cent-exact. This
       is the LEAD_QTY path finishing its arithmetic rather than a second
       mechanism.
    3. An item that carries neither is offered candidate word pairs from
       its own band and from ``donors_for`` -- the member and unclaimed
       neighbour bands around it. The first pair that multiplies out wins.
       Trader Joe's "6 @ $0.49" reaching OCR as "6" / "8" / "S0.49" is the
       motivating case: no clean glyph survives, but 6 x 0.49 == 2.94 is
       unambiguous, and the receipt's own "Items in Transaction: 10"
       corroborates it (4 single items + 6 lemons).

    ``donors_for`` maps item index -> candidate donor bands (each a list
    of word dicts). Omitted, only sources 1-3-own-band apply.
    """
    from receipt_upload.line_items.geometry import (
        accept_quantity_pair,
        implied_unit_price,
        quantity_candidates,
    )

    for idx, it in enumerate(items):
        price = it.get("price")
        if it.get("quantity") is not None and it.get("unit_price") is not None:
            continue
        if it.get("quantity") is not None:
            unit = implied_unit_price(it["quantity"], price)
            if unit is not None:
                it["unit_price"] = unit
            continue
        bands = [it.get("band") or []]
        bands.extend((donors_for or {}).get(idx) or [])
        for donor in bands:
            hit = next(
                (
                    c
                    for c in quantity_candidates(list(donor))
                    if accept_quantity_pair(
                        c["quantity"], c["unit_price"], price
                    )
                ),
                None,
            )
            if hit is None:
                continue
            it["quantity"] = hit["quantity"]
            it["unit_price"] = hit["unit_price"]
            # Provenance follows the arithmetic: point the quantity span
            # at the two words that proved it, so word-level QUANTITY /
            # UNIT_PRICE derivation names the right words even when the
            # pair came from a neighbouring band.
            it["qty_word_ids"] = list(hit["qty_word_ids"])
            break
    return items


def decode_band_blocks(
    ocr_receipt: dict, priors: dict, summary: dict | None = None
) -> list[dict]:
    """Block decode over deskewed visual bands (the corrected unit).

    ``summary`` (optional {subtotal, tax, grand_total}) enables the
    non-product band filter: printed summary figures leaking into the
    ITEMS zone are dropped via filter_summary_figure_items. Callers
    without a summary (golden gate, ingest paths before the summary
    exists) pass None and get the unfiltered decode.
    """
    from receipt_upload.line_items.geometry import (
        NON_PRODUCT_NOTE_RE,
        SALE_PRICE_RE,
        WAS_PRICE_RE,
        is_settlement_row,
        parse_band,
    )

    bands = _zone_bands(ocr_receipt)
    if not bands:
        return []
    for b in bands:
        # Settlement (BALANCE DUE / CREDIT / CHANGE / Balance to pay) and
        # price-comparison ("WAS: $3.59 each") bands are never items, even
        # when a broken ITEMS section includes them and regardless of any
        # template prior. Strip amounts before the settlement test so
        # "CHANGE 0.00" reduces to its vocabulary.
        bare = re.sub(r"\$?\d[\d.,]*", " ", b["text"]).strip()
        if (
            is_settlement_row(bare)
            or WAS_PRICE_RE.search(b["text"])
            or SALE_PRICE_RE.search(b["text"])
            or NON_PRODUCT_NOTE_RE.search(b["text"])
        ):
            b["role"] = "OUTSIDE"
            continue
        prior = priors.get(b["template"])
        if prior and prior["purity"] >= 0.75 and prior["support"] >= 2:
            b["role"] = prior["role"]
        elif b["amounts"]:
            b["role"] = "PRICE"
        elif re.search(r"[A-Za-z]{3,}", b["text"]):
            b["role"] = "MEMBER"
        else:
            b["role"] = "OUTSIDE"

    price_idx = [i for i, b in enumerate(bands) if b["role"] == "PRICE"]

    # META absorption, ported from extract_items -- the corpus sweep vetoed
    # the decoder without it (match 415->389, +70 items): qty bands
    # ("2 @ 8.99") and price echoes emitted as their own items on formats
    # outside the golden set. A PRICE band with no real name is absorbed by
    # an ADJACENT price band when its qty*unit explains that neighbor's
    # price, or when it merely echoes the neighbor's price with SKU/qty
    # signature. Absorbed bands transplant quantity and stop being items.
    from receipt_upload.line_items.geometry import SKU_LIKE_RE, _name_is_real

    # zone price column (same convention as decode_blocks: right-most
    # amount-word x; "in column" = within 0.15)
    _zone_amt_x = [
        w["x"] for b in bands for w in b["words"] if _line_amounts([w])
    ]
    zone_col_x = max(_zone_amt_x) if _zone_amt_x else None

    parsed_cache: dict[int, dict | None] = {
        p: parse_band(list(bands[p]["words"])) for p in price_idx
    }
    absorbed: set[int] = set()
    for pos, p in enumerate(price_idx):
        mp = parsed_cache[p]
        if mp is None or _name_is_real(mp.get("name") or ""):
            continue
        qty, unit = mp.get("quantity"), mp.get("unit_price")
        for npos in (pos - 1, pos + 1):
            if not 0 <= npos < len(price_idx):
                continue
            q = price_idx[npos]
            if q in absorbed:
                continue
            nb = parsed_cache[q]
            if nb is None or nb.get("price") is None:
                continue
            if (
                qty is not None
                and unit is not None
                and abs(qty * unit - nb["price"]) <= 0.02
            ):
                if nb.get("quantity") is None:
                    nb["quantity"], nb["unit_price"] = qty, unit
                absorbed.add(p)
                break
            # Echo absorption ONLY into a real-named neighbor -- the same
            # constraint extract_items enforces via kind==ITEM. Without it,
            # Wild Fork's genuinely distinct same-priced SKU rows (two
            # items at 8.98) absorb each other: measured recall 100->45%.
            if (
                mp.get("price") is not None
                and _name_is_real(nb.get("name") or "")
                and abs(mp["price"]) == abs(nb["price"])
                and (
                    SKU_LIKE_RE.search(mp.get("raw_text") or "")
                    or qty is not None
                )
            ):
                if qty is not None and nb.get("quantity") is None:
                    nb["quantity"], nb["unit_price"] = qty, unit
                absorbed.add(p)
                break
            # Unit-price echo with the qty prefix lost to OCR ("2 @ 3.49"
            # reads as bare "3.49" under a 6.98 item): a bare-amount band
            # (no name, no qty, no SKU) whose amount divides a real-named
            # neighbor's price by an integer 2..12 is that neighbor's
            # unit price. The multiple recovers the quantity.
            #
            # Column gate: only LEFT-column amounts are echoes. A bare
            # amount sitting IN the price column is a real (unnamed) item
            # -- the corpus vetoed the arithmetic test alone: $7.00 next
            # to a $14.00 pizza and $11.99 popcorn next to 3x$11.99
            # tickets are coincidental multiples, both price-column.
            in_price_col = zone_col_x is not None and any(
                abs(w["x"] - zone_col_x) < 0.15
                for w in bands[p]["words"]
                if _line_amounts([w])
            )
            if (
                mp.get("price") is not None
                and qty is None
                and not in_price_col
                and not (mp.get("name") or "").strip()
                and not SKU_LIKE_RE.search(mp.get("raw_text") or "")
                and _name_is_real(nb.get("name") or "")
                and mp["price"] > 0
                and nb["price"] > 0
            ):
                ratio = nb["price"] / mp["price"]
                k = round(ratio)
                if 2 <= k <= 12 and abs(nb["price"] - k * mp["price"]) <= 0.02:
                    if nb.get("quantity") is None:
                        nb["quantity"] = float(k)
                        nb["unit_price"] = mp["price"]
                    absorbed.add(p)
                    break
    price_idx = [p for p in price_idx if p not in absorbed]
    blocks: dict[int, list[int]] = {p: [] for p in price_idx}
    for i, b in enumerate(bands):
        if b["role"] != "MEMBER":
            continue
        prev_p = max((p for p in price_idx if p < i), default=None)
        next_p = min((p for p in price_idx if p > i), default=None)
        if prev_p is None and next_p is None:
            continue
        if prev_p is None:
            blocks[next_p].append(i)
        elif next_p is None:
            blocks[prev_p].append(i)
        else:
            blocks[prev_p if (i - prev_p) <= (next_p - i) else next_p].append(
                i
            )

    def _sku_dominated(name: str) -> bool:
        stripped = re.sub(r"\d{4,}", " ", name or "")
        return len(re.findall(r"[A-Za-z]{2,}", stripped)) < 2

    # Bands no item speaks for: not a price band, not a member of any
    # block. Trader Joe's quantity line reaches OCR as "6 8 S0.49", which
    # carries no parseable amount and no three-letter word, so it lands in
    # OUTSIDE and every existing quantity path skips it. Kept per band
    # index so the quantity pass can offer an item only its own immediate
    # neighbours.
    claimed = set(price_idx) | {i for p in price_idx for i in blocks[p]}
    unclaimed = [i for i in range(len(bands)) if i not in claimed]

    items = []
    donors_for: dict[int, list[list[dict]]] = {}
    # Emission order matches the banded implementation (ascending y --
    # bottom of the receipt first): item order is a pinned guarantee.
    for p in reversed(price_idx):
        parsed = parsed_cache.get(p) or parse_band(list(bands[p]["words"]))
        if parsed is None or parsed.get("price") is None:
            continue
        donors_for[len(items)] = [
            bands[i]["words"]
            for i in sorted(
                set(blocks[p]) | {u for u in unclaimed if abs(u - p) == 1}
            )
        ]
        if parsed.get("quantity") is None:
            for i in blocks[p]:
                mp = parse_band(list(bands[i]["words"]))
                if (
                    mp
                    and mp.get("quantity") is not None
                    and mp.get("unit_price") is not None
                    and abs(
                        mp["quantity"] * mp["unit_price"] - parsed["price"]
                    )
                    <= 0.02
                ):
                    parsed["quantity"] = mp["quantity"]
                    parsed["unit_price"] = mp["unit_price"]
                    break
        if _sku_dominated(parsed.get("name") or ""):
            # Donor criterion is _name_is_real (>=3 alpha chars), NOT the
            # two-token SKU test: single-word product names ("BREAD",
            # "YOGURT") are real names, and requiring two tokens silently
            # discarded them -- the boundary-steal guarantee failure the
            # first integration attempt was reverted on.
            cands = [
                (len(bands[i]["text"]), bands[i]["text"], i)
                for i in blocks[p]
                if _name_is_real(bands[i]["text"])
            ]
            if cands:
                donor = max(cands)
                parsed["name"] = donor[1].strip()
                parsed["stacked"] = True
                # The name now comes from the donor band, so its word
                # provenance must follow it. Left pointing at the price
                # band, name_word_ids would name the SKU words instead of
                # the product words for every stacked layout.
                parsed["name_word_ids"] = [
                    {"line_id": w["line_id"], "word_id": w["word_id"]}
                    for w in bands[donor[2]]["words"]
                ]
        if not _name_is_real(parsed.get("name") or ""):
            # No name anywhere: keep the price, flag the quality --
            # identical semantics to the banded path.
            parsed["name_quality"] = "low"
        parsed["line_ids"] = sorted(
            set(bands[p]["line_ids"]).union(
                *(bands[i]["line_ids"] for i in blocks[p])
            )
            if blocks[p]
            else set(bands[p]["line_ids"])
        )
        items.append(parsed)
    # Quantity attachment runs before the summary-figure filter purely so
    # donor indices line up with `items`; the filter reads only price,
    # name and is_discount, so which items it drops cannot depend on it.
    attach_printed_quantities(items, donors_for)
    return filter_summary_figure_items(items, summary)


def load_default_priors() -> dict:
    """The committed template->role prior asset (v2, self-labeled)."""
    import json
    from pathlib import Path

    # v2: 928 templates self-labeled from 401 corpus receipts whose decoded
    # items reconcile to their own printed subtotal (DeepCPCFG-style labels
    # from records). Harvested from NON-golden receipts only, so the CI
    # floor gate scores golden receipts with priors that never saw them --
    # the train-on-test caveat recorded at integration is resolved.
    # Limitation, measured: self-labeling reinforces the decoder's own
    # systematic beliefs (the WFM tare-weight item survives it); hand
    # labels in the golden set remain the corrective signal.
    path = Path(__file__).parent / "assets" / "block_role_priors_v2.json"
    return json.load(open(path))["templates"]
