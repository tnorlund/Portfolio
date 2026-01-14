#!/usr/bin/env python3
"""SageMaker training entrypoint for LayoutLM models.

This script is called by SageMaker to run training. It:
1. Reads hyperparameters from SageMaker environment
2. Builds DataConfig and TrainingConfig from hyperparameters
3. Runs the trainer directly (no subprocess)
4. Copies model outputs to the SageMaker model directory

SageMaker Environment:
- SM_MODEL_DIR: Where to save model artifacts (will be uploaded to S3)
- SM_OUTPUT_DATA_DIR: Additional output data location
- SM_CHANNEL_*: Input data channels (not used - we read from DynamoDB)
- SM_HPS: Hyperparameters as JSON (or individual SM_HP_* env vars)
"""

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from receipt_layoutlm.config import (
    MERGE_PRESETS,
    DataConfig,
    ModelType,
    TrainingConfig,
)
from receipt_layoutlm.trainer import ReceiptLayoutLMTrainer


def get_hyperparameters() -> dict:
    """Get hyperparameters from SageMaker environment."""
    # SageMaker provides hyperparameters in multiple ways:
    # 1. /opt/ml/input/config/hyperparameters.json (BYOC containers)
    # 2. SM_HPS environment variable (JSON string)
    # 3. SM_HP_* environment variables
    hps: Dict[str, Any] = {}

    # Try hyperparameters.json first (BYOC containers)
    hp_file = Path("/opt/ml/input/config/hyperparameters.json")
    if hp_file.exists():
        try:
            with open(hp_file) as f:
                hps = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: Failed to parse {hp_file}: {e}")

    # Try SM_HPS (JSON format)
    if not hps and "SM_HPS" in os.environ:
        try:
            hps = json.loads(os.environ["SM_HPS"])
        except json.JSONDecodeError as e:
            print(f"Warning: Failed to parse SM_HPS: {e}")

    # Also check individual SM_HP_* variables
    for key, value in os.environ.items():
        if key.startswith("SM_HP_"):
            param_name = key[6:].lower()  # Remove SM_HP_ prefix
            hps[param_name] = value

    return hps


def _parse_bool(value: Any) -> bool:
    """Parse a boolean from string or bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    return bool(value)


def _build_label_merges(hps: dict) -> Optional[Dict[str, List[str]]]:
    """Build label_merges dict from hyperparameters.

    Priority order:
    1. merge_preset (if specified and not 'none')
    2. label_merges JSON (overrides/extends preset)
    3. Legacy boolean flags (only if no preset and no explicit merges)
    """
    result: Dict[str, List[str]] = {}

    # 1. Apply preset if specified
    merge_preset = hps.get("merge_preset")
    if merge_preset and merge_preset != "none":
        preset = MERGE_PRESETS.get(merge_preset)
        if preset:
            result.update(preset)

    # 2. Apply explicit JSON merges (overrides/extends preset)
    label_merges_raw = hps.get("label_merges")
    if label_merges_raw:
        if isinstance(label_merges_raw, str):
            try:
                explicit_merges = json.loads(label_merges_raw)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON for label_merges: {e}") from e
        else:
            explicit_merges = label_merges_raw

        if not isinstance(explicit_merges, dict):
            raise ValueError("label_merges must be a JSON object")
        result.update(explicit_merges)

    # 3. Apply legacy boolean flags (only if no preset and no explicit merges)
    if not merge_preset and not label_merges_raw:
        if _parse_bool(hps.get("merge_amounts", False)):
            result["AMOUNT"] = ["LINE_TOTAL", "SUBTOTAL", "TAX", "GRAND_TOTAL"]
        if _parse_bool(hps.get("merge_date_time", False)):
            result["DATE"] = ["TIME"]
        if _parse_bool(hps.get("merge_address_phone", False)):
            result["ADDRESS"] = ["PHONE_NUMBER", "ADDRESS_LINE"]

    return result if result else None


def build_configs(hps: dict) -> tuple[DataConfig, TrainingConfig, str]:
    """Build DataConfig and TrainingConfig from hyperparameters.

    Returns:
        Tuple of (data_config, training_config, job_name)
    """
    # Required parameters
    job_name = hps.get("job_name", os.environ.get("TRAINING_JOB_NAME", "sagemaker-job"))
    dynamo_table = hps.get("dynamo_table", os.environ.get("DYNAMO_TABLE_NAME"))

    if not dynamo_table:
        raise ValueError("dynamo_table hyperparameter or DYNAMO_TABLE_NAME env var required")

    # Build DataConfig
    data_cfg = DataConfig(
        dynamo_table_name=dynamo_table,
        aws_region=hps.get("region", os.environ.get("AWS_REGION", "us-east-1")),
    )

    # Build TrainingConfig with defaults
    train_cfg = TrainingConfig(
        epochs=int(hps.get("epochs", 10)),
        batch_size=int(hps.get("batch_size", 8)),
        learning_rate=float(hps.get("learning_rate", hps.get("lr", 5e-5))),
        pretrained_model_name=hps.get("pretrained", "microsoft/layoutlm-base-uncased"),
    )

    # Optional training config overrides
    if "warmup_ratio" in hps:
        train_cfg.warmup_ratio = float(hps["warmup_ratio"])
    if "label_smoothing" in hps:
        train_cfg.label_smoothing = float(hps["label_smoothing"])
    if "early_stopping_patience" in hps:
        train_cfg.early_stopping_patience = int(hps["early_stopping_patience"])
    if "gradient_accumulation_steps" in hps:
        train_cfg.gradient_accumulation_steps = int(hps["gradient_accumulation_steps"])

    # O:entity ratio for downsampling all-O lines
    if "o_entity_ratio" in hps:
        os.environ["LAYOUTLM_O_TO_ENTITY_RATIO"] = str(hps["o_entity_ratio"])

    # Allowed labels whitelist
    allowed_labels = hps.get("allowed_labels")
    if allowed_labels:
        if isinstance(allowed_labels, str):
            data_cfg.allowed_labels = [l.strip() for l in allowed_labels.split(",")]
        else:
            data_cfg.allowed_labels = allowed_labels

    # Label merges (preset, JSON, or legacy flags)
    data_cfg.label_merges = _build_label_merges(hps)

    # Legacy merge_amounts for backwards compatibility
    data_cfg.merge_amounts = _parse_bool(hps.get("merge_amounts", False))

    # Two-pass training: region extraction settings
    region_extraction = hps.get("region_extraction")
    if region_extraction and region_extraction != "none":
        data_cfg.region_extraction = region_extraction
        if "region_y_margin" in hps:
            data_cfg.region_y_margin = float(hps["region_y_margin"])

    # Dataset snapshot load/save
    data_cfg.dataset_snapshot_load = hps.get("dataset_snapshot_load")
    data_cfg.dataset_snapshot_save = hps.get("dataset_snapshot_save")

    # Output S3 path
    train_cfg.output_s3_path = hps.get("output_s3_path")

    # CoreML export settings
    train_cfg.auto_export_coreml = _parse_bool(hps.get("export_coreml", False))
    if "coreml_quantize" in hps:
        train_cfg.coreml_quantize = hps["coreml_quantize"]

    # Two-pass model type identification
    model_type = hps.get("model_type", "single_pass")
    train_cfg.model_type = ModelType(model_type)

    return data_cfg, train_cfg, job_name


def copy_model_to_sagemaker_dir(job_name: str):
    """Copy trained model to SageMaker's model directory."""
    model_dir = os.environ.get("SM_MODEL_DIR", "/opt/ml/model")
    training_dir = Path(f"/tmp/receipt_layoutlm/{job_name}")

    if not training_dir.exists():
        print(f"Warning: Training directory {training_dir} not found")
        return

    # Copy all model artifacts
    for item in training_dir.iterdir():
        dest = Path(model_dir) / item.name
        if item.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)

    print(f"Copied model artifacts to {model_dir}")


def main():
    """Main training entrypoint."""
    print("=" * 60)
    print("LayoutLM SageMaker Training")
    print("=" * 60)

    # Print environment info
    print(f"Python: {sys.version}")
    print(f"Working directory: {os.getcwd()}")

    # Get hyperparameters
    hps = get_hyperparameters()
    print(f"Hyperparameters: {json.dumps(hps, indent=2)}")

    # Build configs from hyperparameters
    print("Building configuration...")
    data_cfg, train_cfg, job_name = build_configs(hps)

    print(f"Job name: {job_name}")
    print(f"DynamoDB table: {data_cfg.dynamo_table_name}")
    print(f"Epochs: {train_cfg.epochs}")
    print(f"Batch size: {train_cfg.batch_size}")
    print(f"Learning rate: {train_cfg.learning_rate}")
    print(f"Model type: {train_cfg.model_type.value}")
    if data_cfg.label_merges:
        print(f"Label merges: {data_cfg.label_merges}")
    if data_cfg.region_extraction:
        print(f"Region extraction: {data_cfg.region_extraction}")

    print("=" * 60)
    print("Starting training...")
    print("=" * 60)

    # Run training directly
    trainer = ReceiptLayoutLMTrainer(data_cfg, train_cfg)
    job_id = trainer.train(job_name=job_name)

    print(f"Training job completed: {job_id}")

    # Copy model to SageMaker model directory
    copy_model_to_sagemaker_dir(job_name)

    print("=" * 60)
    print("Training complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
