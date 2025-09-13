#!/usr/bin/env python3
import argparse
import json
import os
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Dict, Iterable, List, Optional, Set, Tuple

from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data.shared_exceptions import EntityNotFoundError
from receipt_dynamo.entities.receipt_line import ReceiptLine
from receipt_dynamo.entities.receipt_metadata import ReceiptMetadata
from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_dynamo.constants import ValidationMethod, MerchantValidationStatus

# Shared normalization utilities
from receipt_label.merchant_validation.normalize import (
    build_full_address_from_lines,
    build_full_address_from_words,
    normalize_phone,
    normalize_url,
)


# -----------------------------------------------------------------------------
# Inputs and environment
# -----------------------------------------------------------------------------
ROOT = Path(__file__).parent
# Ensure local package import works when not installed in editable mode
sys.path.insert(0, str(ROOT / "receipt_label"))
WORDS_EXPORT = ROOT / "dev.words.ndjson"
LINES_EXPORT = ROOT / "dev.lines.ndjson"

env = load_env()
DDB_TABLE = env.get("dynamodb_table_name")


# -----------------------------------------------------------------------------
# IO helpers
# -----------------------------------------------------------------------------


def _load_words_lines(
    client: DynamoClient,
) -> Tuple[List[ReceiptWord], List[ReceiptLine]]:
    if not WORDS_EXPORT.exists() or not LINES_EXPORT.exists():
        words, _ = client.list_receipt_words()
        lines, _ = client.list_receipt_lines()
        if WORDS_EXPORT.exists():
            WORDS_EXPORT.unlink()
        with open(WORDS_EXPORT, "w", encoding="utf-8") as wf:
            for w in words:
                wf.write(json.dumps(dict(w)) + "\n")
        if LINES_EXPORT.exists():
            LINES_EXPORT.unlink()
        with open(LINES_EXPORT, "w", encoding="utf-8") as lf:
            for ln in lines:
                lf.write(json.dumps(dict(ln)) + "\n")
        return words, lines

    words: List[ReceiptWord] = []
    lines: List[ReceiptLine] = []
    with open(WORDS_EXPORT, "r", encoding="utf-8") as wf:
        for line in wf:
            words.append(ReceiptWord(**json.loads(line)))
    with open(LINES_EXPORT, "r", encoding="utf-8") as lf:
        for line in lf:
            lines.append(ReceiptLine(**json.loads(line)))
    return words, lines


# -----------------------------------------------------------------------------
# Clustering by normalized anchors
# -----------------------------------------------------------------------------
ReceiptKey = Tuple[str, int]


def _addresses_by_receipt(
    words: List[ReceiptWord],
    lines: List[ReceiptLine],
) -> Dict[ReceiptKey, str]:
    # Prefer address values from anchor words if present; otherwise build from lines
    words_by_receipt: Dict[ReceiptKey, List[ReceiptWord]] = defaultdict(list)
    lines_by_receipt: Dict[ReceiptKey, List[ReceiptLine]] = defaultdict(list)

    for w in words:
        rk = (str(w.image_id), int(w.receipt_id))
        words_by_receipt[rk].append(w)
    for ln in lines:
        rk = (str(ln.image_id), int(ln.receipt_id))
        lines_by_receipt[rk].append(ln)

    results: Dict[ReceiptKey, str] = {}
    for rk, wlist in words_by_receipt.items():
        addr = build_full_address_from_words(wlist)
        if not addr:
            llist = sorted(
                lines_by_receipt.get(rk, []), key=lambda l: int(l.line_id)
            )
            addr = build_full_address_from_lines(llist)
        results[rk] = addr
    # Include any receipts that only have lines present
    for rk, llist in lines_by_receipt.items():
        if rk not in results:
            results[rk] = build_full_address_from_lines(
                sorted(llist, key=lambda l: int(l.line_id))
            )
    return results


def _phones_by_receipt(words: List[ReceiptWord]) -> Dict[ReceiptKey, Set[str]]:
    results: Dict[ReceiptKey, Set[str]] = defaultdict(set)
    for w in words:
        if not w.extracted_data or w.extracted_data.get("type") != "phone":
            continue
        rk = (str(w.image_id), int(w.receipt_id))
        digits = normalize_phone(
            str(w.extracted_data.get("value") or w.text or "")
        )
        if digits:
            results[rk].add(digits)
    return results


def _urls_by_receipt(words: List[ReceiptWord]) -> Dict[ReceiptKey, Set[str]]:
    results: Dict[ReceiptKey, Set[str]] = defaultdict(set)
    for w in words:
        if not w.extracted_data or w.extracted_data.get("type") != "url":
            continue
        rk = (str(w.image_id), int(w.receipt_id))
        u = normalize_url(str(w.extracted_data.get("value") or w.text or ""))
        if u:
            results[rk].add(u)
    return results


# -----------------------------------------------------------------------------
# Metadata selection within clusters
# -----------------------------------------------------------------------------


def _fetch_existing_metadata_map(
    client: DynamoClient, indices: Iterable[ReceiptKey]
) -> Dict[ReceiptKey, ReceiptMetadata]:
    index_list = list({(img, rec) for img, rec in indices})
    if not index_list:
        return {}
    # Batch to avoid per-receipt fetch cost
    metadatas = client.get_receipt_metadatas_by_indices(index_list)
    return {(m.image_id, int(m.receipt_id)): m for m in metadatas}


def _choose_canonical_metadata(
    metas: List[ReceiptMetadata],
) -> Optional[ReceiptMetadata]:
    if not metas:
        return None

    # Heuristic: prefer MATCHED, then canonical fields, then matched_fields size
    def score(m: ReceiptMetadata) -> Tuple[int, int, int, str]:
        is_matched = int(
            (m.validation_status or "").upper()
            == MerchantValidationStatus.MATCHED.value
        )
        has_canonical = int(
            bool(
                m.canonical_place_id
                or m.canonical_address
                or m.canonical_phone_number
            )
        )
        matched_count = len(m.matched_fields or [])
        return (
            is_matched,
            has_canonical,
            matched_count,
            m.merchant_name or "",
        )

    return sorted(metas, key=score, reverse=True)[0]


def _build_proposed_from_canonical(
    canonical: ReceiptMetadata, target: ReceiptKey, matched_reasons: List[str]
) -> ReceiptMetadata:
    image_id, receipt_id = target
    return ReceiptMetadata(
        image_id=str(image_id),
        receipt_id=int(receipt_id),
        place_id=canonical.canonical_place_id or canonical.place_id or "",
        merchant_name=canonical.canonical_merchant_name
        or canonical.merchant_name
        or "",
        matched_fields=list(
            set([*(canonical.matched_fields or []), *matched_reasons])
        ),
        timestamp=datetime.now(timezone.utc),
        merchant_category=canonical.merchant_category or "",
        address=canonical.canonical_address or canonical.address or "",
        phone_number=canonical.canonical_phone_number
        or canonical.phone_number
        or "",
        validated_by=ValidationMethod.INFERENCE.value,
        reasoning=(
            "Derived from cluster neighbor with existing metadata; "
            + ", ".join(sorted(set(matched_reasons)))
        ),
        canonical_place_id=canonical.canonical_place_id
        or canonical.place_id
        or "",
        canonical_merchant_name=canonical.canonical_merchant_name
        or canonical.merchant_name
        or "",
        canonical_address=canonical.canonical_address
        or canonical.address
        or "",
        canonical_phone_number=canonical.canonical_phone_number
        or canonical.phone_number
        or "",
        validation_status="",
    )


# -----------------------------------------------------------------------------
# Main workflow
# -----------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Find similar receipts by clustering normalized address/phone/url and "
            "propose ReceiptMetadata for receipts lacking metadata."
        )
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="If set, write proposed ReceiptMetadata records to DynamoDB (batch).",
    )
    parser.add_argument(
        "--max-write",
        type=int,
        default=0,
        help="Optional cap on number of proposed records to write (0 means no cap).",
    )
    args = parser.parse_args()

    if not DDB_TABLE:
        raise ValueError("No dynamodb_table_name configured in environment")

    client = DynamoClient(DDB_TABLE)

    words, lines = _load_words_lines(client)

    # Compute normalized anchors
    addr_by_receipt = _addresses_by_receipt(words, lines)
    phones_by_receipt = _phones_by_receipt(words)
    urls_by_receipt = _urls_by_receipt(words)

    # Build clusters keyed by address and url
    address_clusters: Dict[str, Set[ReceiptKey]] = defaultdict(set)
    for rk, addr in addr_by_receipt.items():
        if addr and len(addr.split()) >= 3:
            address_clusters[addr].add(rk)

    url_clusters: Dict[str, Set[ReceiptKey]] = defaultdict(set)
    for rk, urls in urls_by_receipt.items():
        for u in urls:
            if u:
                url_clusters[u].add(rk)

    # Identify receipts missing metadata
    all_receipts: Set[ReceiptKey] = set(addr_by_receipt.keys())
    existing_map = _fetch_existing_metadata_map(client, all_receipts)

    proposed: List[ReceiptMetadata] = []
    selection_log: List[Dict] = []

    # Address-based propagation
    for addr, rks in address_clusters.items():
        cluster_list = sorted(list(rks))
        have = [existing_map[rk] for rk in cluster_list if rk in existing_map]
        missing = [rk for rk in cluster_list if rk not in existing_map]
        if not have or not missing:
            continue
        canonical = _choose_canonical_metadata(have)
        if not canonical:
            continue
        for rk in missing:
            reasons = ["address"]
            if any(
                p in (phones_by_receipt.get(rk) or set())
                for p in (
                    canonical.phone_number,
                    canonical.canonical_phone_number,
                )
            ):
                reasons.append("phone")
            proposed.append(
                _build_proposed_from_canonical(canonical, rk, reasons)
            )
        selection_log.append(
            {
                "cluster_type": "address",
                "key": addr,
                "receipts": cluster_list,
                "canonical_source": {
                    "image_id": canonical.image_id,
                    "receipt_id": int(canonical.receipt_id),
                    "merchant_name": canonical.merchant_name,
                },
                "proposed_count": len(missing),
            }
        )

    # URL-based propagation (only for those still missing)
    existing_after_addr = _fetch_existing_metadata_map(
        client, [rk for rk in all_receipts]
    )
    missing_now = [
        rk
        for rk in all_receipts
        if rk not in existing_after_addr
        and rk not in {(p.image_id, int(p.receipt_id)) for p in proposed}
    ]

    for url, rks in url_clusters.items():
        cluster_list = sorted(list(rks))
        have = [
            existing_after_addr[rk]
            for rk in cluster_list
            if rk in existing_after_addr
        ]
        # exclude those already proposed
        missing = [rk for rk in cluster_list if rk in missing_now]
        if not have or not missing:
            continue
        canonical = _choose_canonical_metadata(have)
        if not canonical:
            continue
        for rk in missing:
            proposed.append(
                _build_proposed_from_canonical(canonical, rk, ["url"])
            )
        selection_log.append(
            {
                "cluster_type": "url",
                "key": url,
                "receipts": cluster_list,
                "canonical_source": {
                    "image_id": canonical.image_id,
                    "receipt_id": int(canonical.receipt_id),
                    "merchant_name": canonical.merchant_name,
                },
                "proposed_count": len(missing),
            }
        )

    # Deduplicate proposed by (image_id, receipt_id)
    deduped: Dict[ReceiptKey, ReceiptMetadata] = {}
    for p in proposed:
        key = (p.image_id, int(p.receipt_id))
        if key not in deduped:
            deduped[key] = p
        else:
            existing = deduped[key]
            # merge matched_fields and reasoning
            existing.matched_fields = sorted(
                list(
                    set(
                        (existing.matched_fields or [])
                        + (p.matched_fields or [])
                    )
                )
            )
            if p.reasoning and p.reasoning not in (existing.reasoning or ""):
                existing.reasoning = (
                    (existing.reasoning + "; " + p.reasoning)
                    if existing.reasoning
                    else p.reasoning
                )

    # Serialize entities to plain JSON objects
    def _serialize_entity(m: ReceiptMetadata) -> Dict:
        return {
            "image_id": m.image_id,
            "receipt_id": int(m.receipt_id),
            "place_id": m.place_id,
            "merchant_name": m.merchant_name,
            "merchant_category": m.merchant_category,
            "address": m.address,
            "phone_number": m.phone_number,
            "matched_fields": m.matched_fields,
            "validated_by": m.validated_by,
            "timestamp": m.timestamp.isoformat() if m.timestamp else "",
            "reasoning": m.reasoning,
            "canonical_place_id": m.canonical_place_id,
            "canonical_merchant_name": m.canonical_merchant_name,
            "canonical_address": m.canonical_address,
            "canonical_phone_number": m.canonical_phone_number,
            "validation_status": m.validation_status,
        }

    proposed_items = [_serialize_entity(v) for v in deduped.values()]
    print(json.dumps(proposed_items, indent=2))

    # Optional write (use deduped list)
    if args.write and deduped:
        to_write = list(deduped.values())
        if args.max_write and args.max_write > 0:
            to_write = to_write[: args.max_write]
        client.add_receipt_metadatas(to_write)
        print(
            f"Wrote {len(to_write)} ReceiptMetadata records to DynamoDB table {DDB_TABLE}."
        )


if __name__ == "__main__":
    main()
