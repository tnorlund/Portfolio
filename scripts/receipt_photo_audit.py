#!/usr/bin/env python3
"""Read dev inventory and evaluate evidence-backed Photos coverage manifests.

No Photos access, uploads, database writes, or deletion. Review attestations
are inputs; this reporter does not perform image classification or semantic QA.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from mypy_boto3_sts import STSClient

DEV_ACCOUNT = "681647709217"
DEV_TABLE = "ReceiptsTable-dc5be22"
REGION = "us-east-1"


class AuditError(ValueError):
    """Coverage cannot be established from the supplied inventory."""


def load_json(path: Path) -> dict[str, Any]:
    """Read a manifest without accepting a non-object root."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AuditError("Expected a JSON object")
    return value


def write_new_json(path: Path, value: dict[str, Any]) -> None:
    """Preserve evidence and keep private output outside Git checkouts."""
    if any((p / ".git").exists() for p in path.resolve().parents):
        raise AuditError("Private audit output must be outside Git checkouts")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, default=str)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def read_pages(method: Callable[..., Any]) -> list[Any]:
    """Exhaust pagination; fail rather than accepting a repeating cursor."""
    records: list[Any] = []
    cursor = None
    seen: set[str] = set()
    while True:
        page, cursor = method(limit=200, last_evaluated_key=cursor)
        records.extend(page)
        if not cursor:
            return records
        marker = json.dumps(cursor, sort_keys=True)
        if marker in seen:
            raise AuditError("Repeated pagination cursor")
        seen.add(marker)


def _identity_value(value: Any, field: str) -> bool:
    if field == "receipt_id":
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            and value > 0
        )
    return isinstance(value, str) and bool(value.strip())


def _indexed(
    rows: Any, fields: tuple[str, ...]
) -> dict[tuple[Any, ...], dict[str, Any]]:
    if not isinstance(rows, list):
        raise AuditError("Expected a record list")
    result = {}
    for row in rows:
        if not isinstance(row, dict) or any(
            not _identity_value(row.get(field), field) for field in fields
        ):
            raise AuditError(f"Missing or invalid identity fields: {fields}")
        key = tuple(row[field] for field in fields)
        if key in result:
            raise AuditError(f"Duplicate identity: {key}")
        result[key] = row
    return result


def project_integrity(project: dict[str, Any]) -> dict[str, Any]:
    """Find project-record orphans without inferring missing Photos assets."""
    images = {k[0] for k in _indexed(project.get("images"), ("image_id",))}
    receipts = set(
        _indexed(project.get("receipts"), ("image_id", "receipt_id"))
    )
    summaries = set(
        _indexed(project.get("summaries"), ("image_id", "receipt_id"))
    )
    return {
        "images": len(images),
        "receipts": len(receipts),
        "summaries": len(summaries),
        "images_without_receipts": sorted(images - {k[0] for k in receipts}),
        "receipts_without_summary": [
            list(k) for k in sorted(receipts - summaries)
        ],
        "summaries_without_receipt": [
            list(k) for k in sorted(summaries - receipts)
        ],
        "receipts_without_parent_image": sorted(
            {k[0] for k in receipts} - images
        ),
    }


def snapshot_project() -> dict[str, Any]:
    """Read the pinned dev account/table through the repository data layer."""
    # Keep offline reporting usable without AWS or repository dependencies.
    # pylint: disable=import-outside-toplevel
    import boto3

    from receipt_dynamo import DynamoClient

    identity_client: STSClient = boto3.client("sts", region_name=REGION)
    account = identity_client.get_caller_identity()["Account"]
    if account != DEV_ACCOUNT:
        raise AuditError(
            "AWS account does not match the permitted dev account"
        )
    client = DynamoClient(DEV_TABLE, region=REGION)
    project = {
        "schema_version": 1,
        "environment": "dev",
        "table": DEV_TABLE,
        "account": account,
        "snapshot_at": datetime.now(timezone.utc).isoformat(),
        "complete": True,
        "images": [asdict(x) for x in read_pages(client.list_images)],
        "receipts": [asdict(x) for x in read_pages(client.list_receipts)],
        "summaries": [
            asdict(x.summary)
            for x in read_pages(client.list_receipt_summaries)
        ],
    }
    project["integrity"] = project_integrity(project)
    return project


def _evidence(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(ref, str) and bool(ref.strip()) for ref in value)
    )


def receipt_fingerprint(
    project: dict[str, Any], image_id: str, receipt_ids: list[int]
) -> str:
    """Bind QA to the image, receipt metadata and summary snapshot reviewed."""
    rows = {}
    for kind in ("images", "receipts", "summaries"):
        selected = [
            row
            for row in project[kind]
            if row["image_id"] == image_id
            and (kind == "images" or row["receipt_id"] in receipt_ids)
        ]
        rows[kind] = sorted(selected, key=lambda row: row.get("receipt_id", 0))
    encoded = json.dumps(
        rows, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def coverage_report(
    library: dict[str, Any], ledger: dict[str, Any], project: dict[str, Any]
) -> dict[str, Any]:
    """Conservatively reconcile reviewed assets with current dev records."""
    # Keep the three independent record sets visible at the decision boundary.
    # pylint: disable=too-many-locals,too-many-statements
    if any(d.get("schema_version") != 1 for d in (library, ledger, project)):
        raise AuditError("Unsupported manifest schema version")
    if (
        project.get("environment") != "dev"
        or project.get("complete") is not True
    ):
        raise AuditError("A complete dev project snapshot is required")
    if library.get("scope") not in {"full", "partial"}:
        raise AuditError("Library scope must be full or partial")
    if not isinstance(library.get("enumeration_complete"), bool):
        raise AuditError("enumeration_complete must be a boolean")
    expected = library.get("expected_assets")
    if expected is not None and (
        not isinstance(expected, int)
        or isinstance(expected, bool)
        or expected < 0
    ):
        raise AuditError(
            "expected_assets must be a nonnegative integer or null"
        )
    assets = {
        k[0]: v
        for k, v in _indexed(library.get("assets"), ("asset_key",)).items()
    }
    entries = {
        k[0]: v
        for k, v in _indexed(ledger.get("photos"), ("asset_key",)).items()
    }
    integrity = project_integrity(project)
    images = {x["image_id"] for x in project["images"]}
    receipts = {(x["image_id"], x["receipt_id"]) for x in project["receipts"]}
    summaries = {
        (x["image_id"], x["receipt_id"]) for x in project["summaries"]
    }
    allowed_classes = {"receipt", "not_receipt", "uncertain", "unavailable"}
    for asset in assets.values():
        if asset.get("classification") not in allowed_classes:
            raise AuditError("Unknown photo classification")
        if not isinstance(asset.get("revision"), str) or not asset["revision"]:
            raise AuditError("Every asset needs a source revision")

    def status(key: str, visited: frozenset[str]) -> tuple[str, str | None]:
        # Each early return names a distinct unresolved state for the operator.
        # pylint: disable=too-many-return-statements,too-many-branches
        if key in visited or key not in assets:
            return "duplicate_unresolved", None
        asset = assets[key]
        if asset["classification"] in {
            "uncertain",
            "unavailable",
        } or not _evidence(asset.get("evidence")):
            return "unresolved", None
        if asset["classification"] == "not_receipt":
            return "not_receipt", None
        entry = entries.get(key)
        if entry is None:
            return "not_imported", None
        if entry.get("revision") != asset["revision"]:
            return "source_changed", None
        if entry.get("upload_state") not in {None, "uploaded"}:
            return "upload_unresolved", None
        duplicate = entry.get("duplicate_of")
        if duplicate is not None:
            if not isinstance(duplicate, str) or not _evidence(
                entry.get("duplicate_evidence")
            ):
                return "duplicate_unresolved", None
            target_status, target_id = status(duplicate, visited | {key})
            if target_status in {"verified", "duplicate_verified"}:
                return "duplicate_verified", target_id
            return "duplicate_unresolved", None
        image_id = entry.get("image_id")
        if not isinstance(image_id, str) or not image_id.strip():
            # A recorded attempt must be reconciled even without a returned ID.
            return "upload_unresolved", None
        ids = entry.get("receipt_ids")
        if (
            not isinstance(ids, list)
            or not ids
            or any(not _identity_value(i, "receipt_id") for i in ids)
        ):
            return "imported_needs_review", image_id
        wanted = {(image_id, i) for i in ids}
        actual = {k for k in receipts if k[0] == image_id}
        verification = entry.get("verification") or {}
        if not isinstance(verification, dict):
            return "imported_needs_review", image_id
        checks = (
            image_id in images,
            len(ids) == len(set(ids)),
            actual == wanted,
            wanted <= summaries,
            verification.get("result") == "passed",
            verification.get("revision") == asset["revision"],
            verification.get("receipt_ids") == ids,
            verification.get("project_fingerprint")
            == receipt_fingerprint(project, image_id, ids),
            _evidence(verification.get("evidence")),
        )
        if all(checks):
            return "verified", image_id
        return "imported_needs_review", image_id

    results = []
    for key in assets:
        state, image_id = status(key, frozenset())
        row = {"asset_key": key, "status": state, "image_id": image_id}
        if image_id:
            ids = sorted(k[1] for k in receipts if k[0] == image_id)
            row["project_fingerprint"] = receipt_fingerprint(
                project, image_id, ids
            )
        results.append(row)
    counts = Counter(row["status"] for row in results)
    enumerated = (
        library["scope"] == "full"
        and library["enumeration_complete"]
        and expected is not None
        and expected == len(assets)
    )
    processed = all(
        row["status"] in {"not_receipt", "verified", "duplicate_verified"}
        for row in results
    )
    return {
        "schema_version": 1,
        "environment": "dev",
        "expected_assets": expected,
        "observed_assets": len(assets),
        "library_enumeration_complete": enumerated,
        "all_receipts_processed": enumerated and processed,
        "counts": dict(counts),
        "distinct_verified_images": len(
            {
                row["image_id"]
                for row in results
                if row["status"] in {"verified", "duplicate_verified"}
            }
        ),
        "cleanup_authorized": False,
        "project_integrity": integrity,
        "assets": results,
    }


def main() -> int:
    """Write a new private snapshot or coverage report."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    snapshot = sub.add_parser(
        "snapshot", help="Read the complete dev inventory"
    )
    snapshot.add_argument("--output", type=Path, required=True)
    report = sub.add_parser(
        "report", help="Reconcile reviewed source manifests"
    )
    for name in ("library", "ledger", "project", "output"):
        report.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "snapshot":
            result = snapshot_project()
        else:
            result = coverage_report(
                load_json(args.library),
                load_json(args.ledger),
                load_json(args.project),
            )
        write_new_json(args.output, result)
        print(f"Wrote {args.command}: {args.output}")
        return 0
    except (AuditError, OSError, ValueError) as exc:
        print(f"Audit failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
