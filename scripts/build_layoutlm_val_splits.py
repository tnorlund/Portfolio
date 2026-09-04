#!/usr/bin/env python
"""Build pinned LayoutLM validation splits (random and merchant-template).

Why this exists
---------------
A run is only comparable to another run when both held out the *same* receipts.
`layoutlm-v31-nonproduct-clean-20260729` did not pin its split: it passed no
``--val-keys-s3`` and persisted ``val_receipt_keys`` only into
``runs/<job>/run.json``, which the bucket lifecycle then deleted. Its split is
now unreproducible -- reconstructing from the recorded seed yields 91 receipts
against a recorded 82, and the hash does not match. Its score cannot be
compared to anything.

So splits are written to ``config/`` (never lifecycle-managed) and pinned with
``--val-keys-s3`` on every run.

Brand keys, not merchant names
------------------------------
A merchant-template holdout must group by *brand*, not by the stored
``merchant_name`` string. The corpus spells one chain several ways --
``TRADER JOE'S`` / ``Trader Joe's`` / ``Trader Joe's Store #0058`` -- so holding
out one spelling while training on another puts the same store's layout on both
sides of the split. The score then measures template familiarity and reads as
generalization.

Canonicalizing against Google Places does *not* fix this: several ``place_id``s
in this corpus resolve to a street address (``'2716 N Green Valley Pkwy'`` for a
Trader Joe's), so adopting canonical names would reintroduce the
address-as-merchant bug. Brand keys are derived from the stored name instead,
which needs no writes and cannot regress the data.

Usage::

    python scripts/build_layoutlm_val_splits.py --show-brands
    python scripts/build_layoutlm_val_splits.py --out-dir /tmp/splits
    python scripts/build_layoutlm_val_splits.py --out-dir /tmp/splits --upload
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import random
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "receipt_dynamo"))

from receipt_dynamo import DynamoClient  # noqa: E402

DEV_TABLE = "ReceiptsTable-dc5be22"
CONFIG_PREFIX = "s3://layoutlm-training-dev-68164770/config"

# Chains whose stored names differ by more than case/suffix noise. Grouping
# these is what stops a holdout from leaking the same layout into training.
BRAND_ALIASES = {
    "cvs": "cvs",
    "cvspharmacy": "cvs",
    "target": "target",
    "targetgrocery": "target",
    "thehomedepot": "homedepot",
    "homedepot": "homedepot",
    "traderjoes": "traderjoes",
    "smiths": "smiths",
    "smithsfreshforeveryone": "smiths",
    "smithsmoneyservices": "smiths",
    "wildfork": "wildfork",
    "wildforkmeatseafoodmarket": "wildfork",
    "diyhomecenter": "diyhomecenter",
    "diyhomecenteragoura": "diyhomecenter",
    "aimmailcenter": "aimmailcenter",
    "sproutsfarmersmarket": "sprouts",
}

# Store-specific decoration to strip before matching a brand.
_SUFFIX = re.compile(
    r"""
      \s*\#\s*\d+           # "#207", "# 18"
    | \s*store\s*\#?\s*\d+  # "Store #0058"
    | \s*\(.*?\)            # "(Westlake Village, CA)"
    | \s+-\s+.*$            # " - Thousand Oaks"
    """,
    re.IGNORECASE | re.VERBOSE,
)


def brand_key(merchant_name: str | None) -> str:
    """Collapse a stored merchant name to a stable brand identifier."""
    if not merchant_name or not merchant_name.strip():
        return "(unresolved)"
    name = _SUFFIX.sub("", merchant_name)
    squashed = re.sub(r"[^a-z0-9]", "", name.lower())
    if squashed in BRAND_ALIASES:
        return BRAND_ALIASES[squashed]
    # Strip common trailing descriptors so "Foo Market" == "Foo".
    trimmed = re.sub(
        r"(market|pharmacy|wholesale|supercenter|grocery|cafe|kitchen)$",
        "",
        squashed,
    )
    return BRAND_ALIASES.get(trimmed, trimmed or squashed)


def _pull(fn):
    out, lek = [], None
    while True:
        batch, lek = fn(limit=500, last_evaluated_key=lek)
        out.extend(batch)
        if not lek:
            return out


def _union_by_place_id(corpus, place_ids):
    """Merge brand keys that share a ``place_id``.

    Two receipts resolved to the same ``place_id`` are the same physical store,
    whatever their names look like -- ``Roast & Rice Asian Fusion`` and
    ``Roast and Rice Kitchen`` normalize differently but are one restaurant.
    Using the id as evidence catches those without trusting Google's canonical
    *name*, which for several stores here is a street address.
    """
    parent: dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            # Keep the lexicographically smaller root for stable output.
            lo, hi = sorted((ra, rb))
            parent[hi] = lo

    by_place = collections.defaultdict(set)
    for row in corpus:
        pid = place_ids.get((row["image_id"], row["receipt_id"]))
        if pid:
            by_place[pid].add(row["brand"])

    for brands in by_place.values():
        brands = sorted(brands)
        for other in brands[1:]:
            union(brands[0], other)

    for row in corpus:
        row["brand"] = find(row["brand"])
    return corpus


def load_corpus(client):
    """Return ``[{image_id, receipt_id, merchant, brand, n_labels}]``."""
    labels = [
        l
        for l in _pull(client.list_receipt_word_labels)
        if getattr(l, "validation_status", "") != "INVALID"
    ]
    counts = collections.Counter((l.image_id, l.receipt_id) for l in labels)
    places = _pull(client.list_receipt_places)
    merchants = {
        (p.image_id, p.receipt_id): getattr(p, "merchant_name", None)
        for p in places
    }
    place_ids = {
        (p.image_id, p.receipt_id): getattr(p, "place_id", None)
        for p in places
    }
    corpus = []
    for (image_id, receipt_id), n in sorted(counts.items()):
        merchant = merchants.get((image_id, receipt_id))
        corpus.append(
            {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "merchant": merchant,
                "brand": brand_key(merchant),
                "n_labels": n,
            }
        )
    return _union_by_place_id(corpus, place_ids)


def receipt_key(row) -> str:
    return f"{row['image_id']}#{row['receipt_id']}"


def hash_keys(keys) -> str:
    """16-char sha256 of sorted keys (matches SplitMetadata.val_receipts_hash)."""
    joined = "\n".join(sorted(keys)).encode()
    return hashlib.sha256(joined).hexdigest()[:16]


def random_split(corpus, seed: int, val_share: float):
    rng = random.Random(seed)
    rows = sorted(corpus, key=receipt_key)
    rng.shuffle(rows)
    n_val = max(1, round(len(rows) * val_share))
    return [receipt_key(r) for r in rows[:n_val]]


def template_split(corpus, holdout_brands):
    wanted = set(holdout_brands)
    return [receipt_key(r) for r in corpus if r["brand"] in wanted]


def write_split(out_dir: Path, name: str, keys, meta) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    payload = dict(meta)
    payload["val_receipt_keys"] = sorted(keys)
    payload["num_val_receipts"] = len(keys)
    payload["val_receipts_hash"] = hash_keys(keys)
    path.write_text(json.dumps(payload, indent=2))
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table", default=DEV_TABLE)
    parser.add_argument("--out-dir", type=Path, default=Path("."))
    parser.add_argument("--seed", type=int, default=20260904)
    parser.add_argument("--val-share", type=float, default=0.18)
    parser.add_argument(
        "--holdout-brand",
        action="append",
        default=None,
        help="Brand key to hold out entirely (repeatable).",
    )
    parser.add_argument("--show-brands", action="store_true")
    parser.add_argument("--upload", action="store_true")
    parser.add_argument("--stamp", default=None, help="Filename date stamp.")
    args = parser.parse_args()

    client = DynamoClient(args.table)
    corpus = load_corpus(client)
    by_brand = collections.Counter(r["brand"] for r in corpus)

    print(
        f"labeled receipts: {len(corpus)}   distinct brands: {len(by_brand)}"
    )

    if args.show_brands:
        print("\nreceipts per brand:")
        for brand, n in by_brand.most_common(30):
            spellings = sorted(
                {r["merchant"] for r in corpus if r["brand"] == brand}
            )
            extra = f"   <- {spellings}" if len(spellings) > 1 else ""
            print(f"  {n:4d}  {brand}{extra}")
        return 0

    holdout = args.holdout_brand or [
        "costco",
        "vons",
        "homedepot",
        "wildfork",
        "target",
    ]
    stamp = args.stamp or "unstamped"

    rnd = random_split(corpus, args.seed, args.val_share)
    rnd_path = write_split(
        args.out_dir,
        f"val_keys_random_{stamp}.json",
        rnd,
        {
            "split_type": "random",
            "seed": args.seed,
            "val_share": args.val_share,
            "source_table": args.table,
            "corpus_size": len(corpus),
            "note": (
                "Random receipt split. Merchant templates appear on both "
                "sides, so this measures in-distribution accuracy, not "
                "generalization to unseen layouts."
            ),
        },
    )

    tpl = template_split(corpus, holdout)
    tpl_path = write_split(
        args.out_dir,
        f"val_keys_template_{stamp}.json",
        tpl,
        {
            "split_type": "merchant_template_holdout",
            "holdout_brands": sorted(holdout),
            "source_table": args.table,
            "corpus_size": len(corpus),
            "note": (
                "Every receipt of the held-out brands. Grouping is by brand "
                "key, not stored merchant_name, so spelling variants of one "
                "chain cannot straddle the split."
            ),
        },
    )

    for label, path, keys in (
        ("random", rnd_path, rnd),
        ("template", tpl_path, tpl),
    ):
        print(
            f"\n{label}: {len(keys)} receipts "
            f"({len(keys) / len(corpus):.1%})  hash={hash_keys(keys)}"
        )
        print(f"  {path}")

    overlap = set(rnd) & set(tpl)
    print(f"\noverlap between the two splits: {len(overlap)} receipts")

    leaked = [b for b in holdout if by_brand.get(b, 0) == 0]
    if leaked:
        print(f"WARNING: holdout brands absent from corpus: {leaked}")

    if args.upload:
        for path in (rnd_path, tpl_path):
            dest = f"{CONFIG_PREFIX}/{path.name}"
            subprocess.run(
                ["aws", "s3", "cp", str(path), dest, "--region", "us-east-1"],
                check=True,
            )
            print(f"uploaded {dest}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
