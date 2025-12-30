#!/usr/bin/env python3
"""SageMaker training entrypoint for LayoutLM models.

This script is called by SageMaker to run training. It:
1. Reads hyperparameters from SageMaker environment
2. Runs the layoutlm-cli train command
3. Copies model outputs to the SageMaker model directory

SageMaker Environment:
- SM_MODEL_DIR: Where to save model artifacts (will be uploaded to S3)
- SM_OUTPUT_DATA_DIR: Additional output data location
- SM_CHANNEL_*: Input data channels (not used - we read from DynamoDB)
- SM_HPS: Hyperparameters as JSON (or individual SM_HP_* env vars)
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def get_hyperparameters() -> dict:
    """Get hyperparameters from SageMaker environment."""
    # SageMaker provides hyperparameters in multiple ways:
    # 1. /opt/ml/input/config/hyperparameters.json (BYOC containers)
    # 2. SM_HPS environment variable (JSON string)
    # 3. SM_HP_* environment variables
    hps = {}

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


def build_train_command(hps: dict) -> list[str]:
    """Build the layoutlm-cli train command from hyperparameters."""
    cmd = ["layoutlm-cli", "train"]

    # Required parameters
    job_name = hps.get("job_name", os.environ.get("TRAINING_JOB_NAME", "sagemaker-job"))
    dynamo_table = hps.get("dynamo_table", os.environ.get("DYNAMO_TABLE_NAME"))

    if not dynamo_table:
        raise ValueError("dynamo_table hyperparameter or DYNAMO_TABLE_NAME env var required")

    cmd.extend(["--job-name", job_name])
    cmd.extend(["--dynamo-table", dynamo_table])

    # Optional hyperparameters with CLI flag mapping
    param_mapping = {
        "epochs": "--epochs",
        "batch_size": "--batch-size",
        "learning_rate": "--lr",
        "lr": "--lr",
        "warmup_ratio": "--warmup-ratio",
        "gradient_accumulation_steps": "--gradient-accumulation-steps",
        "label_smoothing": "--label-smoothing",
        "early_stopping_patience": "--early-stopping-patience",
        "pretrained": "--pretrained",
        "o_entity_ratio": "--o-entity-ratio",
    }

    for hp_name, cli_flag in param_mapping.items():
        if hp_name in hps:
            cmd.extend([cli_flag, str(hps[hp_name])])

    # New universal label merges (preferred over legacy boolean flags)
    if "label_merges" in hps:
        label_merges_value = hps["label_merges"]
        # Handle both string (JSON) and dict forms
        if isinstance(label_merges_value, dict):
            label_merges_value = json.dumps(label_merges_value)
        cmd.extend(["--label-merges", label_merges_value])

    # Merge preset shortcut
    if "merge_preset" in hps:
        cmd.extend(["--merge-preset", str(hps["merge_preset"])])

    # Legacy boolean flags (kept for backwards compatibility)
    # Only applied if no label_merges or merge_preset is specified
    if "label_merges" not in hps and "merge_preset" not in hps:
        if hps.get("merge_amounts", "").lower() == "true":
            cmd.append("--merge-amounts")
        if hps.get("merge_date_time", "").lower() == "true":
            cmd.append("--merge-date-time")
        if hps.get("merge_address_phone", "").lower() == "true":
            cmd.append("--merge-address-phone")

    # Allowed labels (can be comma-separated)
    if "allowed_labels" in hps:
        labels = hps["allowed_labels"]
        if isinstance(labels, str):
            labels = labels.split(",")
        for label in labels:
            cmd.extend(["--allowed-label", label.strip()])

    # Output path - use SageMaker's model dir
    # The trainer will save here, and SageMaker uploads to S3 automatically
    output_path = hps.get("output_s3_path")
    if output_path:
        cmd.extend(["--output-s3-path", output_path])

    return cmd


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

    # Build and run training command
    cmd = build_train_command(hps)
    print(f"Running: {' '.join(cmd)}")
    print("=" * 60)

    # Run training
    result = subprocess.run(cmd, check=False)

    if result.returncode != 0:
        print(f"Training failed with exit code {result.returncode}")
        sys.exit(result.returncode)

    # Copy model to SageMaker model directory
    job_name = hps.get("job_name", os.environ.get("TRAINING_JOB_NAME", "sagemaker-job"))
    copy_model_to_sagemaker_dir(job_name)

    print("=" * 60)
    print("Training complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
