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
    for p in price_idx:
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


def decode_band_blocks(ocr_receipt: dict, priors: dict) -> list[dict]:
    """Block decode over deskewed visual bands (the corrected unit)."""
    from receipt_upload.line_items.geometry import parse_band

    bands = _zone_bands(ocr_receipt)
    if not bands:
        return []
    for b in bands:
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
    from receipt_upload.line_items.geometry import (
        SKU_LIKE_RE,
        _name_is_real,
    )

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

    items = []
    # Emit in the banded path's order (ascending y = band_words order) so
    # item_index semantics match the implementation being replaced.
    for p in sorted(price_idx, key=lambda i: bands[i]["y"]):
        parsed = parsed_cache.get(p) or parse_band(list(bands[p]["words"]))
        if parsed is None or parsed.get("price") is None:
            continue
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
            # Member-name candidates: any band with real alpha content and
            # no SKU run. The former >=2-alpha-token rule rejected
            # single-word real names ("BREAD", "Water") -- caught by the
            # semantic suite as lost names and dropped zero-price items.
            cands = [
                (len(bands[i]["text"]), bands[i]["text"])
                for i in blocks[p]
                if _name_is_real(bands[i]["text"])
                and not SKU_LIKE_RE.search(bands[i]["text"])
            ]
            if not cands:
                cands = [
                    (len(bands[i]["text"]), bands[i]["text"])
                    for i in blocks[p]
                    if not _sku_dominated(bands[i]["text"])
                ]
            if cands:
                parsed["name"] = max(cands)[1].strip()
                parsed["stacked"] = True
        if not _name_is_real(parsed.get("name") or ""):
            if parsed["price"] == 0:
                # unnamed zero band is noise (banded-path parity); a NAMED
                # zero-price item (comped/free line) is kept above.
                continue
            parsed["name_quality"] = "low"
        parsed["line_ids"] = sorted(
            set(bands[p]["line_ids"]).union(
                *(bands[i]["line_ids"] for i in blocks[p])
            )
            if blocks[p]
            else set(bands[p]["line_ids"])
        )
        items.append(parsed)
    return items


def load_default_priors() -> dict:
    """The committed template->role prior asset (golden-set trained)."""
    import json
    from pathlib import Path

    path = Path(__file__).parent / "assets" / "block_role_priors_v1.json"
    return json.load(open(path))["templates"]
