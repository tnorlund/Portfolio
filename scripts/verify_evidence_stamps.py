#!/usr/bin/env python3.12
"""Verify committed evaluation evidence against the ACTIVE merchant fleet.

The default verification path is hermetic and reads the committed
``evidence/ACTIVE_FLEET.json`` snapshot. Refreshing that snapshot is an
explicit, read-only DynamoDB operation:

    python3.12 scripts/verify_evidence_stamps.py --refresh-active-fleet
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE_ROOT = REPO_ROOT / "evidence"
DEFAULT_ACTIVE_FLEET = DEFAULT_EVIDENCE_ROOT / "ACTIVE_FLEET.json"
DEFAULT_TABLE = "ReceiptsTable-dc5be22"

ActiveFleet = dict[str, tuple[int, str]]
AncestorChecker = Callable[[str, str], bool]


def _short_hash(value: str) -> str:
    return f"{value[:8]}…"


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def load_active_fleet(path: Path) -> ActiveFleet:
    """Load and validate the committed ACTIVE-fleet snapshot."""
    with path.open(encoding="utf-8") as handle:
        document = json.load(handle)
    if document.get("schema_version") != 1:
        raise ValueError(f"{path}: unsupported or missing schema_version")
    records = document.get("active")
    if not isinstance(records, dict):
        raise ValueError(f"{path}: active must be an object")

    active: ActiveFleet = {}
    for slug, record in records.items():
        if not isinstance(slug, str) or not isinstance(record, dict):
            raise ValueError(f"{path}: malformed ACTIVE record")
        version = record.get("version")
        bundle_hash = record.get("bundle_hash")
        if (
            not isinstance(version, int)
            or isinstance(version, bool)
            or not isinstance(bundle_hash, str)
            or not bundle_hash
        ):
            raise ValueError(f"{path}: malformed ACTIVE record for {slug}")
        active[slug] = (version, bundle_hash)
    return active


def _stamp_slug(document: dict[str, Any], truth: dict[str, Any]) -> str | None:
    slug = truth.get("slug")
    if isinstance(slug, str) and slug:
        return slug
    receipt = document.get("receipt")
    if isinstance(receipt, dict):
        slug = receipt.get("slug")
        if isinstance(slug, str) and slug:
            return slug
    return None


def _git_is_ancestor(git_sha: str, head: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", git_sha, head],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def verify_document(
    path: Path,
    document: dict[str, Any],
    active: ActiveFleet,
    head: str,
    *,
    is_ancestor: AncestorChecker = _git_is_ancestor,
) -> list[str]:
    """Return every provenance error for one stamped evidence document."""
    stamp = document.get("stamp")
    if not isinstance(stamp, dict):
        return []

    display = _display_path(path)
    findings: list[str] = []
    truth = stamp.get("merchant_truth")
    if not isinstance(truth, dict):
        return [f"{display}: stamp.merchant_truth must be an object"]

    mode = truth.get("mode")
    if mode != "online-active":
        findings.append(
            f"{display}: evidence mode is {mode!r}, not 'online-active'; "
            "fixture and pinned bundles do not describe deployed truth."
        )

    slug = _stamp_slug(document, truth)
    version = truth.get("version")
    bundle_hash = truth.get("bundle_hash")
    if (
        slug is None
        or not isinstance(version, int)
        or isinstance(version, bool)
        or not isinstance(bundle_hash, str)
        or not bundle_hash
    ):
        findings.append(
            f"{display}: stamp must identify merchant truth with slug, "
            "integer version, and bundle_hash."
        )
    elif slug not in active:
        findings.append(
            f"{display}: measured {slug} v{version} "
            f"({_short_hash(bundle_hash)}) but no ACTIVE snapshot entry "
            "exists for that merchant."
        )
    else:
        active_version, active_hash = active[slug]
        if (version, bundle_hash) != (active_version, active_hash):
            findings.append(
                f"{display} measured {slug} v{version} "
                f"({_short_hash(bundle_hash)}) but ACTIVE is "
                f"v{active_version} ({_short_hash(active_hash)}); this "
                "evidence describes a bundle nobody uses."
            )

    dirty = stamp.get("dirty")
    if dirty is True:
        findings.append(
            f"{display}: evidence was measured from a dirty worktree."
        )
    elif dirty not in (None, False):
        findings.append(f"{display}: stamp.dirty must be a boolean.")

    git_sha = stamp.get("git_sha")
    if not isinstance(git_sha, str) or not git_sha:
        findings.append(f"{display}: stamp.git_sha is missing.")
    elif not is_ancestor(git_sha, head):
        findings.append(
            f"{display}: stamped git_sha {git_sha} is not an ancestor "
            f"of PR head {head}."
        )
    return findings


def verify_evidence(
    evidence_root: Path,
    active: ActiveFleet,
    head: str,
    *,
    is_ancestor: AncestorChecker = _git_is_ancestor,
) -> tuple[int, list[str]]:
    """Verify every JSON document below ``evidence_root`` carrying a stamp."""
    checked = 0
    findings: list[str] = []
    for path in sorted(evidence_root.rglob("*.json")):
        try:
            with path.open(encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, json.JSONDecodeError) as error:
            findings.append(f"{_display_path(path)}: invalid JSON: {error}")
            continue
        if not isinstance(document, dict) or "stamp" not in document:
            continue
        checked += 1
        findings.extend(
            verify_document(
                path,
                document,
                active,
                head,
                is_ancestor=is_ancestor,
            )
        )
    return checked, findings


def _snapshot_document(reader: Any, table: str) -> dict[str, Any]:
    """Strong-read ACTIVE tuples discovered through the fleet index."""
    records: dict[str, dict[str, Any]] = {}
    for candidate in reader.list_active_merchant_truth():
        active = reader.get_active_merchant_truth(
            candidate.slug,
            consistent_read=True,
        )
        if active is None:
            raise RuntimeError(
                f"ACTIVE pointer disappeared during refresh: {candidate.slug}"
            )
        records[active.slug] = {
            "bundle_hash": active.bundle_hash,
            "version": active.version,
        }
    return {
        "active": records,
        "schema_version": 1,
        "source": {
            "read_mode": "strong-per-slug",
            "table": table,
        },
    }


def refresh_active_fleet(path: Path, table: str) -> None:
    """Refresh the local snapshot using DynamoDB reads only."""
    from receipt_dynamo.data.dynamo_client import DynamoClient

    document = _snapshot_document(DynamoClient(table), table)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence-root",
        type=Path,
        default=DEFAULT_EVIDENCE_ROOT,
    )
    parser.add_argument(
        "--active-fleet",
        type=Path,
        default=DEFAULT_ACTIVE_FLEET,
    )
    parser.add_argument(
        "--head",
        default=os.environ.get("GITHUB_SHA", "HEAD"),
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--refresh-active-fleet",
        action="store_true",
        help="read ACTIVE pointers from DynamoDB and rewrite the snapshot",
    )
    parser.add_argument(
        "--table",
        default=os.environ.get("DYNAMODB_TABLE_NAME", DEFAULT_TABLE),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.refresh_active_fleet:
        refresh_active_fleet(args.active_fleet, args.table)
        print(f"refreshed {args.active_fleet} from {args.table} (read-only)")
        return 0

    try:
        active = load_active_fleet(args.active_fleet)
        checked, findings = verify_evidence(
            args.evidence_root,
            active,
            args.head,
        )
    except (OSError, ValueError) as error:
        findings = [str(error)]
        checked = 0

    result = {
        "checked": checked,
        "findings": findings,
        "ok": not findings,
    }
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    elif findings:
        print("\n".join(findings), file=sys.stderr)
    else:
        print(f"verified {checked} stamped evidence files")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
