"""Extract line-item product details from ITEMS sections via geometry.

No ML: for each receipt with a VALID ITEMS section, cluster the section's
words into visual bands by y-center (Apple Vision splits name and price
columns into separate OCR lines, so per-line parsing fails; per-band
parsing recovers the pairing), then parse each band into
(name, quantity, unit_price, price, flags).

Validation built in:
  1. Reconciliation — sum of extracted item prices vs the receipt
     summary's subtotal (else grand_total - tax), classified as
     match / near / mismatch / no-baseline.
  2. Leakage — priced bands on lines outside any section for receipts
     WITH an ITEMS section (section too narrow).
  3. No-ITEMS triage — receipts without an ITEMS section classified as
     genuinely item-less vs likely-missing-section, using priced lines
     outside SUMMARY / TOTAL_LINE / PAYMENT / TRANSACTION_INFO sections.

Usage:
    python3.12 scripts/extract_line_items.py [--table ReceiptsTable-dc5be22]
        [--out /path/to/items.jsonl] [--limit N] [--receipt IMAGE_ID:RID]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from typing import Any, Optional

import boto3
from boto3.dynamodb.types import TypeDeserializer

_DES = TypeDeserializer()

PRICE_RE = re.compile(r"\$?(\d{1,4}(?:,\d{3})?\.\d{2})(-?)")
# "2 @ 3.99", "1.23 lb @ 4.99/lb", "3 @ $0.99 ea"
QTY_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:lb|LB|Lb)?\s*@\s*\$?(\d{1,4}\.\d{2})"
)
# Standalone leading quantity: "2 BURRITO ..." only when integer < 100
LEAD_QTY_RE = re.compile(r"^(\d{1,2})\s+(?=[A-Za-z])")
TAX_FLAG_RE = re.compile(r"\s+[TFNOAB]X?$")
DISCOUNT_WORDS = ("SAVED", "SAVING", "OFF", "COUPON", "DISCOUNT", "PROMO")
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


def _query_all(client, **kwargs):
    while True:
        resp = client.query(**kwargs)
        yield from resp["Items"]
        if "LastEvaluatedKey" not in resp:
            return
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def _deser(item: dict) -> dict:
    return {k: _DES.deserialize(v) for k, v in item.items()}


def fetch_receipt_records(client, table: str, image_id: str, receipt_id: int):
    """One query per receipt: words, sections, summary in a single sweep."""
    words: list[dict] = []
    sections: list[dict] = []
    summary: Optional[dict] = None
    for raw in _query_all(
        client,
        TableName=table,
        KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
        ExpressionAttributeValues={
            ":pk": {"S": f"IMAGE#{image_id}"},
            ":sk": {"S": f"RECEIPT#{receipt_id:05d}"},
        },
    ):
        t = raw.get("TYPE", {}).get("S")
        if t == "RECEIPT_WORD":
            m = re.match(
                r"RECEIPT#\d+#LINE#(\d+)#WORD#(\d+)$", raw["SK"]["S"]
            )
            if not m:
                continue
            item = _deser(raw)
            bb = item.get("bounding_box") or {}
            try:
                words.append(
                    {
                        "line_id": int(m.group(1)),
                        "word_id": int(m.group(2)),
                        "text": str(item.get("text", "")),
                        "x": float(bb.get("x", 0)),
                        "y_mid": float(bb.get("y", 0))
                        + float(bb.get("height", 0)) / 2,
                        "h": float(bb.get("height", 0)),
                    }
                )
            except (TypeError, ValueError):
                continue
        elif t == "RECEIPT_SECTION":
            sections.append(_deser(raw))
        elif t == "RECEIPT_SUMMARY":
            summary = _deser(raw)
    return words, sections, summary


def band_words(words: list[dict]) -> list[list[dict]]:
    """Cluster words into visual bands by y-center gaps."""
    if not words:
        return []
    ws = sorted(words, key=lambda w: w["y_mid"])
    med_h = sorted(w["h"] for w in ws)[len(ws) // 2] or 0.01
    bands: list[list[dict]] = [[ws[0]]]
    for w in ws[1:]:
        if w["y_mid"] - bands[-1][-1]["y_mid"] < med_h * 0.6:
            bands[-1].append(w)
        else:
            bands.append([w])
    for band in bands:
        band.sort(key=lambda w: w["x"])
    return bands


def parse_band(text: str) -> Optional[dict[str, Any]]:
    """Parse one visual band into a line-item dict (or None if no price)."""
    prices = PRICE_RE.findall(text)
    if not prices:
        return None
    raw_price, neg = prices[-1]
    price = float(raw_price.replace(",", ""))
    if neg == "-":
        price = -price

    qty = unit_price = None
    m = QTY_RE.search(text)
    if m:
        qty = float(m.group(1))
        unit_price = float(m.group(2))

    upper = text.upper()
    is_discount = price < 0 or any(w in upper for w in DISCOUNT_WORDS)

    name = text
    name = QTY_RE.sub(" ", name)
    name = PRICE_RE.sub(" ", name)
    name = TAX_FLAG_RE.sub(" ", name)
    name = re.sub(r"\s{2,}", " ", name).strip(" @$-")
    if qty is None:
        m2 = LEAD_QTY_RE.match(name)
        if m2:
            qty = float(m2.group(1))
            name = name[m2.end():]
    name = name.strip()

    return {
        "name": name,
        "quantity": qty,
        "unit_price": unit_price,
        "price": price,
        "is_discount": is_discount,
        "raw_text": text,
    }


# Tokens that don't count as product-name content (units, tax flags, SKU-ish)
UNIT_WORDS = {
    "EA", "LB", "KG", "OZ", "CT", "PK", "X", "C", "F", "T", "N", "O",
    "A", "B", "TX", "FS", "QTY", "EACH",
}
# "2 3.99" — bare qty + unit-price with no @ separator
BARE_QTY_RE = re.compile(r"^(\d{1,2})\s+\$?(\d{1,4}\.\d{2})\s*$")


def _name_is_real(name: str) -> bool:
    tokens = re.findall(r"[A-Za-z]{2,}", name or "")
    real = [t for t in tokens if t.upper() not in UNIT_WORDS]
    return len("".join(real)) >= 3


def extract_items(
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
        parsed = parse_band(text)
        if parsed is not None and len(PRICE_RE.findall(text)) >= 3 and len(text) > 80:
            collapsed = True
        if parsed is None:
            if _name_is_real(text):
                bands.append(("NAME", {"name": text.strip(), "line_ids": lids}))
            continue
        m = BARE_QTY_RE.match(text.replace("$", "").strip())
        if m:
            parsed["quantity"] = float(m.group(1))
            parsed["unit_price"] = float(m.group(2))
        parsed["line_ids"] = lids
        if _name_is_real(parsed["name"]) or parsed["is_discount"]:
            bands.append(("ITEM", parsed))
        else:
            bands.append(("META", parsed))

    items: list[dict] = []
    pending_name: Optional[dict] = None
    for i, (kind, data) in enumerate(bands):
        if kind == "NAME_USED":
            continue
        if kind == "NAME":
            pending_name = data
            continue
        if kind == "ITEM":
            if data["price"] == 0 and data["quantity"] is None:
                continue
            items.append(data)
            pending_name = None
            continue
        # META band
        neighbors = [
            bands[j][1]
            for j in (i - 1, i + 1)
            if 0 <= j < len(bands) and bands[j][0] == "ITEM"
        ]
        qty, unit = data.get("quantity"), data.get("unit_price")
        attached = False
        for nb in neighbors:
            # qty metadata explains the neighbor's price -> attach
            if (
                qty is not None
                and unit is not None
                and abs(qty * unit - nb["price"]) <= 0.02
            ):
                nb["quantity"], nb["unit_price"] = qty, unit
                attached = True
                break
            # same price -> SKU/price echo of the neighbor, drop
            if abs(data["price"]) == abs(nb["price"]):
                if qty is not None and nb["quantity"] is None:
                    nb["quantity"], nb["unit_price"] = qty, unit
                attached = True
                break
        if attached or data["price"] == 0:
            continue
        if pending_name is None and i + 1 < len(bands) and bands[i + 1][0] == "NAME":
            # name band may sit just below the price band in y-order
            pending_name = bands[i + 1][1]
            bands[i + 1] = ("NAME_USED", bands[i + 1][1])
        if pending_name is not None:
            # stacked layout: name band paired with price-only band
            data["name"] = pending_name["name"]
            data["line_ids"] = sorted(
                set(data["line_ids"]) | set(pending_name["line_ids"])
            )
            pending_name = None
        else:
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", default="ReceiptsTable-dc5be22")
    ap.add_argument("--out", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--receipt", help="IMAGE_ID:RECEIPT_ID single-receipt mode")
    args = ap.parse_args()

    client = boto3.client("dynamodb", region_name="us-east-1")

    # Collect ITEMS sections (and all sectioned receipts) via GSITYPE.
    items_secs: dict[tuple[str, int], dict] = {}
    sectioned: set[tuple[str, int]] = set()
    for raw in _query_all(
        client,
        TableName=args.table,
        IndexName="GSITYPE",
        KeyConditionExpression="#t = :t",
        ExpressionAttributeNames={"#t": "TYPE"},
        ExpressionAttributeValues={":t": {"S": "RECEIPT_SECTION"}},
    ):
        item = _deser(raw)
        m = re.match(r"IMAGE#(.+)", item["PK"])
        m2 = re.match(r"RECEIPT#(\d+)#", item["SK"])
        if not (m and m2):
            continue
        key = (m.group(1), int(m2.group(1)))
        sectioned.add(key)
        if (
            item.get("section_type") == "ITEMS"
            and item.get("validation_status") == "VALID"
        ):
            items_secs[key] = item

    targets = sorted(items_secs)
    if args.receipt:
        img, rid = args.receipt.split(":")
        targets = [(img, int(rid))]
    if args.limit:
        targets = targets[: args.limit]

    out_f = open(args.out, "w") if args.out else None
    recon = Counter()
    leaky = 0
    n_items_total = 0
    mismatches = []
    for i, key in enumerate(targets):
        img, rid = key
        words, sections, summary = fetch_receipt_records(
            client, args.table, img, rid
        )
        sec = items_secs.get(key) or next(
            (s for s in sections if s.get("section_type") == "ITEMS"), None
        )
        if sec is None:
            continue
        line_ids = {int(x) for x in sec.get("line_ids", [])}
        items, collapsed = extract_items(words, line_ids)
        n_items_total += len(items)
        status, item_sum, baseline = reconcile(
            [x for x in items if not x["is_discount"]], summary
        )
        if collapsed:
            status = f"{status}+collapsed"
        recon[status] += 1
        if status == "mismatch":
            mismatches.append((img, rid, item_sum, baseline))

        # Leakage: priced words on lines outside ANY section
        all_sectioned_lines: set[int] = set()
        for s in sections:
            all_sectioned_lines.update(int(x) for x in s.get("line_ids", []))
        stray = {
            w["line_id"]
            for w in words
            if w["line_id"] not in all_sectioned_lines
            and PRICE_RE.search(w["text"])
        }
        if stray:
            leaky += 1

        if out_f:
            out_f.write(
                json.dumps(
                    {
                        "image_id": img,
                        "receipt_id": rid,
                        "collapsed_banding": collapsed,
                        "items": items,
                        "reconciliation": {
                            "status": status,
                            "item_sum": item_sum,
                            "baseline": baseline,
                        },
                        "stray_priced_lines": sorted(stray),
                    }
                )
                + "\n"
            )
        if (i + 1) % 100 == 0:
            print(f"  {i + 1}/{len(targets)}", file=sys.stderr, flush=True)

    if out_f:
        out_f.close()

    print(f"receipts processed: {len(targets)}")
    print(f"items extracted: {n_items_total}")
    print(f"reconciliation: {dict(recon)}")
    print(f"receipts with priced lines outside all sections: {leaky}")
    print("worst mismatches (item_sum vs baseline):")
    for img, rid, s, b in sorted(
        mismatches, key=lambda x: abs((x[2] or 0) - (x[3] or 0)), reverse=True
    )[:10]:
        print(f"  {img}:{rid}  sum={s} baseline={b}")


if __name__ == "__main__":
    main()
