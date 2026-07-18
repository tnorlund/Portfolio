"""Evaluate every saved checkpoint of a training run on its frozen val set.

Training reports a per-epoch ``val_f1`` from inside the loop, but that number
can be misleading (eval-set drift across runs, early-stopping latching onto a
noisy peak, label-merge config differences). To *prove* which epoch actually
generalizes best, this module re-scores **every** retained checkpoint against
the **same** held-out receipts and emits an honest entity-level F1 curve.

The key correctness guarantee is the frozen split. ``data_loader.load_datasets``
derives the train/val split by:

    random.seed(seed)
    unique_receipts = sorted({receipt_key for ex in examples})
    random.shuffle(unique_receipts)
    cut = max(1, int(len(unique_receipts) * 0.9))
    val_receipts = unique_receipts[cut:]

and records ``sha256(",".join(sorted(val_receipts)))[:16]`` as
``val_receipts_hash``. The set of ``unique_receipts`` is exactly the set of
receipts that have at least one VALID label (every such receipt yields words,
hence windowed examples, hence a ``receipt_key``). So we can reconstruct the
identical val set from the recorded seed alone and *verify* it by recomputing
the hash — a mismatch means the underlying data changed since training, which
we surface loudly rather than silently scoring a different set.

Outputs (written under ``output_dir`` and optionally synced to S3):

    epochs.json
        The curve: one entry per checkpoint with held-out F1/precision/recall
        (entity-level, seqeval) plus the training-time reported number for
        comparison, per-label F1, token accuracy, and the argmax best epoch.

    receipts/epoch-<n>/receipt-<image_id>-<receipt_id>.json
        Per-(epoch, showcase-receipt) predictions in the same shape the
        existing inference viz cache emits, so the frontend can scrub a sample
        receipt across epochs and watch labels stabilize.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
import os
import random
import shutil
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from .inference import InferenceResult, LayoutLMInference
from .data_loader import _box_from_word, _normalize_box_from_extents

logger = logging.getLogger(__name__)

_PRODUCT_DETAIL_LABELS = ("PRODUCT_NAME", "QUANTITY", "UNIT_PRICE", "LINE_TOTAL")

# ----------------------------------------------------------------------------
# BIO / label helpers (mirrors the inference-cache Lambda so the per-receipt
# records this emits are byte-compatible with what the frontend already renders)
# ----------------------------------------------------------------------------


def _get_base_label(bio_label: Optional[str]) -> str:
    """Strip a BIO prefix: ``B-DATE``/``I-DATE`` -> ``DATE``; ``O``/None -> ``O``."""
    if not bio_label or bio_label == "O":
        return "O"
    if bio_label.startswith("B-") or bio_label.startswith("I-"):
        return bio_label[2:]
    return bio_label


def _combine_bio_probabilities(
    all_probs: Dict[str, float],
) -> Dict[str, float]:
    """Sum B-/I- probabilities into base-label probabilities (preserves sum)."""
    base_probs: Dict[str, float] = {}
    for bio_label, prob in all_probs.items():
        base = _get_base_label(bio_label)
        base_probs[base] = base_probs.get(base, 0.0) + prob
    return base_probs


def _normalize_label_with_model(
    raw_label: Optional[str], infer: LayoutLMInference
) -> str:
    """Normalize a DB label to the model's base label set (uses run.json merges)."""
    if not raw_label:
        return "O"
    label = raw_label.upper()
    if label.startswith("B-") or label.startswith("I-"):
        label = label[2:]
    return infer.normalize_label(label)


def _bio_from_base_labels(base_labels: List[str]) -> List[str]:
    """Convert a per-line sequence of base labels to BIO (B- first, I- contiguous)."""
    bio: List[str] = []
    prev = "O"
    for base in base_labels:
        if base == "O":
            bio.append("O")
            prev = "O"
        else:
            bio.append(("B-" if prev != base else "I-") + base)
            prev = base
    return bio


# ----------------------------------------------------------------------------
# Frozen val-split reconstruction
# ----------------------------------------------------------------------------


def reconstruct_val_receipts(
    dynamo: Any,
    seed: int,
    *,
    label_status: str = "VALID",
    val_fraction: float = 0.1,
) -> Tuple[List[Tuple[str, int]], str]:
    """Rebuild the exact held-out receipt set a run trained against.

    Mirrors the receipt-level split in ``data_loader.load_datasets`` so the same
    seed yields the same val receipts. Returns ``(receipts, val_receipts_hash)``
    where ``receipts`` is a list of ``(image_id, receipt_id)`` and the hash is
    the 16-char sha256 used by ``SplitMetadata.val_receipts_hash`` for
    verification against the run's recorded value.

    Args:
        dynamo: DynamoClient instance.
        seed: The run's ``random_seed`` (from run.json / JobMetric).
        label_status: Validation status that defines the labeled universe.
        val_fraction: Fraction held out for validation (training uses 0.1).
    """
    from receipt_dynamo.constants import ValidationStatus

    all_labels, _ = dynamo.list_receipt_word_labels_with_status(
        ValidationStatus(label_status), limit=None, last_evaluated_key=None
    )

    # receipt_key format matches data_loader: f"{image_id}#{receipt_id:05d}"
    receipt_keys = sorted(
        {f"{lbl.image_id}#{lbl.receipt_id:05d}" for lbl in all_labels}
    )

    # Reproduce the deterministic shuffle. random.Random(seed) and
    # `random.seed(seed); random.shuffle` share the Mersenne Twister state, so
    # the shuffle order is identical without touching global RNG state.
    rng = random.Random(seed)
    rng.shuffle(receipt_keys)
    cut = max(1, int(len(receipt_keys) * (1.0 - val_fraction)))
    val_keys = receipt_keys[cut:]

    return [_parse_receipt_key(k) for k in val_keys], _hash_receipt_keys(
        val_keys
    )


def _parse_receipt_key(key: str) -> Tuple[str, int]:
    """Parse a ``"image_id#receipt_id"`` split key into ``(image_id, receipt_id)``."""
    image_id, rec_str = key.rsplit("#", 1)
    return image_id, int(rec_str)


def _hash_receipt_keys(keys: List[str]) -> str:
    """16-char sha256 of sorted keys (matches ``SplitMetadata.val_receipts_hash``)."""
    return hashlib.sha256(
        ",".join(sorted(keys)).encode()
    ).hexdigest()[:16]


# ----------------------------------------------------------------------------
# S3 discovery
# ----------------------------------------------------------------------------


def _parse_s3_uri(uri: str) -> Tuple[str, str]:
    parsed = urlparse(uri)
    return parsed.netloc, parsed.path.lstrip("/")


def list_checkpoints(
    s3: Any, bucket: str, run_prefix: str
) -> List[Dict[str, Any]]:
    """List every checkpoint directory under a run prefix.

    The trainer keeps the full per-epoch set under ``<run>/checkpoints/`` and
    only the last few (plus ``best/``) at the run root, so both locations are
    scanned and deduped by step. Returns ``{"name", "step", "s3_uri"}`` sorted
    by step with a synthetic ``best`` entry (step=None) last. Only directories
    that actually contain model weights are included.
    """
    if not run_prefix.endswith("/"):
        run_prefix += "/"

    by_step: Dict[int, Dict[str, Any]] = {}
    best: Optional[Dict[str, Any]] = None

    # checkpoints/ first so it wins ties; run root supplies best/ and any
    # checkpoints not retained in the subdir.
    for base in (run_prefix + "checkpoints/", run_prefix):
        token: Optional[str] = None
        while True:
            kwargs: Dict[str, Any] = {
                "Bucket": bucket,
                "Prefix": base,
                "Delimiter": "/",
            }
            if token:
                kwargs["ContinuationToken"] = token
            resp = s3.list_objects_v2(**kwargs)
            for cp in resp.get("CommonPrefixes", []) or []:
                prefix = cp["Prefix"]
                name = prefix[len(base):].rstrip("/")
                if name == "best":
                    if best is None and _has_weights(s3, bucket, prefix):
                        best = {
                            "name": "best",
                            "step": None,
                            "s3_uri": f"s3://{bucket}/{prefix}",
                        }
                    continue
                if not name.startswith("checkpoint-"):
                    continue
                try:
                    step = int(name.split("-", 1)[1])
                except ValueError:
                    continue
                if step in by_step:
                    continue
                if not _has_weights(s3, bucket, prefix):
                    continue
                by_step[step] = {
                    "name": name,
                    "step": step,
                    "s3_uri": f"s3://{bucket}/{prefix}",
                }
            if resp.get("IsTruncated"):
                token = resp.get("NextContinuationToken")
            else:
                break

    checkpoints = sorted(by_step.values(), key=lambda c: c["step"])
    if best is not None:
        checkpoints.append(best)
    return checkpoints


# Only these files are needed to load a model for inference. Crucially this
# excludes optimizer.pt (~900MB) / scheduler.pt / rng_state, which would
# otherwise be downloaded for every checkpoint.
_INFERENCE_FILES = (
    "config.json",
    "model.safetensors",
    "pytorch_model.bin",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.txt",
    "special_tokens_map.json",
    "preprocessor_config.json",
)


def _download_checkpoint_files(
    s3: Any,
    ckpt_s3_uri: str,
    dest: str,
    run_json: Optional[Dict[str, Any]] = None,
) -> None:
    """Download just the inference files of a checkpoint into ``dest``.

    Also writes ``run.json`` locally so ``LayoutLMInference`` picks up the
    label-merge config without a second S3 round trip. Skips the large
    optimizer/scheduler state that is irrelevant to inference.
    """
    os.makedirs(dest, exist_ok=True)
    bucket, prefix = _parse_s3_uri(ckpt_s3_uri)
    if not prefix.endswith("/"):
        prefix += "/"
    for fname in _INFERENCE_FILES:
        try:
            s3.download_file(bucket, prefix + fname, os.path.join(dest, fname))
        except Exception:  # pylint: disable=broad-exception-caught
            # Most checkpoints have only one of safetensors/bin; missing
            # optional files are expected.
            continue
    if run_json is not None:
        with open(
            os.path.join(dest, "run.json"), "w", encoding="utf-8"
        ) as f:
            json.dump(run_json, f)


def _has_weights(s3: Any, bucket: str, prefix: str) -> bool:
    page = s3.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1000)
    for obj in page.get("Contents", []) or []:
        key = obj["Key"]
        if key.endswith("model.safetensors") or key.endswith(
            "pytorch_model.bin"
        ):
            return True
    return False


def load_run_json(s3: Any, bucket: str, run_prefix: str) -> Dict[str, Any]:
    """Download and parse ``<run_prefix>/run.json`` (empty dict if absent)."""
    if not run_prefix.endswith("/"):
        run_prefix += "/"
    key = f"{run_prefix}run.json"
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        return json.loads(obj["Body"].read())
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.warning(
            "Could not load run.json at s3://%s/%s: %s", bucket, key, e
        )
        return {}


def _build_step_epoch_map(
    run_json: Dict[str, Any],
) -> Tuple[Dict[int, int], Dict[int, float]]:
    """From run.json, map global_step -> (epoch, training-reported f1).

    Tolerant of schema differences: scans any list of per-epoch dicts and reads
    step/epoch/f1 under common key aliases.
    """
    step_to_epoch: Dict[int, int] = {}
    step_to_f1: Dict[int, float] = {}

    candidates: List[Dict[str, Any]] = []
    for key in ("epoch_metrics", "epochs", "metrics", "history"):
        val = run_json.get(key)
        if isinstance(val, list):
            candidates = [e for e in val if isinstance(e, dict)]
            if candidates:
                break

    for entry in candidates:
        step = entry.get("global_step", entry.get("step"))
        epoch = entry.get("epoch")
        if step is None:
            continue
        try:
            step = int(step)
        except (TypeError, ValueError):
            continue
        if epoch is not None:
            try:
                step_to_epoch[step] = int(round(float(epoch)))
            except (TypeError, ValueError):
                pass
        for f1_key in ("eval_f1", "val_f1", "f1", "eval_val_f1"):
            if f1_key in entry and entry[f1_key] is not None:
                try:
                    step_to_f1[step] = float(entry[f1_key])
                except (TypeError, ValueError):
                    pass
                break

    return step_to_epoch, step_to_f1


# ----------------------------------------------------------------------------
# Per-receipt inference + scoring
# ----------------------------------------------------------------------------


def _receipt_line_inputs(
    details: Any,
) -> Tuple[List[List[str]], List[List[List[int]]], List[int]]:
    """Build per-line tokens + normalized boxes from cached receipt details.

    Replicates ``LayoutLMInference.predict_receipt_from_dynamo`` normalization so
    we can reuse one DynamoDB fetch across all checkpoints instead of re-fetching
    each receipt for every epoch.
    """
    by_line: Dict[int, List[Tuple[int, str, List[float]]]] = {}
    max_x = 0.0
    max_y = 0.0
    for w in details.words:
        x0, y0, x1, y1 = _box_from_word(w)
        max_x = max(max_x, x1)
        max_y = max(max_y, y1)
        by_line.setdefault(w.line_id, []).append(
            (w.word_id, w.text, [x0, y0, x1, y1])
        )

    tokens_per_line: List[List[str]] = []
    boxes_per_line: List[List[List[int]]] = []
    line_ids: List[int] = []
    for line_id, items in sorted(by_line.items()):
        items.sort(key=lambda t: t[0])
        tokens_per_line.append([tok for _, tok, _ in items])
        boxes_per_line.append(
            [
                _normalize_box_from_extents(
                    b[0], b[1], b[2], b[3], max_x, max_y
                )
                for _, _, b in items
            ]
        )
        line_ids.append(line_id)
    return tokens_per_line, boxes_per_line, line_ids


def build_receipt_record(
    infer: LayoutLMInference,
    details: Any,
    image_id: str,
    receipt_id: int,
    *,
    valid_status: Any,
) -> Tuple[Dict[str, Any], List[List[str]], List[List[str]]]:
    """Run inference for one receipt and build a viz-cache-shaped record.

    Returns ``(record, y_true_lines, y_pred_lines)`` where the two sequence lists
    are per-line BIO tag lists suitable for seqeval entity-level scoring.
    """
    t0 = time.perf_counter()
    # Default to windowed inference (matches how the model was trained); set
    # LAYOUTLM_INFERENCE_MODE=per_line to A/B against the legacy per-line path.
    if os.getenv("LAYOUTLM_INFERENCE_MODE", "windowed") == "windowed":
        inference_result = infer.predict_receipt_windowed(
            image_id, receipt_id, details.words
        )
    else:
        tokens_per_line, boxes_per_line, line_ids = _receipt_line_inputs(details)
        inference_result = InferenceResult(
            image_id=image_id,
            receipt_id=receipt_id,
            lines=infer.predict_lines(
                tokens_per_line, boxes_per_line, line_ids
            ),
            meta={},
        )
    inference_time_ms = (time.perf_counter() - t0) * 1000.0

    # Ground truth (base labels, keyed by (line_id, word_id) since word_id is
    # only unique within a line).
    ground_truth_base: Dict[Tuple[int, int], str] = {}
    ground_truth_original: Dict[Tuple[int, int], str] = {}
    for label in details.labels:
        if label.validation_status == valid_status:
            ground_truth_original[(label.line_id, label.word_id)] = label.label
            ground_truth_base[(label.line_id, label.word_id)] = (
                _normalize_label_with_model(label.label, infer)
            )

    # Map tokens back to word_ids per line (position first, then text fallback).
    words_by_line: Dict[int, List[Any]] = {}
    word_lookup: Dict[Tuple[int, str], int] = {}
    for w in details.words:
        word_lookup[(w.line_id, w.text)] = w.word_id
        word_lookup[(w.line_id, w.text.strip())] = w.word_id
        words_by_line.setdefault(w.line_id, []).append(w)
    for line_id in words_by_line:
        words_by_line[line_id].sort(key=lambda w: w.word_id)

    predictions: List[Dict[str, Any]] = []
    y_true_lines: List[List[str]] = []
    y_pred_lines: List[List[str]] = []

    for line_pred in inference_result.lines:
        line_id = line_pred.line_id
        line_words = words_by_line.get(line_id, [])

        line_word_ids: List[Optional[int]] = []
        line_base_labels: List[str] = []
        for idx, token in enumerate(line_pred.tokens):
            word_id: Optional[int] = None
            if idx < len(line_words):
                w = line_words[idx]
                if token == w.text or token.strip() == w.text.strip():
                    word_id = w.word_id
            if word_id is None:
                word_id = word_lookup.get((line_id, token)) or word_lookup.get(
                    (line_id, token.strip())
                )
            if word_id is None:
                for w in line_words:
                    if token == w.text or token.strip() == w.text.strip():
                        word_id = w.word_id
                        break
            line_word_ids.append(word_id)
            line_base_labels.append(
                ground_truth_base.get((line_id, word_id), "O")
                if word_id is not None
                else "O"
            )

        line_bio_gt = _bio_from_base_labels(line_base_labels)

        # Predicted BIO labels straight from the model (id2label values).
        line_bio_pred = [
            line_pred.labels[i] if i < len(line_pred.labels) else "O"
            for i in range(len(line_pred.tokens))
        ]
        y_true_lines.append(line_bio_gt)
        y_pred_lines.append(line_bio_pred)

        for idx, token in enumerate(line_pred.tokens):
            word_id = line_word_ids[idx]
            gt_bio = line_bio_gt[idx]
            pred_label = (
                line_pred.labels[idx] if idx < len(line_pred.labels) else "O"
            )
            confidence = (
                line_pred.confidences[idx]
                if idx < len(line_pred.confidences)
                else 0.0
            )
            all_probs = {}
            if line_pred.all_probabilities and idx < len(
                line_pred.all_probabilities
            ):
                all_probs = line_pred.all_probabilities[idx]

            predictions.append(
                {
                    "word_id": word_id,
                    "line_id": line_id,
                    "text": token,
                    "predicted_label": pred_label,
                    "ground_truth_label": gt_bio if gt_bio != "O" else None,
                    "predicted_label_base": _get_base_label(pred_label),
                    "ground_truth_label_base": (
                        _get_base_label(gt_bio) if gt_bio != "O" else None
                    ),
                    "ground_truth_label_original": (
                        ground_truth_original.get((line_id, word_id))
                        if word_id is not None
                        else None
                    ),
                    "predicted_confidence": float(confidence),
                    "is_correct": pred_label == gt_bio,
                    "all_class_probabilities": all_probs,
                    "all_class_probabilities_base": _combine_bio_probabilities(
                        all_probs
                    ),
                }
            )

    record = {
        "receipt_id": f"{image_id}_{receipt_id}",
        "original": {
            "receipt": dict(details.receipt),
            "words": [dict(w) for w in details.words],
            "predictions": predictions,
        },
        "inference_time_ms": round(inference_time_ms, 2),
    }
    return record, y_true_lines, y_pred_lines


def _seqeval_scores(
    y_true_lines: List[List[str]],
    y_pred_lines: List[List[str]],
) -> Dict[str, Any]:
    """Entity-level F1/precision/recall via seqeval, with per-label breakdown.

    Falls back to token-level accuracy if seqeval is unavailable.
    """
    try:
        seqeval = importlib.import_module("seqeval.metrics")
    except ModuleNotFoundError:
        acc = _token_accuracy(y_true_lines, y_pred_lines)
        return {
            "metric": "token_accuracy",
            "f1": acc,
            "precision": acc,
            "recall": acc,
            "per_label_f1": {},
            "per_label_precision": {},
            "per_label_recall": {},
            "per_label_support": {},
        }

    f1 = float(seqeval.f1_score(y_true_lines, y_pred_lines))
    precision = float(seqeval.precision_score(y_true_lines, y_pred_lines))
    recall = float(seqeval.recall_score(y_true_lines, y_pred_lines))

    per_label_f1: Dict[str, float] = {}
    per_label_precision: Dict[str, float] = {}
    per_label_recall: Dict[str, float] = {}
    per_label_support: Dict[str, int] = {}
    report_fn = getattr(seqeval, "classification_report", None)
    if report_fn is not None:
        try:
            report = report_fn(
                y_true_lines, y_pred_lines, output_dict=True, zero_division=0
            )
            for label, stats in report.items():
                if isinstance(stats, dict) and "f1-score" in stats:
                    per_label_f1[label] = float(stats["f1-score"])
                    per_label_precision[label] = float(
                        stats.get("precision", 0.0)
                    )
                    per_label_recall[label] = float(stats.get("recall", 0.0))
                    per_label_support[label] = int(stats.get("support", 0))
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    return {
        "metric": "seqeval_entity_f1",
        "f1": f1,
        "precision": precision,
        "recall": recall,
        "per_label_f1": per_label_f1,
        "per_label_precision": per_label_precision,
        "per_label_recall": per_label_recall,
        "per_label_support": per_label_support,
    }


def _token_accuracy(
    y_true_lines: List[List[str]], y_pred_lines: List[List[str]]
) -> float:
    total = correct = 0
    for yt, yp in zip(y_true_lines, y_pred_lines):
        for a, b in zip(yt, yp):
            total += 1
            correct += int(a == b)
    return correct / total if total else 0.0


def _safe_ratio(numerator: int | float, denominator: int | float) -> Optional[float]:
    return float(numerator) / float(denominator) if denominator else None


def _bio_spans(tags: List[str]) -> List[Tuple[str, int, int]]:
    """Return entity spans as ``(base_label, start, end_exclusive)``."""
    spans: List[Tuple[str, int, int]] = []
    active_label: Optional[str] = None
    active_start: Optional[int] = None

    for idx, tag in enumerate([*tags, "O"]):
        if not tag or tag == "O":
            prefix = "O"
            base = "O"
        elif tag.startswith("B-") or tag.startswith("I-"):
            prefix = tag[:1]
            base = tag[2:]
        else:
            prefix = "B"
            base = tag

        starts_new = base != "O" and (
            prefix == "B" or active_label is None or active_label != base
        )
        closes_active = base == "O" or starts_new

        if closes_active and active_label is not None and active_start is not None:
            spans.append((active_label, active_start, idx))
            active_label = None
            active_start = None

        if starts_new:
            active_label = base
            active_start = idx

    return spans


def _evaluation_diagnostics(
    y_true_lines: List[List[str]],
    y_pred_lines: List[List[str]],
    *,
    per_label_f1: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Compute explanation-oriented metrics for the held-out curve."""
    per_label_f1 = per_label_f1 or {}
    total_tokens = 0
    correct_tokens = 0
    gold_o_tokens = 0
    pred_o_tokens = 0
    gold_entity_tokens = 0
    pred_entity_tokens = 0
    correct_o_tokens = 0
    correct_entity_tokens = 0
    gold_label_tokens: Dict[str, int] = {}
    pred_label_tokens: Dict[str, int] = {}
    true_positive_label_tokens: Dict[str, int] = {}
    token_confusions: Dict[Tuple[str, str], int] = {}
    gold_entity_counts: Dict[str, int] = {}
    pred_entity_counts: Dict[str, int] = {}

    for y_true, y_pred in zip(y_true_lines, y_pred_lines):
        for label, _start, _end in _bio_spans(y_true):
            gold_entity_counts[label] = gold_entity_counts.get(label, 0) + 1
        for label, _start, _end in _bio_spans(y_pred):
            pred_entity_counts[label] = pred_entity_counts.get(label, 0) + 1

        for true_tag, pred_tag in zip(y_true, y_pred):
            gold = _get_base_label(true_tag)
            pred = _get_base_label(pred_tag)
            total_tokens += 1
            if gold == pred:
                correct_tokens += 1
            else:
                token_confusions[(gold, pred)] = (
                    token_confusions.get((gold, pred), 0) + 1
                )

            if gold == "O":
                gold_o_tokens += 1
                if pred == "O":
                    correct_o_tokens += 1
            else:
                gold_entity_tokens += 1
                gold_label_tokens[gold] = gold_label_tokens.get(gold, 0) + 1
                if gold == pred:
                    correct_entity_tokens += 1
                    true_positive_label_tokens[gold] = (
                        true_positive_label_tokens.get(gold, 0) + 1
                    )

            if pred == "O":
                pred_o_tokens += 1
            else:
                pred_entity_tokens += 1
                pred_label_tokens[pred] = pred_label_tokens.get(pred, 0) + 1

    labels = sorted(
        set(gold_label_tokens)
        | set(pred_label_tokens)
        | set(gold_entity_counts)
        | set(pred_entity_counts)
    )
    per_label_token_counts: Dict[str, Dict[str, Any]] = {}
    per_label_entity_counts: Dict[str, Dict[str, Any]] = {}
    for label in labels:
        gold_tokens = gold_label_tokens.get(label, 0)
        pred_tokens = pred_label_tokens.get(label, 0)
        token_tp = true_positive_label_tokens.get(label, 0)
        per_label_token_counts[label] = {
            "gold_tokens": gold_tokens,
            "predicted_tokens": pred_tokens,
            "true_positive_tokens": token_tp,
            "prediction_gold_ratio": _safe_ratio(pred_tokens, gold_tokens),
        }
        gold_entities = gold_entity_counts.get(label, 0)
        pred_entities = pred_entity_counts.get(label, 0)
        per_label_entity_counts[label] = {
            "gold_entities": gold_entities,
            "predicted_entities": pred_entities,
            "prediction_gold_ratio": _safe_ratio(pred_entities, gold_entities),
        }

    product_f1_values = [
        per_label_f1[label]
        for label in _PRODUCT_DETAIL_LABELS
        if isinstance(per_label_f1.get(label), (int, float))
    ]
    product_gold_entities = sum(
        gold_entity_counts.get(label, 0) for label in _PRODUCT_DETAIL_LABELS
    )
    product_pred_entities = sum(
        pred_entity_counts.get(label, 0) for label in _PRODUCT_DETAIL_LABELS
    )

    top_confusions = [
        {"gold": gold, "predicted": pred, "count": count}
        for (gold, pred), count in sorted(
            token_confusions.items(), key=lambda item: item[1], reverse=True
        )[:20]
    ]

    return {
        "token_counts": {
            "total": total_tokens,
            "correct": correct_tokens,
            "gold_o": gold_o_tokens,
            "predicted_o": pred_o_tokens,
            "gold_entity": gold_entity_tokens,
            "predicted_entity": pred_entity_tokens,
        },
        "entity_counts": {
            "gold": sum(gold_entity_counts.values()),
            "predicted": sum(pred_entity_counts.values()),
            "prediction_gold_ratio": _safe_ratio(
                sum(pred_entity_counts.values()), sum(gold_entity_counts.values())
            ),
        },
        "rates": {
            "gold_entity_token_rate": _safe_ratio(
                gold_entity_tokens, total_tokens
            ),
            "predicted_entity_token_rate": _safe_ratio(
                pred_entity_tokens, total_tokens
            ),
            "o_token_accuracy": _safe_ratio(correct_o_tokens, gold_o_tokens),
            "entity_token_accuracy": _safe_ratio(
                correct_entity_tokens, gold_entity_tokens
            ),
        },
        "product_detail": {
            "labels": list(_PRODUCT_DETAIL_LABELS),
            "macro_f1": (
                sum(product_f1_values) / len(product_f1_values)
                if product_f1_values
                else None
            ),
            "gold_entities": product_gold_entities,
            "predicted_entities": product_pred_entities,
            "prediction_gold_ratio": _safe_ratio(
                product_pred_entities, product_gold_entities
            ),
        },
        "per_label_token_counts": per_label_token_counts,
        "per_label_entity_counts": per_label_entity_counts,
        "top_token_confusions": top_confusions,
    }


# ----------------------------------------------------------------------------
# Orchestration
# ----------------------------------------------------------------------------


def _score_checkpoint(
    infer: LayoutLMInference,
    details_by_receipt: Dict[Tuple[str, int], Any],
    showcase_keys: List[str],
    valid_status: Any,
) -> Tuple[
    List[List[str]], List[List[str]], Dict[str, Dict[str, Any]], List[float]
]:
    """Run one checkpoint over all val receipts; collect sequences + showcase.

    Returns ``(y_true_lines, y_pred_lines, showcase_records, inference_times_ms)``
    where the records dict is keyed by ``"image_id_receipt_id"`` for the
    showcase subset and ``inference_times_ms`` is the per-receipt windowed
    inference wall-time (used to surface device throughput in the viz).
    """
    all_true: List[List[str]] = []
    all_pred: List[List[str]] = []
    showcase: Dict[str, Dict[str, Any]] = {}
    inference_times_ms: List[float] = []
    for (image_id, receipt_id), details in details_by_receipt.items():
        try:
            record, yt, yp = build_receipt_record(
                infer, details, image_id, receipt_id, valid_status=valid_status
            )
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning(
                "Inference failed for %s/%s: %s", image_id, receipt_id, e
            )
            continue
        all_true.extend(yt)
        all_pred.extend(yp)
        t = record.get("inference_time_ms")
        if isinstance(t, (int, float)):
            inference_times_ms.append(float(t))
        key = f"{image_id}_{receipt_id}"
        if key in showcase_keys:
            showcase[key] = record
    return all_true, all_pred, showcase, inference_times_ms


def _device_info() -> Dict[str, Any]:
    """Best-effort descriptor of the compute the eval ran on.

    Lets the viz label the timing as GPU vs CPU (and name the GPU), so the
    "faster GPU times" are self-describing rather than a bare millisecond count.
    """
    info: Dict[str, Any] = {
        "device": "unknown",
        "gpu_name": None,
        # Set by the Processing job / trainer when known (see infra component).
        "instance_type": os.getenv("EVAL_INSTANCE_TYPE")
        or os.getenv("SM_CURRENT_INSTANCE_TYPE"),
    }
    try:
        import torch  # heavy dep; imported lazily

        if torch.cuda.is_available():
            info["device"] = "cuda"
            try:
                info["gpu_name"] = torch.cuda.get_device_name(0)
            except Exception:  # pylint: disable=broad-exception-caught
                pass
        else:
            info["device"] = "cpu"
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    return info


def evaluate_run(
    *,
    dynamo: Any,
    bucket: str,
    run_prefix: str,
    job_name: str,
    output_dir: str,
    seed: Optional[int] = None,
    max_receipts: Optional[int] = None,
    num_showcase: int = 5,
    window_size: Optional[int] = None,
    window_stride: Optional[int] = None,
    allow_hash_mismatch: bool = False,
    s3_client: Any = None,
) -> Dict[str, Any]:
    """Evaluate all checkpoints of a run on its frozen val set.

    Args:
        dynamo: DynamoClient instance.
        bucket: S3 bucket holding the run.
        run_prefix: Key prefix of the run (e.g. ``runs/<job>/``).
        job_name: Human-readable job name (recorded in the output).
        output_dir: Local directory to write ``epochs.json`` + per-receipt JSON.
        seed: Override the run's recorded random seed (else read from run.json).
        max_receipts: Cap val receipts evaluated (None = full frozen set).
        num_showcase: How many val receipts to persist per epoch for scrubbing.
        allow_hash_mismatch: Proceed (with a warning) if the reconstructed val
            hash doesn't match the run's recorded value. Default fails loudly.
        s3_client: Optional boto3 S3 client (created if not supplied).

    Returns:
        The ``epochs.json`` payload (also written to disk).
    """
    from receipt_dynamo.constants import ValidationStatus

    valid_status = ValidationStatus.VALID
    if s3_client is None:
        boto3 = importlib.import_module("boto3")
        s3_client = boto3.client("s3")

    if not run_prefix.endswith("/"):
        run_prefix += "/"

    run_json = load_run_json(s3_client, bucket, run_prefix)
    split_meta = (run_json.get("split_metadata") or {}) if run_json else {}
    recorded_hash = split_meta.get("val_receipts_hash")
    recorded_seed = split_meta.get("random_seed")

    # Window inference exactly as the run trained. Precedence: explicit arg >
    # the run's recorded window config > env > data_loader defaults (200/150).
    # Export to env so predict_receipt_windowed (called without window args in
    # build_receipt_record) uses the resolved values.
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
    logger.info(
        "Windowed inference config: window_size=%s stride=%s (source: %s)",
        resolved_ws,
        resolved_stride,
        "arg" if window_size is not None
        else "run.json" if split_meta.get("window_size") is not None
        else "env/default",
    )

    persisted_keys = split_meta.get("val_receipt_keys")
    if persisted_keys:
        # Drift-proof: use exactly the receipts training held out. Newer runs
        # persist this list, so no re-derivation (or drift risk) is needed.
        val_set_source = "persisted_val_receipt_keys"
        val_receipts = [_parse_receipt_key(k) for k in persisted_keys]
        computed_hash = _hash_receipt_keys(list(persisted_keys))
        hash_verified = (
            computed_hash == recorded_hash if recorded_hash else True
        )
        if seed is None:
            seed = recorded_seed
        logger.info(
            "Using persisted val set: %d receipts (hash %s, verified=%s)",
            len(val_receipts),
            computed_hash,
            hash_verified,
        )
    else:
        # Older runs only recorded the seed + hash. Reconstruct the split and
        # verify against the recorded hash; a mismatch means the labeled data
        # drifted since training (the exact training set is unrecoverable).
        val_set_source = "reconstructed_from_seed"
        if seed is None:
            seed = recorded_seed
        if seed is None:
            raise ValueError(
                "No random seed available: pass seed= or ensure run.json "
                "contains split_metadata.random_seed"
            )
        logger.info("Reconstructing frozen val split (seed=%s)", seed)
        val_receipts, computed_hash = reconstruct_val_receipts(
            dynamo, int(seed)
        )
        hash_verified = bool(recorded_hash) and computed_hash == recorded_hash
        if recorded_hash and not hash_verified:
            msg = (
                f"val_receipts_hash mismatch: recorded={recorded_hash} "
                f"computed={computed_hash}. The labeled receipt set changed "
                f"since training, so this is NOT the exact set the model "
                f"trained against (every epoch is still scored on the same "
                f"reconstructed set, so the comparison remains valid)."
            )
            if not allow_hash_mismatch:
                raise ValueError(
                    msg + " Pass allow_hash_mismatch=True to override."
                )
            logger.warning(msg)
        logger.info(
            "Reconstructed val set: %d receipts (hash %s, verified=%s)",
            len(val_receipts),
            computed_hash,
            hash_verified,
        )

    if max_receipts is not None:
        val_receipts = val_receipts[:max_receipts]
    showcase_keys = [f"{i}_{r}" for i, r in val_receipts[:num_showcase]]

    # Fetch each val receipt's details once and reuse across all checkpoints.
    logger.info("Prefetching %d receipt details", len(val_receipts))
    details_by_receipt: Dict[Tuple[str, int], Any] = {}
    for image_id, receipt_id in val_receipts:
        try:
            details_by_receipt[(image_id, receipt_id)] = (
                dynamo.get_receipt_details(image_id, receipt_id)
            )
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning(
                "Skipping receipt %s/%s (details fetch failed: %s)",
                image_id,
                receipt_id,
                e,
            )

    checkpoints = list_checkpoints(s3_client, bucket, run_prefix)
    if not checkpoints:
        raise ValueError(
            f"No checkpoints with weights found under "
            f"s3://{bucket}/{run_prefix}"
        )
    step_to_epoch, step_to_f1 = _build_step_epoch_map(run_json)
    logger.info("Found %d checkpoints to evaluate", len(checkpoints))

    os.makedirs(output_dir, exist_ok=True)

    epoch_entries: List[Dict[str, Any]] = []
    label_list: Optional[List[str]] = None
    label_merges: Dict[str, Any] = {}

    for ckpt in checkpoints:
        name = ckpt["name"]
        step = ckpt["step"]
        logger.info("Evaluating checkpoint %s", name)
        model_cache = os.path.join(output_dir, "_models", name)
        try:
            # Pull only inference files (not optimizer state) and load locally.
            _download_checkpoint_files(
                s3_client, ckpt["s3_uri"], model_cache, run_json=run_json
            )
            infer = LayoutLMInference(model_dir=model_cache)
            if label_list is None:
                label_list = infer.label_list
                label_merges = infer.label_merges

            epoch_num = step_to_epoch.get(step) if step is not None else None
            epoch_label = (
                epoch_num
                if epoch_num is not None
                else (name if name == "best" else step)
            )

            all_true, all_pred, showcase, inf_times = _score_checkpoint(
                infer, details_by_receipt, showcase_keys, valid_status
            )

            for key, record in showcase.items():
                img, rid = key.rsplit("_", 1)
                showcase_dir = os.path.join(
                    output_dir, "receipts", f"epoch-{epoch_label}"
                )
                os.makedirs(showcase_dir, exist_ok=True)
                record["epoch"] = epoch_num
                record["checkpoint"] = name
                record["label_list"] = label_list
                with open(
                    os.path.join(showcase_dir, f"receipt-{img}-{rid}.json"),
                    "w",
                    encoding="utf-8",
                ) as f:
                    json.dump(record, f, default=str)

            scores = _seqeval_scores(all_true, all_pred)
            diagnostics = _evaluation_diagnostics(
                all_true,
                all_pred,
                per_label_f1=scores.get("per_label_f1", {}),
            )
            entry = {
                "checkpoint": name,
                "step": step,
                "epoch": epoch_num,
                "heldout_f1": scores["f1"],
                "heldout_precision": scores["precision"],
                "heldout_recall": scores["recall"],
                "heldout_metric": scores["metric"],
                "per_label_f1": scores["per_label_f1"],
                "per_label_precision": scores.get("per_label_precision", {}),
                "per_label_recall": scores.get("per_label_recall", {}),
                "per_label_support": scores.get("per_label_support", {}),
                "diagnostics": diagnostics,
                "product_detail_macro_f1": diagnostics["product_detail"][
                    "macro_f1"
                ],
                "entity_prediction_rate": diagnostics["rates"][
                    "predicted_entity_token_rate"
                ],
                "gold_entity_rate": diagnostics["rates"]["gold_entity_token_rate"],
                "heldout_f1_delta_vs_training_reported": (
                    scores["f1"] - step_to_f1[step]
                    if step is not None and step_to_f1.get(step) is not None
                    else None
                ),
                "token_accuracy": _token_accuracy(all_true, all_pred),
                "training_reported_f1": (
                    step_to_f1.get(step) if step is not None else None
                ),
                "num_receipts_evaluated": len(details_by_receipt),
                "avg_inference_ms": (
                    round(sum(inf_times) / len(inf_times), 2)
                    if inf_times
                    else None
                ),
                "total_inference_ms": (
                    round(sum(inf_times), 2) if inf_times else None
                ),
            }
            epoch_entries.append(entry)
            logger.info(
                "  %s: held-out F1=%.4f (training reported=%s)",
                name,
                entry["heldout_f1"],
                entry["training_reported_f1"],
            )
        finally:
            # Free disk before the next checkpoint (~450MB weights each).
            shutil.rmtree(model_cache, ignore_errors=True)

    # Best epoch by held-out F1, excluding the synthetic "best" alias so the
    # winner is an actual epoch the curve can point at.
    scored_epochs = [e for e in epoch_entries if e["checkpoint"] != "best"]
    best_entry = (
        max(scored_epochs, key=lambda e: e["heldout_f1"])
        if scored_epochs
        else None
    )

    job_results = (run_json.get("results") or {}) if run_json else {}
    best_reported = job_results.get("best_epoch")
    if best_reported is None and step_to_f1:
        # run.json carries no results block (best_epoch lives on the Dynamo Job
        # entity), so derive the training-reported winner from epoch_metrics.
        best_step = max(step_to_f1, key=step_to_f1.get)
        best_reported = step_to_epoch.get(best_step, best_step)
    payload = {
        "job_name": job_name,
        "run_s3_uri": f"s3://{bucket}/{run_prefix}",
        "val_set_source": val_set_source,
        "val_receipts_hash": computed_hash,
        "val_receipts_hash_recorded": recorded_hash,
        "val_receipts_hash_verified": hash_verified,
        "num_val_receipts": len(details_by_receipt),
        "random_seed": int(seed) if seed is not None else None,
        "label_list": label_list,
        "label_merges": label_merges,
        "metric": "seqeval_entity_f1",
        "inference_mode": os.getenv("LAYOUTLM_INFERENCE_MODE", "windowed"),
        "window_size": resolved_ws,
        "window_stride": resolved_stride,
        "compute": _device_info(),
        "epochs": epoch_entries,
        "best_epoch_heldout": (best_entry["epoch"] if best_entry else None),
        "best_checkpoint_heldout": (
            best_entry["checkpoint"] if best_entry else None
        ),
        "best_epoch_training_reported": best_reported,
        "showcase_receipt_keys": showcase_keys,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    with open(
        os.path.join(output_dir, "epochs.json"), "w", encoding="utf-8"
    ) as f:
        json.dump(payload, f, indent=2, default=str)

    return payload


def sync_outputs_to_s3(
    output_dir: str, output_s3_uri: str, s3_client: Any = None
) -> None:
    """Upload ONLY the eval artifacts (``epochs.json`` + the ``receipts/`` tree)
    to an S3 prefix.

    Deliberately scoped: in the in-training path ``output_dir`` is the HF Trainer
    output dir, which also holds multi-GB ``checkpoint-*/`` binaries (synced
    separately) and ``_models/`` checkpoint weights — none of which belong here.
    """
    if s3_client is None:
        boto3 = importlib.import_module("boto3")
        s3_client = boto3.client("s3")
    bucket, prefix = _parse_s3_uri(output_s3_uri)
    if prefix and not prefix.endswith("/"):
        prefix += "/"

    for root, _dirs, files in os.walk(output_dir):
        rel_root = os.path.relpath(root, output_dir)
        top = rel_root.split(os.sep)[0]
        # Only the top-level epochs.json and everything under receipts/.
        if top not in (".", "receipts"):
            continue
        for fname in files:
            if top == "." and fname != "epochs.json":
                continue
            local_path = os.path.join(root, fname)
            rel = os.path.relpath(local_path, output_dir).replace(os.sep, "/")
            extra = (
                {"ContentType": "application/json"}
                if fname.endswith(".json")
                else None
            )
            s3_client.upload_file(
                local_path, bucket, f"{prefix}{rel}", ExtraArgs=extra
            )
    logger.info("Synced eval outputs to %s", output_s3_uri)


# ---------------------------------------------------------------------------
# Live (in-training) evaluation
#
# The standalone ``evaluate_run`` spins up a separate Processing job that
# re-downloads every checkpoint. But during training the model, the frozen val
# set, and the GPU are already hot — so the trainer can score each epoch's
# just-saved checkpoint in-process and emit the same ``epochs.json`` live. These
# helpers expose the per-checkpoint primitives for that path; they reuse the
# exact scoring used by ``evaluate_run`` so live and retro numbers match.
# ---------------------------------------------------------------------------


def load_val_details(
    dynamo: Any, val_keys: List[Tuple[str, int]]
) -> Dict[Tuple[str, int], Any]:
    """Load ``ReceiptDetails`` for the given (image_id, receipt_id) val keys."""
    details_by_receipt: Dict[Tuple[str, int], Any] = {}
    for (image_id, receipt_id) in val_keys:
        try:
            details_by_receipt[(image_id, receipt_id)] = (
                dynamo.get_receipt_details(image_id, receipt_id)
            )
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning(
                "Failed to load val receipt %s/%s: %s",
                image_id,
                receipt_id,
                e,
            )
    return details_by_receipt


def evaluate_live_checkpoint(
    checkpoint_dir: str,
    details_by_receipt: Dict[Tuple[str, int], Any],
    *,
    output_dir: str,
    step: Optional[int],
    epoch_num: Optional[int],
    training_reported_f1: Optional[float],
    showcase_keys: List[str],
    valid_status: Any,
) -> Tuple[Dict[str, Any], List[str], Dict[str, List[str]]]:
    """Score one just-saved checkpoint dir against pre-loaded val receipts.

    Loads ``LayoutLMInference`` from the checkpoint dir (same as the standalone
    path → identical numbers), runs windowed inference, writes showcase records
    under ``output_dir/receipts/epoch-<n>/``, and returns
    ``(entry, label_list, label_merges)`` where ``entry`` matches the shape of
    ``evaluate_run``'s epoch entries.
    """
    infer = LayoutLMInference(model_dir=checkpoint_dir)
    label_list = infer.label_list
    label_merges = infer.label_merges

    all_true, all_pred, showcase, inf_times = _score_checkpoint(
        infer, details_by_receipt, showcase_keys, valid_status
    )

    epoch_label = epoch_num if epoch_num is not None else step
    checkpoint_name = (
        f"checkpoint-{step}" if step is not None else "live"
    )
    for key, record in showcase.items():
        img, rid = key.rsplit("_", 1)
        showcase_dir = os.path.join(
            output_dir, "receipts", f"epoch-{epoch_label}"
        )
        os.makedirs(showcase_dir, exist_ok=True)
        record["epoch"] = epoch_num
        record["checkpoint"] = checkpoint_name
        record["label_list"] = label_list
        with open(
            os.path.join(showcase_dir, f"receipt-{img}-{rid}.json"),
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(record, f, default=str)

    scores = _seqeval_scores(all_true, all_pred)
    diagnostics = _evaluation_diagnostics(
        all_true,
        all_pred,
        per_label_f1=scores.get("per_label_f1", {}),
    )
    entry = {
        "checkpoint": checkpoint_name,
        "step": step,
        "epoch": epoch_num,
        "heldout_f1": scores["f1"],
        "heldout_precision": scores["precision"],
        "heldout_recall": scores["recall"],
        "heldout_metric": scores["metric"],
        "per_label_f1": scores["per_label_f1"],
        "per_label_precision": scores.get("per_label_precision", {}),
        "per_label_recall": scores.get("per_label_recall", {}),
        "per_label_support": scores.get("per_label_support", {}),
        "diagnostics": diagnostics,
        "product_detail_macro_f1": diagnostics["product_detail"]["macro_f1"],
        "entity_prediction_rate": diagnostics["rates"][
            "predicted_entity_token_rate"
        ],
        "gold_entity_rate": diagnostics["rates"]["gold_entity_token_rate"],
        "heldout_f1_delta_vs_training_reported": (
            scores["f1"] - training_reported_f1
            if training_reported_f1 is not None
            else None
        ),
        "token_accuracy": _token_accuracy(all_true, all_pred),
        "training_reported_f1": training_reported_f1,
        "num_receipts_evaluated": len(details_by_receipt),
        "avg_inference_ms": (
            round(sum(inf_times) / len(inf_times), 2) if inf_times else None
        ),
        "total_inference_ms": (
            round(sum(inf_times), 2) if inf_times else None
        ),
    }

    # Free the inference model promptly — during training this runs alongside
    # the (much larger) training model + optimizer state on the same GPU.
    del infer
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:  # pylint: disable=broad-exception-caught
        pass

    return entry, label_list, label_merges


def write_epochs_json_live(
    output_dir: str,
    *,
    job_name: str,
    run_s3_uri: str,
    epoch_entries: List[Dict[str, Any]],
    label_list: Optional[List[str]],
    label_merges: Optional[Dict[str, List[str]]],
    window_size: int,
    window_stride: int,
    val_set_source: str,
    val_hash: Optional[str],
    num_val_receipts: int,
    seed: Optional[int],
    showcase_keys: List[str],
    best_reported: Optional[int] = None,
) -> Dict[str, Any]:
    """Assemble + write ``epochs.json`` from accumulated live entries.

    Produces the SAME payload shape as ``evaluate_run`` so the API and viz are
    unchanged. Kept as a focused writer (rather than refactoring the working
    ``evaluate_run``) — the key set must stay in sync with that function.
    """
    scored = [e for e in epoch_entries if e.get("checkpoint") != "best"]
    best_entry = (
        max(scored, key=lambda e: e["heldout_f1"]) if scored else None
    )
    payload = {
        "job_name": job_name,
        "run_s3_uri": run_s3_uri,
        "val_set_source": val_set_source,
        "val_receipts_hash": val_hash,
        "val_receipts_hash_recorded": val_hash,
        "val_receipts_hash_verified": True,
        "num_val_receipts": num_val_receipts,
        "random_seed": int(seed) if seed is not None else None,
        "label_list": label_list,
        "label_merges": label_merges,
        "metric": "seqeval_entity_f1",
        "inference_mode": os.getenv("LAYOUTLM_INFERENCE_MODE", "windowed"),
        "window_size": window_size,
        "window_stride": window_stride,
        "compute": _device_info(),
        "epochs": epoch_entries,
        "best_epoch_heldout": (best_entry["epoch"] if best_entry else None),
        "best_checkpoint_heldout": (
            best_entry["checkpoint"] if best_entry else None
        ),
        "best_epoch_training_reported": best_reported,
        "showcase_receipt_keys": showcase_keys,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(
        os.path.join(output_dir, "epochs.json"), "w", encoding="utf-8"
    ) as f:
        json.dump(payload, f, indent=2, default=str)
    return payload
