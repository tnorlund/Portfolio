#!/usr/bin/env python3
"""Fleet status v1: ACTIVE merchant-truth rows vs legacy enumerations.

One GSITYPE query (TYPE=MERCHANT_TRUTH_ACTIVE) through the DynamoClient
``_MerchantTruth`` reader surface (``list_active_merchant_truth``), then a
cross-check against the two legacy merchant enumerations:

  * ``scripts/merchant_profiles.json`` profile keys
  * ``tools/glyph-studio/server/env.mjs`` FONT_MERCHANTS values

Merchants present in the legacy stores but with NO ACTIVE truth row are
listed as missing. Pre-mint, "0 ACTIVE / 16 missing" is the truthful
steady state, not an error.

Usage:
    python synthesis_loop/fleet_status.py [--json] [--check] [--table NAME]

Exit codes: 0 always for status display; with ``--check``, 1 if any
ACTIVE merchant has gate_status != PASS; 2 for refused configuration.

Read-only against DynamoDB. Env: DYNAMODB_TABLE_NAME (default
ReceiptsTable-dc5be22), AWS_REGION. The prod table (ReceiptsTable-d7ff76a)
is refused explicitly, matching the migration writer's stance.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
for _p in (os.path.join(REPO, "receipt_dynamo"),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from receipt_dynamo.migrations.merchant_truth_v1 import (  # noqa: E402
    slugify_merchant,
)

if TYPE_CHECKING:
    from receipt_dynamo.entities.merchant_truth import MerchantTruthActive
    from receipt_dynamo.merchant_truth_loader import MerchantTruthReader

DEV_TABLE_NAME = "ReceiptsTable-dc5be22"
PROD_TABLE_NAME = "ReceiptsTable-d7ff76a"
PROD_TABLE_MARKER = "d7ff76a"
PROFILES_PATH = os.path.join(REPO, "scripts", "merchant_profiles.json")
ENV_MJS_PATH = os.path.join(REPO, "tools", "glyph-studio", "server", "env.mjs")
SHORT_HASH_LEN = 12

PROFILES_SOURCE = "merchant_profiles.json"
FONT_MERCHANTS_SOURCE = "env.mjs FONT_MERCHANTS"


def resolve_table(cli_table: str | None) -> str:
    """Resolve the target table and refuse prod explicitly."""
    table = (
        cli_table or os.environ.get("DYNAMODB_TABLE_NAME") or DEV_TABLE_NAME
    )
    if table == PROD_TABLE_NAME or PROD_TABLE_MARKER in table:
        raise ValueError(
            f"fleet_status never reads prod; refusing table {table!r} "
            f"(prod = {PROD_TABLE_NAME!r})"
        )
    return table


def parse_font_merchants(env_mjs_text: str) -> dict[str, str]:
    """Extract the FONT_MERCHANTS font-dir -> merchant-name map."""
    match = re.search(
        r"export const FONT_MERCHANTS = \{(.*?)^\};",
        env_mjs_text,
        re.DOTALL | re.MULTILINE,
    )
    if match is None:
        raise ValueError("FONT_MERCHANTS block not found in env.mjs")
    entries = re.findall(r'(\w+):\s*"([^"]+)"', match.group(1))
    if not entries:
        raise ValueError("FONT_MERCHANTS block parsed to zero entries")
    return dict(entries)


def load_legacy_merchants(
    profiles_path: str = PROFILES_PATH,
    env_mjs_path: str = ENV_MJS_PATH,
) -> dict[str, dict[str, Any]]:
    """Union both legacy enumerations, keyed by canonical slug."""
    with open(profiles_path, encoding="utf-8") as handle:
        profiles = json.load(handle)["profiles"]
    with open(env_mjs_path, encoding="utf-8") as handle:
        font_merchants = parse_font_merchants(handle.read())

    legacy: dict[str, dict[str, Any]] = {}
    for source, names in (
        (PROFILES_SOURCE, sorted(profiles)),
        (FONT_MERCHANTS_SOURCE, sorted(font_merchants.values())),
    ):
        for name in names:
            slug = slugify_merchant(name)
            entry = legacy.setdefault(
                slug, {"merchant_name": name, "sources": []}
            )
            if source not in entry["sources"]:
                entry["sources"].append(source)
    return legacy


def _staleness_days(activated_at: str, now: datetime) -> int:
    activated = datetime.fromisoformat(activated_at)
    if activated.tzinfo is None:
        activated = activated.replace(tzinfo=timezone.utc)
    return int((now - activated).total_seconds() // 86400)


def build_report(
    active_records: list["MerchantTruthActive"],
    legacy: dict[str, dict[str, Any]],
    *,
    table: str,
    now: datetime,
) -> dict[str, Any]:
    """Assemble the full status document (source of both outputs)."""
    active_rows = [
        {
            "slug": record.slug,
            "version": record.version,
            "bundle_hash": record.bundle_hash,
            "bundle_hash_short": record.bundle_hash[:SHORT_HASH_LEN],
            "gate_status": record.gate_status,
            "activated_at": record.activated_at,
            "staleness_days": _staleness_days(record.activated_at, now),
        }
        for record in sorted(active_records, key=lambda item: item.slug)
    ]
    active_slugs = {row["slug"] for row in active_rows}
    missing = [
        {
            "slug": slug,
            "merchant_name": entry["merchant_name"],
            "sources": entry["sources"],
        }
        for slug, entry in sorted(legacy.items())
        if slug not in active_slugs
    ]
    return {
        "table": table,
        "generated_at": now.isoformat(),
        "active_count": len(active_rows),
        "legacy_count": len(legacy),
        "missing_count": len(missing),
        "active": active_rows,
        "missing": missing,
        "unlisted_in_legacy": sorted(active_slugs - set(legacy)),
        "check_failures": [
            row["slug"] for row in active_rows if row["gate_status"] != "PASS"
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    """Render the report as a markdown fleet table + missing list."""
    lines = [
        "# Merchant-truth fleet status",
        "",
        f"Table: `{report['table']}` | Generated: {report['generated_at']}",
        "",
        f"**{report['active_count']} ACTIVE / "
        f"{report['missing_count']} missing** "
        f"(legacy enumerations: {report['legacy_count']} merchants)",
        "",
        "| merchant | version | bundle_hash | gate | activated_at "
        "| staleness (days) |",
        "|---|---|---|---|---|---|",
    ]
    if report["active"]:
        for row in report["active"]:
            lines.append(
                f"| {row['slug']} | {row['version']} "
                f"| `{row['bundle_hash_short']}` | {row['gate_status']} "
                f"| {row['activated_at']} | {row['staleness_days']} |"
            )
    else:
        lines.append("| (no ACTIVE merchant-truth rows) | | | | | |")
    lines += [
        "",
        "## Missing from truth store (present in legacy enumerations)",
        "",
    ]
    if report["missing"]:
        lines += [
            "| slug | merchant_name | legacy sources |",
            "|---|---|---|",
        ]
        for row in report["missing"]:
            lines.append(
                f"| {row['slug']} | {row['merchant_name']} "
                f"| {', '.join(row['sources'])} |"
            )
    else:
        lines.append("None: every legacy merchant has an ACTIVE truth row.")
    if report["unlisted_in_legacy"]:
        lines += [
            "",
            "## ACTIVE but absent from legacy enumerations",
            "",
        ]
        lines += [f"- {slug}" for slug in report["unlisted_in_legacy"]]
    return "\n".join(lines) + "\n"


def main(
    argv: list[str] | None = None,
    *,
    reader: "MerchantTruthReader | None" = None,
    now: datetime | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--json", action="store_true", help="emit JSON instead of markdown"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if any ACTIVE merchant has gate_status != PASS",
    )
    parser.add_argument(
        "--table",
        default=None,
        help="DynamoDB table (default: DYNAMODB_TABLE_NAME env or "
        f"{DEV_TABLE_NAME})",
    )
    args = parser.parse_args(argv)

    try:
        table = resolve_table(args.table)
    except ValueError as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        return 2

    legacy = load_legacy_merchants()
    if reader is None:
        from receipt_dynamo.data.dynamo_client import (  # noqa: PLC0415
            DynamoClient,
        )

        region = os.environ.get("AWS_REGION", "us-east-1")
        reader = DynamoClient(table_name=table, region=region)
    active_records = reader.list_active_merchant_truth()

    report = build_report(
        active_records,
        legacy,
        table=table,
        now=now or datetime.now(timezone.utc),
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report), end="")

    if args.check and report["check_failures"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
