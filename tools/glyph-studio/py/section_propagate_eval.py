#!/usr/bin/env python3
"""Evaluate section propagation on a local Chroma words snapshot (no cloud).

Loads a downloaded receipt_chroma ``words`` snapshot and a section map
(``{"image|receipt|line": SECTION}`` JSON built from the ReceiptSection seeds),
labels each word with its line's section, and reports cross-receipt KNN
propagation accuracy.

Needs chromadb 1.5.x to open the snapshot (the compaction writer's version).

Usage:
  python section_propagate_eval.py --snapshot /path/words --section-map map.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from glyphstudio.section_propagate import evaluate_cross_receipt  # noqa: E402


def load_words(snapshot: str, section_map: dict):
    import chromadb

    client = chromadb.PersistentClient(path=snapshot)
    coll = next(
        (c for c in client.list_collections() if c.name == "words"), None
    )
    if coll is None:
        raise ValueError(f"no 'words' collection in snapshot {snapshot}")
    n = coll.count()
    emb, labels, receipts = [], [], []
    batch_size = 5000
    for off in range(0, n, batch_size):
        r = coll.get(
            limit=batch_size, offset=off, include=["metadatas", "embeddings"]
        )
        for md, e in zip(r["metadatas"], r["embeddings"], strict=True):
            k = f"{md.get('image_id')}|{md.get('receipt_id')}|{md.get('line_id')}"
            emb.append(e)
            labels.append(section_map.get(k))
            receipts.append(f"{md.get('image_id')}|{md.get('receipt_id')}")
    return np.asarray(emb, dtype=np.float32), labels, receipts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--section-map", required=True)
    ap.add_argument("--k", type=int, default=15)
    args = ap.parse_args(argv)

    with open(args.section_map, encoding="utf-8") as fh:
        section_map = json.load(fh)
    emb, labels, receipts = load_words(args.snapshot, section_map)
    labeled = sum(1 for s in labels if s is not None)
    print(
        f"words: {len(labels)} | labeled (line has section): {labeled} "
        f"({labeled / max(len(labels),1):.1%})",
        file=sys.stderr,
    )
    ev = evaluate_cross_receipt(emb, labels, receipts, k=args.k)
    print(f"\ncross-receipt KNN (k={args.k}) accuracy: {ev.accuracy:.1%} "
          f"(n_test={ev.n_test})")
    print("per-section (support | recall):")
    for s, (sup, rec) in sorted(ev.per_section_recall.items()):
        print(f"  {s:15s} {sup:5d}  {rec:.1%}")
    for th, (frac, acc) in ev.coverage_at.items():
        print(f"conf>={th}: {frac:.0%} of words at {acc:.1%} accuracy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
