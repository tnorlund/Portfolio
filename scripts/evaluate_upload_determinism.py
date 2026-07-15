#!/usr/bin/env python3
"""Read-only, leakage-aware evaluation of the upload resolver on dev.

The DynamoDB table is accepted only from ``DYNAMODB_TABLE_NAME``.  The
command refuses any environment other than dev and verifies both the
``Environment=dev`` and ``Pulumi_Stack=dev`` resource tags before reading.
It never calls a write API.
"""

# pylint: disable=too-many-lines,too-many-locals,too-many-statements
# pylint: disable=wrong-import-position
# pylint: disable=broad-exception-caught

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import random
import sys
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean, median
from typing import Any, Iterable, Mapping, Sequence

import boto3

_ROOT = Path(__file__).resolve().parent.parent
for _package in ("receipt_dynamo", "receipt_chroma", "receipt_upload"):
    sys.path.insert(0, str(_ROOT / _package))

from receipt_chroma.embedding.formatting import build_receipt_rows
from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    OperationError,
)
from receipt_dynamo.entities import (
    ReceiptLabelReconciliation,
    ReceiptWordLabel,
)
from receipt_upload.label_reconciliation import (
    MODEL_SOURCE,
    plan_label_reconciliation,
)
from receipt_upload.section_assignment import (
    RowAssignment,
    assign_row_sections,
    extract_row_features,
    learn_prior,
    load_prior_model,
    normalize_merchant_key,
    sections_from_assignments,
)
from receipt_upload.structured_details import build_receipt_resolved_details

_FOLDS = 5
_BOOTSTRAP_SAMPLES = 1000
_SEED = 20260714
_LOCAL = threading.local()
_AUDIT_FOOTPRINT = {
    "PRODUCT_NAME": {"ITEMS"},
    "QUANTITY": {"ITEMS"},
    "UNIT_PRICE": {"ITEMS"},
    "LINE_TOTAL": {"ITEMS"},
    "SUBTOTAL": {"SUMMARY"},
    "TAX": {"SUMMARY"},
    "GRAND_TOTAL": {"SUMMARY", "TOTAL_LINE"},
    "DISCOUNT": {"ITEMS", "SUMMARY"},
    "CHANGE": {"PAYMENT"},
    "CASH_BACK": {"PAYMENT"},
    "REFUND": {"PAYMENT"},
    "PAYMENT_METHOD": {"PAYMENT"},
}


@dataclass(frozen=True)
class CorpusReceipt:
    """One receipt and all read-only inputs needed by D2-D4."""

    image_id: str
    receipt_id: int
    merchant_name: str | None
    lines: tuple[Any, ...]
    words: tuple[Any, ...]
    labels: tuple[Any, ...]
    rows: tuple[Any, ...]
    gold_sections: tuple[Any, ...]
    labeled_rows: tuple[tuple[Any, str], ...]

    @property
    def key(self) -> tuple[str, int]:
        """Return the stable receipt key."""

        return self.image_id, self.receipt_id


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--environment", default=os.environ.get("DEPLOYMENT_ENVIRONMENT")
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    return parser.parse_args()


def assert_private_destination(path: Path) -> Path:
    """Resolve a sensitive output path and reject the repository tree."""

    resolved = path.expanduser().resolve(strict=False)
    repository = _ROOT.resolve()
    if resolved == repository or repository in resolved.parents:
        raise RuntimeError(
            "Sensitive evaluation output must stay outside the repository"
        )
    return resolved


def _create_private_directories(path: Path) -> None:
    """Create missing destination directories with owner-only permissions."""

    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    for directory in missing:
        directory.chmod(0o700)


def write_private_text(path: Path, value: str) -> Path:
    """Write a sensitive text artifact without following a final symlink."""

    destination = assert_private_destination(path)
    _create_private_directories(destination.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(destination, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            descriptor = -1
            output.write(value)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return destination


def assert_dev_table(environment: str | None, table_name: str | None) -> str:
    """Fail closed unless the table is owned by the Pulumi dev stack."""

    if environment != "dev":
        raise RuntimeError("DEPLOYMENT_ENVIRONMENT must be exactly dev")
    if not table_name:
        raise RuntimeError("DYNAMODB_TABLE_NAME is required")
    dynamodb = boto3.client("dynamodb")
    table = dynamodb.describe_table(TableName=table_name)["Table"]
    tags = {
        item["Key"]: item["Value"]
        for item in dynamodb.list_tags_of_resource(
            ResourceArn=table["TableArn"]
        )["Tags"]
    }
    if tags.get("Environment") != "dev" or tags.get("Pulumi_Stack") != "dev":
        raise RuntimeError(
            "Refusing table without Environment=dev and Pulumi_Stack=dev tags"
        )
    return table_name


def _client(table_name: str) -> DynamoClient:
    client = getattr(_LOCAL, "client", None)
    if client is None or client.table_name != table_name:
        client = DynamoClient(table_name)
        _LOCAL.client = client
    return client


def _all_sections(client: DynamoClient) -> list[Any]:
    sections: list[Any] = []
    cursor = None
    while True:
        batch, cursor = client.list_receipt_sections(
            limit=1000, last_evaluated_key=cursor
        )
        sections.extend(batch)
        if cursor is None:
            return sections


def _merchant_name(
    client: DynamoClient, image_id: str, receipt_id: int
) -> str:
    try:
        merchant = client.get_receipt_place(image_id, receipt_id).merchant_name
        return str(merchant) if merchant else ""
    except EntityNotFoundError:
        return ""
    except OperationError as error:
        if "does not match entity keys" not in str(error):
            raise
        response = client._client.get_item(  # pylint: disable=protected-access
            TableName=client.table_name,
            Key={
                "PK": {"S": f"IMAGE#{image_id}"},
                "SK": {"S": f"RECEIPT#{receipt_id:05d}#PLACE"},
            },
            ProjectionExpression="merchant_name",
            ConsistentRead=True,
        )
        return response.get("Item", {}).get("merchant_name", {}).get("S", "")


def _gold_by_line(
    sections: Sequence[Any],
) -> tuple[dict[int, str], set[int]]:
    candidates: dict[int, set[str]] = defaultdict(set)
    for section in sections:
        if section.validation_status != ValidationStatus.VALID.value:
            continue
        for line_id in section.line_ids:
            candidates[int(line_id)].add(str(section.section_type))
    gold = {
        line_id: next(iter(values))
        for line_id, values in candidates.items()
        if len(values) == 1
    }
    ambiguous = {
        line_id for line_id, values in candidates.items() if len(values) > 1
    }
    return gold, ambiguous


def _majority_row_label(row: Any, gold: Mapping[int, str]) -> str | None:
    votes = Counter(
        gold[line_id] for line_id in row.line_ids if line_id in gold
    )
    if not votes:
        return None
    maximum = max(votes.values())
    leaders = sorted(
        label for label, count in votes.items() if count == maximum
    )
    primary = gold.get(row.row_id)
    return primary if primary in leaders else leaders[0]


def _load_receipt(
    table_name: str,
    key: tuple[str, int],
    sections: Sequence[Any],
) -> CorpusReceipt:
    client = _client(table_name)
    image_id, receipt_id = key
    lines = tuple(client.list_receipt_lines_from_receipt(image_id, receipt_id))
    words = tuple(client.list_receipt_words_from_receipt(image_id, receipt_id))
    labels, cursor = client.list_receipt_word_labels_for_receipt(
        image_id, receipt_id
    )
    if cursor is not None:
        raise RuntimeError(f"unexpected label pagination for {key}")
    rows = tuple(build_receipt_rows(lines, words)) if lines else ()
    gold, _ = _gold_by_line(sections)
    labeled_rows = tuple(
        (feature, label)
        for feature in extract_row_features(rows, lines)
        if (label := _majority_row_label(feature.row, gold)) is not None
    )
    return CorpusReceipt(
        image_id=image_id,
        receipt_id=receipt_id,
        merchant_name=_merchant_name(client, image_id, receipt_id) or None,
        lines=lines,
        words=words,
        labels=tuple(labels),
        rows=rows,
        gold_sections=tuple(sections),
        labeled_rows=labeled_rows,
    )


def _stable_hash(*values: object) -> str:
    return hashlib.sha256("|".join(map(str, values)).encode()).hexdigest()


def assign_folds(receipts: Sequence[CorpusReceipt]) -> dict[str, int]:
    """Assign image groups to deterministic merchant-stratified folds."""

    by_image: dict[str, list[CorpusReceipt]] = defaultdict(list)
    for receipt in receipts:
        by_image[receipt.image_id].append(receipt)
    strata: dict[str, list[str]] = defaultdict(list)
    for image_id, group in by_image.items():
        merchants = sorted(
            normalize_merchant_key(receipt.merchant_name)
            for receipt in group
            if receipt.merchant_name
        )
        strata[merchants[0] if merchants else "<unknown>"].append(image_id)
    folds = {}
    for stratum, image_ids in sorted(strata.items()):
        ordered = sorted(
            image_ids, key=lambda value: _stable_hash(_SEED, value)
        )
        offset = int(_stable_hash(stratum)[:8], 16) % _FOLDS
        for index, image_id in enumerate(ordered):
            folds[image_id] = (index + offset) % _FOLDS
    return folds


def fit_model(receipts: Sequence[CorpusReceipt]) -> dict[str, Any]:
    """Fit the same priors as the production builder on training receipts."""

    labeled = [
        list(receipt.labeled_rows)
        for receipt in receipts
        if receipt.labeled_rows
    ]
    by_merchant: dict[str, list[list[Any]]] = defaultdict(list)
    for receipt in receipts:
        if not receipt.labeled_rows:
            continue
        merchant = normalize_merchant_key(receipt.merchant_name)
        if merchant:
            by_merchant[merchant].append(list(receipt.labeled_rows))
    counts = [len(items) for items in by_merchant.values()]
    cutoff = int(median(counts)) + 1 if counts else 1
    eligible = {
        merchant: items
        for merchant, items in by_merchant.items()
        if len(items) >= cutoff
    }
    return {
        "global": learn_prior(labeled),
        "merchants": {
            merchant: learn_prior(items, include_tokens=False)
            for merchant, items in sorted(eligible.items())
        },
        "evaluation_training": {
            "receipt_count": len(labeled),
            "merchant_prior_min_receipts": cutoff,
        },
    }


def _new_section_metrics() -> dict[str, Any]:
    return {
        "eligible_lines": 0,
        "covered_lines": 0,
        "correct_lines": 0,
        "ambiguous_gold_lines": 0,
        "unlabeled_predicted_lines": 0,
        "eligible_rows": 0,
        "correct_rows": 0,
        "boundary_tp": 0,
        "boundary_fp": 0,
        "boundary_fn": 0,
        "receipts_with_gold": 0,
        "receipts_without_gold": 0,
        "exact_receipts": 0,
        "receipt_scores": [],
        "receipt_line_counts": [],
        "per_section": defaultdict(Counter),
        "confusion": Counter(),
        "confidence": [],
    }


def _score_sections(
    metrics: dict[str, Any],
    receipt: CorpusReceipt,
    assignments: Sequence[RowAssignment],
) -> None:
    gold, ambiguous = _gold_by_line(receipt.gold_sections)
    predicted = {
        line_id: assignment.section_type
        for assignment in assignments
        for line_id in assignment.row.line_ids
    }
    confidence = {
        line_id: assignment.confidence
        for assignment in assignments
        for line_id in assignment.row.line_ids
    }
    metrics["ambiguous_gold_lines"] += len(ambiguous)
    if not gold:
        metrics["receipts_without_gold"] += 1
        return
    metrics["receipts_with_gold"] += 1
    correct = 0
    for line_id, truth in gold.items():
        prediction = predicted.get(line_id)
        metrics["eligible_lines"] += 1
        metrics["per_section"][truth]["support"] += 1
        if prediction is None:
            metrics["per_section"][truth]["fn"] += 1
            metrics["confusion"][(truth, "<MISSING>")] += 1
            continue
        metrics["covered_lines"] += 1
        is_correct = prediction == truth
        metrics["confidence"].append((confidence[line_id], is_correct))
        metrics["confusion"][(truth, prediction)] += 1
        if is_correct:
            correct += 1
            metrics["correct_lines"] += 1
            metrics["per_section"][truth]["tp"] += 1
        else:
            metrics["per_section"][truth]["fn"] += 1
            metrics["per_section"][prediction]["fp"] += 1
    metrics["unlabeled_predicted_lines"] += len(set(predicted) - set(gold))
    support = len(gold)
    metrics["receipt_scores"].append(correct / support)
    metrics["receipt_line_counts"].append((correct, support))
    if correct == support and all(line_id in predicted for line_id in gold):
        metrics["exact_receipts"] += 1

    ordered = list(assignments)
    row_truth = [_majority_row_label(item.row, gold) for item in ordered]
    for assignment, row_label in zip(ordered, row_truth, strict=True):
        if row_label is None:
            continue
        metrics["eligible_rows"] += 1
        metrics["correct_rows"] += int(assignment.section_type == row_label)
    for index in range(1, len(ordered)):
        if row_truth[index - 1] is None or row_truth[index] is None:
            continue
        gold_boundary = row_truth[index - 1] != row_truth[index]
        pred_boundary = (
            ordered[index - 1].section_type != ordered[index].section_type
        )
        metrics["boundary_tp"] += int(gold_boundary and pred_boundary)
        metrics["boundary_fp"] += int(not gold_boundary and pred_boundary)
        metrics["boundary_fn"] += int(gold_boundary and not pred_boundary)


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def _bootstrap_ci(counts: Sequence[tuple[int, int]]) -> list[float] | None:
    if not counts:
        return None
    rng = random.Random(_SEED)
    samples = []
    for _ in range(_BOOTSTRAP_SAMPLES):
        picked = [counts[rng.randrange(len(counts))] for _ in counts]
        samples.append(sum(x for x, _ in picked) / sum(n for _, n in picked))
    samples.sort()
    return [samples[24], samples[974]]


def _finalize_section_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    per_section = {}
    f1_values = []
    for section, counts in sorted(metrics["per_section"].items()):
        precision = _ratio(counts["tp"], counts["tp"] + counts["fp"])
        recall = _ratio(counts["tp"], counts["tp"] + counts["fn"])
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision is not None
            and recall is not None
            and precision + recall
            else 0.0
        )
        if counts["support"]:
            f1_values.append(f1)
        per_section[section] = {
            **dict(counts),
            "precision": precision,
            "recall": recall,
            "f1": f1,
        }
    boundary_precision = _ratio(
        metrics["boundary_tp"],
        metrics["boundary_tp"] + metrics["boundary_fp"],
    )
    boundary_recall = _ratio(
        metrics["boundary_tp"],
        metrics["boundary_tp"] + metrics["boundary_fn"],
    )
    boundary_f1 = (
        2
        * boundary_precision
        * boundary_recall
        / (boundary_precision + boundary_recall)
        if boundary_precision is not None
        and boundary_recall is not None
        and boundary_precision + boundary_recall
        else 0.0
    )
    confidence = metrics["confidence"]
    thresholds = {}
    for threshold in (0.5, 0.7, 0.8, 0.9):
        selected = [
            correct for score, correct in confidence if score >= threshold
        ]
        thresholds[str(threshold)] = {
            "coverage": _ratio(len(selected), len(confidence)),
            "accuracy": _ratio(sum(selected), len(selected)),
            "support": len(selected),
        }
    bins = []
    ece = 0.0
    for index in range(10):
        low, high = index / 10, (index + 1) / 10
        values = [
            (score, correct)
            for score, correct in confidence
            if (low <= score <= high if index == 9 else low <= score < high)
        ]
        if not values:
            continue
        mean_confidence = fmean(score for score, _ in values)
        accuracy = fmean(float(correct) for _, correct in values)
        ece += (
            len(values)
            / max(len(confidence), 1)
            * abs(mean_confidence - accuracy)
        )
        bins.append(
            {
                "low": low,
                "high": high,
                "support": len(values),
                "mean_confidence": mean_confidence,
                "accuracy": accuracy,
            }
        )
    return {
        "receipts_with_gold": metrics["receipts_with_gold"],
        "receipts_without_valid_gold": metrics["receipts_without_gold"],
        "eligible_lines": metrics["eligible_lines"],
        "ambiguous_gold_lines": metrics["ambiguous_gold_lines"],
        "prediction_coverage": _ratio(
            metrics["covered_lines"], metrics["eligible_lines"]
        ),
        "line_micro_accuracy": _ratio(
            metrics["correct_lines"], metrics["eligible_lines"]
        ),
        "line_micro_accuracy_cluster_bootstrap_95_ci": _bootstrap_ci(
            metrics["receipt_line_counts"]
        ),
        "receipt_macro_accuracy": (
            fmean(metrics["receipt_scores"])
            if metrics["receipt_scores"]
            else None
        ),
        "eligible_gold_exact_receipt_rate": _ratio(
            metrics["exact_receipts"], metrics["receipts_with_gold"]
        ),
        "row_accuracy": _ratio(
            metrics["correct_rows"], metrics["eligible_rows"]
        ),
        "boundary_precision": boundary_precision,
        "boundary_recall": boundary_recall,
        "boundary_f1": boundary_f1,
        "macro_f1": fmean(f1_values) if f1_values else None,
        "per_section": per_section,
        "unlabeled_predicted_lines": metrics["unlabeled_predicted_lines"],
        "confidence_brier": (
            fmean(
                (score - float(correct)) ** 2 for score, correct in confidence
            )
            if confidence
            else None
        ),
        "confidence_ece_10_equal_width_bins": ece if confidence else None,
        "confidence_bins": bins,
        "selective_accuracy": thresholds,
        "confusion": [
            {"gold": gold, "predicted": pred, "count": count}
            for (gold, pred), count in metrics["confusion"].most_common()
        ],
    }


def _new_consistency_metrics() -> dict[str, Any]:
    return {
        "violations": 0,
        "violations_acted": 0,
        "consistent": 0,
        "consistent_acted": 0,
        "corrections_assessable": 0,
        "corrections_conformant": 0,
        "d3_actions": Counter(),
        "d3_conflicts": Counter(),
        "d4_status": Counter(),
        "d4_conflicts": Counter(),
        "d4_fields": Counter(),
        "d4_provenance": Counter(),
        "d4_assessable": 0,
        "d4_conformant": 0,
        "d4_item_assessable": 0,
        "d4_item_conformant": 0,
    }


def project_reconciled_labels(
    labels: Sequence[Any], plan: Any, image_id: str, receipt_id: int
) -> list[Any]:
    """Apply D3 events to copies only, mirroring the additive writer."""

    projected = copy.deepcopy(list(labels))
    index = {
        (label.line_id, label.word_id, label.label): label
        for label in projected
    }
    for event in plan.corrections:
        line_id = int(event["line_id"])
        word_id = int(event["word_id"])
        original_label = event.get("original_label")
        original = index.get((line_id, word_id, str(original_label)))
        if original is not None and event.get("original_new_status"):
            original.validation_status = str(event["original_new_status"])
        corrected = event.get("corrected_label")
        corrected_status = event.get("corrected_status")
        if not corrected or not corrected_status:
            continue
        existing = index.get((line_id, word_id, str(corrected)))
        if existing is not None:
            existing.validation_status = str(corrected_status)
            continue
        created = ReceiptWordLabel(
            image_id=image_id,
            receipt_id=receipt_id,
            line_id=line_id,
            word_id=word_id,
            label=str(corrected),
            reasoning=str(event["reason"]),
            timestamp_added=datetime.now(timezone.utc),
            validation_status=str(corrected_status),
            label_proposed_by=f"{MODEL_SOURCE}:{event['provenance']}",
            label_consolidated_from=(
                str(original_label) if original_label else None
            ),
        )
        projected.append(created)
        index[(line_id, word_id, str(corrected))] = created
    return projected


def _leaf_fields(value: Any, path: str = "") -> Iterable[tuple[str, dict]]:
    if isinstance(value, list):
        for item in value:
            yield from _leaf_fields(item, path + "[]")
        return
    if not isinstance(value, dict):
        return
    if "value" in value:
        yield path, value
        return
    for name, nested in value.items():
        next_path = f"{path}.{name}" if path else name
        yield from _leaf_fields(nested, next_path)


def _score_d3_d4(
    metrics: dict[str, Any],
    receipt: CorpusReceipt,
    plan: Any,
    details: Any,
) -> None:
    gold, _ = _gold_by_line(receipt.gold_sections)
    section_events = {
        (
            int(event["line_id"]),
            int(event["word_id"]),
            str(event.get("original_label")),
        )
        for event in plan.corrections
        if str(event.get("rule_id", "")) == "label-section-footprint"
    }
    for label in receipt.labels:
        if label.validation_status == ValidationStatus.INVALID.value:
            continue
        allowed = _AUDIT_FOOTPRINT.get(label.label)
        truth = gold.get(label.line_id)
        if allowed is None or truth is None:
            continue
        acted = (label.line_id, label.word_id, label.label) in section_events
        if truth not in allowed:
            metrics["violations"] += 1
            metrics["violations_acted"] += int(acted)
        else:
            metrics["consistent"] += 1
            metrics["consistent_acted"] += int(acted)
    for event in plan.corrections:
        rule = str(event.get("rule_id") or "<unknown>")
        metrics["d3_actions"][(rule, str(event.get("action")))] += 1
        if event.get("conflict"):
            metrics["d3_conflicts"][rule] += 1
        corrected = event.get("corrected_label")
        truth = gold.get(int(event["line_id"]))
        allowed = _AUDIT_FOOTPRINT.get(str(corrected))
        if (
            event.get("conflict")
            or not corrected
            or truth is None
            or not allowed
        ):
            continue
        metrics["corrections_assessable"] += 1
        metrics["corrections_conformant"] += int(truth in allowed)

    document = details.to_document()
    metrics["d4_status"][details.validation_status] += 1
    for conflict in details.conflicts:
        metrics["d4_conflicts"][str(conflict.get("rule_id"))] += 1
    for path, field in _leaf_fields(
        {
            "merchant": document["merchant"],
            "transaction": document["transaction"],
            "items": document["items"],
            "totals": document["totals"],
            "tender": document["tender"],
        }
    ):
        if field.get("value") is None:
            continue
        metrics["d4_fields"][path] += 1
        metrics["d4_provenance"][str(field.get("provenance"))] += 1
        sources = field.get("source_words") or []
        if not sources:
            continue
        truths = [gold.get(int(source["line_id"])) for source in sources]
        if any(truth is None for truth in truths):
            continue
        metrics["d4_assessable"] += 1
        labels = [str(source.get("label")) for source in sources]
        conformant = all(
            label not in _AUDIT_FOOTPRINT or truth in _AUDIT_FOOTPRINT[label]
            for label, truth in zip(labels, truths, strict=True)
        )
        metrics["d4_conformant"] += int(conformant)
        if path.startswith("items[]"):
            metrics["d4_item_assessable"] += 1
            metrics["d4_item_conformant"] += int(
                all(truth == "ITEMS" for truth in truths)
            )


def _finalize_consistency(metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "d3": {
            "footprint_violation_support": metrics["violations"],
            "footprint_violation_action_recall": _ratio(
                metrics["violations_acted"], metrics["violations"]
            ),
            "footprint_consistent_support": metrics["consistent"],
            "footprint_false_action_rate": _ratio(
                metrics["consistent_acted"], metrics["consistent"]
            ),
            "correction_section_precision": _ratio(
                metrics["corrections_conformant"],
                metrics["corrections_assessable"],
            ),
            "actions_by_rule": [
                {"rule": rule, "action": action, "count": count}
                for (rule, action), count in metrics[
                    "d3_actions"
                ].most_common()
            ],
            "conflicts_by_rule": dict(metrics["d3_conflicts"].most_common()),
        },
        "d4": {
            "status_counts": dict(metrics["d4_status"]),
            "field_counts": dict(metrics["d4_fields"].most_common()),
            "provenance_counts": dict(metrics["d4_provenance"].most_common()),
            "field_section_conformance": _ratio(
                metrics["d4_conformant"], metrics["d4_assessable"]
            ),
            "field_section_assessable_support": metrics["d4_assessable"],
            "item_section_conformance": _ratio(
                metrics["d4_item_conformant"],
                metrics["d4_item_assessable"],
            ),
            "item_section_assessable_support": metrics["d4_item_assessable"],
            "conflicts_by_rule": dict(metrics["d4_conflicts"].most_common()),
        },
    }


def _manual_payload(receipt: CorpusReceipt, plan: Any, details: Any) -> dict:
    return {
        "image_id": receipt.image_id,
        "receipt_id": receipt.receipt_id,
        "merchant": receipt.merchant_name,
        "sections": [
            {
                "section_type": section.section_type,
                "line_ids": section.line_ids,
                "validation_status": section.validation_status,
            }
            for section in receipt.gold_sections
        ],
        "ocr_lines": [
            {"line_id": line.line_id, "text": line.text}
            for line in sorted(receipt.lines, key=lambda item: item.line_id)
        ],
        "d3": {
            "corrections": plan.corrections,
            "checks": plan.checks,
        },
        "d4": details.to_document(),
    }


def select_manual_sample(
    payloads: Sequence[dict], merchant_counts: Mapping[str, int]
) -> tuple[list[dict], dict[str, Any]]:
    """Pre-register five sentinels plus 5/5/5 frequency strata."""

    ordered = sorted(
        payloads,
        key=lambda item: _stable_hash(
            _SEED, item["image_id"], item["receipt_id"]
        ),
    )
    sentinels = {
        "Sprouts": lambda value: "sprouts" in value,
        "Costco": lambda value: "costco" in value,
        "Vons": lambda value: value == "vons",
        "Smith's": lambda value: "smith" in value,
        "Italia Deli": lambda value: "italia" in value and "deli" in value,
    }
    selected: list[dict] = []
    sentinel_found = {}
    for name, predicate in sentinels.items():
        match = next(
            (
                item
                for item in ordered
                if predicate(normalize_merchant_key(item.get("merchant")))
            ),
            None,
        )
        sentinel_found[name] = bool(match)
        if match and match not in selected:
            tagged = dict(match)
            tagged["sample_stratum"] = f"sentinel:{name}"
            selected.append(tagged)
    selected_keys = {
        (item["image_id"], item["receipt_id"]) for item in selected
    }
    strata: dict[str, list[dict[str, Any]]] = {
        "common": [],
        "mid_frequency": [],
        "long_tail": [],
    }
    for item in ordered:
        if (item["image_id"], item["receipt_id"]) in selected_keys:
            continue
        merchant = normalize_merchant_key(item.get("merchant"))
        count = merchant_counts.get(merchant, 0)
        stratum = (
            "common"
            if count >= 20
            else "mid_frequency" if count >= 5 else "long_tail"
        )
        strata[stratum].append(item)
    for stratum, candidates in strata.items():
        for item in candidates[:5]:
            tagged = dict(item)
            tagged["sample_stratum"] = stratum
            selected.append(tagged)
    return selected, {
        "design": (
            "5 merchant sentinels plus 5 common, 5 mid-frequency, and "
            "5 long-tail receipts"
        ),
        "seed": _SEED,
        "sentinels_found": sentinel_found,
        "population_by_stratum": {
            name: len(items) for name, items in strata.items()
        },
        "sample_count": len(selected),
    }


def _section_snapshot(sections: Sequence[Any]) -> str:
    records = [
        {
            "image_id": section.image_id,
            "receipt_id": section.receipt_id,
            "section_type": str(section.section_type),
            "line_ids": sorted(section.line_ids),
            "validation_status": section.validation_status,
        }
        for section in sections
    ]
    payload = json.dumps(
        sorted(
            records,
            key=lambda item: (
                item["image_id"],
                item["receipt_id"],
                item["section_type"],
            ),
        ),
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def main() -> int:
    args = _args()
    output = assert_private_destination(args.output)
    table_name = assert_dev_table(
        args.environment, os.environ.get("DYNAMODB_TABLE_NAME")
    )
    sections = _all_sections(DynamoClient(table_name))
    by_key: dict[tuple[str, int], list[Any]] = defaultdict(list)
    for section in sections:
        by_key[(section.image_id, section.receipt_id)].append(section)
    keys = sorted(by_key)
    receipts: list[CorpusReceipt] = []
    failures = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(_load_receipt, table_name, key, by_key[key]): key
            for key in keys
        }
        for index, future in enumerate(as_completed(futures), start=1):
            key = futures[future]
            try:
                receipts.append(future.result())
            except Exception as error:  # noqa: BLE001 - reported, never hidden
                failures.append({"key": key, "error": repr(error)})
            if index % 50 == 0 or index == len(futures):
                print(f"loaded {index}/{len(futures)} receipts", flush=True)
    receipts.sort(key=lambda receipt: receipt.key)
    if failures:
        raise RuntimeError(
            f"failed to load {len(failures)} receipts: {failures[:3]}"
        )

    folds = assign_folds(receipts)
    crossfit = _new_section_metrics()
    consistency = _new_consistency_metrics()
    manual_payloads = []
    fold_summaries = []
    for fold in range(_FOLDS):
        training = [
            receipt for receipt in receipts if folds[receipt.image_id] != fold
        ]
        testing = [
            receipt for receipt in receipts if folds[receipt.image_id] == fold
        ]
        model = fit_model(training)
        fold_summaries.append(
            {
                "fold": fold,
                "training_receipts": len(training),
                "test_receipts": len(testing),
                **model["evaluation_training"],
            }
        )
        for receipt in testing:
            assignments = assign_row_sections(
                receipt.rows,
                receipt.lines,
                model,
                receipt.merchant_name,
            )
            _score_sections(crossfit, receipt, assignments)
            predicted_sections = sections_from_assignments(assignments)
            plan = plan_label_reconciliation(
                receipt.words,
                receipt.labels,
                receipt.rows,
                predicted_sections,
                receipt.merchant_name,
            )
            reconciliation = ReceiptLabelReconciliation(
                image_id=receipt.image_id,
                receipt_id=receipt.receipt_id,
                corrections=plan.corrections,
                checks=plan.checks,
                validation_status=(
                    ValidationStatus.NEEDS_REVIEW.value
                    if plan.conflict_count
                    else ValidationStatus.VALID.value
                ),
                model_source=MODEL_SOURCE,
                created_at=datetime.now(timezone.utc),
            )
            projected_labels = project_reconciled_labels(
                receipt.labels,
                plan,
                receipt.image_id,
                receipt.receipt_id,
            )
            details = build_receipt_resolved_details(
                image_id=receipt.image_id,
                receipt_id=receipt.receipt_id,
                words=receipt.words,
                labels=projected_labels,
                rows=receipt.rows,
                sections=predicted_sections,
                reconciliation=reconciliation,
                merchant_name=receipt.merchant_name,
                fingerprint=None,
            )
            _score_d3_d4(consistency, receipt, plan, details)
            manual_payloads.append(_manual_payload(receipt, plan, details))
        print(f"evaluated fold {fold + 1}/{_FOLDS}", flush=True)

    frozen = _new_section_metrics()
    frozen_model = load_prior_model()
    for receipt in receipts:
        _score_sections(
            frozen,
            receipt,
            assign_row_sections(
                receipt.rows,
                receipt.lines,
                frozen_model,
                receipt.merchant_name,
            ),
        )

    merchant_counts = Counter(
        normalize_merchant_key(receipt.merchant_name) for receipt in receipts
    )
    manual_sample, sample_design = select_manual_sample(
        manual_payloads, merchant_counts
    )
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model_source": MODEL_SOURCE,
        "source": {
            "environment": "dev",
            "section_count": len(sections),
            "valid_section_count": sum(
                section.validation_status == ValidationStatus.VALID.value
                for section in sections
            ),
            "pending_section_count": sum(
                section.validation_status == ValidationStatus.PENDING.value
                for section in sections
            ),
            "sectioned_receipt_count": len(keys),
            "processed_receipt_count": len(receipts),
            "section_snapshot_sha256": _section_snapshot(sections),
        },
        "methodology": {
            "cross_validation": (
                "5-fold, grouped by source image and merchant-stratified"
            ),
            "fold_seed": _SEED,
            "folds": fold_summaries,
            "ground_truth": "unique VALID ReceiptSection membership only",
            "d3_d4_scope": (
                "section-consistency against held-out QA sections; current "
                "VALID labels are inputs, not independent field-value truth"
            ),
        },
        "d2_cross_fitted": _finalize_section_metrics(crossfit),
        "d2_frozen_in_sample_apparent_fit": _finalize_section_metrics(frozen),
        "d3_d4_cross_fitted_section_consistency": _finalize_consistency(
            consistency
        ),
        "manual_sample_design": sample_design,
        "manual_sample": manual_sample,
    }
    write_private_text(
        output,
        json.dumps(report, indent=2, sort_keys=True) + "\n",
    )
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
