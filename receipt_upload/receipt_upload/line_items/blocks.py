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

__all__ = ["derive_block_labels", "templatize"]


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

    items = []
    for p in price_idx:
        pl = lines[p]
        if not pl["amounts"]:
            continue
        price = pl["amounts"][-1]
        # name: alpha-rich member text, SKU-stripped; fall back to the
        # PRICE line's own non-amount text
        cands = []
        for i in blocks[p]:
            t = lines[i]["text"]
            stripped = re.sub(r"\d{4,}", " ", t)
            toks = re.findall(r"[A-Za-z]{2,}", stripped)
            if len(toks) >= 2:
                cands.append((len(" ".join(toks)), t))
        if cands:
            name = max(cands)[1]
        else:
            name = " ".join(
                w["text"]
                for w in words_by_line[pl["line_id"]]
                if not _line_amounts([w])
            )
        items.append(
            {
                "name": name.strip(),
                "price": price,
                "quantity": None,
                "line_ids": sorted(
                    {pl["line_id"]} | {lines[i]["line_id"] for i in blocks[p]}
                ),
            }
        )
    return items
