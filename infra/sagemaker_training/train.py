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
            suffix = key[6:]  # Remove SM_HP_ prefix
            # Preserve original case for `env_*` keys — they get promoted to
            # process env vars in main() and the downstream code reads
            # LAYOUTLM_CLASS_WEIGHT_MAX, not layoutlm_class_weight_max.
            if suffix.lower().startswith("env_"):
                param_name = "env_" + suffix[len("env_"):]
            else:
                param_name = suffix.lower()
            hps[param_name] = value

    return hps


def build_train_command(hps: dict) -> list[str]:
    """Build the layoutlm-cli train command from hyperparameters."""
    cmd = ["layoutlm-cli", "train"]

    # Required parameters
    job_name = hps.get(
        "job_name", os.environ.get("TRAINING_JOB_NAME", "sagemaker-job")
    )
    dynamo_table = hps.get("dynamo_table", os.environ.get("DYNAMO_TABLE_NAME"))

    if not dynamo_table:
        raise ValueError(
            "dynamo_table hyperparameter or DYNAMO_TABLE_NAME env var required"
        )

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
        "resume_from_s3": "--resume-from-s3",
        "model_version": "--model-version",
        "synthetic_training_examples": "--synthetic-training-examples",
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

    # In-training windowed held-out eval is ON by default; allow opting out via
    # hyperparameter (eval_heldout_windowed=false).
    if str(hps.get("eval_heldout_windowed", "")).lower() == "false":
        cmd.append("--no-eval-heldout-windowed")

    # Pinned canonical val split (shared across runs for comparability).
    if hps.get("val_keys_s3"):
        cmd.extend(["--val-keys-s3", str(hps["val_keys_s3"])])

    # Scoped second-pass training: line-item band crop + curated receipt subset.
    if hps.get("scope"):
        cmd.extend(["--scope", str(hps["scope"])])
    if hps.get("receipt_allowlist_s3"):
        cmd.extend(
            ["--receipt-allowlist-s3", str(hps["receipt_allowlist_s3"])]
        )

    # Output path - use SageMaker's model dir
    # The trainer will save here, and SageMaker uploads to S3 automatically
    output_path = hps.get("output_s3_path")
    if output_path:
        cmd.extend(["--output-s3-path", output_path])

    return cmd


def copy_model_to_sagemaker_dir(job_name: str):
    """Copy trained model to SageMaker's model directory.

    The training output directory is resolved by
    ``receipt_layoutlm.trainer._resolve_output_dir`` so the SageMaker
    container side stays in lockstep with the trainer's own writes
    (avoids the kind of path-mismatch silent data loss this PR
    addresses for spot restarts).
    """
    model_dir = os.environ.get("SM_MODEL_DIR", "/opt/ml/model")
    # Import locally so train.py is still importable in environments
    # that don't have receipt_layoutlm installed (it's only required at
    # SageMaker container runtime, not at static-analysis time).
    from receipt_layoutlm.trainer import _resolve_output_dir

    training_dir = Path(_resolve_output_dir(job_name))

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

    # Promote hyperparameters of the form `env_VAR=val` to process env vars
    # before launching the trainer. Lets callers tune env-driven knobs
    # (LAYOUTLM_WINDOW_SIZE, LAYOUTLM_CLASS_WEIGHT_*, etc.) per training job
    # without adding a new CLI flag for each one.
    child_env = os.environ.copy()
    for key, value in hps.items():
        if key.startswith("env_"):
            env_name = key[len("env_") :]
            child_env[env_name] = str(value)
            print(f"  setenv {env_name}={value}")

    # Pin the synthetic-data quality gate ON for every training run unless a job
    # EXPLICITLY opted out via env_LAYOUTLM_SYNTHETIC_QUALITY_GATE. That gate
    # (receipt_layoutlm.data_loader) is what rejects ungrounded, weak-geometry,
    # or arithmetic-invalid synthetic rows; leaving it implicit means a stray
    # environment could silently disable it and let corrupted geometry into
    # training. We make the resolved state explicit and auditable in the log.
    gate_key = "LAYOUTLM_SYNTHETIC_QUALITY_GATE"
    child_env.setdefault(gate_key, "1")
    gate_on = child_env[gate_key] not in {"0", "false", "False"}
    struct_thr = child_env.get(
        "LAYOUTLM_SYNTHETIC_MIN_STRUCTURE_SIMILARITY", "0.60 (default)"
    )
    qual_thr = child_env.get(
        "LAYOUTLM_SYNTHETIC_MIN_CANDIDATE_QUALITY", "0.70 (default)"
    )
    print("-" * 60)
    print("Synthetic quality gate:")
    print(f"  {gate_key} = {child_env[gate_key]} -> "
          f"{'ENABLED' if gate_on else 'DISABLED'}")
    print(f"  min_structure_similarity = {struct_thr}")
    print(f"  min_candidate_quality    = {qual_thr}")
    if not gate_on:
        print(
            "  WARNING: synthetic quality gate is DISABLED for this run — "
            "synthetic rows will be admitted with only basic shape/geometry "
            "checks. Unset env_LAYOUTLM_SYNTHETIC_QUALITY_GATE to re-enable."
        )
    print("-" * 60)

    # Run training
    result = subprocess.run(cmd, check=False, env=child_env)

    if result.returncode != 0:
        print(f"Training failed with exit code {result.returncode}")
        sys.exit(result.returncode)

    # Copy model to SageMaker model directory
    job_name = hps.get(
        "job_name", os.environ.get("TRAINING_JOB_NAME", "sagemaker-job")
    )
    copy_model_to_sagemaker_dir(job_name)

    print("=" * 60)
    print("Training complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
