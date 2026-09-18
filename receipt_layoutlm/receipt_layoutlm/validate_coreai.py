"""Validate Core AI (.aimodel) export against PyTorch reference.

Conversion success alone is not sufficient: this module compares labels,
confidences, and raw logits, and fails loudly on NaN/Inf.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from receipt_layoutlm.export_coreai import COREAI_SEQ_LENGTH
from receipt_layoutlm.validate_coreml import (
    _generate_synthetic_samples,
    _load_test_samples,
)
from receipt_layoutlm.validate_parity import (
    ValidationResult,
    aggregate_word_predictions,
    assert_finite_logits,
    compare_predictions,
    summarize_comparisons,
)

__all__ = ["ValidationResult", "validate_coreai"]


def validate_coreai(
    checkpoint_dir: str,
    coreai_bundle_dir: str,
    test_samples: Optional[List[dict]] = None,
    num_samples: int = 100,
    dynamo_table: Optional[str] = None,
    region: str = "us-east-1",
    seq_length: int = COREAI_SEQ_LENGTH,
    raise_on_nonfinite: bool = True,
) -> ValidationResult:
    """Compare PyTorch and Core AI model predictions.

    Args:
        checkpoint_dir: Path to PyTorch checkpoint.
        coreai_bundle_dir: Path to Core AI bundle (contains ``*.aimodel``).
        test_samples: Optional list of samples with ``tokens`` / ``bboxes``.
        num_samples: Sample count when loading from DynamoDB / synthetic.
        dynamo_table: Optional DynamoDB table for real receipt words.
        region: AWS region.
        seq_length: Fixed sequence length used at Core AI export time.
        raise_on_nonfinite: If True, raise on NaN/Inf logits.

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

    print(f"Loading Core AI model from {coreai_bundle_dir}...")
    coreai_fn = _load_coreai_function(coreai_bundle_dir)

    if test_samples is None:
        test_samples = _load_test_samples(dynamo_table, region, num_samples)

    print(f"Validating on {len(test_samples)} samples...")

    all_pytorch_labels: List[str] = []
    all_backend_labels: List[str] = []
    all_pytorch_confs: List[float] = []
    all_backend_confs: List[float] = []
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
            pytorch_model, tokenizer, tokens, bboxes, seq_length
        )
        backend_labels, backend_confs, backend_logits = _coreai_inference(
            coreai_fn, tokenizer, tokens, bboxes, id2label, seq_length
        )

        sample_cmp = compare_predictions(
            tokens=tokens,
            pytorch_labels=pytorch_labels,
            backend_labels=backend_labels,
            pytorch_confs=pytorch_confs,
            backend_confs=backend_confs,
            pytorch_logits=pytorch_logits,
            backend_logits=backend_logits,
            backend_name="CoreAI",
            raise_on_nonfinite=raise_on_nonfinite,
        )
        all_pytorch_labels.extend(sample_cmp["pytorch_labels"])
        all_backend_labels.extend(sample_cmp["backend_labels"])
        all_pytorch_confs.extend(sample_cmp["pytorch_confs"])
        all_backend_confs.extend(sample_cmp["backend_confs"])
        all_logit_rmses.extend(sample_cmp["logit_rmses"])
        mismatches.extend(sample_cmp["mismatches"])
        nan_inf_count += sample_cmp["nan_inf_count"]

    return summarize_comparisons(
        all_pytorch_labels=all_pytorch_labels,
        all_backend_labels=all_backend_labels,
        all_pytorch_confs=all_pytorch_confs,
        all_backend_confs=all_backend_confs,
        all_logit_rmses=all_logit_rmses,
        mismatches=mismatches,
        backend_name="CoreAI",
        nan_inf_count=nan_inf_count,
    )


def _find_aimodel(bundle_dir: str) -> Path:
    bundle_path = Path(bundle_dir)
    preferred = bundle_path / "LayoutLM.aimodel"
    if preferred.exists():
        return preferred
    matches = sorted(bundle_path.glob("*.aimodel"))
    if matches:
        return matches[0]
    raise FileNotFoundError(f"No .aimodel found in {bundle_dir}")


def _load_coreai_function(bundle_dir: str):
    """Load a callable Core AI function from an ``.aimodel`` asset.

    Uses the installed coreai-core / coreai-torch runtime APIs. Exact symbol
    names vary by version; try the documented paths and fail clearly.
    """
    aimodel_path = _find_aimodel(bundle_dir)

    # Path 1: coreai_core high-level asset load.
    try:
        from coreai_core import AIProgram  # type: ignore

        program = AIProgram.load_asset(str(aimodel_path))
        if hasattr(program, "executable"):
            return program.executable()
        if hasattr(program, "load_function"):
            return program.load_function("main")
        return program
    except Exception:
        pass

    # Path 2: asset returned by save_asset may itself be executable.
    try:
        from coreai_torch import load_asset  # type: ignore

        asset = load_asset(str(aimodel_path))
        if hasattr(asset, "executable"):
            return asset.executable()
        return asset
    except Exception:
        pass

    # Path 3: inspect package for a load helper.
    try:
        import coreai_core

        for attr in ("load_asset", "AIModel", "Asset"):
            loader = getattr(coreai_core, attr, None)
            if loader is None:
                continue
            if attr == "load_asset":
                asset = loader(str(aimodel_path))
            else:
                asset = loader(str(aimodel_path))
            if hasattr(asset, "executable"):
                return asset.executable()
            if hasattr(asset, "load_function"):
                return asset.load_function("main")
            return asset
    except Exception as e:
        raise ImportError(
            "Unable to load Core AI runtime for validation. Install "
            "receipt_layoutlm[coreai] in a Python 3.13 environment and ensure "
            f"the .aimodel at {aimodel_path} is readable. Last error: {e}"
        ) from e

    raise ImportError(
        f"Loaded {aimodel_path} but could not obtain an executable function. "
        "Inspect the installed coreai-core API on this machine."
    )


def _encode_sample(
    tokenizer,
    tokens: List[str],
    bboxes: List[List[int]],
    seq_length: int,
) -> Tuple[dict, List[Optional[int]], List[List[int]]]:
    enc = tokenizer(
        tokens,
        is_split_into_words=True,
        truncation=True,
        padding="max_length",
        max_length=seq_length,
        return_attention_mask=True,
    )
    word_ids = enc.word_ids()
    bbox_aligned = [
        [0, 0, 0, 0] if wid is None else bboxes[wid] for wid in word_ids
    ]
    return enc, word_ids, bbox_aligned


def _pytorch_inference(
    model,
    tokenizer,
    tokens: List[str],
    bboxes: List[List[int]],
    seq_length: int,
) -> Tuple[List[str], List[float], List[List[float]]]:
    import torch

    enc, word_ids, bbox_aligned = _encode_sample(
        tokenizer, tokens, bboxes, seq_length
    )
    with torch.no_grad():
        outputs = model(
            input_ids=torch.tensor([enc["input_ids"]]),
            attention_mask=torch.tensor([enc["attention_mask"]]),
            bbox=torch.tensor([bbox_aligned]),
            token_type_ids=torch.tensor([[0] * len(enc["input_ids"])]),
        )
    logits = outputs.logits[0].numpy()
    assert_finite_logits(logits, "PyTorch")
    return aggregate_word_predictions(
        logits, word_ids, len(tokens), model.config.id2label
    )


def _as_numpy_logits(raw: Any) -> np.ndarray:
    """Normalize Core AI output to a 2-D float numpy array [seq, labels]."""
    if hasattr(raw, "numpy"):
        arr = raw.numpy()
    elif isinstance(raw, dict):
        if "logits" in raw:
            return _as_numpy_logits(raw["logits"])
        # Take the first tensor-like value.
        return _as_numpy_logits(next(iter(raw.values())))
    elif isinstance(raw, (list, tuple)):
        return _as_numpy_logits(raw[0])
    else:
        arr = np.asarray(raw)

    arr = np.asarray(arr, dtype=np.float32)
    if arr.ndim == 3 and arr.shape[0] == 1:
        arr = arr[0]
    return arr


def _coreai_inference(
    coreai_fn,
    tokenizer,
    tokens: List[str],
    bboxes: List[List[int]],
    id2label: dict,
    seq_length: int,
) -> Tuple[List[str], List[float], List[List[float]]]:
    enc, word_ids, bbox_aligned = _encode_sample(
        tokenizer, tokens, bboxes, seq_length
    )
    seq_len = len(enc["input_ids"])

    input_ids = np.array(enc["input_ids"], dtype=np.int32).reshape(1, seq_len)
    attention_mask = np.array(enc["attention_mask"], dtype=np.int32).reshape(
        1, seq_len
    )
    bbox_array = np.array(bbox_aligned, dtype=np.int32).reshape(1, seq_len, 4)
    token_type_ids = np.zeros((1, seq_len), dtype=np.int32)

    # Try keyword / dict call conventions used by Core AI runtimes.
    raw = None
    call_errors: List[str] = []
    for kwargs in (
        {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "bbox": bbox_array,
            "token_type_ids": token_type_ids,
        },
    ):
        try:
            raw = coreai_fn(**kwargs)
            break
        except TypeError as e:
            call_errors.append(str(e))
        except Exception as e:
            call_errors.append(str(e))

    if raw is None:
        try:
            raw = coreai_fn(
                input_ids, attention_mask, bbox_array, token_type_ids
            )
        except Exception as e:
            call_errors.append(str(e))
            raise RuntimeError(
                "Core AI function call failed. Tried keyword and positional "
                f"invocations. Errors: {call_errors}"
            ) from e

    logits = _as_numpy_logits(raw)
    assert_finite_logits(logits, "CoreAI")
    return aggregate_word_predictions(logits, word_ids, len(tokens), id2label)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Validate Core AI model against PyTorch"
    )
    parser.add_argument(
        "--checkpoint-dir",
        required=True,
        help="Path to PyTorch checkpoint",
    )
    parser.add_argument(
        "--coreai-bundle",
        required=True,
        help="Path to Core AI bundle directory",
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

    result = validate_coreai(
        checkpoint_dir=args.checkpoint_dir,
        coreai_bundle_dir=args.coreai_bundle,
        dynamo_table=args.dynamo_table,
        region=args.region,
        num_samples=args.num_samples,
    )
    print(result)
    if args.output_json:
        with open(args.output_json, "w") as f:
            json.dump(result.to_dict(), f, indent=2)
        print(f"\nDetailed results saved to {args.output_json}")
