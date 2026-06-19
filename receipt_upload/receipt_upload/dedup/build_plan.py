"""Build the deterministic duplicate-merge plan (survivor + VALID gap-fills).

Usage: python -m receipt_upload.dedup.build_plan --env dev --out merge_plan.json

Runs the full deterministic pipeline (group by receipt-sha -> dossiers ->
merge resolutions) and writes one reviewable plan. Mutates nothing; applying the
plan is a separate, gated step.
"""

from __future__ import annotations

import argparse
import json

from receipt_upload.dedup.context import build_merge_dossiers
from receipt_upload.dedup.dossiers import ENV_TABLE, load_inputs
from receipt_upload.dedup.resolver import resolve_all


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", choices=list(ENV_TABLE), default="dev")
    ap.add_argument("--out", help="write the full plan to this JSON path")
    args = ap.parse_args()

    receipts, words, labels = load_inputs(ENV_TABLE[args.env])
    resolutions = resolve_all(build_merge_dossiers(receipts, words, labels))

    drop = sum(len(r.receipts_to_drop) for r in resolutions)
    gaps = sum(len(r.gap_fills) for r in resolutions)
    skipped = sum(len(r.skipped_gaps) for r in resolutions)
    clean = sum(1 for r in resolutions if r.action == "drop_redundant")

    print(f"[{args.env}] {len(receipts)} receipts")
    print(f"  merge groups: {len(resolutions)} (within-image "
          f"{sum(1 for r in resolutions if r.scope == 'within_image')}, cross-image "
          f"{sum(1 for r in resolutions if r.scope == 'cross_image')})")
    print(f"  receipts to drop (redundant copies): {drop}")
    print(f"  VALID gap-fill labels migrated onto survivors: {gaps}")
    print(f"  gap labels skipped (ambiguous target / disagreement): {skipped}")
    print(f"  groups needing no gap-fill (drop_redundant): {clean}")
    print("\n  sample groups:")
    for r in sorted(resolutions, key=lambda x: -len(x.gap_fills))[:6]:
        print(f"    {r.group_id} [{r.scope}] survivor {r.survivor[-6:]} "
              f"({r.survivor_label_count} labels) | drop {len(r.receipts_to_drop)} "
              f"| +{len(r.gap_fills)} gap-fills | {r.action}")

    if args.out:
        with open(args.out, "w") as f:
            json.dump([r.to_dict() for r in resolutions], f, indent=2, default=str)
        print(f"\n  wrote {args.out}")


if __name__ == "__main__":
    main()
