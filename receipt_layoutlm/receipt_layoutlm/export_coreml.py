"""Export trained LayoutLM model to CoreML format for Swift inference."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import torch

from receipt_layoutlm.exceptions import (
    CoreMLExportError,
    MissingDependencyError,
    NaNWeightsError,
)
from receipt_layoutlm.export_bundle import write_export_sidecars
from receipt_layoutlm.model_wrappers import (
    LayoutLMv3Wrapper,
    LayoutLMWrapper,
)

__all__ = [
    "CoreMLExportError",
    "LayoutLMWrapper",
    "LayoutLMv3Wrapper",
    "MissingDependencyError",
    "NaNWeightsError",
]


def export_coreml(
    checkpoint_dir: str,
    output_dir: str,
    model_name: str = "LayoutLM",
    max_seq_length: int = 512,
    min_seq_length: int = 1,
    quantize: Optional[str] = None,
    model_version: str = "v1",
) -> str:
    """Export a trained LayoutLM checkpoint to CoreML format.

    Args:
        checkpoint_dir: Path to checkpoint containing model files.
        output_dir: Directory to write CoreML bundle.
        model_name: Name for the .mlpackage file.
        max_seq_length: Maximum sequence length for model.
        min_seq_length: Minimum sequence length for model.
        quantize: Quantization mode: None, "float16", "int8", or "int4".
        model_version: Model version to export: "v1" or "v3".

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

        if model_version == "v3":
            from transformers import (
                LayoutLMv3ForTokenClassification,
                LayoutLMv3TokenizerFast,
            )
        else:
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
    if model_version == "v3":
        model = LayoutLMv3ForTokenClassification.from_pretrained(
            checkpoint_path
        )
        tokenizer = LayoutLMv3TokenizerFast.from_pretrained(checkpoint_path)
    else:
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
    if model_version == "v3":
        wrapper = LayoutLMv3Wrapper(model)
    else:
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

    trace_inputs = (
        sample_input_ids,
        sample_attention_mask,
        sample_bbox,
        sample_token_type_ids,
    )

    if model_version == "v3":
        sample_pixel_values = torch.randn(1, 3, 224, 224)
        trace_inputs = trace_inputs + (sample_pixel_values,)

    print("Tracing model with TorchScript...")

    # Trace the model
    with torch.no_grad():
        traced_model = torch.jit.trace(wrapper, trace_inputs)

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

    coreml_inputs = [
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
    ]

    if model_version == "v3":
        coreml_inputs.append(
            ct.TensorType(
                name="pixel_values",
                shape=(1, 3, 224, 224),
                dtype=np.float32,
            ),
        )

    # Convert to CoreML with explicit int32 dtype to match Swift MLMultiArray
    mlmodel = ct.convert(
        traced_model,
        inputs=coreml_inputs,
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

    # Validate that the weight file contains no NaN/Inf values.
    # Float16 conversion can overflow certain weights (FP16 max is 65504),
    # producing -Inf that causes NaN at inference via 0 * (-Inf).
    # Only check for float16 exports where weight.bin actually stores fp16
    # data. FP32 and int8/int4 exports use different binary layouts.
    weight_path = (
        mlpackage_path / "Data" / "com.apple.CoreML" / "weights" / "weight.bin"
    )
    if weight_path.exists() and quantize == "float16":
        raw = weight_path.read_bytes()
        fp16_arr = np.frombuffer(raw, dtype=np.float16)
        bad_count = int(np.isnan(fp16_arr).sum() + np.isinf(fp16_arr).sum())
        if bad_count > 0:
            raise NaNWeightsError(bad_count, weight_path)
        print(f"Weight validation passed: {len(fp16_arr):,} values, 0 NaN/Inf")

    write_export_sidecars(
        checkpoint_path=checkpoint_path,
        output_path=output_path,
        tokenizer=tokenizer,
        config=config,
        id2label=id2label,
        label2id=label2id,
        num_labels=num_labels,
        model_version=model_version,
    )

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
    model_version: str = "v1",
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
        model_version: Model version to export: "v1" or "v3".

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
            model_version=model_version,
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
    parser.add_argument(
        "--model-version",
        choices=["v1", "v3"],
        default="v1",
        help="LayoutLM model version: v1 or v3",
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
            model_version=args.model_version,
        )
    else:
        export_coreml(
            checkpoint_dir=args.checkpoint_dir,
            output_dir=args.output_dir,
            model_name=args.model_name,
            quantize=args.quantize,
            max_seq_length=args.max_seq_length,
            min_seq_length=args.min_seq_length,
            model_version=args.model_version,
        )
