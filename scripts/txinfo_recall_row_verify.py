#!/usr/bin/env python3
"""Reconcile verified line candidates with live visual-row semantics.

The line verifier intentionally used sequential OCR context to maximize recall.
This pass adds the missing spatial constraint: a line can only cause a section
relabel when its complete RECEIPT_ROW is a coherent TRANSACTION_INFO example.
It is read-only and emits a review queue for every mixed row.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


ACCEPT = "ACCEPT_TXINFO_ROW"
REJECT = "KEEP_SOURCE_ROW"
REVIEW = "REVIEW_MIXED_ROW"

NON_DIRECT_SIGNALS = {"barcode-register-stamp"}
SPATIAL_PAYMENT_OWNER_RE = re.compile(
    r"(?:^|\b)(?:"
    r"tsi|ts1|isi|tvr|iad|aid|arc|atc|cvm|tc|ac|"
    r"mid|merchant\s+id|auth(?:orization)?|approval|appr(?:oval)?|"
    r"batch|card|pan|visa|mastercard|amex|debit|credit|"
    r"entry\s+method|cryptogram|payment\s+(?:amount|id)|"
    r"total\s+(?:transaction\s+)?amount|resp(?:onse)?\s+code|"
    r"application\s+(?:id|label)"
    r")(?:\b|\s*[:#])",
    re.IGNORECASE,
)
SPATIAL_TXINFO_OWNER_RE = re.compile(
    r"(?:"
    r"\b(?:transaction|txn|order|invoice|ticket|receipt|confirmation|"
    r"reference|trace|rrn|stan|sequence|seq|jrnl|journal|terminal|"
    r"device|station|till|register|cashier|clerk|server|employee|"
    r"operator|associate|agent|table|guest|seat|party|member|membership|"
    r"loyalty|customer)\b|"
    r"\b(?:tid|ptid)\b|"
    r"\b(?:ref|inv|reg|term|check|shift)\s*(?:#|:|no\.?|number)"
    r")",
    re.IGNORECASE,
)
STRONG_PAYMENT_PAYLOAD_RE = re.compile(
    r"(?:^|\b)(?:"
    r"tsi|ts1|isi|tvr|iad|aid|arc|atc|cvm|tc|ac|"
    r"card|pan|visa|mastercard|amex|debit|credit|"
    r"entry\s+method|cryptogram|purchase|cashback|"
    r"payment\s+amount|total|amount|change|balance|"
    r"account\s+type|transaction\s+type|contactless|mobile"
    r")(?:\b|\s*[:#])",
    re.IGNORECASE,
)
COMPACT_IDENTIFIER_RE = re.compile(
    r"(?:"
    r"\b(?:transaction|txn|order|invoice|ticket|receipt|confirmation|"
    r"reference|trace|rrn|stan|sequence|seq|jrnl|journal|terminal|"
    r"device|station|till)\b|"
    r"\b(?:tid|ptid)\b|"
    r"\b(?:ref|inv|term|check)\s*(?:#|:|no\.?|number)"
    r")",
    re.IGNORECASE,
)
NON_METADATA_TRANSACTION_RE = re.compile(
    r"^\s*(?:items?\s+in\s+transaction|total\s+transaction\s+amount|"
    r"transaction\s+type)\b",
    re.IGNORECASE,
)


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()


def row_key(image_id: str, receipt_id: int, row_id: int) -> str:
    return f"{image_id}:{receipt_id}:{row_id}"


def direct_signals(candidate: dict[str, Any]) -> list[str]:
    return sorted(
        signal
        for signal in candidate.get("signals", [])
        if not signal.startswith("paired-") and signal not in NON_DIRECT_SIGNALS
    )


def neutral_value(text: str) -> bool:
    """Return whether a noncandidate sibling is plausibly a field value."""
    value = text.strip()
    if not value or len(value) > 64:
        return False
    if re.search(r"\d|[*#]", value):
        return re.fullmatch(r"[\w$#* .,:/+'-]+", value, re.UNICODE) is not None
    return re.fullmatch(r"[A-Z]{2,20}", value) is not None


def default_decision(row: dict[str, Any]) -> tuple[str, str, str]:
    """Return decision, family, and rationale before manual adjudication."""
    row_text = " | ".join(line["text"] for line in row["row_lines"])
    candidates = row["candidates"]
    all_lines_are_candidates = all(
        line["is_final_candidate"] for line in row["row_lines"]
    )
    has_direct_candidate = any(direct_signals(item) for item in candidates)
    has_payment_owner = SPATIAL_PAYMENT_OWNER_RE.search(row_text) is not None
    has_txinfo_owner = SPATIAL_TXINFO_OWNER_RE.search(row_text) is not None
    has_strong_payment_payload = (
        STRONG_PAYMENT_PAYLOAD_RE.search(row_text) is not None
    )
    has_compact_identifier = COMPACT_IDENTIFIER_RE.search(row_text) is not None
    noncandidate_lines = [
        line["text"] for line in row["row_lines"] if not line["is_final_candidate"]
    ]

    if candidates and all(
        NON_METADATA_TRANSACTION_RE.search(str(item["line_text"]))
        for item in candidates
    ):
        return (
            REJECT,
            "non-metadata-transaction-phrase",
            "transaction item counts and payment headings are not transaction identifiers",
        )

    if (
        not has_direct_candidate
        and has_payment_owner
        and not has_txinfo_owner
    ):
        return (
            REJECT,
            "spatial-payment-owner",
            "paired-only candidate is visually owned by an explicit payment/EMV field",
        )
    if all_lines_are_candidates:
        return (
            ACCEPT,
            "all-lines-verified",
            "every line in the visual row independently passed line verification",
        )
    if (
        has_direct_candidate
        and has_compact_identifier
        and len(row["row_lines"]) <= 5
        and not has_strong_payment_payload
    ):
        return (
            ACCEPT,
            "compact-identifier-metadata",
            "a compact identifier row gives transaction/terminal identity precedence over payment configuration",
        )
    if has_direct_candidate and has_strong_payment_payload:
        return (
            REJECT,
            "mixed-payment-payload",
            "moving the complete row would absorb card, EMV, tender, or amount payload",
        )
    if (
        has_direct_candidate
        and not has_payment_owner
        and not has_strong_payment_payload
        and (
            len(candidates) > len(noncandidate_lines)
            or (
                len(row["row_lines"]) <= 3
                and all(neutral_value(text) for text in noncandidate_lines)
            )
        )
    ):
        return (
            ACCEPT,
            "txinfo-with-neutral-siblings",
            "direct evidence dominates the row and every remaining sibling is value-like",
        )
    if has_txinfo_owner and has_payment_owner:
        return (
            REVIEW,
            "mixed-txinfo-payment",
            "the visual row contains both transaction-info and payment fields",
        )
    if has_direct_candidate:
        return (
            REVIEW,
            "direct-candidate-with-payment-siblings",
            "direct evidence survives, but sibling semantics require row-level review",
        )
    return (
        REVIEW,
        "context-only-with-neutral-siblings",
        "context-paired evidence has no unambiguous spatial owner",
    )


def load_adjudications(path: Path | None) -> dict[str, dict[str, str]]:
    if path is None:
        return {}
    payload = json.loads(path.read_text())
    if not isinstance(payload, list):
        raise ValueError("row adjudications must be a JSON list")
    result: dict[str, dict[str, str]] = {}
    for item in payload:
        key = str(item["row_key"])
        if key in result:
            raise ValueError(f"duplicate row adjudication: {key}")
        decision = str(item["decision"])
        if decision not in {ACCEPT, REJECT}:
            raise ValueError(f"invalid adjudication decision for {key}: {decision}")
        reason = str(item.get("reason", "")).strip()
        if not reason:
            raise ValueError(f"adjudication reason is required for {key}")
        result[key] = {"decision": decision, "reason": reason}
    return result


def build_rows(
    positives: list[dict[str, Any]], plan: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    candidates_by_id = {str(item["id"]): item for item in positives}
    if len(candidates_by_id) != len(positives):
        raise ValueError("final positives contain duplicate canonical IDs")

    rows: dict[str, dict[str, Any]] = {}
    seen_candidate_ids: set[str] = set()
    for receipt in plan["per_receipt"].values():
        image_id = str(receipt["image_id"])
        receipt_id = int(receipt["receipt_id"])
        for expansion in receipt["row_expansions"]:
            key = row_key(image_id, receipt_id, int(expansion["row_id"]))
            if key in rows:
                continue
            candidate_ids = [
                f"{image_id}:{receipt_id}:{int(line['line_id'])}"
                for line in expansion["row_lines"]
                if line["is_final_candidate"]
            ]
            unknown = sorted(set(candidate_ids) - set(candidates_by_id))
            if unknown:
                raise ValueError(f"row {key} references unknown candidates: {unknown}")
            seen_candidate_ids.update(candidate_ids)
            rows[key] = {
                "row_key": key,
                "image_id": image_id,
                "receipt_id": receipt_id,
                "row_id": int(expansion["row_id"]),
                "merchant": receipt.get("merchant", ""),
                "source": expansion["source"],
                "row_lines": expansion["row_lines"],
                "candidate_ids": candidate_ids,
                "candidates": [candidates_by_id[item] for item in candidate_ids],
            }

    missing = sorted(set(candidates_by_id) - seen_candidate_ids)
    if missing:
        raise ValueError(f"final candidates missing from visual rows: {missing[:10]}")
    return rows


def write_report(summary: dict[str, Any], path: Path) -> None:
    decisions = summary["decisions"]
    text = f"""# TRANSACTION_INFO spatial-row verification

**Mode:** read-only verification; no DynamoDB writes performed.

| Check | Result |
|---|---:|
| Provisional line positives | {summary['provisional_line_positives']} |
| Unique visual rows | {summary['visual_rows']} |
| Accepted rows | {decisions.get(ACCEPT, 0)} |
| Rows kept in their source section | {decisions.get(REJECT, 0)} |
| Rows awaiting manual review | {decisions.get(REVIEW, 0)} |
| Accepted triggering lines | {summary['accepted_triggering_lines']} |
| Spatially refuted triggering lines | {summary['spatially_refuted_lines']} |

The line-level pass maximized recall with sequential OCR context. This pass requires
the complete visual row to be semantically coherent before it can enter a section
mutation plan. Mixed rows remain blocked until they receive an explicit adjudication.
"""
    path.write_text(text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--final-positives", type=Path, required=True)
    parser.add_argument("--correction-plan", type=Path, required=True)
    parser.add_argument("--adjudications", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    positives = json.loads(args.final_positives.read_text())
    plan = json.loads(args.correction_plan.read_text())
    adjudications = load_adjudications(args.adjudications)
    rows = build_rows(positives, plan)

    unknown_adjudications = sorted(set(adjudications) - set(rows))
    if unknown_adjudications:
        raise ValueError(
            f"adjudications reference unknown rows: {unknown_adjudications[:10]}"
        )

    verified_rows = []
    for key, row in sorted(rows.items()):
        decision, family, rationale = default_decision(row)
        adjudication = adjudications.get(key)
        if adjudication:
            decision = adjudication["decision"]
            rationale = adjudication["reason"]
            decision_source = "manual-adjudication"
        else:
            decision_source = "deterministic-row-rule"
        verified_rows.append(
            {
                **row,
                "candidate_direct_signals": {
                    item["id"]: direct_signals(item) for item in row["candidates"]
                },
                "family": family,
                "decision": decision,
                "decision_source": decision_source,
                "rationale": rationale,
            }
        )

    accepted_ids = {
        item
        for row in verified_rows
        if row["decision"] == ACCEPT
        for item in row["candidate_ids"]
    }
    rejected_ids = {
        item
        for row in verified_rows
        if row["decision"] == REJECT
        for item in row["candidate_ids"]
    }
    review_rows = [row for row in verified_rows if row["decision"] == REVIEW]
    accepted = [item for item in positives if item["id"] in accepted_ids]
    rejected = [item for item in positives if item["id"] in rejected_ids]
    decision_counts = Counter(row["decision"] for row in verified_rows)
    family_counts = Counter(row["family"] for row in verified_rows)
    summary = {
        "mode": "read-only-spatial-row-verification",
        "provisional_line_positives": len(positives),
        "visual_rows": len(verified_rows),
        "decisions": dict(sorted(decision_counts.items())),
        "families": dict(sorted(family_counts.items())),
        "manual_adjudications": len(adjudications),
        "accepted_triggering_lines": len(accepted),
        "spatially_refuted_lines": len(rejected),
        "review_triggering_lines": sum(
            len(row["candidate_ids"]) for row in review_rows
        ),
        "complete": not review_rows,
    }
    payload = {
        "summary": summary,
        "rows": verified_rows,
        "input_fingerprints": {
            "final_positives_sha256": canonical_hash(positives),
            "correction_plan_sha256": canonical_hash(plan),
            "adjudications_sha256": canonical_hash(adjudications),
        },
    }

    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "ROW_VERIFICATION.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n"
    )
    (args.output / "REVIEW_QUEUE.json").write_text(
        json.dumps(review_rows, indent=2, sort_keys=True) + "\n"
    )
    (args.output / "SPATIALLY_REFUTED_LINES.json").write_text(
        json.dumps(rejected, indent=2, sort_keys=True) + "\n"
    )
    (args.output / "FILTERED_POSITIVES.json").write_text(
        json.dumps(accepted, indent=2, sort_keys=True) + "\n"
    )
    (args.output / "ROW_VERIFICATION_SUMMARY.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    write_report(summary, args.output / "ROW_VERIFICATION.md")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
