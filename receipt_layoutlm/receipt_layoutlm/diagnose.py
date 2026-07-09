"""Receipt-level diagnostics for LayoutLM runs.

This module is deliberately different from the training scorecard. The training
curve answers "which checkpoint scored best?" Diagnostics answer "why did it
score that way?" by saving per-receipt, per-template, and token-level evidence.
"""

from __future__ import annotations

import csv
import importlib
import json
import logging
import math
import os
import re
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .evaluate_checkpoints import (
    _download_checkpoint_files,
    _evaluation_diagnostics,
    _get_base_label,
    _hash_receipt_keys,
    _parse_receipt_key,
    _parse_s3_uri,
    _seqeval_scores,
    _token_accuracy,
    build_receipt_record,
    list_checkpoints,
    load_run_json,
    reconstruct_val_receipts,
)
from .inference import LayoutLMInference
from .data_loader import _box_from_word, _normalize_box_from_extents

logger = logging.getLogger(__name__)

PRODUCT_DETAIL_LABELS = (
    "PRODUCT_NAME",
    "QUANTITY",
    "UNIT_PRICE",
    "LINE_TOTAL",
)
ROBUST_GROUP_MIN_RECEIPTS = 3


def diagnose_run(
    *,
    dynamo: Any,
    bucket: str,
    run_prefix: str,
    job_name: str,
    output_dir: str,
    checkpoint: str = "best",
    epoch: Optional[int] = None,
    max_receipts: Optional[int] = None,
    window_size: Optional[int] = None,
    window_stride: Optional[int] = None,
    include_train_context: bool = True,
    max_train_context_receipts: Optional[int] = None,
    error_confidence_threshold: float = 0.75,
    allow_hash_mismatch: bool = False,
    s3_client: Any = None,
) -> Dict[str, Any]:
    """Score one checkpoint and emit proof-oriented diagnostic artifacts."""
    from receipt_dynamo.constants import ValidationStatus

    if s3_client is None:
        boto3 = importlib.import_module("boto3")
        s3_client = boto3.client("s3")

    if not run_prefix.endswith("/"):
        run_prefix += "/"
    os.makedirs(output_dir, exist_ok=True)

    run_json = load_run_json(s3_client, bucket, run_prefix)
    split_meta = (run_json.get("split_metadata") or {}) if run_json else {}
    recorded_hash = split_meta.get("val_receipts_hash")
    recorded_seed = split_meta.get("random_seed")

    resolved_ws = (
        window_size
        if window_size is not None
        else split_meta.get("window_size")
        if split_meta.get("window_size") is not None
        else int(os.getenv("LAYOUTLM_WINDOW_SIZE", "200"))
    )
    resolved_stride = (
        window_stride
        if window_stride is not None
        else split_meta.get("window_stride")
        if split_meta.get("window_stride") is not None
        else int(os.getenv("LAYOUTLM_WINDOW_STRIDE", "150"))
    )
    os.environ["LAYOUTLM_WINDOW_SIZE"] = str(resolved_ws)
    os.environ["LAYOUTLM_WINDOW_STRIDE"] = str(resolved_stride)

    val_receipts, val_hash, hash_verified, val_source, seed = _resolve_val_receipts(
        dynamo=dynamo,
        split_meta=split_meta,
        recorded_hash=recorded_hash,
        recorded_seed=recorded_seed,
        allow_hash_mismatch=allow_hash_mismatch,
    )
    if max_receipts is not None:
        val_receipts = val_receipts[:max_receipts]

    logger.info("Loading %d validation receipts", len(val_receipts))
    details_by_receipt = _load_receipt_details(dynamo, val_receipts)
    valid_status = ValidationStatus.VALID

    checkpoints = list_checkpoints(s3_client, bucket, run_prefix)
    if not checkpoints:
        raise ValueError(f"No checkpoints found under s3://{bucket}/{run_prefix}")
    selected = _select_checkpoint(
        checkpoints,
        run_json,
        checkpoint=checkpoint,
        epoch=epoch,
    )

    model_cache = os.path.join(output_dir, "_model", selected["name"])
    _download_checkpoint_files(
        s3_client, selected["s3_uri"], model_cache, run_json=run_json
    )

    train_context: Dict[str, Any] = {}
    if include_train_context:
        train_context = _build_train_context(
            dynamo,
            val_receipts,
            valid_status=valid_status,
            max_receipts=max_train_context_receipts,
        )

    try:
        infer = LayoutLMInference(model_dir=model_cache)
        rows, token_errors, aggregate_true, aggregate_pred = _diagnose_receipts(
            infer=infer,
            details_by_receipt=details_by_receipt,
            valid_status=valid_status,
            train_context=train_context,
            error_confidence_threshold=error_confidence_threshold,
        )
    finally:
        shutil.rmtree(model_cache, ignore_errors=True)

    aggregate_scores = _seqeval_scores(aggregate_true, aggregate_pred)
    aggregate_diagnostics = _evaluation_diagnostics(
        aggregate_true,
        aggregate_pred,
        per_label_f1=aggregate_scores.get("per_label_f1", {}),
    )

    summaries = {
        "merchant": _summarize_groups(rows, "merchant_name"),
        "place": _summarize_groups(rows, "place_id"),
        "template": _summarize_groups(rows, "template_signature"),
        "line_item_shape": _summarize_groups(rows, "line_item_shape"),
        "merchant_seen_in_training": _summarize_groups(
            rows, "merchant_seen_in_training"
        ),
        "template_seen_in_training": _summarize_groups(
            rows, "template_seen_in_training"
        ),
        "nearest_template_distance_bucket": _summarize_groups(
            rows, "nearest_template_distance_bucket"
        ),
    }
    evidence = _build_evidence_summary(rows, token_errors)

    payload = {
        "job_name": job_name,
        "run_s3_uri": f"s3://{bucket}/{run_prefix}",
        "checkpoint": selected,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "validation": {
            "source": val_source,
            "num_receipts_requested": len(val_receipts),
            "num_receipts_loaded": len(details_by_receipt),
            "hash": val_hash,
            "recorded_hash": recorded_hash,
            "hash_verified": hash_verified,
            "seed": int(seed) if seed is not None else None,
        },
        "inference": {
            "mode": os.getenv("LAYOUTLM_INFERENCE_MODE", "windowed"),
            "window_size": resolved_ws,
            "window_stride": resolved_stride,
        },
        "aggregate": {
            "scores": aggregate_scores,
            "diagnostics": aggregate_diagnostics,
            "token_accuracy": _token_accuracy(aggregate_true, aggregate_pred),
        },
        "train_context": {
            "enabled": include_train_context,
            "num_train_receipts_loaded": len(train_context.get("features", [])),
            "source": train_context.get("source"),
        },
        "evidence": evidence,
        "artifacts": {
            "summary_json": "summary.json",
            "report_md": "report.md",
            "per_receipt_jsonl": "per_receipt.jsonl",
            "per_receipt_csv": "per_receipt.csv",
            "token_errors_jsonl": "token_errors.jsonl",
            "groups_json": "groups.json",
        },
    }

    _write_outputs(
        output_dir=output_dir,
        payload=payload,
        rows=rows,
        token_errors=token_errors,
        groups=summaries,
    )
    return payload


def sync_diagnostics_to_s3(
    output_dir: str, output_s3_uri: str, s3_client: Any = None
) -> None:
    """Upload the diagnostic artifact directory to S3."""
    if s3_client is None:
        boto3 = importlib.import_module("boto3")
        s3_client = boto3.client("s3")
    bucket, prefix = _parse_s3_uri(output_s3_uri)
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    for root, _dirs, files in os.walk(output_dir):
        for fname in files:
            if fname.startswith("."):
                continue
            local_path = os.path.join(root, fname)
            rel = os.path.relpath(local_path, output_dir).replace(os.sep, "/")
            extra = (
                {"ContentType": "application/json"}
                if fname.endswith((".json", ".jsonl"))
                else {"ContentType": "text/markdown"}
                if fname.endswith(".md")
                else {"ContentType": "text/csv"}
                if fname.endswith(".csv")
                else None
            )
            kwargs = {"ExtraArgs": extra} if extra else {}
            s3_client.upload_file(local_path, bucket, f"{prefix}{rel}", **kwargs)
    logger.info("Synced diagnostics to %s", output_s3_uri)


def _resolve_val_receipts(
    *,
    dynamo: Any,
    split_meta: Dict[str, Any],
    recorded_hash: Optional[str],
    recorded_seed: Optional[int],
    allow_hash_mismatch: bool,
) -> Tuple[List[Tuple[str, int]], str, bool, str, Optional[int]]:
    persisted_keys = split_meta.get("val_receipt_keys")
    if persisted_keys:
        val_receipts = [_parse_receipt_key(k) for k in persisted_keys]
        computed_hash = _hash_receipt_keys(list(persisted_keys))
        hash_verified = computed_hash == recorded_hash if recorded_hash else True
        return (
            val_receipts,
            computed_hash,
            hash_verified,
            "persisted_val_receipt_keys",
            recorded_seed,
        )

    if recorded_seed is None:
        raise ValueError(
            "No persisted val_receipt_keys or split random_seed in run.json"
        )
    val_receipts, computed_hash = reconstruct_val_receipts(
        dynamo, int(recorded_seed)
    )
    hash_verified = bool(recorded_hash) and computed_hash == recorded_hash
    if recorded_hash and not hash_verified and not allow_hash_mismatch:
        raise ValueError(
            f"Validation hash mismatch: recorded={recorded_hash} "
            f"computed={computed_hash}. Pass --allow-hash-mismatch to proceed."
        )
    return (
        val_receipts,
        computed_hash,
        hash_verified,
        "reconstructed_from_seed",
        recorded_seed,
    )


def _load_receipt_details(
    dynamo: Any, keys: Iterable[Tuple[str, int]]
) -> Dict[Tuple[str, int], Any]:
    details_by_receipt: Dict[Tuple[str, int], Any] = {}
    for image_id, receipt_id in keys:
        try:
            details_by_receipt[(image_id, receipt_id)] = dynamo.get_receipt_details(
                image_id, receipt_id
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Skipping %s#%05d: %s", image_id, receipt_id, e)
    return details_by_receipt


def _select_checkpoint(
    checkpoints: List[Dict[str, Any]],
    run_json: Dict[str, Any],
    *,
    checkpoint: str,
    epoch: Optional[int],
) -> Dict[str, Any]:
    if epoch is not None:
        step_to_epoch, _step_to_f1 = _build_step_epoch_map(run_json)
        for ckpt in checkpoints:
            step = ckpt.get("step")
            if step is not None and step_to_epoch.get(step) == epoch:
                return dict(ckpt)
        raise ValueError(f"No checkpoint found for epoch {epoch}")

    if checkpoint == "best":
        for ckpt in checkpoints:
            if ckpt["name"] == "best":
                return dict(ckpt)
        raise ValueError("No best/ checkpoint found")

    wanted = checkpoint
    if wanted.isdigit():
        wanted = f"checkpoint-{wanted}"
    for ckpt in checkpoints:
        if ckpt["name"] == wanted:
            return dict(ckpt)
    raise ValueError(f"No checkpoint named {checkpoint!r}")


def _build_step_epoch_map(
    run_json: Dict[str, Any],
) -> Tuple[Dict[int, int], Dict[int, float]]:
    from .evaluate_checkpoints import _build_step_epoch_map as build_map

    return build_map(run_json)


def _build_train_context(
    dynamo: Any,
    val_receipts: List[Tuple[str, int]],
    *,
    valid_status: Any,
    max_receipts: Optional[int] = None,
) -> Dict[str, Any]:
    from receipt_dynamo.constants import ValidationStatus

    val_keys = {f"{image_id}#{receipt_id:05d}" for image_id, receipt_id in val_receipts}
    labels, _ = dynamo.list_receipt_word_labels_with_status(
        ValidationStatus.VALID, limit=None, last_evaluated_key=None
    )
    train_keys = sorted(
        {
            (label.image_id, label.receipt_id)
            for label in labels
            if f"{label.image_id}#{label.receipt_id:05d}" not in val_keys
        }
    )
    if max_receipts is not None:
        train_keys = train_keys[:max_receipts]

    features: List[Dict[str, Any]] = []
    merchant_counts: Counter[str] = Counter()
    template_counts: Counter[str] = Counter()
    for image_id, receipt_id in train_keys:
        try:
            details = dynamo.get_receipt_details(image_id, receipt_id)
        except Exception as e:  # noqa: BLE001
            logger.debug("Could not load train context %s#%05d: %s", image_id, receipt_id, e)
            continue
        f = _receipt_features(details, valid_status=valid_status)
        features.append(f)
        merchant_counts[f["merchant_name"]] += 1
        template_counts[f["template_signature"]] += 1

    return {
        "source": "current_VALID_labels_minus_validation_receipts",
        "features": features,
        "merchant_counts": dict(merchant_counts),
        "template_counts": dict(template_counts),
    }


def _diagnose_receipts(
    *,
    infer: LayoutLMInference,
    details_by_receipt: Dict[Tuple[str, int], Any],
    valid_status: Any,
    train_context: Dict[str, Any],
    error_confidence_threshold: float,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[List[str]], List[List[str]]]:
    rows: List[Dict[str, Any]] = []
    token_errors: List[Dict[str, Any]] = []
    aggregate_true: List[List[str]] = []
    aggregate_pred: List[List[str]] = []
    train_features = train_context.get("features") or []
    merchant_counts = train_context.get("merchant_counts") or {}
    template_counts = train_context.get("template_counts") or {}

    for (image_id, receipt_id), details in details_by_receipt.items():
        record, y_true, y_pred = build_receipt_record(
            infer, details, image_id, receipt_id, valid_status=valid_status
        )
        aggregate_true.extend(y_true)
        aggregate_pred.extend(y_pred)

        scores = _seqeval_scores(y_true, y_pred)
        diagnostics = _evaluation_diagnostics(
            y_true, y_pred, per_label_f1=scores.get("per_label_f1", {})
        )
        features = _receipt_features(details, valid_status=valid_status)
        errors, error_summary = _token_error_rows(
            record,
            features=features,
            confidence_threshold=error_confidence_threshold,
        )
        nearest = _nearest_train_template(features, train_features)

        merchant_train_count = int(merchant_counts.get(features["merchant_name"], 0))
        template_train_count = int(template_counts.get(features["template_signature"], 0))
        row = {
            **features,
            "heldout_f1": scores["f1"],
            "heldout_precision": scores["precision"],
            "heldout_recall": scores["recall"],
            "token_accuracy": _token_accuracy(y_true, y_pred),
            "product_detail_macro_f1": diagnostics["product_detail"]["macro_f1"],
            "product_gold_entities": diagnostics["product_detail"]["gold_entities"],
            "product_predicted_entities": diagnostics["product_detail"][
                "predicted_entities"
            ],
            "product_prediction_gold_ratio": diagnostics["product_detail"][
                "prediction_gold_ratio"
            ],
            "per_label_f1": scores.get("per_label_f1", {}),
            "per_label_precision": scores.get("per_label_precision", {}),
            "per_label_recall": scores.get("per_label_recall", {}),
            "per_label_support": scores.get("per_label_support", {}),
            "entity_prediction_rate": diagnostics["rates"][
                "predicted_entity_token_rate"
            ],
            "gold_entity_rate": diagnostics["rates"]["gold_entity_token_rate"],
            "merchant_train_receipts": merchant_train_count,
            "template_train_receipts": template_train_count,
            "merchant_seen_in_training": merchant_train_count > 0,
            "template_seen_in_training": template_train_count > 0,
            "nearest_train_receipt_key": nearest.get("receipt_key"),
            "nearest_train_merchant": nearest.get("merchant_name"),
            "nearest_template_distance": nearest.get("distance"),
            "nearest_template_distance_bucket": _distance_bucket(
                nearest.get("distance")
            ),
            **error_summary,
        }
        rows.append(row)
        for err in errors:
            err["receipt_key"] = features["receipt_key"]
            token_errors.append(err)

    return rows, token_errors, aggregate_true, aggregate_pred


def _receipt_features(details: Any, *, valid_status: Any) -> Dict[str, Any]:
    receipt = details.receipt
    image_id = receipt.image_id
    receipt_id = receipt.receipt_id
    merchant_name = _merchant_name(details)
    place_id = getattr(details.place, "place_id", None) if details.place else None
    labels_by_word = _valid_labels_by_word(details, valid_status)
    words_by_key = {(w.line_id, w.word_id): w for w in details.words}

    line_product_labels: Dict[int, set[str]] = defaultdict(set)
    label_centers_x: Dict[str, List[float]] = defaultdict(list)
    label_centers_y: Dict[str, List[float]] = defaultdict(list)
    max_x, max_y = _receipt_extents(details.words)

    for key, labels in labels_by_word.items():
        word = words_by_key.get(key)
        if word is None:
            continue
        x0, y0, x1, y1 = _box_from_word(word)
        box = _normalize_box_from_extents(x0, y0, x1, y1, max_x, max_y)
        cx = (box[0] + box[2]) / 2 / 1000.0
        cy = (box[1] + box[3]) / 2 / 1000.0
        for label in labels:
            base = _normalize_base_label(label)
            if base in PRODUCT_DETAIL_LABELS:
                line_product_labels[word.line_id].add(base)
                label_centers_x[base].append(cx)
                label_centers_y[base].append(cy)

    product_lines = {
        line_id
        for line_id, labels in line_product_labels.items()
        if "PRODUCT_NAME" in labels or "LINE_TOTAL" in labels
    }
    product_name_lines = {
        line_id
        for line_id, labels in line_product_labels.items()
        if "PRODUCT_NAME" in labels
    }
    line_total_lines = {
        line_id
        for line_id, labels in line_product_labels.items()
        if "LINE_TOTAL" in labels
    }
    product_y = [
        y for label in PRODUCT_DETAIL_LABELS for y in label_centers_y.get(label, [])
    ]

    features: Dict[str, Any] = {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "receipt_key": f"{image_id}#{receipt_id:05d}",
        "merchant_name": merchant_name,
        "merchant_key": _slug(merchant_name),
        "place_id": place_id or "UNKNOWN_PLACE",
        "num_words": len(details.words),
        "num_lines": len(details.lines),
        "product_line_count": len(product_lines),
        "product_name_line_count": len(product_name_lines),
        "line_total_line_count": len(line_total_lines),
        "has_quantity_column": bool(label_centers_x.get("QUANTITY")),
        "has_unit_price_column": bool(label_centers_x.get("UNIT_PRICE")),
        "has_line_total_column": bool(label_centers_x.get("LINE_TOTAL")),
        "has_product_name_column": bool(label_centers_x.get("PRODUCT_NAME")),
        "product_band_y_min": min(product_y) if product_y else None,
        "product_band_y_max": max(product_y) if product_y else None,
    }
    for label in PRODUCT_DETAIL_LABELS:
        xs = label_centers_x.get(label) or []
        features[f"{label.lower()}_x_mean"] = (
            sum(xs) / len(xs) if xs else None
        )
    features["line_item_shape"] = _line_item_shape(features)
    features["template_signature"] = _template_signature(features)
    features["template_vector"] = _template_vector(features)
    return features


def _valid_labels_by_word(details: Any, valid_status: Any) -> Dict[Tuple[int, int], List[str]]:
    out: Dict[Tuple[int, int], List[str]] = defaultdict(list)
    for label in details.labels:
        if _status_name(label.validation_status) == _status_name(valid_status):
            out[(label.line_id, label.word_id)].append(label.label)
    return out


def _merchant_name(details: Any) -> str:
    if details.place and getattr(details.place, "merchant_name", None):
        return str(details.place.merchant_name).upper()
    for label in details.labels:
        if _normalize_base_label(label.label) == "MERCHANT_NAME":
            return "LABELED_MERCHANT"
    return "UNKNOWN_MERCHANT"


def _receipt_extents(words: List[Any]) -> Tuple[float, float]:
    max_x = max_y = 1.0
    for word in words:
        x0, y0, x1, y1 = _box_from_word(word)
        del x0, y0
        max_x = max(max_x, x1)
        max_y = max(max_y, y1)
    return max_x, max_y


def _line_item_shape(features: Dict[str, Any]) -> str:
    return "|".join(
        [
            f"items:{_count_bucket(features['product_name_line_count'])}",
            f"qty:{int(bool(features['has_quantity_column']))}",
            f"unit:{int(bool(features['has_unit_price_column']))}",
            f"total:{int(bool(features['has_line_total_column']))}",
            f"name_x:{_x_bucket(features.get('product_name_x_mean'))}",
            f"unit_x:{_x_bucket(features.get('unit_price_x_mean'))}",
            f"total_x:{_x_bucket(features.get('line_total_x_mean'))}",
        ]
    )


def _template_signature(features: Dict[str, Any]) -> str:
    return "|".join(
        [
            features["merchant_key"],
            f"lines:{_count_bucket(features['num_lines'])}",
            f"words:{_count_bucket(features['num_words'])}",
            features["line_item_shape"],
        ]
    )


def _template_vector(features: Dict[str, Any]) -> List[float]:
    return [
        min(float(features["num_lines"]) / 100.0, 2.0),
        min(float(features["num_words"]) / 600.0, 2.0),
        min(float(features["product_name_line_count"]) / 80.0, 2.0),
        1.0 if features["has_quantity_column"] else 0.0,
        1.0 if features["has_unit_price_column"] else 0.0,
        1.0 if features["has_line_total_column"] else 0.0,
        _none_to(features.get("product_name_x_mean"), 0.0),
        _none_to(features.get("quantity_x_mean"), 0.0),
        _none_to(features.get("unit_price_x_mean"), 0.0),
        _none_to(features.get("line_total_x_mean"), 0.0),
        _none_to(features.get("product_band_y_min"), 0.0),
        _none_to(features.get("product_band_y_max"), 0.0),
    ]


def _nearest_train_template(
    features: Dict[str, Any], train_features: List[Dict[str, Any]]
) -> Dict[str, Any]:
    if not train_features:
        return {"distance": None, "receipt_key": None, "merchant_name": None}
    best: Optional[Tuple[float, Dict[str, Any]]] = None
    for candidate in train_features:
        dist = _template_distance(features, candidate)
        if best is None or dist < best[0]:
            best = (dist, candidate)
    assert best is not None
    return {
        "distance": round(best[0], 4),
        "receipt_key": best[1]["receipt_key"],
        "merchant_name": best[1]["merchant_name"],
    }


def _template_distance(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    av = a.get("template_vector") or _template_vector(a)
    bv = b.get("template_vector") or _template_vector(b)
    dist = math.sqrt(sum((float(x) - float(y)) ** 2 for x, y in zip(av, bv)))
    if a["merchant_key"] != b["merchant_key"]:
        dist += 0.75
    return dist


def _token_error_rows(
    record: Dict[str, Any],
    *,
    features: Dict[str, Any],
    confidence_threshold: float,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    errors: List[Dict[str, Any]] = []
    counts: Counter[str] = Counter()
    product_counts: Counter[str] = Counter()
    confidence_sum = 0.0
    incorrect_count = 0
    high_conf_incorrect_count = 0
    high_conf_product_fp = 0

    predictions = record.get("original", {}).get("predictions", [])
    for pred in predictions:
        gold = pred.get("ground_truth_label_base") or "O"
        guessed = pred.get("predicted_label_base") or "O"
        confidence = float(pred.get("predicted_confidence") or 0.0)
        base_correct = gold == guessed
        bio_correct = bool(pred.get("is_correct"))
        if base_correct and bio_correct:
            continue

        kind = _error_kind(gold, guessed, base_correct)
        counts[kind] += 1
        incorrect_count += 1
        confidence_sum += confidence
        if confidence >= confidence_threshold:
            high_conf_incorrect_count += 1
        product_related = gold in PRODUCT_DETAIL_LABELS or guessed in PRODUCT_DETAIL_LABELS
        if product_related:
            product_counts[kind] += 1
        if (
            gold == "O"
            and guessed in PRODUCT_DETAIL_LABELS
            and confidence >= confidence_threshold
        ):
            high_conf_product_fp += 1

        probs = pred.get("all_class_probabilities_base") or {}
        top_probs = sorted(
            (
                {"label": label, "probability": float(prob)}
                for label, prob in probs.items()
                if isinstance(prob, (int, float))
            ),
            key=lambda item: item["probability"],
            reverse=True,
        )[:5]
        errors.append(
            {
                "image_id": features["image_id"],
                "receipt_id": features["receipt_id"],
                "merchant_name": features["merchant_name"],
                "template_signature": features["template_signature"],
                "line_item_shape": features["line_item_shape"],
                "line_id": pred.get("line_id"),
                "word_id": pred.get("word_id"),
                "text": pred.get("text"),
                "ground_truth_label": pred.get("ground_truth_label"),
                "predicted_label": pred.get("predicted_label"),
                "ground_truth_label_base": gold,
                "predicted_label_base": guessed,
                "ground_truth_label_original": pred.get(
                    "ground_truth_label_original"
                ),
                "confidence": confidence,
                "error_kind": kind,
                "product_related": product_related,
                "high_confidence": confidence >= confidence_threshold,
                "top_probabilities": top_probs,
            }
        )

    return errors, {
        "incorrect_token_count": incorrect_count,
        "incorrect_token_mean_confidence": (
            confidence_sum / incorrect_count if incorrect_count else None
        ),
        "high_confidence_incorrect_tokens": high_conf_incorrect_count,
        "high_confidence_product_false_positives": high_conf_product_fp,
        "error_kind_counts": dict(counts),
        "product_error_kind_counts": dict(product_counts),
    }


def _error_kind(gold: str, guessed: str, base_correct: bool) -> str:
    if base_correct:
        return "boundary_error"
    if gold == "O" and guessed != "O":
        return "false_positive"
    if gold != "O" and guessed == "O":
        return "missed_entity"
    return "wrong_label"


def _summarize_groups(rows: List[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(key))].append(row)

    summaries: List[Dict[str, Any]] = []
    for value, group in groups.items():
        summaries.append(
            {
                "group": value,
                "receipt_count": len(group),
                "avg_product_detail_macro_f1": _avg(
                    r.get("product_detail_macro_f1") for r in group
                ),
                "avg_heldout_f1": _avg(r.get("heldout_f1") for r in group),
                "avg_token_accuracy": _avg(r.get("token_accuracy") for r in group),
                "avg_nearest_template_distance": _avg(
                    r.get("nearest_template_distance") for r in group
                ),
                "total_product_gold_entities": sum(
                    int(r.get("product_gold_entities") or 0) for r in group
                ),
                "total_product_predicted_entities": sum(
                    int(r.get("product_predicted_entities") or 0) for r in group
                ),
                "total_incorrect_tokens": sum(
                    int(r.get("incorrect_token_count") or 0) for r in group
                ),
                "total_high_confidence_product_false_positives": sum(
                    int(r.get("high_confidence_product_false_positives") or 0)
                    for r in group
                ),
            }
        )
    return sorted(
        summaries,
        key=lambda row: (
            row["avg_product_detail_macro_f1"]
            if row["avg_product_detail_macro_f1"] is not None
            else -1,
            row["receipt_count"],
        ),
    )


def _build_evidence_summary(
    rows: List[Dict[str, Any]], token_errors: List[Dict[str, Any]]
) -> Dict[str, Any]:
    seen = [r for r in rows if r.get("merchant_seen_in_training")]
    unseen = [r for r in rows if not r.get("merchant_seen_in_training")]
    template_seen = [r for r in rows if r.get("template_seen_in_training")]
    template_unseen = [r for r in rows if not r.get("template_seen_in_training")]
    distances = [
        r.get("nearest_template_distance")
        for r in rows
        if isinstance(r.get("nearest_template_distance"), (int, float))
    ]
    product_scores = [
        r.get("product_detail_macro_f1")
        for r in rows
        if isinstance(r.get("product_detail_macro_f1"), (int, float))
        and isinstance(r.get("nearest_template_distance"), (int, float))
    ]
    matching_distances = [
        r.get("nearest_template_distance")
        for r in rows
        if isinstance(r.get("product_detail_macro_f1"), (int, float))
        and isinstance(r.get("nearest_template_distance"), (int, float))
    ]
    high_conf_fp = [
        e
        for e in token_errors
        if e["error_kind"] == "false_positive" and e["high_confidence"]
    ]
    high_conf_product_fp = [
        e
        for e in high_conf_fp
        if e["predicted_label_base"] in PRODUCT_DETAIL_LABELS
    ]
    product_errors = [e for e in token_errors if e["product_related"]]
    low_conf_product_errors = [
        e for e in product_errors if float(e.get("confidence") or 0.0) < 0.45
    ]

    worst_merchants = _summarize_groups(rows, "merchant_name")[:10]
    worst_shapes = _summarize_groups(rows, "line_item_shape")[:10]
    robust_shapes = [
        group
        for group in _summarize_groups(rows, "line_item_shape")
        if group["receipt_count"] >= ROBUST_GROUP_MIN_RECEIPTS
    ][:10]

    return {
        "template_coverage": {
            "seen_merchant_receipts": len(seen),
            "unseen_merchant_receipts": len(unseen),
            "seen_merchant_avg_product_macro_f1": _avg(
                r.get("product_detail_macro_f1") for r in seen
            ),
            "unseen_merchant_avg_product_macro_f1": _avg(
                r.get("product_detail_macro_f1") for r in unseen
            ),
            "seen_template_receipts": len(template_seen),
            "unseen_template_receipts": len(template_unseen),
            "seen_template_avg_product_macro_f1": _avg(
                r.get("product_detail_macro_f1") for r in template_seen
            ),
            "unseen_template_avg_product_macro_f1": _avg(
                r.get("product_detail_macro_f1") for r in template_unseen
            ),
            "nearest_template_distance_mean": _avg(distances),
            "nearest_template_distance_vs_product_f1_correlation": _pearson(
                matching_distances, product_scores
            ),
            "worst_merchants": worst_merchants,
        },
        "line_item_structure": {
            "worst_line_item_shapes": worst_shapes,
            "worst_line_item_shapes_min_receipts": ROBUST_GROUP_MIN_RECEIPTS,
            "worst_robust_line_item_shapes": robust_shapes,
            "product_f1_by_item_count_bucket": _summarize_rows_by_derived_key(
                rows, lambda row: _count_bucket(int(row.get("product_name_line_count") or 0))
            ),
            "product_f1_by_line_total_presence": _summarize_rows_by_derived_key(
                rows, lambda row: str(bool(row.get("has_line_total_column")))
            ),
            "product_f1_by_unit_price_presence": _summarize_rows_by_derived_key(
                rows, lambda row: str(bool(row.get("has_unit_price_column")))
            ),
            "product_f1_by_quantity_presence": _summarize_rows_by_derived_key(
                rows, lambda row: str(bool(row.get("has_quantity_column")))
            ),
            "product_f1_by_product_name_x_bucket": _summarize_rows_by_derived_key(
                rows, lambda row: _x_bucket(row.get("product_name_x_mean"))
            ),
        },
        "label_eval_mismatch": {
            "high_confidence_false_positive_tokens": len(high_conf_fp),
            "high_confidence_product_false_positive_tokens": len(
                high_conf_product_fp
            ),
            "error_kind_counts": dict(Counter(e["error_kind"] for e in token_errors)),
            "product_error_kind_counts": dict(
                Counter(e["error_kind"] for e in product_errors)
            ),
            "top_product_confusions": _top_confusions(product_errors),
            "top_false_positive_labels": _top_label_counts(
                (e for e in token_errors if e["error_kind"] == "false_positive"),
                "predicted_label_base",
            ),
            "top_missed_gold_labels": _top_label_counts(
                (e for e in token_errors if e["error_kind"] == "missed_entity"),
                "ground_truth_label_base",
            ),
            "top_high_confidence_false_positive_examples": high_conf_fp[:25],
        },
        "model_weakness": {
            "product_error_tokens": len(product_errors),
            "low_confidence_product_error_tokens": len(low_conf_product_errors),
            "low_confidence_product_error_rate": _safe_ratio(
                len(low_conf_product_errors), len(product_errors)
            ),
        },
    }


def _summarize_rows_by_derived_key(
    rows: List[Dict[str, Any]], key_fn: Any
) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(key_fn(row))].append(row)
    return sorted(
        (_summarize_row_group(value, group) for value, group in grouped.items()),
        key=lambda row: (
            row["avg_product_detail_macro_f1"]
            if row["avg_product_detail_macro_f1"] is not None
            else -1,
            row["receipt_count"],
        ),
    )


def _summarize_row_group(value: str, group: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "group": value,
        "receipt_count": len(group),
        "avg_product_detail_macro_f1": _avg(
            r.get("product_detail_macro_f1") for r in group
        ),
        "avg_heldout_f1": _avg(r.get("heldout_f1") for r in group),
        "avg_token_accuracy": _avg(r.get("token_accuracy") for r in group),
        "avg_nearest_template_distance": _avg(
            r.get("nearest_template_distance") for r in group
        ),
        "total_product_gold_entities": sum(
            int(r.get("product_gold_entities") or 0) for r in group
        ),
        "total_product_predicted_entities": sum(
            int(r.get("product_predicted_entities") or 0) for r in group
        ),
        "total_incorrect_tokens": sum(
            int(r.get("incorrect_token_count") or 0) for r in group
        ),
        "total_high_confidence_product_false_positives": sum(
            int(r.get("high_confidence_product_false_positives") or 0)
            for r in group
        ),
    }


def _top_confusions(errors: Iterable[Dict[str, Any]], limit: int = 20) -> List[Dict[str, Any]]:
    counts = Counter(
        (
            e.get("ground_truth_label_base") or "O",
            e.get("predicted_label_base") or "O",
        )
        for e in errors
    )
    return [
        {"gold": gold, "predicted": predicted, "count": count}
        for (gold, predicted), count in counts.most_common(limit)
    ]


def _top_label_counts(
    errors: Iterable[Dict[str, Any]], field: str, limit: int = 20
) -> List[Dict[str, Any]]:
    counts = Counter(str(e.get(field) or "O") for e in errors)
    return [
        {"label": label, "count": count}
        for label, count in counts.most_common(limit)
    ]


def _write_outputs(
    *,
    output_dir: str,
    payload: Dict[str, Any],
    rows: List[Dict[str, Any]],
    token_errors: List[Dict[str, Any]],
    groups: Dict[str, Any],
) -> None:
    with open(os.path.join(output_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    with open(os.path.join(output_dir, "groups.json"), "w", encoding="utf-8") as f:
        json.dump(groups, f, indent=2, default=str)
    _write_jsonl(os.path.join(output_dir, "per_receipt.jsonl"), rows)
    _write_jsonl(os.path.join(output_dir, "token_errors.jsonl"), token_errors)
    _write_receipt_csv(os.path.join(output_dir, "per_receipt.csv"), rows)
    with open(os.path.join(output_dir, "report.md"), "w", encoding="utf-8") as f:
        f.write(_markdown_report(payload, groups))


def _write_jsonl(path: str, rows: List[Dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, default=str) + "\n")


def _write_receipt_csv(path: str, rows: List[Dict[str, Any]]) -> None:
    fields = [
        "receipt_key",
        "merchant_name",
        "place_id",
        "template_signature",
        "line_item_shape",
        "heldout_f1",
        "product_detail_macro_f1",
        "token_accuracy",
        "product_gold_entities",
        "product_predicted_entities",
        "merchant_train_receipts",
        "template_train_receipts",
        "merchant_seen_in_training",
        "template_seen_in_training",
        "nearest_template_distance",
        "nearest_template_distance_bucket",
        "incorrect_token_count",
        "high_confidence_product_false_positives",
    ]
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fields})


def _markdown_report(payload: Dict[str, Any], groups: Dict[str, Any]) -> str:
    evidence = payload["evidence"]
    aggregate = payload["aggregate"]["scores"]
    product = payload["aggregate"]["diagnostics"]["product_detail"]
    lines = [
        f"# LayoutLM Diagnostic Report: {payload['job_name']}",
        "",
        f"- checkpoint: `{payload['checkpoint']['name']}`",
        f"- validation receipts: `{payload['validation']['num_receipts_loaded']}`",
        f"- held-out F1: `{_fmt(aggregate.get('f1'))}`",
        f"- product-detail macro F1: `{_fmt(product.get('macro_f1'))}`",
        "",
        "## Hypothesis Evidence",
        "",
        "### Template Coverage",
        "",
        _bullet_metric(
            "Seen merchant avg product F1",
            evidence["template_coverage"].get("seen_merchant_avg_product_macro_f1"),
        ),
        _bullet_metric(
            "Unseen merchant avg product F1",
            evidence["template_coverage"].get("unseen_merchant_avg_product_macro_f1"),
        ),
        _bullet_metric(
            "Seen template avg product F1",
            evidence["template_coverage"].get("seen_template_avg_product_macro_f1"),
        ),
        _bullet_metric(
            "Unseen template avg product F1",
            evidence["template_coverage"].get("unseen_template_avg_product_macro_f1"),
        ),
        _bullet_metric(
            "Nearest-template distance vs product F1 correlation",
            evidence["template_coverage"].get(
                "nearest_template_distance_vs_product_f1_correlation"
            ),
        ),
        "",
        "### Line-Item Structure",
        "",
        "Robust buckets:",
        "",
    ]
    for group in evidence["line_item_structure"][
        "product_f1_by_item_count_bucket"
    ]:
        lines.append(
            f"- item rows `{group['group']}`: n={group['receipt_count']}, "
            f"product F1={_fmt(group['avg_product_detail_macro_f1'])}, "
            f"nearest distance={_fmt(group['avg_nearest_template_distance'])}"
        )
    lines.extend(["", "Line total column present:"])
    for group in evidence["line_item_structure"][
        "product_f1_by_line_total_presence"
    ]:
        lines.append(
            f"- `{group['group']}`: n={group['receipt_count']}, "
            f"product F1={_fmt(group['avg_product_detail_macro_f1'])}"
        )
    robust_shapes = evidence["line_item_structure"].get(
        "worst_robust_line_item_shapes", []
    )
    if robust_shapes:
        lines.extend(["", "Worst repeated line-item shapes:"])
        for group in robust_shapes[:5]:
            lines.append(
                f"- `{group['group']}`: n={group['receipt_count']}, "
                f"product F1={_fmt(group['avg_product_detail_macro_f1'])}"
            )
    lines.extend(["", "Worst one-off line-item shapes:"])
    for group in evidence["line_item_structure"]["worst_line_item_shapes"][:5]:
        lines.append(
            f"- `{group['group']}`: n={group['receipt_count']}, "
            f"product F1={_fmt(group['avg_product_detail_macro_f1'])}"
        )
    lines.extend(
        [
            "",
            "### Label/Eval Mismatch",
            "",
            "- high-confidence false-positive tokens: "
            f"`{evidence['label_eval_mismatch']['high_confidence_false_positive_tokens']}`",
            "- high-confidence product false-positive tokens: "
            f"`{evidence['label_eval_mismatch']['high_confidence_product_false_positive_tokens']}`",
            "- token error kinds: "
            f"`{evidence['label_eval_mismatch']['error_kind_counts']}`",
            "- product token error kinds: "
            f"`{evidence['label_eval_mismatch']['product_error_kind_counts']}`",
            "",
            "Top product confusions:",
            "",
        ]
    )
    for item in evidence["label_eval_mismatch"]["top_product_confusions"][:8]:
        lines.append(
            f"- `{item['gold']}` -> `{item['predicted']}`: {item['count']}"
        )
    lines.extend(
        [
            "",
            "### Model Weakness",
            "",
            "- product error tokens: "
            f"`{evidence['model_weakness']['product_error_tokens']}`",
            "- low-confidence product error tokens: "
            f"`{evidence['model_weakness']['low_confidence_product_error_tokens']}`",
            "- low-confidence product error rate: "
            f"`{_fmt(evidence['model_weakness']['low_confidence_product_error_rate'])}`",
            "",
            "## Worst Merchant Groups",
            "",
        ]
    )
    for group in groups["merchant"][:10]:
        lines.append(
            f"- `{group['group']}`: n={group['receipt_count']}, "
            f"product F1={_fmt(group['avg_product_detail_macro_f1'])}, "
            f"nearest distance={_fmt(group['avg_nearest_template_distance'])}"
        )
    lines.append("")
    return "\n".join(lines)


def _bullet_metric(label: str, value: Any) -> str:
    return f"- {label}: `{_fmt(value)}`"


def _normalize_base_label(label: Optional[str]) -> str:
    return _get_base_label(label.upper() if isinstance(label, str) else label)


def _status_name(status: Any) -> str:
    return getattr(status, "name", str(status)).upper()


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Z0-9]+", "_", value.upper()).strip("_")
    return slug or "UNKNOWN"


def _count_bucket(value: int) -> str:
    if value <= 0:
        return "0"
    if value <= 4:
        return "1-4"
    if value <= 9:
        return "5-9"
    if value <= 19:
        return "10-19"
    if value <= 39:
        return "20-39"
    return "40+"


def _x_bucket(value: Optional[float]) -> str:
    if value is None:
        return "none"
    return str(max(0, min(9, int(value * 10))))


def _distance_bucket(value: Optional[float]) -> str:
    if value is None:
        return "unknown"
    if value < 0.15:
        return "near"
    if value < 0.35:
        return "medium"
    if value < 0.75:
        return "far"
    return "very_far"


def _avg(values: Iterable[Any]) -> Optional[float]:
    nums = [float(v) for v in values if isinstance(v, (int, float))]
    return sum(nums) / len(nums) if nums else None


def _safe_ratio(num: int, den: int) -> Optional[float]:
    return num / den if den else None


def _pearson(xs: List[Any], ys: List[Any]) -> Optional[float]:
    pairs = [
        (float(x), float(y))
        for x, y in zip(xs, ys)
        if isinstance(x, (int, float)) and isinstance(y, (int, float))
    ]
    if len(pairs) < 2:
        return None
    x_vals = [p[0] for p in pairs]
    y_vals = [p[1] for p in pairs]
    x_mean = sum(x_vals) / len(x_vals)
    y_mean = sum(y_vals) / len(y_vals)
    num = sum((x - x_mean) * (y - y_mean) for x, y in pairs)
    x_den = math.sqrt(sum((x - x_mean) ** 2 for x in x_vals))
    y_den = math.sqrt(sum((y - y_mean) ** 2 for y in y_vals))
    if x_den == 0 or y_den == 0:
        return None
    return num / (x_den * y_den)


def _none_to(value: Optional[float], fallback: float) -> float:
    return float(value) if isinstance(value, (int, float)) else fallback


def _fmt(value: Any) -> str:
    return f"{float(value):.4f}" if isinstance(value, (int, float)) else "n/a"
