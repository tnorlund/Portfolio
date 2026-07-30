#!/usr/bin/env python3
"""Benchmark AWS Textract AnalyzeExpense against the golden line-item set.

Runs each golden receipt's warped crop through AnalyzeExpense, maps its
LineItemGroups onto our item schema, and scores BOTH Textract and our block
decoder with the same matcher on the same hand-labeled truth -- vendor
comparison on our yardstick, not theirs. Responses are cached to avoid
re-billing (~$0.10/page).

First run (2026-07-30): OURS 85%/57%/86% vs TEXTRACT 90%/47%/99%
(recall/names/precision). Complementary, not dominant: Textract's recall
edge is mostly OCR quality (it reads the Costco/Target price digits our
Vision pass shattered), while our names beat it overall and Home Depot
names are 42% vs their 0% (they emit SKU rows as names). Both Trader Joe's
receipts -- the skewed ones -- drop Textract to 33/43% recall while our
deskewed decoder holds 100%.
"""

import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import boto3

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "receipt_upload" / "tests" / "fixtures"
CROPS = Path(os.environ.get("GOLDEN_CROPS_DIR", "."))
CACHE = Path(os.environ.get("TEXTRACT_CACHE", "textract_bench.json"))


def fetch(gold: dict) -> dict:
    cache = json.load(open(CACHE)) if CACHE.exists() else {}
    tx = boto3.client("textract", region_name="us-east-1")
    for key in gold:
        ck = f"{key[0][:8]}_{key[1]}"
        if ck in cache:
            continue
        img = open(CROPS / f"{ck}.jpg", "rb").read()
        r = tx.analyze_expense(Document={"Bytes": img})
        items = []
        for d in r.get("ExpenseDocuments", []):
            for g in d.get("LineItemGroups", []):
                for li in g.get("LineItems", []):
                    f = {
                        x["Type"]["Text"]: x.get("ValueDetection", {}).get(
                            "Text"
                        )
                        for x in li.get("LineItemExpenseFields", [])
                    }
                    if f.get("PRICE") is None and f.get("ITEM") is None:
                        continue
                    items.append(
                        {
                            "name": (f.get("ITEM") or "")
                            .replace("\n", " ")
                            .strip(),
                            "price": f.get("PRICE"),
                            "quantity": f.get("QUANTITY"),
                        }
                    )
        cache[ck] = items
        time.sleep(0.3)
    json.dump(cache, open(CACHE, "w"), indent=1)
    return cache


def main() -> None:
    sys.path.insert(0, str(REPO / "receipt_upload" / "tests"))
    import test_line_item_golden_regression as T

    from receipt_upload.line_items.blocks import (
        build_role_priors,
        decode_band_blocks,
        derive_band_labels,
    )

    gold = {
        (r["image_id"], r["receipt_id"]): r
        for r in json.load(open(FIXTURES / "line_items_golden.json"))[
            "receipts"
        ]
    }
    ocr = {
        (r["image_id"], r["receipt_id"]): r
        for r in json.load(open(FIXTURES / "line_items_golden_ocr.json"))[
            "receipts"
        ]
    }
    tex = fetch(gold)
    all_labels = {k: derive_band_labels(gold[k], ocr[k]) for k in gold}
    res = defaultdict(lambda: defaultdict(lambda: [0, 0, 0, 0]))
    for key, g in gold.items():
        truth = [t for t in g["true_items"] if not t.get("is_discount")]
        ours = decode_band_blocks(
            ocr[key],
            build_role_priors([v for k, v in all_labels.items() if k != key]),
        )
        preds = {
            "OURS": [
                {"name": p.get("name"), "price": p.get("price")} for p in ours
            ],
            "TEXTRACT": tex[f"{key[0][:8]}_{key[1]}"],
        }
        for tag, pred in preds.items():
            m, n, tc = T._score_receipt(truth, pred)
            a = res[tag][g["merchant"]]
            a[0] += m
            a[1] += n
            a[2] += tc
            a[3] += len(pred)
    for tag in ("OURS", "TEXTRACT"):
        per = res[tag]
        tm = tn = tt = tp = 0
        print(f"\n=== {tag} ===")
        for merch, (m, n, tc, np_) in sorted(
            per.items(), key=lambda x: -x[1][2]
        ):
            tm += m
            tn += n
            tt += tc
            tp += np_
            print(
                f"{merch[:29]:<30}"
                f"{(m / tc if tc else 0):>8.0%}"
                f"{(n / m if m else 0):>7.0%}"
                f"{(m / np_ if np_ else 0):>7.0%}"
            )
        print(f"{'TOTAL':<30}{tm / tt:>8.0%}{tn / tm:>7.0%}{tm / tp:>7.0%}")


if __name__ == "__main__":
    main()
