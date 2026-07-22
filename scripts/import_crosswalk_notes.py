#!/usr/bin/env python3
"""Import discarded merchant-profile curator comments as PROPOSED records.

The v1 MerchantTruth migration discarded every ``merchant_profiles.json``
``_comment`` / ``_face_source_comment`` leaf as documentation-only. Contract
§7.8 requires that curator knowledge - including the original Costco
``SELF-CHECKOUT`` observation - to be preserved into the truth system as
PROPOSED records. This script extracts those leaves *from git history* (the
exact SHA the v1 mint recorded in provenance) and writes one PROPOSED record
per leaf under the owning merchant's ``MERCHANT_TRUTH#{slug}`` partition.

Safety:
* **Dry-run by default.** Nothing is written without ``--apply`` (owner-gated).
* **Dev-pinned, prod refused unconditionally** *before* any boto3 client is
  built (the sibling ``migrate_merchant_truth_v1`` pattern).
* **Every leaf persists.** Preservation never depends on a heuristic: each
  extracted leaf writes its own full record. A leaf that is textually related
  to an existing proposal is *annotated* with a ``related_to`` cross-reference,
  not suppressed. The only non-writing disposition is an idempotent skip when
  the leaf's claim_slug was already imported (its text already persists).
* **Zero text loss = persistence.** The report re-extracts every leaf from a
  fresh ``git show`` and asserts each maps to a record whose text is
  byte-identical (a planless leaf fails), before ``--apply`` writes anything.
  This proves extraction-faithfulness + envelope round-trip; the re-extract
  shares the import walker, so it is not a database read-back.

Examples::

    # read-only dry run against dev (git + dev, no writes)
    uv run scripts/import_crosswalk_notes.py

    # owner apply to dev
    uv run scripts/import_crosswalk_notes.py --apply
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from botocore.exceptions import ClientError

from receipt_dynamo import DynamoClient
from receipt_dynamo.migrations.crosswalk_notes import (
    ImportPlan,
    extract_comment_leaves,
    plan_import,
    verify_persistence,
)
from receipt_dynamo.migrations.merchant_truth_v1 import DEV_TABLE_NAME
from receipt_dynamo.migrations.merchant_truth_v1_live import (
    validate_live_table,
)

PROFILES_SOURCE_PATH = "scripts/merchant_profiles.json"
GIT_SHA_MERCHANT = "costco_wholesale"  # v1 manifest carrying the mint git_sha


def _git_show_document(repo_root: Path, git_sha: str) -> dict:
    """Read the exact profile document out of git history at ``git_sha``."""
    raw = subprocess.run(
        ["git", "show", f"{git_sha}:{PROFILES_SOURCE_PATH}"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return json.loads(raw)


def _resolve_v1_git_sha(client: DynamoClient, table_name: str) -> str:
    """Read the git_sha the v1 mint recorded in provenance (contract §7.8).

    The real run reads it live from the v1 manifest; tests seed the manifest
    through moto and exercise the identical path.
    """
    manifest = client.get_merchant_truth_manifest(GIT_SHA_MERCHANT, 1)
    if manifest is None:
        raise SystemExit(
            f"no v1 manifest for {GIT_SHA_MERCHANT!r} in {table_name!r}; "
            "pass --git-sha explicitly"
        )
    git_sha = (manifest.provenance or {}).get("git_sha")
    if not git_sha:
        raise SystemExit(
            f"v1 manifest for {GIT_SHA_MERCHANT!r} carries no git_sha "
            "provenance; pass --git-sha explicitly"
        )
    return str(git_sha)


def _print_report(
    plan: ImportPlan,
    verifications: list,
    *,
    table_name: str,
    git_sha: str,
    apply: bool,
) -> bool:
    """Print the full per-merchant report. Return True when every leaf's text
    is verified to persist byte-identical."""
    mode = "APPLY" if apply else "DRY-RUN"
    print("=" * 72)
    print(f"import_crosswalk_notes  [{mode}]  table={table_name}")
    print(f"source={PROFILES_SOURCE_PATH}@{git_sha}")
    print("=" * 72)

    by_merchant: dict[str, list] = {}
    for decision in plan.decisions:
        by_merchant.setdefault(decision.leaf.merchant_name, []).append(
            decision
        )

    for merchant_name in sorted(by_merchant):
        decisions = by_merchant[merchant_name]
        slug = decisions[0].leaf.slug
        print(f"\n### {merchant_name}  (MERCHANT_TRUTH#{slug})")
        for decision in decisions:
            leaf = decision.leaf
            if decision.action == "WRITE":
                head = f"  [WOULD-WRITE] claim_slug={leaf.claim_slug}"
                if decision.related_to is not None:
                    head += (
                        f"  (related to: {decision.related_to}; "
                        f"shared: {decision.matched_phrase!r})"
                    )
            else:
                head = f"  [SKIP-EXISTS] claim_slug={leaf.claim_slug}"
            print(head)
            print(f"     leaf_path: {leaf.leaf_path}")
            print(f"     text: {leaf.text!r}")

    print("\n" + "-" * 72)
    print("PERSISTENCE / EXTRACTION-FAITHFULNESS CHECK")
    print(
        "  (every extracted leaf must map to a record that persists its text "
        "byte-identical;"
    )
    print(
        "   extraction re-uses the import walker, so this proves round-trip "
        "faithfulness, not a DB read-back)"
    )
    print("-" * 72)
    clean = True
    for result in verifications:
        flag = "PASS" if result.ok else "FAIL"
        if not result.ok:
            clean = False
        print(f"  [{flag}] {result.leaf_path}  ({result.detail})")

    print("\n" + "-" * 72)
    print(
        f"SUMMARY: {len(plan.decisions)} leaves | "
        f"{len(plan.to_write)} to write | "
        f"{len(plan.related)} related-to-existing (annotated, not suppressed) "
        f"| {len(plan.skipped)} already-imported | "
        f"persistence={'CLEAN' if clean else 'DIRTY'}"
    )
    print("-" * 72)
    return clean


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--table",
        "--table-name",
        dest="table_name",
        default=None,
        help=(
            f"DynamoDB table (default: exact dev table {DEV_TABLE_NAME}). "
            "The prod table is refused unconditionally; any other table "
            "requires passing --table explicitly."
        ),
    )
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument(
        "--git-sha",
        help=(
            "Profile SHA to read from git history (default: the git_sha the "
            "v1 mint recorded in the Costco v1 manifest provenance)."
        ),
    )
    parser.add_argument(
        "--merchants",
        help="Comma-separated merchant slugs to restrict the run to.",
    )
    parser.add_argument("--created-at")
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Owner-gated: write the planned PROPOSED records. Without this "
            "flag no DynamoDB write ever happens."
        ),
    )
    args = parser.parse_args(argv)

    explicit_table = args.table_name is not None
    table_name = args.table_name or DEV_TABLE_NAME
    # Unconditional on every path (dry run included): prod refusal precedes
    # the boto3 client and any read.
    validate_live_table(table_name, explicit=explicit_table)

    repo_root = Path(__file__).resolve().parents[1]
    created_at = args.created_at or datetime.now(timezone.utc).isoformat()

    client = DynamoClient(table_name, region=args.region)
    git_sha = args.git_sha or _resolve_v1_git_sha(client, table_name)

    document = _git_show_document(repo_root, git_sha)
    leaves = extract_comment_leaves(document)
    if args.merchants:
        wanted = {
            slug.strip() for slug in args.merchants.split(",") if slug.strip()
        }
        leaves = [leaf for leaf in leaves if leaf.slug in wanted]

    existing_by_slug: dict[str, list] = {}
    for slug in {leaf.slug for leaf in leaves}:
        existing_by_slug[slug] = client.list_merchant_truth_proposals(slug)

    plan = plan_import(
        leaves, existing_by_slug, git_sha=git_sha, created_at=created_at
    )

    # Second read of the exact same source: re-extract the leaves and assert
    # every one persists byte-identical in a scheduled record (no planless
    # leaf), so preservation never depends on the relatedness heuristic.
    reference_document = _git_show_document(repo_root, git_sha)
    scope = {leaf.leaf_path for leaf in leaves}
    verifications = verify_persistence(plan, reference_document, scope=scope)

    clean = _print_report(
        plan,
        verifications,
        table_name=table_name,
        git_sha=git_sha,
        apply=args.apply,
    )

    if not args.apply:
        print("\nDRY-RUN: no writes. Re-run with --apply (owner) to persist.")
        return 0 if clean else 2

    if not clean:
        raise SystemExit(
            "refusing to --apply: persistence verification failed "
            "(a leaf's text would not persist byte-identical)"
        )

    written = 0
    skipped = 0
    for decision in plan.to_write:
        assert decision.proposal is not None
        try:
            client.add_proposal(decision.proposal, table_name)
            written += 1
            print(f"  wrote {decision.leaf.slug}#{decision.leaf.claim_slug}")
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code")
            if code == "ConditionalCheckFailedException":
                skipped += 1  # idempotent: another run already created it
                print(
                    f"  exists {decision.leaf.slug}#{decision.leaf.claim_slug}"
                )
            else:
                raise
    print(f"\nAPPLIED: {written} written, {skipped} already-present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
