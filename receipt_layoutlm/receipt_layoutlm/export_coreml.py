"""Export trained LayoutLM model to CoreML format for Swift inference."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Optional

import numpy as np
import torch
from torch import nn


class CoreMLExportError(Exception):
    """Base exception for CoreML export errors."""


class MissingDependencyError(CoreMLExportError):
    """Raised when required dependencies are not installed."""


class LayoutLMWrapper(nn.Module):
    """Wrapper to trace LayoutLM with explicit input order."""

    def __init__(self, model):
        super().__init__()
        self.model = model

    def forward(
        self,
        input_ids: torch.Tensor,
        attention_mask: torch.Tensor,
        bbox: torch.Tensor,
        token_type_ids: torch.Tensor,
    ) -> torch.Tensor:
        outputs = self.model(
            input_ids=input_ids,
            attention_mask=attention_mask,
            bbox=bbox,
            token_type_ids=token_type_ids,
        )
        return outputs.logits


def export_coreml(
    checkpoint_dir: str,
    output_dir: str,
    model_name: str = "LayoutLM",
    max_seq_length: int = 512,
    min_seq_length: int = 1,
    quantize: Optional[str] = None,
) -> str:
    """Export a trained LayoutLM checkpoint to CoreML format.

    Args:
        checkpoint_dir: Path to checkpoint containing model files.
        output_dir: Directory to write CoreML bundle.
        model_name: Name for the .mlpackage file.
        max_seq_length: Maximum sequence length for model.
        min_seq_length: Minimum sequence length for model.
        quantize: Quantization mode: None, "float16", "int8", or "int4".

    Returns:
        Path to the created model bundle directory.

    Raises:
        ValueError: If min_seq_length > max_seq_length.
        MissingDependencyError: If coremltools or transformers not installed.
    """
    if min_seq_length > max_seq_length:
        raise ValueError(
            f"min_seq_length ({min_seq_length}) cannot be greater than "
            f"max_seq_length ({max_seq_length})"
        )

    try:
        import coremltools as ct
        from transformers import (
            LayoutLMForTokenClassification,
            LayoutLMTokenizerFast,
        )
    except ImportError as e:
        msg = "coremltools and transformers required: pip install coremltools transformers"
        raise MissingDependencyError(msg) from e

    checkpoint_path = Path(checkpoint_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    print(f"Loading model from {checkpoint_path}...")

    # Load the trained model and tokenizer
    model = LayoutLMForTokenClassification.from_pretrained(checkpoint_path)
    tokenizer = LayoutLMTokenizerFast.from_pretrained(checkpoint_path)
    model.eval()

    # Get model config for label mapping
    config = model.config
    num_labels = config.num_labels
    id2label = config.id2label
    label2id = config.label2id

    print(f"Model has {num_labels} labels: {list(id2label.values())}")

    # Create wrapper for tracing
    wrapper = LayoutLMWrapper(model)
    wrapper.eval()

    # Create sample inputs for tracing
    # Use a typical sequence length for tracing (128 is a good middle ground between min and max)
    sample_seq_len = 128
    sample_input_ids = torch.randint(
        0, tokenizer.vocab_size, (1, sample_seq_len)
    )
    sample_attention_mask = torch.ones(1, sample_seq_len, dtype=torch.long)
    # LayoutLM uses normalized coordinates in [0, 1000] range for bboxes
    # Format is [x1, y1, x2, y2] where x2 >= x1 and y2 >= y1
    # Generate valid bboxes with proper coordinate ordering
    x1 = torch.randint(0, 500, (1, sample_seq_len))
    y1 = torch.randint(0, 500, (1, sample_seq_len))
    x2 = x1 + torch.randint(1, 500, (1, sample_seq_len))  # x2 > x1
    y2 = y1 + torch.randint(1, 500, (1, sample_seq_len))  # y2 > y1
    sample_bbox = torch.stack([x1, y1, x2, y2], dim=-1)
    sample_token_type_ids = torch.zeros(1, sample_seq_len, dtype=torch.long)

    print("Tracing model with TorchScript...")

    # Trace the model
    with torch.no_grad():
        traced_model = torch.jit.trace(
            wrapper,
            (
                sample_input_ids,
                sample_attention_mask,
                sample_bbox,
                sample_token_type_ids,
            ),
        )

    print("Converting to CoreML...")

    # Define input shapes with flexible sequence length
    # CoreML ct.RangeDim allows variable-length sequences
    seq_dim = ct.RangeDim(
        lower_bound=min_seq_length,
        upper_bound=max_seq_length,
        default=sample_seq_len,
    )

    # Determine compute precision - float16 must be applied at conversion time
    compute_precision = (
        ct.precision.FLOAT16 if quantize == "float16" else ct.precision.FLOAT32
    )
    if quantize == "float16":
        print("Applying float16 precision at conversion time...")

    # Convert to CoreML with explicit int32 dtype to match Swift MLMultiArray
    mlmodel = ct.convert(
        traced_model,
        inputs=[
            ct.TensorType(
                name="input_ids",
                shape=(1, seq_dim),
                dtype=np.int32,
            ),
            ct.TensorType(
                name="attention_mask",
                shape=(1, seq_dim),
                dtype=np.int32,
            ),
            ct.TensorType(
                name="bbox",
                shape=(1, seq_dim, 4),
                dtype=np.int32,
            ),
            ct.TensorType(
                name="token_type_ids",
                shape=(1, seq_dim),
                dtype=np.int32,
            ),
        ],
        outputs=[
            ct.TensorType(name="logits"),
        ],
        minimum_deployment_target=ct.target.macOS13,
        convert_to="mlprogram",
        compute_precision=compute_precision,
    )

    # Set model metadata
    mlmodel.author = "LayoutLM Training Pipeline"
    mlmodel.short_description = (
        f"LayoutLM token classification model with {num_labels} labels"
    )
    mlmodel.version = "1.0"

    # Apply post-conversion quantization if requested (int8/int4 only, float16 handled above)
    if quantize and quantize != "float16":
        print(f"Applying {quantize} quantization...")
        if quantize == "int8":
            # Use linear quantization for INT8
            op_config = ct.optimize.coreml.OpLinearQuantizerConfig(
                mode="linear_symmetric", dtype="int8"
            )
            config = ct.optimize.coreml.OptimizationConfig(
                global_config=op_config
            )
            mlmodel = ct.optimize.coreml.linear_quantize_weights(
                mlmodel, config
            )
        elif quantize == "int4":
            # Use palettization for INT4-like compression
            op_config = ct.optimize.coreml.OpPalettizerConfig(
                mode="kmeans", nbits=4
            )
            config = ct.optimize.coreml.OptimizationConfig(
                global_config=op_config
            )
            mlmodel = ct.optimize.coreml.palettize_weights(mlmodel, config)
        else:
            print(f"Warning: Unknown quantization mode '{quantize}', skipping")

    # Save CoreML model
    mlpackage_path = output_path / f"{model_name}.mlpackage"
    print(f"Saving CoreML model to {mlpackage_path}...")
    mlmodel.save(str(mlpackage_path))

    # Copy vocab.txt for tokenizer
    vocab_src = checkpoint_path / "vocab.txt"
    vocab_dst = output_path / "vocab.txt"
    if vocab_src.exists():
        shutil.copy(vocab_src, vocab_dst)
        print(f"Copied vocab.txt to {vocab_dst}")
    else:
        # Try to save vocab from tokenizer (may create additional files like added_tokens.json)
        tokenizer.save_vocabulary(str(output_path))
        print(f"Saved vocabulary to {output_path}")

    # Sort label IDs for consistent ordering in JSON output
    sorted_ids = sorted(id2label.keys())

    # Copy config.json
    config_src = checkpoint_path / "config.json"
    config_dst = output_path / "config.json"
    if config_src.exists():
        shutil.copy(config_src, config_dst)
        print(f"Copied config.json to {config_dst}")
    else:
        # Save config manually with sorted id2label
        with open(config_dst, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "id2label": {str(k): id2label[k] for k in sorted_ids},
                    "label2id": label2id,
                    "num_labels": num_labels,
                    "max_position_embeddings": config.max_position_embeddings,
                    "vocab_size": config.vocab_size,
                },
                f,
                indent=2,
            )
        print(f"Saved config.json to {config_dst}")

    # Create label_map.json for easy label lookup
    label_map_path = output_path / "label_map.json"
    with open(label_map_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "id2label": {str(k): id2label[k] for k in sorted_ids},
                "label2id": label2id,
                "labels": [id2label[k] for k in sorted_ids],
            },
            f,
            indent=2,
        )
    print(f"Saved label_map.json to {label_map_path}")

    # Copy tokenizer config for reference
    tokenizer_config_src = checkpoint_path / "tokenizer_config.json"
    tokenizer_config_dst = output_path / "tokenizer_config.json"
    if tokenizer_config_src.exists():
        shutil.copy(tokenizer_config_src, tokenizer_config_dst)

    print(f"\nCoreML bundle created at: {output_path}")
    print("Contents:")
    for item in output_path.iterdir():
        if item.is_dir():
            print(f"  {item.name}/")
        else:
            print(f"  {item.name}")

    return str(output_path)


def export_from_s3(
    s3_uri: str,
    output_dir: str,
    model_name: str = "LayoutLM",
    local_cache: Optional[str] = None,
    quantize: Optional[str] = None,
    max_seq_length: int = 512,
    min_seq_length: int = 1,
) -> str:
    """Export a model from S3 to CoreML format.

    Args:
        s3_uri: S3 URI to model checkpoint (s3://bucket/prefix/).
        output_dir: Directory to write CoreML bundle.
        model_name: Name for the .mlpackage file.
        local_cache: Local directory to cache downloaded model.
        quantize: Quantization mode: None, "float16", "int8", or "int4".
        max_seq_length: Maximum sequence length for model.
        min_seq_length: Minimum sequence length for model.

    Returns:
        Path to the created model bundle directory.
    """
    import tempfile
    from urllib.parse import urlparse

    try:
        import boto3
    except ImportError as e:
        msg = "boto3 required for S3 download: pip install boto3"
        raise MissingDependencyError(msg) from e

    # Parse S3 URI
    parsed = urlparse(s3_uri)
    bucket = parsed.netloc
    prefix = parsed.path.lstrip("/")

    # Use provided cache dir or create temp dir that auto-cleans up
    temp_dir_obj = None
    if local_cache:
        cache_dir = Path(local_cache)
        cache_dir.mkdir(parents=True, exist_ok=True)
    else:
        temp_dir_obj = tempfile.TemporaryDirectory(prefix="layoutlm_")
        cache_dir = Path(temp_dir_obj.name)

    try:
        print(f"Downloading model from {s3_uri} to {cache_dir}...")

        # Download all files from S3 prefix
        s3 = boto3.client("s3")
        paginator = s3.get_paginator("list_objects_v2")

        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                rel_path = key[len(prefix) :].lstrip("/")
                if not rel_path:
                    continue

                local_path = cache_dir / rel_path
                local_path.parent.mkdir(parents=True, exist_ok=True)

                print(f"  Downloading {rel_path}...")
                s3.download_file(bucket, key, str(local_path))

        # Export from downloaded checkpoint
        return export_coreml(
            checkpoint_dir=str(cache_dir),
            output_dir=output_dir,
            model_name=model_name,
            quantize=quantize,
            max_seq_length=max_seq_length,
            min_seq_length=min_seq_length,
        )
    finally:
        # Clean up temp directory if we created one
        if temp_dir_obj is not None:
            temp_dir_obj.cleanup()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Export LayoutLM model to CoreML format"
    )
    parser.add_argument(
        "--checkpoint-dir",
        help="Local directory containing model checkpoint",
    )
    parser.add_argument(
        "--s3-uri",
        help="S3 URI to model checkpoint (s3://bucket/prefix/)",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory to write CoreML bundle",
    )
    parser.add_argument(
        "--model-name",
        default="LayoutLM",
        help="Name for the .mlpackage file",
    )
    parser.add_argument(
        "--local-cache",
        help="Local directory to cache S3 downloads",
    )
    parser.add_argument(
        "--quantize",
        choices=["float16", "int8", "int4"],
        default=None,
        help="Quantization mode for smaller model size",
    )
    parser.add_argument(
        "--max-seq-length",
        type=int,
        default=512,
        help="Maximum sequence length for model (default: 512)",
    )
    parser.add_argument(
        "--min-seq-length",
        type=int,
        default=1,
        help="Minimum sequence length for model (default: 1)",
    )

    args = parser.parse_args()

    if not args.checkpoint_dir and not args.s3_uri:
        parser.error("Either --checkpoint-dir or --s3-uri is required")

    if args.min_seq_length > args.max_seq_length:
        parser.error(
            "--min-seq-length cannot be greater than --max-seq-length"
        )

    if args.s3_uri:
        export_from_s3(
            s3_uri=args.s3_uri,
            output_dir=args.output_dir,
            model_name=args.model_name,
            local_cache=args.local_cache,
            quantize=args.quantize,
            max_seq_length=args.max_seq_length,
            min_seq_length=args.min_seq_length,
        )
    else:
        export_coreml(
            checkpoint_dir=args.checkpoint_dir,
            output_dir=args.output_dir,
            model_name=args.model_name,
            quantize=args.quantize,
            max_seq_length=args.max_seq_length,
            min_seq_length=args.min_seq_length,
        )
