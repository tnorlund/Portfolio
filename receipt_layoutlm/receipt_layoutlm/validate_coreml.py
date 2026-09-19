"""Validate CoreML model against PyTorch reference.

Compares predictions between PyTorch (FP32) and CoreML exports to measure
any accuracy degradation from conversion or quantization.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from receipt_layoutlm.validate_parity import (
    ValidationResult,
    aggregate_word_predictions,
    compare_predictions,
    softmax,
    summarize_comparisons,
)

# Re-export for existing callers / CLI.
__all__ = ["ValidationResult", "validate_coreml"]


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

    print(f"Loading PyTorch model from {checkpoint_dir}...")
    pytorch_model = LayoutLMForTokenClassification.from_pretrained(
        checkpoint_dir
    )
    pytorch_model.eval()
    tokenizer = LayoutLMTokenizerFast.from_pretrained(checkpoint_dir)

    print(f"Loading CoreML model from {coreml_bundle_dir}...")
    coreml_model = _load_coreml_model(coreml_bundle_dir)

    if test_samples is None:
        test_samples = _load_test_samples(dynamo_table, region, num_samples)

    print(f"Validating on {len(test_samples)} samples...")

    all_pytorch_labels: List[str] = []
    all_coreml_labels: List[str] = []
    all_pytorch_confs: List[float] = []
    all_coreml_confs: List[float] = []
    all_logit_rmses: List[float] = []
    mismatches: List[Dict[str, Any]] = []
    nan_inf_count = 0

    id2label = pytorch_model.config.id2label

    for sample in test_samples:
        tokens = sample["tokens"]
        bboxes = sample["bboxes"]
        if not tokens:
            continue

        pytorch_labels, pytorch_confs, pytorch_logits = _pytorch_inference(
            pytorch_model, tokenizer, tokens, bboxes
        )
        coreml_labels, coreml_confs, coreml_logits = _coreml_inference(
            coreml_model, tokenizer, tokens, bboxes, id2label
        )

        sample_cmp = compare_predictions(
            tokens=tokens,
            pytorch_labels=pytorch_labels,
            backend_labels=coreml_labels,
            pytorch_confs=pytorch_confs,
            backend_confs=coreml_confs,
            pytorch_logits=pytorch_logits,
            backend_logits=coreml_logits,
            backend_name="CoreML",
            raise_on_nonfinite=False,
        )
        all_pytorch_labels.extend(sample_cmp["pytorch_labels"])
        all_coreml_labels.extend(sample_cmp["backend_labels"])
        all_pytorch_confs.extend(sample_cmp["pytorch_confs"])
        all_coreml_confs.extend(sample_cmp["backend_confs"])
        all_logit_rmses.extend(sample_cmp["logit_rmses"])
        mismatches.extend(sample_cmp["mismatches"])
        nan_inf_count += sample_cmp["nan_inf_count"]

    return summarize_comparisons(
        all_pytorch_labels=all_pytorch_labels,
        all_backend_labels=all_coreml_labels,
        all_pytorch_confs=all_pytorch_confs,
        all_backend_confs=all_coreml_confs,
        all_logit_rmses=all_logit_rmses,
        mismatches=mismatches,
        backend_name="CoreML",
        nan_inf_count=nan_inf_count,
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

    enc = tokenizer(
        tokens,
        is_split_into_words=True,
        truncation=True,
        padding="max_length",
        max_length=512,
        return_attention_mask=True,
    )
    word_ids = enc.word_ids()
    bbox_aligned = [
        [0, 0, 0, 0] if wid is None else bboxes[wid] for wid in word_ids
    ]

    with torch.no_grad():
        outputs = model(
            input_ids=torch.tensor([enc["input_ids"]]),
            attention_mask=torch.tensor([enc["attention_mask"]]),
            bbox=torch.tensor([bbox_aligned]),
            token_type_ids=torch.tensor([[0] * len(enc["input_ids"])]),
        )

    logits = outputs.logits[0].numpy()
    return aggregate_word_predictions(
        logits, word_ids, len(tokens), model.config.id2label
    )


def _coreml_inference(
    model,
    tokenizer,
    tokens: List[str],
    bboxes: List[List[int]],
    id2label: dict,
) -> Tuple[List[str], List[float], List[List[float]]]:
    """Run CoreML inference and return labels, confidences, and logits."""
    enc = tokenizer(
        tokens,
        is_split_into_words=True,
        truncation=True,
        padding="max_length",
        max_length=512,
        return_attention_mask=True,
    )
    word_ids = enc.word_ids()
    bbox_aligned = [
        [0, 0, 0, 0] if wid is None else bboxes[wid] for wid in word_ids
    ]
    seq_len = len(enc["input_ids"])

    input_ids = np.array(enc["input_ids"], dtype=np.int32).reshape(1, seq_len)
    attention_mask = np.array(enc["attention_mask"], dtype=np.int32).reshape(
        1, seq_len
    )
    bbox_array = np.array(bbox_aligned, dtype=np.int32).reshape(1, seq_len, 4)
    token_type_ids = np.zeros((1, seq_len), dtype=np.int32)

    prediction = model.predict(
        {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "bbox": bbox_array,
            "token_type_ids": token_type_ids,
        }
    )
    logits = prediction["logits"][0]
    return aggregate_word_predictions(logits, word_ids, len(tokens), id2label)


def _load_test_samples(
    dynamo_table: Optional[str],
    region: str,
    num_samples: int,
) -> List[dict]:
    """Load test samples from DynamoDB or generate synthetic ones."""
    if dynamo_table:
        try:
            from receipt_dynamo import DynamoClient
            from receipt_dynamo.constants import ValidationStatus

            from .data_loader import (
                _box_from_word,
                _normalize_box_from_extents,
            )

            dyn = DynamoClient(table_name=dynamo_table, region=region)
            labels, _ = dyn.list_receipt_word_labels_with_status(
                ValidationStatus.VALID
            )
            if not labels:
                print("No VALID labels found, using synthetic data")
                return _generate_synthetic_samples(num_samples)

            receipts: Dict[tuple, int] = {}
            for label in labels[:500]:
                key = (label.image_id, label.receipt_id)
                receipts[key] = receipts.get(key, 0) + 1

            samples = []
            for (image_id, receipt_id), _ in list(receipts.items())[
                :num_samples
            ]:
                try:
                    details = dyn.get_receipt_details(image_id, receipt_id)
                    words = details.words
                    max_x, max_y = 0.0, 0.0
                    raw_boxes = []
                    for w in words:
                        x0, y0, x1, y1 = _box_from_word(w)
                        max_x = max(max_x, x1)
                        max_y = max(max_y, y1)
                        raw_boxes.append((x0, y0, x1, y1))
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


# Keep private helper importable for tests that monkeypatch it.
_softmax = softmax


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
