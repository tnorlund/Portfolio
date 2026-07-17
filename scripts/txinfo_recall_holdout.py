#!/usr/bin/env python3
"""Freeze a fresh metadata-only holdout before rebuilding section priors."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "receipt_dynamo"))

from receipt_dynamo import DynamoClient  # noqa: E402
from txinfo_recall_execute import batch_get_items  # noqa: E402


DEV_TABLE = "ReceiptsTable-dc5be22"
PROD_TABLE = "ReceiptsTable-d7ff76a"
TXINFO = "TRANSACTION_INFO"
SALT = "txinfo-aware-holdout-2026-07-17-v1"


def spent_keys(payload: dict[str, Any]) -> set[tuple[str, int]]:
    keys = {
        (str(item["image_id"]), int(item["receipt_id"]))
        for item in payload.get("mapping_opaque_to_source", {}).values()
    }
    keys.update(
        (str(item["image_id"]), int(item["receipt_id"]))
        for item in payload.get("excluded_pinned_goldens", [])
    )
    return keys


def manifest_keys(payload: list[dict[str, Any]]) -> set[tuple[str, int]]:
    """Return receipt keys from a previously frozen holdout manifest."""

    return {
        (str(item["image_id"]), int(item["receipt_id"])) for item in payload
    }


def rank(key: tuple[str, int], salt: str = SALT) -> str:
    return hashlib.sha256(f"{salt}:{key[0]}:{key[1]}".encode()).hexdigest()


def select_stratum(
    candidates: list[dict[str, Any]], count: int
) -> list[dict[str, Any]]:
    ordered = sorted(candidates, key=lambda item: (item["rank"], item["image_id"], item["receipt_id"]))
    selected = []
    deferred = []
    merchants = set()
    images = set()
    for item in ordered:
        if item["image_id"] in images:
            continue
        merchant = item["merchant_key"]
        if merchant and merchant in merchants:
            deferred.append(item)
            continue
        selected.append(item)
        images.add(item["image_id"])
        if merchant:
            merchants.add(merchant)
        if len(selected) == count:
            return selected
    for item in deferred:
        if item["image_id"] in images:
            continue
        selected.append(item)
        images.add(item["image_id"])
        if len(selected) == count:
            return selected
    raise ValueError(f"only {len(selected)} eligible receipts for requested {count}")


def canonical_manifest_hash(items: list[dict[str, Any]]) -> str:
    keys = sorted((str(item["image_id"]), int(item["receipt_id"])) for item in items)
    return hashlib.sha256(
        json.dumps(keys, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def read_all(client: DynamoClient, method_name: str) -> list[Any]:
    items = []
    cursor = None
    method = getattr(client, method_name)
    while True:
        page, cursor = method(last_evaluated_key=cursor)
        items.extend(page)
        if cursor is None:
            return items


def build_candidates(
    sections: list[Any],
    place_names: dict[tuple[str, int], str],
    excluded: set[tuple[str, int]],
    selection_salt: str = SALT,
) -> list[dict[str, Any]]:
    by_receipt: dict[tuple[str, int], list[Any]] = defaultdict(list)
    for section in sections:
        if section.validation_status == "VALID":
            by_receipt[(section.image_id, section.receipt_id)].append(section)

    candidates = []
    for key, receipt_sections in sorted(by_receipt.items()):
        if key in excluded:
            continue
        section_types = sorted(str(section.section_type) for section in receipt_sections)
        row_ids = {
            int(row_id)
            for section in receipt_sections
            for row_id in (section.row_ids or [])
        }
        if len(section_types) < 4 or len(row_ids) < 12:
            continue
        merchant = place_names.get(key, "")
        candidates.append(
            {
                "image_id": key[0],
                "receipt_id": key[1],
                "merchant": merchant,
                "merchant_key": " ".join(merchant.lower().split()),
                "stratum": "with_txinfo" if TXINFO in section_types else "without_txinfo",
                "qa_rows": len(row_ids),
                "section_types": section_types,
                "rank": rank(key, selection_salt),
            }
        )
    return candidates


def write_preregistration(
    selected: list[dict[str, Any]],
    manifest_hash: str,
    training_exclusion_hash: str,
    training_exclusion_count: int,
    prior_exclusion_count: int,
    selection_salt: str,
    path: Path,
) -> None:
    counts = {
        stratum: sum(item["stratum"] == stratum for item in selected)
        for stratum in ("with_txinfo", "without_txinfo")
    }
    rows = sum(item["qa_rows"] for item in selected)
    text = f"""# Pre-registration: TRANSACTION_INFO-aware D2 holdout

Version: 1.0-FROZEN
Date: 2026-07-17
Manifest SHA-256: `{manifest_hash}`

## Cohort

- {len(selected)} dev receipts selected before rebuilding priors or running decoder predictions.
- {counts['with_txinfo']} receipts contain QA-VALID TRANSACTION_INFO; {counts['without_txinfo']} do not.
- {rows} QA-VALID visual rows in the frozen manifest.
- Selection used only receipt keys, merchant identity, VALID section presence, and row counts.
- Deterministic rank salt: `{selection_salt}`; unique merchants are preferred within each stratum.
- {prior_exclusion_count} previously spent receipts are excluded from selection.

## Training boundary

The generated `TRAINING_EXCLUSIONS.json` contains this cohort plus every prior spent
cohort: {training_exclusion_count} receipts with canonical SHA-256
`{training_exclusion_hash}`. It must be passed to
`build_section_order_priors.py --exclude-manifest`, and the rebuilt artifact must record
the same hash. No past or current holdout receipt may contribute a training row or
merchant prior.

## Frozen gates

- P1 overall QA row agreement: at least 0.80.
- P2 ITEMS recall: at least 0.70.
- P3 TRANSACTION_INFO recall: at least 0.70.
- P4 TRANSACTION_INFO precision: at least 0.70.
- P5 deterministic section-assignment and receipt golden tests remain green.

All denominators, unassigned rows, per-type precision/recall, and the confusion matrix
must be reported. This is a held-out QA-agreement study, not an independent blind-human
accuracy study. The cohort becomes spent after its first prediction run.
"""
    path.write_text(text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spent-mapping", type=Path, required=True)
    parser.add_argument(
        "--prior-holdout",
        action="append",
        default=[],
        type=Path,
        help="Previously spent holdout manifest; may be repeated",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--per-stratum", type=int, default=15)
    parser.add_argument("--selection-salt", default=SALT)
    parser.add_argument("--table", default=DEV_TABLE)
    args = parser.parse_args()

    if args.table == PROD_TABLE or args.table != DEV_TABLE:
        raise ValueError(f"holdout builder is dev-only; refusing {args.table!r}")
    excluded = spent_keys(json.loads(args.spent_mapping.read_text()))
    for prior_path in args.prior_holdout:
        excluded.update(
            manifest_keys(json.loads(prior_path.read_text(encoding="utf-8")))
        )
    prior_exclusion_count = len(excluded)
    client = DynamoClient(args.table, "us-east-1")
    sections = read_all(client, "list_receipt_sections")
    valid_keys = {
        (section.image_id, section.receipt_id)
        for section in sections
        if section.validation_status == "VALID"
    }
    place_items = batch_get_items(
        client._client,  # pylint: disable=protected-access
        args.table,
        [
            (
                f"IMAGE#{image_id}",
                f"RECEIPT#{receipt_id:05d}#PLACE",
            )
            for image_id, receipt_id in valid_keys
        ],
    )
    place_names = {
        (
            key[0].removeprefix("IMAGE#"),
            int(key[1].split("#PLACE", 1)[0].removeprefix("RECEIPT#")),
        ): str(item.get("merchant_name", ""))
        for key, item in place_items.items()
    }
    candidates = build_candidates(
        sections, place_names, excluded, args.selection_salt
    )
    by_stratum = defaultdict(list)
    for item in candidates:
        by_stratum[item["stratum"]].append(item)
    selected = []
    for stratum in ("with_txinfo", "without_txinfo"):
        selected.extend(select_stratum(by_stratum[stratum], args.per_stratum))
    selected = sorted(selected, key=lambda item: (item["stratum"], item["rank"]))
    manifest = [
        {key: value for key, value in item.items() if key not in {"merchant_key", "rank"}}
        for item in selected
    ]
    manifest_hash = canonical_manifest_hash(manifest)
    training_exclusions = [
        {"image_id": image_id, "receipt_id": receipt_id}
        for image_id, receipt_id in sorted(
            excluded
            | {
                (str(item["image_id"]), int(item["receipt_id"]))
                for item in manifest
            }
        )
    ]
    training_exclusion_hash = canonical_manifest_hash(training_exclusions)
    summary = {
        "manifest_sha256": manifest_hash,
        "selection_salt": args.selection_salt,
        "receipts": len(manifest),
        "qa_rows": sum(item["qa_rows"] for item in manifest),
        "spent_overlap": len(
            {(item["image_id"], item["receipt_id"]) for item in manifest} & excluded
        ),
        "prior_exclusion_count": prior_exclusion_count,
        "training_exclusion_count": len(training_exclusions),
        "training_exclusion_sha256": training_exclusion_hash,
        "strata": dict(sorted({key: len(value) for key, value in by_stratum.items()}.items())),
        "selected_strata": dict(sorted({
            key: sum(item["stratum"] == key for item in manifest)
            for key in ("with_txinfo", "without_txinfo")
        }.items())),
        "unique_merchants": len({item["merchant_key"] for item in selected if item["merchant_key"]}),
    }
    if summary["spent_overlap"]:
        raise RuntimeError("fresh holdout overlaps the spent holdout")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "HOLDOUT_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    (args.output_dir / "HOLDOUT_SUMMARY.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    (args.output_dir / "TRAINING_EXCLUSIONS.json").write_text(
        json.dumps(training_exclusions, indent=2, sort_keys=True) + "\n"
    )
    write_preregistration(
        manifest,
        manifest_hash,
        training_exclusion_hash,
        len(training_exclusions),
        prior_exclusion_count,
        args.selection_salt,
        args.output_dir / "PREREGISTRATION.md",
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
