"""Export trained LayoutLM v1 checkpoints to Apple Core AI (.aimodel).

Uses torch.export + coreai-torch. Runs in an isolated Python 3.13 environment
with torch>=2.8; does not constrain the main LayoutLM package.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Optional, Tuple

import torch

from receipt_layoutlm.exceptions import (
    CoreAIExportError,
    MissingDependencyError,
)
from receipt_layoutlm.export_bundle import write_export_sidecars
from receipt_layoutlm.model_wrappers import LayoutLMWrapper

# Fixed sequence length for the first Core AI bring-up. Dynamic dims
# complicate torch.export / Core AI conversion; Swift already pads to 512.
COREAI_SEQ_LENGTH = 512


def _sample_inputs(
    vocab_size: int, seq_len: int = COREAI_SEQ_LENGTH
) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
    """Build deterministic-shaped sample tensors for torch.export."""
    input_ids = torch.randint(0, max(vocab_size, 1), (1, seq_len))
    attention_mask = torch.ones(1, seq_len, dtype=torch.long)
    x1 = torch.randint(0, 500, (1, seq_len))
    y1 = torch.randint(0, 500, (1, seq_len))
    x2 = x1 + torch.randint(1, 500, (1, seq_len))
    y2 = y1 + torch.randint(1, 500, (1, seq_len))
    bbox = torch.stack([x1, y1, x2, y2], dim=-1)
    token_type_ids = torch.zeros(1, seq_len, dtype=torch.long)
    return input_ids, attention_mask, bbox, token_type_ids


def export_coreai(
    checkpoint_dir: str,
    output_dir: str,
    model_name: str = "LayoutLM",
    seq_length: int = COREAI_SEQ_LENGTH,
) -> str:
    """Export a LayoutLM v1 checkpoint to a Core AI ``.aimodel`` bundle.

    Args:
        checkpoint_dir: Path to HF checkpoint (config, weights, tokenizer).
        output_dir: Directory to write the bundle.
        model_name: Stem for the ``.aimodel`` asset (default LayoutLM).
        seq_length: Fixed sequence length for export (default 512).

    Returns:
        Path to the created model bundle directory.

    Raises:
        MissingDependencyError: If coreai-torch / transformers are missing.
        CoreAIExportError: On conversion failure.
        ValueError: If seq_length is not positive.
    """
    if seq_length <= 0:
        raise ValueError(f"seq_length must be positive, got {seq_length}")

    try:
        from coreai_torch import TorchConverter, get_decomp_table
        from transformers import (
            LayoutLMForTokenClassification,
            LayoutLMTokenizerFast,
        )
    except ImportError as e:
        raise MissingDependencyError(
            "coreai-torch and transformers required for Core AI export: "
            "pip install -e 'receipt_layoutlm[coreai]'"
        ) from e

    checkpoint_path = Path(checkpoint_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    print(f"Loading LayoutLM v1 checkpoint from {checkpoint_path}...")
    model = LayoutLMForTokenClassification.from_pretrained(checkpoint_path)
    tokenizer = LayoutLMTokenizerFast.from_pretrained(checkpoint_path)
    model.eval()

    config = model.config
    num_labels = config.num_labels
    id2label = config.id2label
    label2id = config.label2id
    print(f"Model has {num_labels} labels: {list(id2label.values())}")

    wrapper = LayoutLMWrapper(model)
    wrapper.eval()
    sample_inputs = _sample_inputs(tokenizer.vocab_size, seq_length)

    print(
        f"Exporting with torch.export at fixed seq_length={seq_length} "
        "(float32)..."
    )
    try:
        with torch.no_grad():
            exported = torch.export.export(wrapper, args=sample_inputs)
            exported = exported.run_decompositions(get_decomp_table())
    except Exception as e:
        raise CoreAIExportError(
            f"torch.export failed for LayoutLM wrapper: {e}"
        ) from e

    input_names = [
        "input_ids",
        "attention_mask",
        "bbox",
        "token_type_ids",
    ]
    print("Converting ExportedProgram to Core AI...")
    try:
        converter = TorchConverter()
        # Prefer the documented kwargs form; fall back if an older API
        # rejects keyword arguments.
        try:
            converter.add_exported_program(
                exported,
                input_names=input_names,
                output_names=["logits"],
            )
        except TypeError:
            converter.add_exported_program(exported)
        program = converter.to_coreai()
        program.optimize()
    except Exception as e:
        raise CoreAIExportError(f"Core AI conversion failed: {e}") from e

    aimodel_path = output_path / f"{model_name}.aimodel"
    if aimodel_path.exists():
        # save_asset typically creates the directory; remove a stale one.
        shutil.rmtree(aimodel_path)

    print(f"Saving Core AI asset to {aimodel_path}...")
    try:
        program.save_asset(str(aimodel_path))
    except Exception as e:
        raise CoreAIExportError(f"Failed to save .aimodel asset: {e}") from e

    if not aimodel_path.exists():
        raise CoreAIExportError(
            f"save_asset completed but {aimodel_path} was not created"
        )

    write_export_sidecars(
        checkpoint_path=checkpoint_path,
        output_path=output_path,
        tokenizer=tokenizer,
        config=config,
        id2label=id2label,
        label2id=label2id,
        num_labels=num_labels,
        model_version="v1",
    )

    # Record export provenance for the Swift / agent tooling.
    provenance = {
        "format": "coreai",
        "model_name": model_name,
        "seq_length": seq_length,
        "precision": "float32",
        "model_version": "v1",
        "inputs": input_names,
        "outputs": ["logits"],
    }
    provenance_path = output_path / "coreai_export.json"
    with open(provenance_path, "w", encoding="utf-8") as f:
        json.dump(provenance, f, indent=2)
        f.write("\n")

    print(f"\nCore AI bundle created at: {output_path}")
    for item in sorted(output_path.iterdir(), key=lambda p: p.name):
        suffix = "/" if item.is_dir() else ""
        print(f"  {item.name}{suffix}")

    return str(output_path)


def export_coreai_from_s3(
    s3_uri: str,
    output_dir: str,
    model_name: str = "LayoutLM",
    local_cache: Optional[str] = None,
    seq_length: int = COREAI_SEQ_LENGTH,
) -> str:
    """Download a checkpoint from S3 and export it to Core AI."""
    import tempfile
    from urllib.parse import urlparse

    try:
        import boto3
    except ImportError as e:
        raise MissingDependencyError(
            "boto3 required for S3 download: pip install boto3"
        ) from e

    parsed = urlparse(s3_uri)
    bucket = parsed.netloc
    prefix = parsed.path.lstrip("/")

    temp_dir_obj = None
    if local_cache:
        cache_dir = Path(local_cache)
        cache_dir.mkdir(parents=True, exist_ok=True)
    else:
        temp_dir_obj = tempfile.TemporaryDirectory(prefix="layoutlm_coreai_")
        cache_dir = Path(temp_dir_obj.name)

    try:
        print(f"Downloading model from {s3_uri} to {cache_dir}...")
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

        return export_coreai(
            checkpoint_dir=str(cache_dir),
            output_dir=output_dir,
            model_name=model_name,
            seq_length=seq_length,
        )
    finally:
        if temp_dir_obj is not None:
            temp_dir_obj.cleanup()
