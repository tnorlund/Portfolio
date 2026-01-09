"""Validate CoreML model against PyTorch reference.

Compares predictions between PyTorch (FP32) and CoreML exports to measure
any accuracy degradation from conversion or quantization.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


@dataclass
class ValidationResult:
    """Results from comparing PyTorch vs CoreML predictions."""

    total_tokens: int
    matching_labels: int
    label_agreement_rate: float

    # Per-label breakdown: {label: (matches, total, rate)}
    per_label_agreement: Dict[str, Tuple[int, int, float]]

    # Confidence comparison
    avg_confidence_diff: float
    max_confidence_diff: float

    # Logit comparison (before softmax)
    avg_logit_rmse: float
    max_logit_rmse: float

    # Mismatches for analysis: [{token, pytorch_label, coreml_label, ...}]
    mismatches: List[Dict[str, Any]]

    def __str__(self) -> str:
        lines = [
            "=" * 60,
            "CoreML Validation Results",
            "=" * 60,
            f"Total tokens evaluated: {self.total_tokens}",
            f"Label agreement rate:   {self.label_agreement_rate:.4f} ({self.matching_labels}/{self.total_tokens})",
            "",
            "Confidence comparison:",
            f"  Average diff: {self.avg_confidence_diff:.6f}",
            f"  Max diff:     {self.max_confidence_diff:.6f}",
            "",
            "Logit RMSE (before softmax):",
            f"  Average: {self.avg_logit_rmse:.6f}",
            f"  Max:     {self.max_logit_rmse:.6f}",
            "",
            "Per-label agreement:",
        ]
        for label, (matches, total, rate) in sorted(
            self.per_label_agreement.items()
        ):
            lines.append(f"  {label:20s}: {rate:.4f} ({matches}/{total})")

        if self.mismatches:
            lines.append("")
            lines.append(
                f"Sample mismatches (showing first 10 of {len(self.mismatches)}):"
            )
            for m in self.mismatches[:10]:
                lines.append(
                    f"  '{m['token']}': PyTorch={m['pytorch_label']} "
                    f"({m['pytorch_conf']:.3f}) vs CoreML={m['coreml_label']} "
                    f"({m['coreml_conf']:.3f})"
                )

        lines.append("=" * 60)
        return "\n".join(lines)

    def to_dict(self, max_mismatches: int = 100) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "total_tokens": self.total_tokens,
            "matching_labels": self.matching_labels,
            "label_agreement_rate": self.label_agreement_rate,
            "per_label_agreement": {
                k: {"matches": v[0], "total": v[1], "rate": v[2]}
                for k, v in self.per_label_agreement.items()
            },
            "avg_confidence_diff": self.avg_confidence_diff,
            "max_confidence_diff": self.max_confidence_diff,
            "avg_logit_rmse": self.avg_logit_rmse,
            "max_logit_rmse": self.max_logit_rmse,
            "num_mismatches": len(self.mismatches),
            "mismatches": self.mismatches[:max_mismatches],
        }


def validate_coreml(
    checkpoint_dir: str,
    coreml_bundle_dir: str,
    test_samples: Optional[List[dict]] = None,
    num_samples: int = 100,
    dynamo_table: Optional[str] = None,
    region: str = "us-east-1",
) -> ValidationResult:
    """Compare PyTorch and CoreML model predictions.

    Args:
        checkpoint_dir: Path to PyTorch checkpoint.
        coreml_bundle_dir: Path to CoreML bundle.
        test_samples: Optional list of test samples with 'tokens' and 'bboxes'.
        num_samples: Number of samples to test if loading from DynamoDB.
        dynamo_table: DynamoDB table name for loading test data.
        region: AWS region.

    Returns:
        ValidationResult with comparison metrics.
    """
    import torch
    from transformers import (
        LayoutLMForTokenClassification,
        LayoutLMTokenizerFast,
    )

    # Load PyTorch model
    print(f"Loading PyTorch model from {checkpoint_dir}...")
    pytorch_model = LayoutLMForTokenClassification.from_pretrained(
        checkpoint_dir
    )
    pytorch_model.eval()
    tokenizer = LayoutLMTokenizerFast.from_pretrained(checkpoint_dir)

    # Load CoreML model
    print(f"Loading CoreML model from {coreml_bundle_dir}...")
    coreml_model = _load_coreml_model(coreml_bundle_dir)

    # Get test samples
    if test_samples is None:
        test_samples = _load_test_samples(dynamo_table, region, num_samples)

    print(f"Validating on {len(test_samples)} samples...")

    # Run comparison
    all_pytorch_labels = []
    all_coreml_labels = []
    all_pytorch_confs = []
    all_coreml_confs = []
    all_logit_rmses = []
    all_tokens = []
    mismatches = []

    id2label = pytorch_model.config.id2label

    for sample in test_samples:
        tokens = sample["tokens"]
        bboxes = sample["bboxes"]

        if not tokens:
            continue

        # PyTorch inference
        pytorch_labels, pytorch_confs, pytorch_logits = _pytorch_inference(
            pytorch_model, tokenizer, tokens, bboxes
        )

        # CoreML inference
        coreml_labels, coreml_confs, coreml_logits = _coreml_inference(
            coreml_model, tokenizer, tokens, bboxes, id2label
        )

        # Compare
        for tok, pt_lbl, cm_lbl, pt_conf, cm_conf in zip(
            tokens,
            pytorch_labels,
            coreml_labels,
            pytorch_confs,
            coreml_confs,
        ):
            all_tokens.append(tok)
            all_pytorch_labels.append(pt_lbl)
            all_coreml_labels.append(cm_lbl)
            all_pytorch_confs.append(pt_conf)
            all_coreml_confs.append(cm_conf)

            if pt_lbl != cm_lbl:
                mismatches.append(
                    {
                        "token": tok,
                        "pytorch_label": pt_lbl,
                        "coreml_label": cm_lbl,
                        "pytorch_conf": pt_conf,
                        "coreml_conf": cm_conf,
                    }
                )

        # Logit RMSE (compare raw logits before softmax)
        if pytorch_logits is not None and coreml_logits is not None:
            min_len = min(len(pytorch_logits), len(coreml_logits))
            for pt_log, cm_log in zip(
                pytorch_logits[:min_len], coreml_logits[:min_len]
            ):
                rmse = np.sqrt(
                    np.mean((np.array(pt_log) - np.array(cm_log)) ** 2)
                )
                all_logit_rmses.append(rmse)

    # Compute metrics
    total = len(all_pytorch_labels)
    matches = sum(
        1 for p, c in zip(all_pytorch_labels, all_coreml_labels) if p == c
    )

    # Per-label breakdown
    per_label = {}
    for label in set(all_pytorch_labels):
        label_matches = sum(
            1
            for p, c in zip(all_pytorch_labels, all_coreml_labels)
            if p == label and p == c
        )
        label_total = sum(1 for p in all_pytorch_labels if p == label)
        rate = label_matches / label_total if label_total > 0 else 0.0
        per_label[label] = (label_matches, label_total, rate)

    # Confidence diffs
    conf_diffs = [
        abs(p - c) for p, c in zip(all_pytorch_confs, all_coreml_confs)
    ]

    return ValidationResult(
        total_tokens=total,
        matching_labels=matches,
        label_agreement_rate=matches / total if total > 0 else 0.0,
        per_label_agreement=per_label,
        avg_confidence_diff=float(np.mean(conf_diffs)) if conf_diffs else 0.0,
        max_confidence_diff=max(conf_diffs) if conf_diffs else 0.0,
        avg_logit_rmse=(
            float(np.mean(all_logit_rmses)) if all_logit_rmses else 0.0
        ),
        max_logit_rmse=max(all_logit_rmses) if all_logit_rmses else 0.0,
        mismatches=mismatches,
    )


def _load_coreml_model(bundle_dir: str):
    """Load CoreML model from bundle."""
    try:
        import coremltools as ct
    except ImportError as e:
        raise ImportError(
            "coremltools required: pip install coremltools"
        ) from e

    bundle_path = Path(bundle_dir)
    mlpackage_path = bundle_path / "LayoutLM.mlpackage"

    if not mlpackage_path.exists():
        # Try to find any .mlpackage
        mlpackages = list(bundle_path.glob("*.mlpackage"))
        if mlpackages:
            mlpackage_path = mlpackages[0]
        else:
            raise FileNotFoundError(f"No .mlpackage found in {bundle_dir}")

    return ct.models.MLModel(str(mlpackage_path))


def _pytorch_inference(
    model, tokenizer, tokens: List[str], bboxes: List[List[int]]
) -> Tuple[List[str], List[float], List[List[float]]]:
    """Run PyTorch inference and return labels, confidences, and logits."""
    import torch

    # Tokenize
    enc = tokenizer(
        tokens,
        is_split_into_words=True,
        truncation=True,
        padding="max_length",
        max_length=512,
        return_attention_mask=True,
    )

    word_ids = enc.word_ids()

    # Build bbox tensor
    bbox_aligned = [
        [0, 0, 0, 0] if wid is None else bboxes[wid] for wid in word_ids
    ]

    # Run model
    with torch.no_grad():
        outputs = model(
            input_ids=torch.tensor([enc["input_ids"]]),
            attention_mask=torch.tensor([enc["attention_mask"]]),
            bbox=torch.tensor([bbox_aligned]),
            token_type_ids=torch.tensor([[0] * len(enc["input_ids"])]),
        )

    logits = outputs.logits[0].numpy()  # [seq_len, num_labels]
    id2label = model.config.id2label

    # Aggregate per word (same as inference.py)
    word_to_tokens: Dict[int, List[int]] = {}
    for i, wid in enumerate(word_ids):
        if wid is not None:
            word_to_tokens.setdefault(wid, []).append(i)

    labels = []
    confs = []
    word_logits = []

    for wid in range(len(tokens)):
        token_idxs = word_to_tokens.get(wid, [])
        if not token_idxs:
            labels.append("O")
            confs.append(0.0)
            word_logits.append([0.0] * logits.shape[1])
            continue

        # Average logits
        avg_logits = np.mean([logits[i] for i in token_idxs], axis=0)
        probs = _softmax(avg_logits)
        pred_id = np.argmax(probs)

        labels.append(id2label.get(int(pred_id), "O"))
        confs.append(float(probs[pred_id]))
        word_logits.append(avg_logits.tolist())

    return labels, confs, word_logits


def _coreml_inference(
    model,
    tokenizer,
    tokens: List[str],
    bboxes: List[List[int]],
    id2label: dict,
) -> Tuple[List[str], List[float], List[List[float]]]:
    """Run CoreML inference and return labels, confidences, and logits."""
    import numpy as np

    # Tokenize
    enc = tokenizer(
        tokens,
        is_split_into_words=True,
        truncation=True,
        padding="max_length",
        max_length=512,
        return_attention_mask=True,
    )

    word_ids = enc.word_ids()

    # Build bbox tensor
    bbox_aligned = [
        [0, 0, 0, 0] if wid is None else bboxes[wid] for wid in word_ids
    ]

    seq_len = len(enc["input_ids"])

    # Prepare inputs for CoreML
    input_ids = np.array(enc["input_ids"], dtype=np.int32).reshape(1, seq_len)
    attention_mask = np.array(enc["attention_mask"], dtype=np.int32).reshape(
        1, seq_len
    )
    bbox_array = np.array(bbox_aligned, dtype=np.int32).reshape(1, seq_len, 4)
    token_type_ids = np.zeros((1, seq_len), dtype=np.int32)

    # Run CoreML
    prediction = model.predict(
        {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "bbox": bbox_array,
            "token_type_ids": token_type_ids,
        }
    )

    # Get logits
    logits = prediction["logits"][0]  # [seq_len, num_labels]

    # Aggregate per word
    word_to_tokens: Dict[int, List[int]] = {}
    for i, wid in enumerate(word_ids):
        if wid is not None:
            word_to_tokens.setdefault(wid, []).append(i)

    labels: List[str] = []
    confs: List[float] = []
    word_logits: List[Any] = []

    for wid in range(len(tokens)):
        token_idxs = word_to_tokens.get(wid, [])
        if not token_idxs:
            labels.append("O")
            confs.append(0.0)
            word_logits.append([0.0] * logits.shape[1])
            continue

        # Average logits
        avg_logits = np.mean([logits[i] for i in token_idxs], axis=0)
        probs = _softmax(avg_logits)
        pred_id = np.argmax(probs)

        labels.append(id2label.get(int(pred_id), "O"))
        confs.append(float(probs[pred_id]))
        word_logits.append(avg_logits.tolist())

    return labels, confs, word_logits


def _softmax(x):
    """Compute softmax."""
    exp_x = np.exp(x - np.max(x))
    return exp_x / exp_x.sum()


def _load_test_samples(
    dynamo_table: Optional[str],
    region: str,
    num_samples: int,
) -> List[dict]:
    """Load test samples from DynamoDB or generate synthetic ones."""
    # Try to load from DynamoDB
    if dynamo_table:
        try:
            from receipt_dynamo import DynamoClient
            from receipt_dynamo.constants import ValidationStatus

            from .data_loader import (
                _box_from_word,
                _normalize_box_from_extents,
            )

            dyn = DynamoClient(table_name=dynamo_table, region=region)

            # Get some receipts with labels
            labels, _ = dyn.list_receipt_word_labels_with_status(
                ValidationStatus.VALID
            )
            if not labels:
                print("No VALID labels found, using synthetic data")
                return _generate_synthetic_samples(num_samples)

            # Group by receipt
            receipts: Dict[tuple, int] = {}
            for label in labels[:500]:  # Limit search
                key = (label.image_id, label.receipt_id)
                receipts[key] = receipts.get(key, 0) + 1

            samples = []
            for (image_id, receipt_id), _ in list(receipts.items())[
                :num_samples
            ]:
                try:
                    details = dyn.get_receipt_details(image_id, receipt_id)
                    words = details.words

                    # Compute extents
                    max_x, max_y = 0.0, 0.0
                    raw_boxes = []
                    for w in words:
                        x0, y0, x1, y1 = _box_from_word(w)
                        max_x = max(max_x, x1)
                        max_y = max(max_y, y1)
                        raw_boxes.append((x0, y0, x1, y1))

                    # Normalize
                    tokens = [w.text for w in words]
                    bboxes = [
                        _normalize_box_from_extents(
                            x0, y0, x1, y1, max_x, max_y
                        )
                        for x0, y0, x1, y1 in raw_boxes
                    ]

                    samples.append({"tokens": tokens, "bboxes": bboxes})
                except Exception as e:
                    print(f"Skipping receipt {image_id}/{receipt_id}: {e}")
                    continue

            if samples:
                return samples

        except Exception as e:
            print(f"Could not load from DynamoDB: {e}")

    return _generate_synthetic_samples(num_samples)


def _generate_synthetic_samples(num_samples: int) -> List[dict]:
    """Generate synthetic test samples."""
    import random

    samples = []
    receipt_words = [
        "WALMART",
        "STORE",
        "#123",
        "123",
        "MAIN",
        "ST",
        "ITEM",
        "1",
        "$",
        "5.99",
        "SUBTOTAL",
        "TAX",
        "TOTAL",
        "VISA",
        "****1234",
        "THANK",
        "YOU",
    ]

    for _ in range(num_samples):
        num_words = random.randint(5, 20)
        tokens = [random.choice(receipt_words) for _ in range(num_words)]

        # Generate plausible bboxes (left-to-right, top-to-bottom)
        bboxes = []
        y = 0
        x = 0
        for _ in tokens:
            w = random.randint(50, 150)
            h = random.randint(20, 40)
            bboxes.append([x, y, min(x + w, 1000), min(y + h, 1000)])
            x += w + 10
            if x > 800:
                x = 0
                y += h + 10

        samples.append({"tokens": tokens, "bboxes": bboxes})

    return samples


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Validate CoreML model against PyTorch"
    )
    parser.add_argument(
        "--checkpoint-dir",
        required=True,
        help="Path to PyTorch checkpoint",
    )
    parser.add_argument(
        "--coreml-bundle",
        required=True,
        help="Path to CoreML bundle directory",
    )
    parser.add_argument(
        "--dynamo-table",
        default=os.getenv("DYNAMO_TABLE_NAME"),
        help="DynamoDB table for test data",
    )
    parser.add_argument(
        "--region",
        default="us-east-1",
        help="AWS region",
    )
    parser.add_argument(
        "--num-samples",
        type=int,
        default=100,
        help="Number of samples to test",
    )
    parser.add_argument(
        "--output-json",
        help="Save detailed results to JSON file",
    )

    args = parser.parse_args()

    result = validate_coreml(
        checkpoint_dir=args.checkpoint_dir,
        coreml_bundle_dir=args.coreml_bundle,
        dynamo_table=args.dynamo_table,
        region=args.region,
        num_samples=args.num_samples,
    )

    print(result)

    if args.output_json:
        with open(args.output_json, "w") as f:
            json.dump(result.to_dict(), f, indent=2)
        print(f"\nDetailed results saved to {args.output_json}")
