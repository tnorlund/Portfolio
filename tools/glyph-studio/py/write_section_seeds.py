#!/usr/bin/env python3
"""Write canonical section seeds to the DEV table as ReceiptSection rows.

Persists per-(receipt, section) rows (line-level; words inherit their line's
section) stamped model_source="section-seed-v0", validation_status="PENDING".
Additive: a row that already exists for (receipt, section_type) is skipped, so
re-runs and concurrent writers are safe.

  --delete-legacy   first delete unversioned legacy rows (model_source is None)
                    — the superseded 2025-05 experiment. Required so old FOOTER
                    rows don't block new canonical FOOTER rows (same SK).

DEV-ONLY: refuses any table other than the known dev table unless
--allow-other-table is passed.

Usage:
  python write_section_seeds.py --merchant-name "Sprouts Farmers Market" \
      [--slug sprouts] [--limit N] [--delete-legacy] --write --yes
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
for _pkg in ("receipt_dynamo",):
    _p = os.path.join(_ROOT, _pkg)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from glyphstudio.section_seeds import (  # noqa: E402
    merchant_slug,
    receipt_section_specs,
    seed_receipt,
)
from seed_section_report import build_seed_words  # noqa: E402

DEV_TABLE = "ReceiptsTable-dc5be22"
MODEL_SOURCE = "section-seed-v0"


def delete_legacy_rows(client, dry_run: bool) -> int:
    """Delete unversioned (model_source is None) ReceiptSection rows."""
    from receipt_dynamo.entities.receipt_section import ReceiptSection

    stale: list[ReceiptSection] = []
    lek = None
    while True:
        batch, lek = client.list_receipt_sections(
            limit=500, last_evaluated_key=lek
        )
        stale.extend(s for s in batch if s.model_source is None)
        if lek is None:
            break
    types = Counter(s.section_type for s in stale)
    print(
        f"legacy rows (model_source is None): {len(stale)} "
        f"across {len({(s.image_id, s.receipt_id) for s in stale})} receipts; "
        f"types={dict(types)}",
        file=sys.stderr,
    )
    if dry_run or not stale:
        return len(stale)
    # batch delete
    client.delete_receipt_sections(stale)
    print(f"deleted {len(stale)} legacy rows", file=sys.stderr)
    return len(stale)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--merchant-name")
    ap.add_argument("--slug", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--include-medium", action="store_true")
    ap.add_argument(
        "--label-only",
        action="store_true",
        help="withhold stylescan-only lines (low-agreement merchants)",
    )
    ap.add_argument("--delete-legacy", action="store_true")
    ap.add_argument(
        "--write", action="store_true", help="actually write (else dry run)"
    )
    ap.add_argument("--yes", action="store_true", help="skip confirmation")
    ap.add_argument("--allow-other-table", action="store_true")
    ap.add_argument(
        "--table",
        default=os.environ.get("DYNAMODB_TABLE_NAME", DEV_TABLE),
    )
    args = ap.parse_args(argv)

    if args.table != DEV_TABLE and not args.allow_other_table:
        ap.error(
            f"refusing to write to {args.table!r} (not the dev table "
            f"{DEV_TABLE!r}); pass --allow-other-table to override"
        )
    if args.write and not args.yes:
        ap.error("--write requires --yes (deliberate confirmation)")

    from receipt_dynamo.data.dynamo_client import DynamoClient

    client = DynamoClient(args.table)
    dry = not args.write
    banner = "DRY RUN" if dry else "WRITE"
    print(f"[{banner}] table={args.table}", file=sys.stderr)

    if args.delete_legacy:
        delete_legacy_rows(client, dry_run=dry)

    if not args.merchant_name:
        return 0

    slug = args.slug or merchant_slug(args.merchant_name)
    if not slug:
        ap.error(f"no slug for {args.merchant_name!r}; pass --slug")

    # enumerate receipts (stop early on --limit)
    pairs, lek = [], None
    while True:
        page = 1000 if not args.limit else min(1000, args.limit - len(pairs))
        places, lek = client.get_receipt_places_by_merchant(
            merchant_name=args.merchant_name,
            limit=page,
            last_evaluated_key=lek,
        )
        pairs.extend((p.image_id, p.receipt_id) for p in places)
        if lek is None or (args.limit and len(pairs) >= args.limit):
            break
    if args.limit:
        pairs = pairs[: args.limit]

    from receipt_dynamo.data.shared_exceptions import EntityAlreadyExistsError
    from receipt_dynamo.entities.receipt_section import ReceiptSection

    now = datetime.now(timezone.utc)
    written = skipped = errors = rows = 0
    for image_id, receipt_id in pairs:
        try:
            details = client.get_receipt_details(image_id, receipt_id)
        except Exception as e:  # noqa: BLE001
            print(f"  skip {image_id}#{receipt_id}: {e}", file=sys.stderr)
            continue
        specs = receipt_section_specs(
            seed_receipt(
                build_seed_words(details),
                slug,
                include_medium=args.include_medium,
            ),
            label_only=args.label_only,
        )
        for spec in specs:
            rows += 1
            if dry:
                continue
            section = ReceiptSection(
                receipt_id=receipt_id,
                image_id=image_id,
                section_type=spec.section_type,
                line_ids=list(spec.line_ids),
                created_at=now,
                confidence=spec.confidence,
                model_source=MODEL_SOURCE,
                validation_status="PENDING",
            )
            try:
                client.add_receipt_section(section)
                written += 1
            except EntityAlreadyExistsError:
                skipped += 1
            except Exception as e:  # noqa: BLE001
                errors += 1
                print(
                    f"  err {image_id}#{receipt_id} {spec.section_type}: {e}",
                    file=sys.stderr,
                )

    detail = (
        "planned"
        if dry
        else f"written={written} skipped={skipped} errors={errors}"
    )
    print(
        f"[{banner}] {slug}: {len(pairs)} receipts, {rows} section rows "
        f"({detail})",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
