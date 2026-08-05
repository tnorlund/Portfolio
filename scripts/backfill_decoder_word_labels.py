"""Backfill deterministic ReceiptWordLabel rows from the reconciled decode.

The band-block decoder knows word for word which words are a product's
name and which word carries its extended price, but nothing wrote that
down: receipts ingested by the Swift worker get header/footer metadata
labels and zero product or financial labels.

This script runs ``receipt_upload.line_items.labels.derive_labels`` over
every receipt with an ITEMS section, gates on the decode reconciling to
the receipt's printed baseline as a full ``match``, and writes the
proposals as ``label_proposed_by="decoder_reconciled"``.

Never clobbers. Every write goes through the singular conditional put
(``add_receipt_word_label``, ``attribute_not_exists(PK)``), and any word
that already carries ANY label -- human, LLM or otherwise -- is skipped
before the write is even attempted.

Usage:
    # dry run over the dev corpus, with a blast-radius report
    python3.12 scripts/backfill_decoder_word_labels.py

    # one receipt, showing every proposed word
    python3.12 scripts/backfill_decoder_word_labels.py \
        --receipt 90af9793-b468-475c-bd31-902a922830d4:1 --verbose

    # write to dev
    python3.12 scripts/backfill_decoder_word_labels.py --apply

    # prod writes need the explicit opt-in
    python3.12 scripts/backfill_decoder_word_labels.py \
        --table ReceiptsTable-d7ff76a --apply --allow-prod
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import boto3
from boto3.dynamodb.types import TypeDeserializer

_REPO_ROOT = Path(__file__).resolve().parent.parent
for _pkg in ("receipt_dynamo", "receipt_upload"):
    _p = _REPO_ROOT / _pkg
    if _p.is_dir():
        sys.path.insert(0, str(_p))

from receipt_dynamo.data.dynamo_client import DynamoClient  # noqa: E402
from receipt_dynamo.data.shared_exceptions import (  # noqa: E402
    EntityAlreadyExistsError,
)
from receipt_dynamo.entities.receipt_word import ReceiptWord  # noqa: E402
from receipt_dynamo.entities.receipt_word_label import (  # noqa: E402
    ReceiptWordLabel,
)

from receipt_upload.line_items.labels import (  # noqa: E402
    DECODER_PROPOSED_BY,
    GATE_OK,
    derive_labels,
)

DEV_TABLE = "ReceiptsTable-dc5be22"
PROD_MARKER = "d7ff76a"
_DES = TypeDeserializer()
_WORD_SK_RE = re.compile(r"^RECEIPT#\d+#LINE#(\d+)#WORD#(\d+)$")
_LABEL_SK_RE = re.compile(r"^RECEIPT#\d+#LINE#(\d+)#WORD#(\d+)#LABEL#")


def _query_all(client, **kwargs):
    while True:
        resp = client.query(**kwargs)
        yield from resp["Items"]
        if "LastEvaluatedKey" not in resp:
            return
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def _deser(item: dict) -> dict:
    return {k: _DES.deserialize(v) for k, v in item.items()}


def _fetch(client, table: str, image_id: str, receipt_id: int):
    """One query per receipt: words, sections, summary, existing labels."""
    words: list[ReceiptWord] = []
    sections: list[dict] = []
    summary: Optional[dict] = None
    labeled: dict[tuple[int, int], set[str]] = {}
    for raw in _query_all(
        client,
        TableName=table,
        KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
        ExpressionAttributeValues={
            ":pk": {"S": f"IMAGE#{image_id}"},
            ":sk": {"S": f"RECEIPT#{receipt_id:05d}"},
        },
    ):
        entity_type = raw.get("TYPE", {}).get("S")
        if entity_type == "RECEIPT_WORD":
            if _WORD_SK_RE.match(raw["SK"]["S"]):
                try:
                    words.append(ReceiptWord.from_item(raw))
                except (ValueError, KeyError):
                    continue
        elif entity_type == "RECEIPT_SECTION":
            sections.append(_deser(raw))
        elif entity_type == "RECEIPT_SUMMARY":
            summary = _deser(raw)
        elif entity_type == "RECEIPT_WORD_LABEL":
            match = _LABEL_SK_RE.match(raw["SK"]["S"])
            if match:
                key = (int(match.group(1)), int(match.group(2)))
                label = raw["SK"]["S"].split("#LABEL#", 1)[1]
                labeled.setdefault(key, set()).add(label)
    return words, sections, summary, labeled


def _items_line_ids(sections: list[dict]) -> set[int]:
    """Line ids of the receipt's ITEMS section, whatever its status.

    Worker-ingested receipts carry PENDING sections (model_source
    ``swift-worker-v1``); older ones carry VALID. The section's status is
    not the trust signal here -- the arithmetic gate is.
    """
    for section in sections:
        if section.get("section_type") == "ITEMS":
            return {int(x) for x in (section.get("line_ids") or [])}
    return set()


def _scan_targets(client, table: str) -> list[tuple[str, int]]:
    """Every receipt with an ITEMS section."""
    targets: set[tuple[str, int]] = set()
    kwargs: dict[str, Any] = dict(
        TableName=table,
        IndexName="GSITYPE",
        KeyConditionExpression="#t = :t",
        ExpressionAttributeNames={"#t": "TYPE"},
        ExpressionAttributeValues={":t": {"S": "RECEIPT_SECTION"}},
    )
    for raw in _query_all(client, **kwargs):
        if raw.get("section_type", {}).get("S") != "ITEMS":
            continue
        image = re.match(r"IMAGE#(.+)", raw["PK"]["S"])
        receipt = re.match(r"RECEIPT#(\d+)#", raw["SK"]["S"])
        if image and receipt:
            targets.add((image.group(1), int(receipt.group(1))))
    return sorted(targets)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--table", default=DEV_TABLE)
    parser.add_argument(
        "--apply", action="store_true", help="write labels (default dry-run)"
    )
    parser.add_argument(
        "--allow-prod",
        action="store_true",
        help="required alongside --apply to write to the prod table",
    )
    parser.add_argument("--receipt", help="IMAGE_ID:RID single-receipt mode")
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--require-proven",
        action="store_true",
        help="tighten the gate to the PROVEN tier (bank-amount agreement)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="print every proposed word label",
    )
    parser.add_argument("--json-out", help="write per-receipt detail as JSONL")
    args = parser.parse_args()

    is_prod = PROD_MARKER in args.table
    if is_prod and args.apply and not args.allow_prod:
        sys.exit(
            "REFUSED: writing to the prod table requires --allow-prod. "
            "Dry runs against prod are always allowed."
        )

    client = boto3.client("dynamodb", region_name="us-east-1")
    dynamo = DynamoClient(args.table) if args.apply else None

    if args.receipt:
        image_id, rid = args.receipt.split(":")
        targets = [(image_id, int(rid))]
    else:
        targets = _scan_targets(client, args.table)
        if args.limit:
            targets = targets[: args.limit]

    now = datetime.now(timezone.utc).isoformat()
    gates: Counter = Counter()
    minted: Counter = Counter()
    collided: Counter = Counter()
    written: Counter = Counter()
    agreement: Counter = Counter()
    disagreements: Counter = Counter()
    receipts_with_labels = 0
    out_handle = open(args.json_out, "w") if args.json_out else None

    for index, (image_id, receipt_id) in enumerate(targets):
        words, sections, summary, labeled = _fetch(
            client, args.table, image_id, receipt_id
        )
        result = derive_labels(
            words,
            _items_line_ids(sections),
            summary,
            require_proven=args.require_proven,
        )
        gates[result.gate] += 1
        if result.gate != GATE_OK:
            if out_handle:
                out_handle.write(
                    json.dumps(
                        {
                            "image_id": image_id,
                            "receipt_id": receipt_id,
                            "gate": result.gate,
                            "reconciliation_status": (
                                result.reconciliation_status
                            ),
                        }
                    )
                    + "\n"
                )
            continue

        fresh = [p for p in result.labels if p.word_key not in labeled]
        for proposal in result.labels:
            existing = labeled.get(proposal.word_key)
            if existing is None:
                minted[proposal.label] += 1
                continue
            collided[proposal.label] += 1
            # The collisions are free validation: where the corpus
            # already has an opinion about a word, does the arithmetic
            # derivation agree with it?
            agreement[
                "agree" if proposal.label in existing else "disagree"
            ] += 1
            if proposal.label not in existing:
                disagreements[
                    (proposal.label, "|".join(sorted(existing)))
                ] += 1
        if fresh:
            receipts_with_labels += 1

        if args.verbose or args.receipt:
            print(
                f"\n{image_id}:{receipt_id}  recon="
                f"{result.reconciliation_status} "
                f"agree={result.baseline_figures_agreeing} "
                f"items={result.item_count} "
                f"sum={result.item_sum} baseline={result.baseline}"
            )
            for proposal in result.labels:
                flag = (
                    "SKIP(existing label)"
                    if proposal.word_key in labeled
                    else "MINT"
                )
                print(
                    f"  {flag:20s} L{proposal.line_id:05d}"
                    f"W{proposal.word_id:05d} {proposal.label:13s} "
                    f"{proposal.text!r}"
                )

        if out_handle:
            out_handle.write(
                json.dumps(
                    {
                        "image_id": image_id,
                        "receipt_id": receipt_id,
                        "gate": result.gate,
                        "reconciliation_status": result.reconciliation_status,
                        "baseline_figures_agreeing": (
                            result.baseline_figures_agreeing
                        ),
                        "labels": [
                            {
                                "line_id": p.line_id,
                                "word_id": p.word_id,
                                "label": p.label,
                                "text": p.text,
                                "existing": sorted(
                                    labeled.get(p.word_key, ())
                                ),
                            }
                            for p in result.labels
                        ],
                    }
                )
                + "\n"
            )

        if args.apply and dynamo is not None:
            for proposal in fresh:
                label = ReceiptWordLabel(
                    image_id=image_id,
                    receipt_id=receipt_id,
                    line_id=proposal.line_id,
                    word_id=proposal.word_id,
                    label=proposal.label,
                    reasoning=proposal.reasoning,
                    timestamp_added=now,
                    validation_status="PENDING",
                    label_proposed_by=DECODER_PROPOSED_BY,
                )
                try:
                    dynamo.add_receipt_word_label(label)
                    written[proposal.label] += 1
                except EntityAlreadyExistsError:
                    collided[proposal.label] += 1

        if (index + 1) % 100 == 0:
            print(f"  {index + 1}/{len(targets)}", file=sys.stderr, flush=True)

    if out_handle:
        out_handle.close()

    mode = "APPLY" if args.apply else "dry-run"
    print(f"\n[{mode}] table={args.table} receipts_scanned={len(targets)}")
    print(f"  gates: {dict(sorted(gates.items()))}")
    print(f"  receipts producing new labels: {receipts_with_labels}")
    print(
        f"  would mint: {dict(sorted(minted.items()))} "
        f"total={sum(minted.values())}"
    )
    print(
        f"  collisions skipped: {dict(sorted(collided.items()))} "
        f"total={sum(collided.values())}"
    )
    if agreement:
        agree, disagree = agreement["agree"], agreement["disagree"]
        rate = agree / max(1, agree + disagree)
        print(
            f"  collision agreement: {agree} agree / {disagree} disagree "
            f"({rate:.1%})"
        )
        print("  top disagreements (derived -> existing):")
        for (derived, existing), count in disagreements.most_common(15):
            print(f"    {derived:13s} -> {existing:30s} {count}")
    if args.apply:
        print(
            f"  written: {dict(sorted(written.items()))} "
            f"total={sum(written.values())}"
        )


if __name__ == "__main__":
    main()
