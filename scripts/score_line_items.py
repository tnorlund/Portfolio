"""Score line-item extractors against the hand-labeled golden set.

Reports PER MERCHANT, not just aggregate -- the corpus sweep showed defects
are format-concentrated (Wild Fork 100% continuation-line, Sprouts mostly
amount-left), so an average hides exactly what we need to see.

Metrics, per receipt then rolled up:
  recall     = matched / true items          (did we find the item at all)
  precision  = matched / predicted items     (did we invent items)
  name_exact = names equal after light normalization
  name_fuzzy = names equal after aggressive normalization (SKU/punct stripped)
  price_exact= prices equal as decimals
  qty_exact  = quantities equal (only where truth has one)

Matching is by PRICE first, then by name similarity, because price is the
more reliable key -- the whole point of this exercise is that names are the
weak signal. Each true item matches at most one predicted item.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict
from decimal import Decimal, InvalidOperation

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.environ.get("GOLDEN_FIXTURE") or os.path.join(
    _REPO, "receipt_upload", "tests", "fixtures", "line_items_golden.json"
)
TABLE = os.environ.get("DYNAMO_TABLE_NAME", "ReceiptsTable-dc5be22")


def money(v) -> Decimal | None:
    if v is None:
        return None
    t = str(v).replace("$", "").replace(",", "").strip()
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()").lstrip("-")
    try:
        d = Decimal(t)
    except InvalidOperation:
        return None
    if neg or str(v).strip().startswith("-"):
        d = -d
    return d


def norm_light(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().upper())


def norm_hard(s: str) -> str:
    """Drop SKUs, punctuation and tax flags so only word content remains."""
    t = norm_light(s)
    t = re.sub(r"<[A-Z]>", " ", t)  # Home Depot tax flags
    t = re.sub(r"\b\d{5,}\b", " ", t)  # SKU / UPC
    t = re.sub(r"[^A-Z0-9 ]", " ", t)
    t = re.sub(r"\b\d+\b", " ", t)  # bare numbers (qty, PLU)
    return re.sub(r"\s+", " ", t).strip()


def name_sim(a: str, b: str) -> float:
    """Token-overlap similarity on hard-normalized names."""
    ta, tb = set(norm_hard(a).split()), set(norm_hard(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(len(ta), len(tb))


def match(truth: list[dict], pred: list[dict]) -> list[tuple]:
    """Greedy 1:1 match. Price equality dominates; name breaks ties."""
    pairs, used = [], set()
    for t in truth:
        tp = money(t.get("price"))
        best, best_score = None, 0.0
        for i, p in enumerate(pred):
            if i in used:
                continue
            pp = money(p.get("price"))
            price_ok = tp is not None and pp is not None and tp == pp
            sim = name_sim(t.get("name", ""), p.get("name", ""))
            score = (2.0 if price_ok else 0.0) + sim
            if score > best_score:
                best, best_score = i, score
        # require price match OR a genuine name overlap; else it's a miss
        if best is not None and best_score >= 0.5:
            used.add(best)
            pairs.append((t, pred[best]))
        else:
            pairs.append((t, None))
    for i, p in enumerate(pred):
        if i not in used:
            pairs.append((None, p))
    return pairs


def score(golden: dict, predictions: dict, label: str) -> None:
    per_merchant = defaultdict(lambda: defaultdict(int))
    misses: list[str] = []

    for key, g in sorted(golden.items()):
        merch = g["merchant"]
        truth = [i for i in g["true_items"] if not i.get("is_discount")]
        pred = predictions.get(key, [])
        m = per_merchant[merch]
        m["receipts"] += 1
        m["true"] += len(truth)
        m["pred"] += len(pred)

        for t, p in match(truth, pred):
            if t is None:
                m["spurious"] += 1
                continue
            if p is None:
                m["missed"] += 1
                if len(misses) < 25:
                    misses.append(
                        f"    MISS  {merch[:20]:<22} {t.get('name','')[:34]:<36} {t.get('price')}"
                    )
                continue
            m["matched"] += 1
            if norm_light(t.get("name", "")) == norm_light(p.get("name", "")):
                m["name_exact"] += 1
            if norm_hard(t.get("name", "")) == norm_hard(p.get("name", "")):
                m["name_fuzzy"] += 1
            elif len(misses) < 25:
                misses.append(
                    f"    NAME  {merch[:20]:<22} truth={t.get('name','')[:26]!r} pred={p.get('name','')[:26]!r}"
                )
            if money(t.get("price")) == money(p.get("price")):
                m["price_exact"] += 1
            # Quantities compare NUMERICALLY. They round-trip DynamoDB as
            # float-derived strings ("2.0"), so a string compare against the
            # golden "2" reports 0% even when every parse is right -- which
            # is exactly what the first scoring run did. An unprinted
            # quantity means one unit, so absent values on either side
            # compare as 1.
            tq = money(t.get("quantity"))
            pq = money(p.get("quantity"))
            m["qty_total"] += 1
            if (tq if tq is not None else Decimal(1)) == (
                pq if pq is not None else Decimal(1)
            ):
                m["qty_exact"] += 1

    print(f"\n{'='*104}\n{label}\n{'='*104}")
    hdr = (
        f"{'merchant':<30}{'rcpt':>5}{'true':>6}{'pred':>6}"
        f"{'recall':>8}{'prec':>7}{'name=':>7}{'name~':>7}{'pricematch':>7}{'qty':>7}"
    )
    print(hdr)
    print("-" * 104)
    tot = defaultdict(int)
    for merch in sorted(per_merchant, key=lambda k: -per_merchant[k]["true"]):
        m = per_merchant[merch]
        for k, v in m.items():
            tot[k] += v
        rec = m["matched"] / m["true"] if m["true"] else 0
        pre = m["matched"] / m["pred"] if m["pred"] else 0
        ne = m["name_exact"] / m["matched"] if m["matched"] else 0
        nf = m["name_fuzzy"] / m["matched"] if m["matched"] else 0
        pe = m["price_exact"] / m["matched"] if m["matched"] else 0
        qe = (
            m["qty_exact"] / m["qty_total"] if m["qty_total"] else float("nan")
        )
        qs = "  n/a" if m["qty_total"] == 0 else f"{qe:6.0%}"
        print(
            f"{merch[:29]:<30}{m['receipts']:>5}{m['true']:>6}{m['pred']:>6}"
            f"{rec:>8.0%}{pre:>7.0%}{ne:>7.0%}{nf:>7.0%}{pe:>7.0%}{qs:>7}"
        )
    print("-" * 104)
    rec = tot["matched"] / tot["true"] if tot["true"] else 0
    pre = tot["matched"] / tot["pred"] if tot["pred"] else 0
    ne = tot["name_exact"] / tot["matched"] if tot["matched"] else 0
    nf = tot["name_fuzzy"] / tot["matched"] if tot["matched"] else 0
    pe = tot["price_exact"] / tot["matched"] if tot["matched"] else 0
    qe = tot["qty_exact"] / tot["qty_total"] if tot["qty_total"] else 0
    print(
        f"{'TOTAL':<30}{tot['receipts']:>5}{tot['true']:>6}{tot['pred']:>6}"
        f"{rec:>8.0%}{pre:>7.0%}{ne:>7.0%}{nf:>7.0%}{pe:>7.0%}{qe:>7.0%}"
    )
    print(
        f"\n  matched={tot['matched']}  missed={tot['missed']}  "
        f"spurious={tot['spurious']}"
    )
    if misses:
        print("\n  sample failures:")
        for line in misses:
            print(line)


def main() -> None:
    golden = {}
    doc = json.load(open(GOLDEN))
    for d in doc["receipts"]:
        golden[(d["image_id"], d["receipt_id"])] = d
    if not golden:
        sys.exit("no labels found")
    print(
        f"golden set: {len(golden)} receipts, "
        f"{sum(len(g['true_items']) for g in golden.values())} labeled items"
    )

    # Extractor A: what is currently stored in DynamoDB (RECEIPT_LINE_ITEM)
    from receipt_dynamo import DynamoClient

    client = DynamoClient(TABLE)
    stored = defaultdict(list)
    for key in golden:
        if golden[key].get("local_only"):
            continue  # fixture-only receipts have no stored entities
        for it in client.get_receipt_line_items_from_receipt(*key):
            stored[key].append(
                {
                    "name": it.name,
                    "price": it.price,
                    "quantity": it.quantity,
                }
            )
    score(
        golden,
        stored,
        "EXTRACTOR A — stored RECEIPT_LINE_ITEM (line-items-geom-v1)",
    )


if __name__ == "__main__":
    main()
