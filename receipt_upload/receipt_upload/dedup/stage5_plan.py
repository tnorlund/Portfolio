"""Stage 5 — build a merge plan for confirmed CROSS-IMAGE near-duplicates
(re-scans / re-photos / reprints: same transaction, different pixels).

Input is a list of candidate groups (image#rid keys). Each group is GATED by
the
transaction-identity check (:mod:`near_dup`) — a group is only kept if every
member is provably the same transaction as the survivor (shared
auth/transaction id, or identical total + item prices). Confirmed groups go
through the same resolver as the byte-identical path, producing a plan the
Stage 3 executor
(:mod:`apply`) consumes unchanged.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict

from receipt_dynamo import DynamoClient

from receipt_upload.dedup.context import LabelObs, build_dossiers_for_groups
from receipt_upload.dedup.dossiers import ENV_TABLE
from receipt_upload.dedup.near_dup import (
    frequent_ids,
    same_transaction,
    transaction_fingerprint,
)
from receipt_upload.dedup.resolver import resolve_all

Key = tuple


def _load(table):
    dc = DynamoClient(table)
    receipts = dc.list_receipts()[0]
    words = dc.list_receipt_words()[0]
    labels = dc.list_receipt_word_labels()[0]
    rec = {(r.image_id, r.receipt_id): r for r in receipts}

    words_by, text_at = defaultdict(dict), {}
    for w in words:
        t = getattr(w, "text", "") or ""
        words_by[(w.image_id, w.receipt_id)][(w.line_id, w.word_id)] = t
        text_at[(w.image_id, w.receipt_id, w.line_id, w.word_id)] = t
    labels_by = defaultdict(list)
    for lb in labels:
        labels_by[(lb.image_id, lb.receipt_id)].append(
            LabelObs(
                lb.label,
                lb.line_id,
                lb.word_id,
                text_at.get(
                    (lb.image_id, lb.receipt_id, lb.line_id, lb.word_id), ""
                ),
                getattr(lb, "validation_status", None),
            )
        )
    totals = {}
    for s in dc.list_receipt_summaries()[0]:
        su = getattr(s, "summary", s)
        g = getattr(su, "grand_total", None)
        if g is not None:
            totals[(su.image_id, su.receipt_id)] = float(g)
    merchants = {}
    for m in dc.list_receipt_metadatas()[0]:
        merchants[(m.image_id, m.receipt_id)] = getattr(
            m, "canonical_merchant_name", None
        ) or getattr(m, "merchant_name", None)
    return rec, words_by, labels_by, totals, merchants


def _word_list(words_by, key):
    return [t for _, t in sorted(words_by.get(key, {}).items())]


# A real near-dup group is a small set of copies of one receipt. A large group
# is almost always a false bridge through a shared recurring code — refuse it.
_MAX_GROUP_SIZE = 6


def gate_groups(groups, rec, words_by, totals, merchants=None):
    """Keep a group only if EVERY pair of members is the same transaction.

    Pairwise (clique), not star-shaped against member 0 — so a single corrupted
    / cross-wired member can't validate the rest by bridging through the
    anchor. A corpus-frequency denylist excludes recurring ids
    (card/terminal/AID codes) before matching, and oversized groups are
    rejected outright.
    """
    merchants = merchants or {}

    def fp(k):
        return transaction_fingerprint(
            _word_list(words_by, k), totals.get(k), merchants.get(k)
        )

    # Build the recurring-id denylist over the FULL loaded table (`rec`), not
    # just the supplied group subset — otherwise a terminal/card/AID that
    # recurs across the corpus but appears only twice in `--groups` would not
    # be denylisted, and two different visits sharing it could be approved as a
    # near-dup.
    denylist = frequent_ids([fp(k) for k in rec])

    kept, rejected = [], []
    for g in groups:
        g = [tuple(k) for k in g if tuple(k) in rec]
        if len(g) < 2:
            continue
        if len(g) > _MAX_GROUP_SIZE:
            rejected.append(
                {
                    "group": g,
                    "reasons": [
                        {
                            "member": "group",
                            "is_dup": False,
                            "why": f"group too large "
                            f"({len(g)} > {_MAX_GROUP_SIZE})",
                        }
                    ],
                }
            )
            continue
        fps = {k: fp(k) for k in g}
        reasons, ok = [], True
        for i, ki in enumerate(g):
            for kj in g[i + 1:]:
                is_dup, why = same_transaction(
                    fps[ki], fps[kj], denylist=denylist
                )
                reasons.append(
                    {
                        "pair": f"{ki[0][:8]}#{ki[1]} ~ {kj[0][:8]}#{kj[1]}",
                        "is_dup": is_dup,
                        "why": why,
                    }
                )
                ok = ok and is_dup
        (kept if ok else rejected).append({"group": g, "reasons": reasons})
    return kept, rejected


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", choices=list(ENV_TABLE), default="dev")
    ap.add_argument(
        "--groups",
        required=True,
        help="JSON: {'groups': [[ [img,rid],... ], ...]}",
    )
    ap.add_argument("--out", required=True)
    ap.add_argument("--rejects", help="write gate rejections here")
    args = ap.parse_args()

    with open(args.groups, encoding="utf-8") as f:
        raw = json.load(f)
    groups = raw["groups"] if isinstance(raw, dict) else raw
    rec, words_by, labels_by, totals, merchants = _load(ENV_TABLE[args.env])

    kept, rejected = gate_groups(groups, rec, words_by, totals, merchants)
    print(
        f"[{args.env}] candidate groups: {len(groups)} | "
        f"PASSED transaction-identity gate: {len(kept)} | "
        f"rejected: {len(rejected)}"
    )
    for r in rejected:
        bad = [x for x in r["reasons"] if not x["is_dup"]]
        print(
            f"  REJECT {[f'{k[0][:8]}#{k[1]}' for k in r['group']]}: {bad[:1]}"
        )

    dossiers = build_dossiers_for_groups(
        [r["group"] for r in kept], rec, words_by, labels_by
    )
    resolutions = resolve_all(dossiers)
    drop = sum(len(x.receipts_to_drop) for x in resolutions)
    gaps = sum(len(x.gap_fills) for x in resolutions)
    print(
        f"  -> {len(resolutions)} merge groups | drop {drop} receipts | "
        f"{gaps} VALID gap-fills"
    )
    for x in sorted(resolutions, key=lambda z: -len(z.gap_fills))[:8]:
        print(
            f"     {x.group_id} survivor {x.survivor[-6:]} "
            f"drop {len(x.receipts_to_drop)} "
            f"+{len(x.gap_fills)} gap-fills"
        )

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(
            [x.to_dict() for x in resolutions], f, indent=2, default=str
        )
    print(f"  wrote {args.out}")
    if args.rejects:
        with open(args.rejects, "w", encoding="utf-8") as f:
            json.dump(rejected, f, indent=2, default=str)


if __name__ == "__main__":
    main()
