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

    # Copy config.json, ensuring num_labels is present
    config_src = checkpoint_path / "config.json"
    config_dst = output_path / "config.json"
    if config_src.exists():
        # Read existing config and ensure num_labels is present
        with open(config_src, "r", encoding="utf-8") as f:
            config_data = json.load(f)
        # Add num_labels if missing (required by Swift LayoutLMConfig)
        if "num_labels" not in config_data:
            config_data["num_labels"] = num_labels
            print(f"Added num_labels={num_labels} to config.json")
        with open(config_dst, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2)
            f.write("\n")  # Trailing newline
        print(f"Saved config.json to {config_dst}")
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
            f.write("\n")  # Trailing newline
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


def _export_single_model_from_s3(
    s3_uri: str,
    output_dir: str,
    model_name: str,
    quantize: Optional[str],
    max_seq_length: int,
    min_seq_length: int,
    temp_cache_dir: Path,
) -> dict:
    """Internal helper to download and export a single model from S3.

    Returns dict with model metadata for manifest creation.
    """
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

    # Create model-specific subdirectory in cache
    model_cache_dir = temp_cache_dir / model_name.lower().replace(" ", "_")
    model_cache_dir.mkdir(parents=True, exist_ok=True)

    print(f"Downloading model from {s3_uri} to {model_cache_dir}...")

    # Download all files from S3 prefix
    s3 = boto3.client("s3")
    paginator = s3.get_paginator("list_objects_v2")

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            rel_path = key[len(prefix) :].lstrip("/")
            if not rel_path:
                continue

            local_path = model_cache_dir / rel_path
            local_path.parent.mkdir(parents=True, exist_ok=True)

            print(f"  Downloading {rel_path}...")
            s3.download_file(bucket, key, str(local_path))

    # Export from downloaded checkpoint
    export_coreml(
        checkpoint_dir=str(model_cache_dir),
        output_dir=output_dir,
        model_name=model_name,
        quantize=quantize,
        max_seq_length=max_seq_length,
        min_seq_length=min_seq_length,
    )

    # Read run.json if available for metadata
    run_json_path = model_cache_dir / "run.json"
    model_metadata = {"s3_uri": s3_uri, "model_name": model_name}
    if run_json_path.exists():
        with open(run_json_path, "r", encoding="utf-8") as f:
            run_data = json.load(f)
            model_metadata["model_type"] = run_data.get("model_type", "unknown")
            model_metadata["job_name"] = run_data.get("job_name", "unknown")
            model_metadata["label_list"] = run_data.get("label_list", [])

    return model_metadata


def export_two_pass_coreml(
    pass1_s3_uri: str,
    pass2_s3_uri: str,
    output_dir: str,
    quantize: Optional[str] = None,
    max_seq_length: int = 512,
    min_seq_length: int = 1,
    local_cache: Optional[str] = None,
) -> str:
    """Export two-pass LayoutLM models to CoreML format.

    Creates a bundle with both Pass 1 (region detection) and Pass 2
    (fine-grained classification) models, along with a manifest file
    that describes the two-pass pipeline.

    Args:
        pass1_s3_uri: S3 URI to Pass 1 model checkpoint.
        pass2_s3_uri: S3 URI to Pass 2 model checkpoint.
        output_dir: Directory to write CoreML bundles.
        quantize: Quantization mode: None, "float16", "int8", or "int4".
        max_seq_length: Maximum sequence length for models.
        min_seq_length: Minimum sequence length for models.
        local_cache: Local directory to cache downloaded models.

    Returns:
        Path to the created bundle directory.
    """
    import tempfile

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Use provided cache dir or create temp dir
    temp_dir_obj = None
    if local_cache:
        cache_dir = Path(local_cache)
        cache_dir.mkdir(parents=True, exist_ok=True)
    else:
        temp_dir_obj = tempfile.TemporaryDirectory(prefix="layoutlm_two_pass_")
        cache_dir = Path(temp_dir_obj.name)

    try:
        print("=" * 60)
        print("Exporting Two-Pass LayoutLM Models to CoreML")
        print("=" * 60)

        # Export Pass 1 model
        print("\n[Pass 1] Region Detection Model")
        print("-" * 40)
        pass1_metadata = _export_single_model_from_s3(
            s3_uri=pass1_s3_uri,
            output_dir=output_dir,
            model_name="LayoutLM_Pass1",
            quantize=quantize,
            max_seq_length=max_seq_length,
            min_seq_length=min_seq_length,
            temp_cache_dir=cache_dir,
        )

        # Export Pass 2 model
        print("\n[Pass 2] Fine-Grained Classification Model")
        print("-" * 40)
        pass2_metadata = _export_single_model_from_s3(
            s3_uri=pass2_s3_uri,
            output_dir=output_dir,
            model_name="LayoutLM_Pass2",
            quantize=quantize,
            max_seq_length=max_seq_length,
            min_seq_length=min_seq_length,
            temp_cache_dir=cache_dir,
        )

        # Create two-pass manifest
        manifest = {
            "pipeline_type": "two_pass",
            "version": "1.0",
            "pass1": {
                "model_file": "LayoutLM_Pass1.mlpackage",
                "purpose": "region_detection",
                "region_label": "FINANCIAL_REGION",
                **pass1_metadata,
            },
            "pass2": {
                "model_file": "LayoutLM_Pass2.mlpackage",
                "purpose": "fine_grained_classification",
                **pass2_metadata,
            },
            "inference_config": {
                "min_region_tokens": 3,
                "min_confidence_skip_pass2": 0.95,
            },
        }

        manifest_path = output_path / "two_pass_manifest.json"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
            f.write("\n")

        print("\n" + "=" * 60)
        print(f"Two-pass CoreML bundle created at: {output_path}")
        print("Contents:")
        for item in sorted(output_path.iterdir()):
            if item.is_dir():
                print(f"  {item.name}/")
            else:
                print(f"  {item.name}")
        print("=" * 60)

        return str(output_path)

    finally:
        if temp_dir_obj is not None:
            temp_dir_obj.cleanup()


def export_two_pass_coreml_auto_discover(
    bucket_name: str,
    output_dir: str,
    quantize: Optional[str] = None,
    max_seq_length: int = 512,
    min_seq_length: int = 1,
    local_cache: Optional[str] = None,
) -> str:
    """Export two-pass models to CoreML, auto-discovering from S3 bucket.

    Scans the bucket for models with model_type=two_pass_p1 and two_pass_p2
    in their run.json, selects the latest of each, and exports both.

    Args:
        bucket_name: S3 bucket to scan for two-pass models.
        output_dir: Directory to write CoreML bundles.
        quantize: Quantization mode: None, "float16", "int8", or "int4".
        max_seq_length: Maximum sequence length for models.
        min_seq_length: Minimum sequence length for models.
        local_cache: Local directory to cache downloaded models.

    Returns:
        Path to the created bundle directory.

    Raises:
        ValueError: If matching Pass 1 or Pass 2 model not found.
    """
    # Import discovery function from inference module
    from .inference import discover_two_pass_models

    print(f"Scanning bucket '{bucket_name}' for two-pass models...")
    model_pair = discover_two_pass_models(bucket_name)

    if model_pair is None:
        raise ValueError(
            f"Could not find matching Pass 1 and Pass 2 models in bucket '{bucket_name}'. "
            "Ensure models have model_type='two_pass_p1' and 'two_pass_p2' in run.json."
        )

    print(f"Found Pass 1 model: {model_pair.pass1.job_name} ({model_pair.pass1.s3_uri})")
    print(f"Found Pass 2 model: {model_pair.pass2.job_name} ({model_pair.pass2.s3_uri})")

    return export_two_pass_coreml(
        pass1_s3_uri=model_pair.pass1.s3_uri,
        pass2_s3_uri=model_pair.pass2.s3_uri,
        output_dir=output_dir,
        quantize=quantize,
        max_seq_length=max_seq_length,
        min_seq_length=min_seq_length,
        local_cache=local_cache,
    )


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

    # Single model export options
    parser.add_argument(
        "--checkpoint-dir",
        help="Local directory containing model checkpoint",
    )
    parser.add_argument(
        "--s3-uri",
        help="S3 URI to model checkpoint (s3://bucket/prefix/)",
    )
    parser.add_argument(
        "--model-name",
        default="LayoutLM",
        help="Name for the .mlpackage file",
    )

    # Two-pass export options
    parser.add_argument(
        "--two-pass",
        action="store_true",
        help="Export two-pass model pipeline (requires --pass1-s3-uri and --pass2-s3-uri, or --auto-discover-bucket)",
    )
    parser.add_argument(
        "--pass1-s3-uri",
        help="S3 URI to Pass 1 (region detection) model for two-pass export",
    )
    parser.add_argument(
        "--pass2-s3-uri",
        help="S3 URI to Pass 2 (fine-grained classification) model for two-pass export",
    )
    parser.add_argument(
        "--auto-discover-bucket",
        help="S3 bucket to auto-discover two-pass models (alternative to --pass1-s3-uri and --pass2-s3-uri)",
    )

    # Common options
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory to write CoreML bundle",
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

    if args.min_seq_length > args.max_seq_length:
        parser.error(
            "--min-seq-length cannot be greater than --max-seq-length"
        )

    if args.two_pass:
        # Two-pass export mode
        if args.auto_discover_bucket:
            # Auto-discover models from bucket
            export_two_pass_coreml_auto_discover(
                bucket_name=args.auto_discover_bucket,
                output_dir=args.output_dir,
                quantize=args.quantize,
                max_seq_length=args.max_seq_length,
                min_seq_length=args.min_seq_length,
                local_cache=args.local_cache,
            )
        elif args.pass1_s3_uri and args.pass2_s3_uri:
            # Explicit model URIs
            export_two_pass_coreml(
                pass1_s3_uri=args.pass1_s3_uri,
                pass2_s3_uri=args.pass2_s3_uri,
                output_dir=args.output_dir,
                quantize=args.quantize,
                max_seq_length=args.max_seq_length,
                min_seq_length=args.min_seq_length,
                local_cache=args.local_cache,
            )
        else:
            parser.error(
                "--two-pass requires either --auto-discover-bucket, or both "
                "--pass1-s3-uri and --pass2-s3-uri"
            )
    elif args.s3_uri:
        export_from_s3(
            s3_uri=args.s3_uri,
            output_dir=args.output_dir,
            model_name=args.model_name,
            local_cache=args.local_cache,
            quantize=args.quantize,
            max_seq_length=args.max_seq_length,
            min_seq_length=args.min_seq_length,
        )
    elif args.checkpoint_dir:
        export_coreml(
            checkpoint_dir=args.checkpoint_dir,
            output_dir=args.output_dir,
            model_name=args.model_name,
            quantize=args.quantize,
            max_seq_length=args.max_seq_length,
            min_seq_length=args.min_seq_length,
        )
    else:
        parser.error(
            "Either --checkpoint-dir, --s3-uri, or --two-pass with model "
            "source options is required"
        )
